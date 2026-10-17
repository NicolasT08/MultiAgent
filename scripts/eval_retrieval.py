"""Evaluate retrieval on the reference set Q (Phase 1.4, Eq. 7): Recall@k and MRR.

Methods: BM25 (lexical baseline), every dense model with an index under
data/index/eval/<key>/ (with and without its query instruction), and RRF hybrids
BM25 + dense. Scores are computed against both gold definitions: (c) `gold`, the main
one, and (b) `gold_b`, as a sensitivity check.

The evaluation index is built from corpus_eval.jsonl (corpus minus the queries in Q, so a
query cannot retrieve itself); the system's final index uses the full corpus.jsonl.

Only the dev split is used for model and k selection. `--split test` is for the single
final report and requires `--final`.

Usage:
    python scripts/eval_retrieval.py                      # dev, all available indexes
    python scripts/eval_retrieval.py --models bge-base e5-base
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas.retrieval.bm25 import BM25  # noqa: E402
from formal_mas.retrieval.corpus import load_jsonl  # noqa: E402
from formal_mas.retrieval.evaluate import evaluate, rrf  # noqa: E402

KS = (4, 8, 16, 32)
GOLDS = {"c": "gold", "b": "gold_b"}


def query_vectors(folder: Path, key: str, q: list[dict], instr: bool):
    """Query vectors embedded with the corpus (same run and precision) if present, else encode on CPU."""
    import numpy as np

    label = "instr" if instr else "no_instr"
    if (folder / f"queries_{label}.npy").exists():
        pos = {n: i for i, n in enumerate(json.loads((folder / "queries.json").read_text(encoding="utf-8")))}
        return np.load(folder / f"queries_{label}.npy")[[pos[r["name"]] for r in q]].astype(np.float32)
    from formal_mas.retrieval.encoders import Encoder, load_specs

    spec = load_specs()[key]
    return Encoder(spec).encode([spec.query_text(r["query"], instr) for r in q])


def dense_rankings(key: str, index_root: Path, q: list[dict], depth: int) -> dict[str, list[list[str]]]:
    import faiss

    from formal_mas.retrieval.encoders import load_specs

    folder = index_root / key
    index = faiss.read_index(str(folder / "index.faiss"))
    names = json.loads((folder / "names.json").read_text(encoding="utf-8"))
    settings = {"no_instr": False}
    if load_specs()[key].query_instruction:
        settings["instr"] = True
    out = {}
    for label, instr in settings.items():
        _, ids = index.search(query_vectors(folder, key, q, instr), depth)
        out[f"{key}/{label}"] = [[names[i] for i in row if i >= 0] for row in ids]
    return out


def to_markdown(results: dict, gold: str) -> str:
    cols = [f"R@{k}" for k in KS] + ["MRR"]
    lines = [f"| Method ({gold}) | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for method, res in results.items():
        lines.append(f"| {method} | " + " | ".join(f"{res[gold][c]:.3f}" for c in cols) + " |")
    return "\n".join(lines)


def to_latex(results: dict, gold: str, split: str, n: int) -> str:
    cols = [f"R@{k}" for k in KS] + ["MRR"]
    best = {c: max(r[gold][c] for r in results.values()) for c in cols}
    rows = []
    for method, res in results.items():
        cells = [(f"\\textbf{{{res[gold][c]:.3f}}}" if res[gold][c] == best[c] else f"{res[gold][c]:.3f}") for c in cols]
        rows.append(f"{method.replace('_', chr(92) + '_')} & " + " & ".join(cells) + " \\\\")
    header = " & ".join(["Method"] + [c.replace("@", "@") for c in cols])
    return "\n".join([
        "\\begin{table}[t]", "\\centering",
        f"\\caption{{Retrieval on $Q_{{\\text{{{split}}}}}$ ($n={n}$), gold ({gold}).}}",
        "\\begin{tabular}{l" + "c" * len(cols) + "}", "\\toprule", header + " \\\\", "\\midrule",
        *rows, "\\bottomrule", "\\end{tabular}", "\\end{table}",
    ])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--final", action="store_true", help="required to evaluate on the test split")
    parser.add_argument("--models", nargs="*", help="dense model keys (default: every built eval index)")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "index" / "corpus_eval.jsonl")
    parser.add_argument("--depth", type=int, default=100, help="list length for MRR and RRF")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "retrieval_eval")
    args = parser.parse_args(argv)
    if args.split == "test" and not args.final:
        parser.error("Q-test is reserved for the single final report: pass --final")

    q = load_jsonl(args.out / f"q_{args.split}.jsonl")
    queries = [r["query"] for r in q]
    index_root = args.corpus.parent / "eval"
    keys = args.models if args.models is not None else sorted(
        p.name for p in index_root.glob("*") if (p / "index.faiss").exists() and "_limit" not in p.name)

    corpus = load_jsonl(args.corpus)
    bm25 = BM25([r["text"] for r in corpus])
    names = [r["name"] for r in corpus]
    del corpus
    rankings = {"bm25": [[names[i] for i, _ in bm25.top(t, args.depth)] for t in queries]}
    del bm25

    for key in keys:
        dense = dense_rankings(key, index_root, q, args.depth)
        rankings.update(dense)
        for label, ranked in dense.items():
            rankings[f"rrf(bm25+{label})"] = [rrf([b, d], args.depth) for b, d in zip(rankings["bm25"], ranked)]

    results = {m: {g: evaluate(r, [x[field] for x in q], KS) for g, field in GOLDS.items()}
               for m, r in rankings.items()}

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"results_{args.split}"
    payload = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": args.split, "n": len(q), "ks": list(KS), "depth": args.depth,
        "corpus": args.corpus.name, "results": results,
    }
    stem.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md = "\n\n".join(to_markdown(results, g) for g in GOLDS)
    stem.with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    stem.with_suffix(".tex").write_text("\n\n".join(to_latex(results, g, args.split, len(q)) for g in GOLDS) + "\n",
                                        encoding="utf-8")
    # Per-query rankings, for error analysis and the lean_leansearch comparison.
    with stem.with_name(stem.name + "_rankings.jsonl").open("w", encoding="utf-8") as f:
        for i, item in enumerate(q):
            f.write(json.dumps({"name": item["name"], "top": {m: r[i][:32] for m, r in rankings.items()}},
                               ensure_ascii=False) + "\n")
    sys.stdout.write(md + "\n")


if __name__ == "__main__":
    main()
