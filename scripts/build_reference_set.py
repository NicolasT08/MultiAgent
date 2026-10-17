"""Build the retrieval reference set Q (Phase 1.2) from the pinned Mathlib environment.

1. Run scripts/extract_proof_consts.lean with `lake env lean` (offline data building,
   CLAUDE.md §7) over the project-area modules → data/index/proof_consts.jsonl (~8 min).
2. Derive gold lemmas, select ~200 balanced queries and split them 2/3 dev, 1/3 test.

Outputs (data/retrieval_eval/): q_dev.jsonl, q_test.jsonl, infra_constants.json, meta.json.
Also writes data/index/corpus_eval.jsonl: the corpus without the Q theorems, from which the
evaluation index is built (the queries must not retrieve themselves).

Usage:
    python scripts/build_reference_set.py [--size 200] [--seed 2026] [--skip-extract]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas.retrieval.corpus import AREA_PREFIXES, SourceIndex, load_jsonl  # noqa: E402
from formal_mas.retrieval.reference_set import (  # noqa: E402
    PROJECT_AREAS,
    build_candidates,
    infrastructure,
    select,
    split,
    summary,
)

EXTRACT_SCRIPT = ROOT / "scripts" / "extract_proof_consts.lean"


def run_extraction(lean_project: Path, out: Path) -> None:
    """Dump proof-term constants of the project-area modules. Raises if Lean fails."""
    out.parent.mkdir(parents=True, exist_ok=True)
    prefixes = sorted({p for area in PROJECT_AREAS for p in AREA_PREFIXES[area]})
    env = {**os.environ, "FORMAL_MAS_CONSTS_OUT": str(out.resolve()),
           "FORMAL_MAS_MODULE_PREFIXES": ",".join(prefixes)}
    proc = subprocess.run(
        ["lake", "env", "lean", str(EXTRACT_SCRIPT)], cwd=lean_project, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)


def write_jsonl(rows, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lean-project", type=Path, default=ROOT / "lean_project")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data" / "index")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "retrieval_eval")
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--infra-pct", type=float, default=1.0)
    parser.add_argument("--skip-extract", action="store_true", help="reuse proof_consts.jsonl")
    args = parser.parse_args(argv)

    consts_path = args.index_dir / "proof_consts.jsonl"
    if not args.skip_extract:
        run_extraction(args.lean_project, consts_path)

    corpus_rows = load_jsonl(args.index_dir / "corpus.jsonl")
    corpus = {r["name"]: r for r in corpus_rows}
    entries = load_jsonl(consts_path)
    # Population for infrastructure frequencies: hand-written theorems of the project areas.
    population = [e for e in entries
                  if e["name"] in corpus and corpus[e["name"]]["origin"] == "manual"
                  and corpus[e["name"]]["area"] in PROJECT_AREAS]
    infra = infrastructure(population, corpus, args.infra_pct)

    sources = SourceIndex(args.lean_project / ".lake" / "packages" / "mathlib")
    candidates = build_candidates(population, corpus, set(infra), sources)
    items = split(select(candidates, args.size, args.seed), args.seed)

    for part in ("dev", "test"):
        write_jsonl([q.to_json() for q in items if q.split == part], args.out / f"q_{part}.jsonl")
    (args.out / "infra_constants.json").write_text(json.dumps({
        "threshold_pct": args.infra_pct,
        "population": len(population),
        "constants": [{"name": c, "pct": round(p, 3)} for c, p in infra.items()],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    excluded = {q.name for q in items}
    write_jsonl((r for r in corpus_rows if r["name"] not in excluded), args.index_dir / "corpus_eval.jsonl")

    cand_by = {a: {"concrete": sum(1 for q in candidates if q.area == a and q.concrete),
                   "abstract": sum(1 for q in candidates if q.area == a and not q.concrete)}
               for a in PROJECT_AREAS}
    stats = summary(items)
    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mathlib_commit": json.loads((args.index_dir / "corpus_meta.json").read_text(encoding="utf-8")).get("mathlib_commit"),
        "seed": args.seed,
        "size": args.size,
        "gold_method": "c: proof-term constants in corpus, minus Mathlib.Tactic and infrastructure, "
                       "whose last name component is written in the proof (gold_b = method b)",
        "gold_range": [1, 10],
        "infra_pct": args.infra_pct,
        "population": len(population),
        "candidates": cand_by,
        "corpus_eval_size": len(corpus_rows) - len(excluded),
        "q": stats,
    }
    (args.out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"candidates": cand_by, **stats}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
