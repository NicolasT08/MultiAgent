"""Build data/demo_problems.jsonl: a few extra miniF2F problems for the demo's "New" filter.

    python scripts/build_demo_set.py            # draws, compiles with `sorry`, writes the file
    python scripts/build_demo_set.py --dry-run  # prints the draw, no Lean, nothing written

These problems are NOT part of the study: they are outside data/eval.jsonl, are never used by
run_eval.py's default data, and must reach the demo without any LLM call having been made on
them (so a live solve is really live, with no cache hits). Only Lean's compile-with-sorry check
runs here; no LLM.

Only miniF2F `valid` problems (SOURCE_SPLIT). Same pool, labels and rules as scripts/build_eval_set.py (data/curation/criteria.yaml): area
labels, difficulty by competition, the one allowed big-operator conversion. Excluded: the
evaluation set, data/dev_preview.jsonl, the fidelity discards, earlier compile failures
(data/curation/discarded.json) and any id mentioned in src/, tests/, scripts/ or prompts.
Draw: per difficulty, PER_DIFFICULTY problems in seeded order (seed criteria.seed + 1),
preferring different areas; a problem that does not compile is skipped and logged.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas import evalset as ev  # noqa: E402

OUT = ROOT / "data" / "demo_problems.jsonl"
PER_DIFFICULTY = 2
DRAW_OFFSET = 1  # seed + 1: a draw independent of the evaluation set's
SCAN_DIRS = ("src", "tests", "scripts")
SOURCE_SPLIT = "valid"  # miniF2F split the demo problems come from


def _build_eval_module():
    spec = importlib.util.spec_from_file_location("build_eval_set", ROOT / "scripts" / "build_eval_set.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def mentioned_in_repo(ids: set[str]) -> set[str]:
    """Pool ids that appear in code, tests or prompts (possible contamination of the demo)."""
    text = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and p.suffix in (".py", ".md", ".lean", ".json", ".jsonl", ".yaml", ".txt"):
                text.append(p.read_text(encoding="utf-8", errors="ignore"))
    blob = "\n".join(text)
    return {i for i in ids if i in blob}


def draw(pools: dict, excluded: set[str], seed: int) -> dict[str, list[dict]]:
    """difficulty → candidates in draw order, alternating areas (all of them, for replacement)."""
    out: dict[str, list[dict]] = {}
    for (difficulty, area), pool in sorted(pools.items()):
        ids = ev.seeded_order([r["id"] for r in pool if r["id"] not in excluded], seed + DRAW_OFFSET)
        by_id = {r["id"]: r for r in pool}
        out.setdefault(difficulty, []).append([dict(by_id[i], area=area, difficulty=difficulty) for i in ids])
    ordered = {}
    for difficulty, lists in out.items():
        merged, k = [], 0
        while any(k < len(lst) for lst in lists):  # round robin over areas
            merged += [lst[k] for lst in lists if k < len(lst)]
            k += 1
        ordered[difficulty] = merged
    return ordered


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    bes = _build_eval_module()
    criteria = yaml.safe_load((ROOT / "data" / "curation" / "criteria.yaml").read_text(encoding="utf-8"))
    pools = {cell: [r for r in pool if r.get("origin") != "putnam" and r.get("source_split") == SOURCE_SPLIT]
             for cell, pool in
             bes.load_pools(ROOT / "data" / "raw", criteria).items()}
    pools = {cell: pool for cell, pool in pools.items() if pool}
    all_ids = {r["id"] for pool in pools.values() for r in pool}
    discarded_log = ROOT / "data" / "curation" / "discarded.json"
    excluded = (_ids(ROOT / "data" / "eval.jsonl") | _ids(ROOT / "data" / "dev_preview.jsonl")
                | set((criteria.get("discards") or {}).keys())
                | ({d["id"] for d in json.loads(discarded_log.read_text(encoding="utf-8"))} if discarded_log.is_file() else set()))
    excluded |= mentioned_in_repo(all_ids - excluded)
    candidates = draw(pools, excluded, int(criteria["seed"]))

    runner = None
    if not args.dry_run:
        from formal_mas.lean.runner import LeanRunner

        runner = LeanRunner()
        if not runner.ensure_available():
            raise SystemExit("Lean is not available; nothing written")
    chosen, skipped = [], []
    try:
        for difficulty in ("easy", "medium", "hard"):
            got, areas = [], set()
            for rec in candidates.get(difficulty, []):
                if len(got) == PER_DIFFICULTY:
                    break
                if rec["area"] in areas and any(r["area"] not in areas for r in candidates[difficulty]
                                                if r["id"] not in {g["id"] for g in got}):
                    continue  # prefer a new area while one is left
                out = {**bes.finalize(rec, criteria), "area": rec["area"], "difficulty": difficulty,
                       "split": "demo", "seed": criteria["seed"],
                       "note": "Demo only: not in the study; no LLM call was made on it before the demo."}
                reason = None if runner is None else bes.check_compiles(runner, out)
                if reason:
                    skipped.append({"id": rec["id"], "reason": reason})
                    continue
                got.append(out)
                areas.add(rec["area"])
            chosen += got
    finally:
        if runner is not None:
            runner.close()
    for r in chosen:
        print(f"{r['difficulty']:6} {r['area']:14} {r['id']}")
    for s in skipped:
        print(f"skipped {s['id']}: {s['reason'][:200]}")
    if not args.dry_run:
        args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chosen), encoding="utf-8")
        print(f"wrote {len(chosen)} problems to {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
