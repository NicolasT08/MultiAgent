"""Run one configuration over a dataset → runs/<UTC time>_<config>_<profile>/.

    MODEL_PROFILE=dev python scripts/run_eval.py --config C1 --data data/smoke.jsonl --echo
    MODEL_PROFILE=dev python scripts/run_eval.py --config C2 --data data/smoke.jsonl --limit 3

Phase 3 provides C1 (baseline A) and C2 (baseline B); C3-C5 arrive with the graph (Phase 6).
Outputs: run_config.json (RNF-2, rewritten at the end), trace.jsonl (RNF-5), results.jsonl
(one line per problem). The profile comes from MODEL_PROFILE (models.yaml → default_profile).
Paid profiles need the team's explicit permission (CLAUDE.md §2).

Spend (llm/spend.py, pipeline.yaml → spend): prints the project's spend so far and what is
left before the first problem, and the run's cost at the end. When a paid call could pass
spend.total_usd or spend.run_usd the problem is `budget_stop` and the run stops there
(run_config.json status `budget_stop`); the remaining problems are not run.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from formal_mas.baselines.baseline_a import run_baseline_a  # noqa: E402
from formal_mas.baselines.baseline_b import run_baseline_b  # noqa: E402
from formal_mas.config import ROOT, load_models, load_pipeline  # noqa: E402
from formal_mas.lean.runner import LeanRunner  # noqa: E402
from formal_mas.llm.client import LLMClient  # noqa: E402
from formal_mas.llm.pricing import in_peak  # noqa: E402
from formal_mas.llm.spend import SpendGuard  # noqa: E402
from formal_mas.llm.specs import profile_name  # noqa: E402
from formal_mas.run_config import build_run_config, write_run_config  # noqa: E402
from formal_mas.tracing import Budget, Tracer, iso, utc_now  # noqa: E402

CONFIGS = {"C1": "baseline_a", "C2": "baseline_b"}
LOG = logging.getLogger("formal_mas.run_eval")


def load_problems(path: Path, split: str | None, limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if split:
        rows = [r for r in rows if r.get("split") == split]
    return rows[:limit] if limit else rows


def summarize(rows: list[dict]) -> dict:
    status: dict[str, int] = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    return {"problems": len(rows), "status": status,
            "solved": sum(1 for r in rows if r.get("solved")),
            "claimed_solved": sum(1 for r in rows if r.get("claimed_solved")),
            "llm_calls": sum(r["llm"]["llm_calls"] for r in rows),
            "length_finishes": sum(r["llm"]["length_finishes"] for r in rows),
            "cost_usd": round(sum(r["llm"]["cost_usd"] for r in rows), 6),
            "cost_usd_nominal": round(sum(r["llm"]["cost_usd_nominal"] for r in rows), 6)}


def spend_line(info: dict) -> str:
    return (f"spend: project ${info['total_spent_usd']:.4f} of ${info['total_usd_cap']:.2f} "
            f"(${info['total_remaining_usd']:.4f} left); run cap ${info['run_usd_cap']:.2f}")


def main(argv: list[str] | None = None, *, models_cfg: dict | None = None, pipeline_cfg: dict | None = None,
         client_kwargs: dict | None = None, runner_factory=LeanRunner, runs_dir: Path | None = None) -> int:
    """Keyword arguments are for offline tests (fake transport, clock and Lean; temporary folders)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, choices=sorted(CONFIGS))
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "eval.jsonl")
    ap.add_argument("--split", default="dev", help="dev | test | all")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None, help="default: pipeline.yaml → run.seed")
    ap.add_argument("--run-name", default=None, help="suffix for the run folder")
    ap.add_argument("--echo", action="store_true", help="print one line per problem")
    ap.add_argument("--run-usd", type=float, default=None,
                    help="lower the per-run spend cap for this run (never above pipeline.yaml → spend.run_usd)")
    args = ap.parse_args(argv)

    if not args.data.exists():
        ap.error(f"{args.data} does not exist (data/eval.jsonl arrives in Phase 7; try --data data/smoke.jsonl)")
    models_cfg = models_cfg if models_cfg is not None else load_models()
    pipeline_cfg = pipeline_cfg if pipeline_cfg is not None else load_pipeline()
    profile = profile_name(models_cfg)
    split = None if args.split == "all" else args.split
    problems = load_problems(args.data, split, args.limit)
    seed = args.seed if args.seed is not None else int((pipeline_cfg.get("run") or {}).get("seed", 0))
    b_max = int((pipeline_cfg.get("budget") or {})["b_max"])
    role = CONFIGS[args.config]
    attempts = int(((pipeline_cfg.get("baselines") or {}).get(role) or {}).get("attempts", 1))

    started = utc_now()
    run_id = f"{started:%Y%m%dT%H%M%SZ}_{args.config}_{profile}" + (f"_{args.run_name}" if args.run_name else "")
    run_dir = (runs_dir or ROOT / "runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    console = logging.StreamHandler(sys.stderr)  # warnings only; INFO events go to the trace
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(console)
    tracer = Tracer(run_dir / "trace.jsonl")
    tracer.attach_logging()
    spend = SpendGuard.from_config(pipeline_cfg, run_id=run_id)
    if args.run_usd is not None and spend.run_usd is not None:
        spend.run_usd = min(spend.run_usd, args.run_usd)
    if spend.stopped:
        LOG.warning("spend guard stopped before the run: %s; every paid call will be refused", spend.stopped)
    elif spend.configured:
        print(spend_line(spend.info()), flush=True)
    else:
        LOG.warning("pipeline.yaml has no `spend` section (or no `spend.ledger`): every paid call will be refused")
    client = LLMClient(profile, models_cfg=models_cfg, pipeline_cfg=pipeline_cfg, tracer=tracer, spend=spend,
                       **(client_kwargs or {}))
    peak = in_peak(client.priced_models(), started)
    if peak:
        tracer.event("run_peak_warning", level="warning", models=peak, started=iso(started))
        LOG.warning("starting inside a peak-price window for %s (models.yaml → pricing.peak_windows_utc); "
                    "off-peak is cheaper", peak)
    runner = runner_factory() if role == "baseline_a" else None

    def config(**extra) -> dict:
        return build_run_config(run_id=run_id, config_name=args.config, seed=seed, dataset=args.data, split=split,
                                llm_info=client.info(), lean_run_info=runner.run_info() if runner else None,
                                pipeline_cfg=pipeline_cfg, argv=sys.argv, extra={"spend": spend.info(), **extra})

    write_run_config(run_dir, config(status="running", problems=[p["id"] for p in problems]))
    tracer.event("run_start", run_id=run_id, config=args.config, profile=profile, problems=len(problems),
                 b_max=b_max, attempts=attempts, seed=seed, spend=spend.info())
    rows = []
    try:
        if runner:
            runner.start()  # import Mathlib once (~33 s warm, ~22 min cold on the HDD)
        with (run_dir / "results.jsonl").open("a", encoding="utf-8") as results:
            for p in problems:
                with tracer.context(problem_id=p["id"]):
                    budget = Budget(b_max)
                    t0 = time.perf_counter()
                    tracer.event("problem_start", config=args.config)
                    if role == "baseline_a":
                        res = run_baseline_a(p, client, runner, budget, tracer, attempts=attempts)
                    else:
                        res = run_baseline_b(p, client, budget, tracer, attempts=attempts)
                    row = {"problem_id": p["id"], "config": args.config, "profile": profile, "split": p.get("split"),
                           "area": p.get("area"), "difficulty": p.get("difficulty"), **res,
                           "llm": budget.summary(), "wall_s": round(time.perf_counter() - t0, 2)}
                    tracer.event("problem_end", status=row["status"], solved=row.get("solved"),
                                 claimed_solved=row.get("claimed_solved"), llm=row["llm"], wall_s=row["wall_s"])
                results.write(json.dumps(row, ensure_ascii=False) + "\n")
                results.flush()
                rows.append(row)
                if args.echo:
                    print(f"{p['id']:<28} {row['status']:<17} calls={row['llm']['llm_calls']:<3} "
                          f"cost=${row['llm']['cost_usd']:.4f} length={row['llm']['length_finishes']} "
                          f"{row['wall_s']:.1f}s", flush=True)
                if row["status"] == "budget_stop":
                    tracer.event("run_budget_stop", level="error", problem_id=p["id"], reason=spend.stopped,
                                 not_run=len(problems) - len(rows))
                    break
    finally:
        summary = summarize(rows)
        summary["spend"] = spend.info()
        tracer.event("run_end", **summary)
        if spend.stopped:
            status = "budget_stop"
        else:
            status = "finished" if len(rows) == len(problems) else "interrupted"
        write_run_config(run_dir, config(status=status, problems=[p["id"] for p in problems],
                                         finished_utc=iso(utc_now()), summary=summary))
        if runner:
            runner.close()
        tracer.close()
    info = summary["spend"]
    print(f"run cost: ${info['run_spent_usd']:.6f} in {info['run_paid_calls']} paid calls"
          + (f"; BUDGET STOP: {spend.stopped}" if spend.stopped else ""), flush=True)
    if spend.configured and info["total_spent_usd"] is not None:
        print(spend_line(info), flush=True)
    shown = run_dir.relative_to(ROOT) if run_dir.is_relative_to(ROOT) else run_dir
    print(json.dumps({"run_dir": shown.as_posix(), "status": status, **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
