"""Build data/eval.jsonl from the pinned sources and data/curation/criteria.yaml.

    bash scripts/fetch_eval_sources.sh       # pinned miniF2F port and PutnamBench → data/raw/
    python scripts/build_eval_set.py         # draws, compiles with `sorry`, writes the set

For each cell the problems are taken in seeded order; a problem that does not compile, or that the
fidelity review discarded (criteria.yaml → discards), is logged in data/curation/discarded.json and
replaced by the next one. No LLM runs here, and only Lean's compile-with-sorry check is used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas import evalset as ev  # noqa: E402
from formal_mas.lean.runner import LeanRunner, compiles  # noqa: E402
from formal_mas.lean.statement import parse_theorem  # noqa: E402


def load_pools(raw: Path, criteria: dict) -> dict[tuple[str, str], list[dict]]:
    labels = ev.flatten_labels(criteria["labels"])
    seen = set(criteria["seen_in_repo"])
    pools: dict[tuple[str, str], list[dict]] = {}
    for rec in ev.load_minif2f(raw / "minif2f.jsonl"):
        area = ev.area_of(rec["id"], labels)
        if area and rec["id"] not in seen:
            pools.setdefault((ev.difficulty_of(rec["id"]), area), []).append(rec)
    informal = json.loads((raw / "putnam" / "informal" / "putnam.json").read_text(encoding="utf-8"))
    for entry in informal:
        path = raw / "putnam" / "lean4" / "src" / f"{entry['problem_name']}.lean"
        if not path.exists():
            continue
        rec = ev.putnam_record(entry["problem_name"], entry["tags"], entry["informal_statement"],
                               path.read_text(encoding="utf-8"))
        if rec and ev.putnam_eligible(rec):
            pools.setdefault(("hard", "combinatorics"), []).append(rec)
    return pools


def finalize(rec: dict, criteria: dict) -> dict:
    """Apply the only allowed edit and add provenance. Nothing else changes the statement."""
    src = criteria["sources"][rec["origin"]]
    out = {"id": rec["id"], "source": src["name"], "source_commit": src["commit"], "source_id": rec["id"],
           "source_split": rec["source_split"], "license": src["license"], "informal": rec["informal"],
           "header": rec["header"]}
    if rec.get("tags"):
        out["source_tags"] = rec["tags"]
    converted = ev.convert_big_operators(rec["formal_statement"])
    out["formal_statement"] = converted
    if converted != rec["formal_statement"]:
        out["formal_statement_original"] = rec["formal_statement"]
        out["converted"] = ev.CONVERSION_MARK
    out["statement"] = rec["header"] + converted
    return out


def check_compiles(runner: LeanRunner, rec: dict) -> str | None:
    """None if the statement parses and compiles with `sorry`, else the reason it does not."""
    try:
        thm = parse_theorem(rec["statement"], header=rec["header"])
    except ValueError as exc:
        return f"parse_theorem: {exc}"
    result = runner.verify(f"{rec['header']}{rec['formal_statement'].rstrip()} sorry\n", thm.name)
    if compiles(result):
        return None
    if result.status != "finished":  # a slow machine must not change the set
        raise SystemExit(f"{rec['id']}: Lean did not finish ({result.status}); nothing written")
    errors = " | ".join(m.text for m in result.errors)
    return f"does not compile with sorry ({result.status}): {errors[:300]}"


def reserved_minimum(criteria: dict, pools: dict) -> dict[tuple[str, str], int]:
    reserved = set(criteria["reserved_dev"])
    return {cell: n for cell, pool in pools.items() if (n := sum(r["id"] in reserved for r in pool))}


def build(raw: Path, criteria: dict, runner: LeanRunner) -> tuple[list[dict], list[dict]]:
    seed = criteria["seed"]
    pools = load_pools(raw, criteria)
    manual = criteria["discards"] or {}
    ev.check_discards(manual)
    reserved = set(criteria["reserved_dev"])
    dev_n = ev.dev_counts(criteria["grid"], criteria["total_dev"], seed, reserved_minimum(criteria, pools))
    chosen: list[dict] = []
    discarded: list[dict] = []
    for difficulty, row in criteria["grid"].items():
        for area, target in row.items():
            pool = {r["id"]: r for r in pools.get((difficulty, area), [])}
            if not target:
                continue
            order = [i for i in criteria["reserved_dev"] if i in pool]
            order += ev.seeded_order([i for i in pool if i not in reserved], seed)
            cell: list[dict] = []
            for problem_id in order:
                if len(cell) == target:
                    break
                rec = finalize(pool[problem_id], criteria)
                if problem_id in manual:
                    reason, stage = manual[problem_id], "fidelity_review"
                else:
                    reason, stage = check_compiles(runner, rec), "compile_with_sorry"
                if reason:
                    discarded.append({"id": problem_id, "difficulty": difficulty, "area": area,
                                      "stage": stage, "reason": reason})
                    continue
                cell.append({**rec, "area": area, "difficulty": difficulty})
            if len(cell) < target:
                raise SystemExit(f"{difficulty}/{area}: only {len(cell)} of {target} problems available")
            splits = ev.assign_split([r["id"] for r in cell], dev_n[(difficulty, area)], reserved, seed)
            chosen += [{**r, "split": splits[r["id"]], "seed": seed} for r in cell]
    return chosen, discarded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--criteria", type=Path, default=ROOT / "data" / "curation" / "criteria.yaml")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "eval.jsonl")
    args = parser.parse_args()
    criteria = yaml.safe_load(args.criteria.read_text(encoding="utf-8"))
    with LeanRunner() as runner:
        rows, discarded = build(args.raw, criteria, runner)
        lean_info = runner.run_info()
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    log = args.criteria.parent / "discarded.json"
    log.write_text(json.dumps(discarded, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    info = args.criteria.parent / "build_info.json"
    info.write_text(json.dumps(lean_info, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    json.dump({"problems": len(rows), "discarded": len(discarded)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
