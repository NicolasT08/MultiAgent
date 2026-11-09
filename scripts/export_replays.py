"""Export recorded timelines from runs/ to demo/replays/*.json for the demo's Replay mode.

    python scripts/export_replays.py --candidates   # list problems worth replaying (prints only)
    python scripts/export_replays.py --dry-run      # show what SELECTION would export
    python scripts/export_replays.py                # write demo/replays/*.json + results.md

Reads runs/ (never writes there). Only the paid `experiment` runs of the `test` split are used
(the ones of docs/results/results.md); for each (config, problem) the last row that is not
`budget_stop` is taken.

What a replay keeps (a whitelist, nothing else leaves runs/): per event only the fields of
KEEP; for Generator calls, the strategy and sketch parsed from the model's JSON (no raw model
output, prompts, cache keys, fingerprints, paths, argv, git or platform data); the problem's
metadata and formal statement from data/eval.jsonl, with the informal text dropped for
PutnamBench rows (MAA permission); the result's status, proof, C2 solution and call/cost totals.
results.md: Tables 1 and 2 of docs/results/results.md, copied verbatim.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
OUT = ROOT / "demo" / "replays"
SCHEMA = 1
MAX_TEXT = 4000

# (file stem, problem id, config, title, note) — chosen with the team before exporting.
SELECTION: list[tuple[str, str, str, str, str]] = [
    ("1_amc12_2001_p9_C5", "amc12_2001_p9", "C5", "Medium: solved after one retry",
     "The first Lean attempt is rejected by the parser; the Critic classifies it as `syntax` and retries, "
     "and the second attempt is accepted."),
    ("2_imo_1964_p1_2_C5", "imo_1964_p1_2", "C5", "Hard (IMO 1964): solved by C5",
     "Two tactic failures, two retries guided by Lean's errors, then a proof the kernel accepts. "
     "C4 (no Critic) does not solve it."),
    ("3_algebra_absapb_C5", "algebra_absapbon1pabsapbleqsumabsaon1pabsa", "C5",
     "Critic: retries and a replan (C4 fails)",
     "Missing-lemma errors make the Critic retry with more retrieved lemmas and finally replan; "
     "C4, which stops after the first failure, does not solve this problem (see the side-by-side view)."),
    ("3b_algebra_absapb_C4", "algebra_absapbon1pabsapbleqsumabsaon1pabsa", "C4",
     "Same problem without the Critic (C4)",
     "C4 is the full system without the Critic: the first failed Lean attempt ends the problem, "
     "with no retry and no replan. In the recorded run its 5 LLM calls were answered from the on-disk LLM "
     "cache (the same prompts as C5's first steps), so it took 2 s and $0 (nominal cost $0.0039)."),
    ("4_imo_1979_p1_C2", "imo_1979_p1", "C2", "Baseline B claims a proof: not confirmed by Lean",
     "Baseline B writes a natural-language proof and judges it correct itself. Formalizing that proof, "
     "Lean accepts none of the attempts, and no configuration solved this problem. The claim is "
     "*not confirmed by Lean*; that does not make it incorrect (it may be a correct argument that failed "
     "to formalize). A self-assessed claim cannot tell the two apart; only the Lean kernel can."),
]

COMMON = ("event", "level")
KEEP: dict[str, tuple[str, ...]] = {
    "problem_start": ("problem_id", "config"),
    "prepass": ("targets", "found", "seconds"),
    "llm_call": ("role", "model_returned", "latency_s", "cost_usd", "cached", "finish_reason",
                 "completion_tokens", "reasoning_tokens"),
    "plan_proposed": ("subgoals", "closing", "rationale"),
    "plan_skeleton": ("stage", "compiles", "errors"),
    "plan_refute_check": ("hypotheses_inconsistent", "refuted", "seconds"),
    "plan_closing": ("accepted", "reason"),
    "plan_closing_auto": ("reason", "failed_reason"),
    "plan_fallback": ("reason",),
    "plan_syntax_rejected": ("reason", "detail"),
    "retrieve": ("subgoal", "expanded", "lemmas"),
    "sketch_vote": ("subgoal", "votes", "strategy", "samples", "status"),
    "lean_verify": ("status", "sorry", "axioms", "seconds"),
    "formalize_failed": ("subgoal", "reason", "detail", "tactics", "errors"),
    "formalize_verified": ("subgoal", "tactics"),
    "critique": ("subgoal", "stage", "reason", "error_class", "action", "route", "attempts", "replans",
                 "calls", "missing_identifier", "suggestions"),
    "subgoal_solved": ("subgoal", "solved_by", "attempts"),
    "assemble": ("accepted", "reason", "errors"),
    "pipeline_end": ("status", "solved", "stage", "error_classes", "replans"),
    "self_assessment": ("sample", "verdict", "confidence"),
    "claim_check_start": (),
    "claim_check_attempt": ("attempt", "reason", "error_class"),
    "claim_check_end": ("status", "accepted", "attempts", "error_classes"),
    "invalid_statement": ("error",),
    "llm_retry": (), "llm_json_invalid": (), "llm_json_repaired": (), "llm_length_finish": (),
    "problem_end": ("status", "solved", "claimed_solved", "llm", "wall_s"),
}
LLM_SUMMARY = ("llm_calls", "b_max", "cached_calls", "cost_usd")
RESULT_TABLES = ("### Table 1.", "### Table 2.")

__all__ = ["KEEP", "SELECTION", "clean_event", "export_one", "find_row", "results_tables"]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _clip(value):
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + " … (truncated)"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def clean_event(e: dict, t0: datetime) -> dict | None:
    """Whitelisted copy of a trace event with `t` = seconds since problem_start; None = dropped."""
    name = e.get("event")
    if name not in KEEP:
        return None
    out = {"t": round((_ts(e["ts"]) - t0).total_seconds(), 3)} if e.get("ts") else {"t": 0.0}
    for key in (*COMMON, *KEEP[name]):
        if key in e:
            out[key] = _clip(e[key])
    if name == "problem_end" and isinstance(out.get("llm"), dict):
        out["llm"] = {k: out["llm"][k] for k in LLM_SUMMARY if k in out["llm"]}
    if name == "llm_call" and e.get("role") == "generator":
        try:
            data = json.loads(e.get("content") or "")
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and data.get("sketch"):
            out["strategy"], out["sketch"] = data.get("strategy"), _clip(str(data["sketch"]))
    return out


def _experiment_runs(config: str) -> list[Path]:
    """test-split runs of `config` with profile `experiment` (not experiment_pro), oldest first."""
    out = []
    for d in sorted(RUNS.glob(f"*_{config}_experiment*")):
        cfg_path = d / "run_config.json"
        if not cfg_path.is_file() or not (d / "results.jsonl").is_file():
            continue
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        split = (cfg.get("dataset") or {}).get("split")
        if cfg.get("profile") == "experiment" and split == "test" and cfg.get("config") == config:
            out.append(d)
    return out


def find_row(config: str, problem_id: str) -> tuple[Path, dict] | None:
    """(run folder, result row): the last row of the problem that is not budget_stop."""
    found = None
    for d in _experiment_runs(config):
        for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("problem_id") == problem_id and row.get("status") != "budget_stop":
                found = (d, row)
    return found


def _problem_events(run_dir: Path, problem_id: str) -> list[dict]:
    """Events of the problem's last attempt in the run (from its last problem_start)."""
    events = []
    with (run_dir / "trace.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if f'"{problem_id}"' not in line:
                continue
            e = json.loads(line)
            if e.get("problem_id") != problem_id:
                continue
            if e.get("event") == "problem_start":
                events = []
            events.append(e)
    return events


def _dataset() -> dict[str, dict]:
    path = ROOT / "data" / "eval.jsonl"
    return {r["id"]: r for r in map(json.loads, filter(str.strip, path.read_text(encoding="utf-8").splitlines()))}


def _problem(p: dict) -> dict:
    withheld = "MAA" in str(p.get("license") or "")
    out = {k: p.get(k) for k in ("id", "split", "area", "difficulty", "source", "license")}
    out["informal"] = None if withheld else p.get("informal")
    if withheld:
        out["informal_withheld"] = True
    out["statement"] = p.get("statement")
    return out


def _result(row: dict, withheld: bool) -> dict:
    out = {k: row.get(k) for k in ("status", "solved", "claimed_solved", "stage", "error_classes", "replans", "wall_s")}
    out["llm"] = {k: (row.get("llm") or {}).get(k) for k in LLM_SUMMARY}
    if row.get("proof") and row.get("solved"):
        out["proof"] = row["proof"]
    if row.get("config") == "C2" and not withheld:  # a C2 solution restates the problem
        out["solution"] = _clip(row.get("solution"))
        out["final_answer"] = row.get("final_answer")
    if row.get("check"):
        out["check"] = {"status": row["check"].get("status"), "accepted": row["check"].get("accepted"),
                        "attempts": len(row["check"].get("attempts") or [])}
    return out


def _models(run_dir: Path) -> dict:
    cfg = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    roles = ((cfg.get("models") or {}).get("roles") or {})
    return {role: spec.get("model_id") for role, spec in roles.items() if isinstance(spec, dict)}


def export_one(problem_id: str, config: str, title: str, note: str, dataset: dict) -> dict:
    hit = find_row(config, problem_id)
    if hit is None:
        raise SystemExit(f"no {config} experiment/test row for {problem_id}")
    run_dir, row = hit
    raw = _problem_events(run_dir, problem_id)
    if not raw or raw[0].get("event") != "problem_start":
        raise SystemExit(f"no trace for {problem_id} in {run_dir.name}")
    t0 = _ts(raw[0]["ts"])
    events = [c for c in (clean_event(e, t0) for e in raw) if c is not None]
    problem = _problem(dataset[problem_id])
    return {"schema": SCHEMA, "title": title, "note": note, "config": config, "run_id": run_dir.name,
            "profile": "experiment", "models": _models(run_dir), "problem": problem, "events": events,
            "result": _result(row, bool(problem.get("informal_withheld")))}


def results_tables(text: str) -> str:
    """Tables 1 and 2 of docs/results/results.md (each up to the next `### ` heading)."""
    parts = ["# Results on the `test` split (n = 30)\n",
             "Copied from `docs/results/results.md` (generated by `docs/results/make_tables.py`). "
             "C1 = Baseline A (LLM writes the Lean proof, no feedback), C2 = Baseline B (natural language, "
             "self-assessed, never verified), C3 = full system without RAG, C4 = without the Critic, "
             "C5 = full system.\n"]
    for head in RESULT_TABLES:
        m = re.search(rf"^{re.escape(head)}.*?(?=^### |^## |\Z)", text, flags=re.M | re.S)
        if m:
            parts.append(m.group(0).rstrip() + "\n")
    return "\n".join(parts)


def _table_rows(text: str, head: str) -> list[list[str]]:
    m = re.search(rf"^{re.escape(head)}.*?(?=^### |^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return []
    rows = [ln.strip().strip("|").split("|") for ln in m.group(0).splitlines() if ln.startswith("|")]
    return [[c.strip() for c in r] for r in rows[2:]]  # skip the header and the --- line


def _nums(cell: str) -> list[float]:
    return [float(x) for x in re.findall(r"[+-]?\d+(?:\.\d+)?", cell)]


def results_data(text: str) -> dict:
    """Numbers of Tables 1 and 2 (results.md) for the demo's plain-language results tab."""
    sr, c2 = [], None
    for r in _table_rows(text, "### Table 1."):
        if r[0].startswith("C2"):
            claimed, fpr = _nums(r[4]), _nums(r[5])
            c2 = {"claimed": int(claimed[0]), "n": int(claimed[1]), "claimed_pct": claimed[2],
                  "not_confirmed": int(fpr[0]), "checked": int(fpr[1]), "not_confirmed_pct": fpr[2],
                  "not_confirmed_ci": fpr[3:5]}
        elif re.fullmatch(r"C\d", r[0]):
            solved, ci = _nums(r[1]), _nums(r[3])
            sr.append({"config": r[0], "solved": int(solved[0]), "n": int(solved[1]), "pct": float(r[2]), "ci": ci[:2]})
    tests = []
    for r in _table_rows(text, "### Table 2."):
        if re.fullmatch(r"H\d", r[0]):
            a, b = re.findall(r"C\d", r[1])[:2]
            tests.append({"id": r[0], "a": a, "b": b, "delta_pp": _nums(r[4])[0], "ci": _nums(r[5])[:2],
                          "p_adj": _nums(r[7])[0], "significant": r[8].lower().startswith("yes")})
    return {"source": "docs/results/results.md (Tables 1 and 2)", "sr": sr, "c2": c2, "tests": tests}


def candidates() -> None:
    def rows(config):
        out = {}
        for d in _experiment_runs(config):
            for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r.get("status") != "budget_stop":
                        out[r["problem_id"]] = r
        return out

    c = {k: rows(k) for k in ("C1", "C2", "C3", "C4", "C5")}
    print("C5 solved and C4 not (the Critic's contribution):")
    for pid, r in c["C5"].items():
        if r.get("solved") and not c["C4"].get(pid, {}).get("solved"):
            crit = [(x["error_class"], x["action"]) for x in r.get("critiques") or []]
            print(f"  {pid:45} {r['difficulty']:6} calls={r['llm']['llm_calls']:2} {r['wall_s'] / 60:4.1f} min {crit}")
    print("C2 claimed but the Lean check did not verify:")
    for pid, r in c["C2"].items():
        if r.get("claimed_solved") and (r.get("check") or {}).get("status") != "verified":
            by = [k for k in ("C1", "C3", "C4", "C5") if c[k].get(pid, {}).get("solved")]
            print(f"  {pid:30} {r['difficulty']:6} solved by {by or 'no configuration'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", action="store_true", help="list candidate problems and exit")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    if args.candidates:
        candidates()
        return 0
    dataset = _dataset()
    if not args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)
    for stem, pid, config, title, note in SELECTION:
        rep = export_one(pid, config, title, note, dataset)
        r = rep["result"]
        print(f"{stem:30} {config} {pid:45} {r['status']:15} events={len(rep['events']):3} "
              f"calls={r['llm']['llm_calls']} from {rep['run_id']}")
        if not args.dry_run:
            (args.out / f"{stem}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
                                                   encoding="utf-8")
    tables = results_tables((ROOT / "docs" / "results" / "results.md").read_text(encoding="utf-8"))
    data = results_data((ROOT / "docs" / "results" / "results.md").read_text(encoding="utf-8"))
    if not (data["sr"] and data["c2"] and data["tests"]):
        raise SystemExit("could not read Tables 1 and 2 of docs/results/results.md")
    if not args.dry_run:
        (args.out / "results.md").write_text(tables, encoding="utf-8")
        (args.out / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"results.md: {len(tables)} chars" + (" (dry run, nothing written)" if args.dry_run else f" → {args.out}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
