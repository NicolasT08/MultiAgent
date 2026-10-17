"""Build the retrieval corpus and the dense FAISS index from the pinned Mathlib environment.

Stage 1 (Phase 1.1): run scripts/extract_corpus.lean with `lake env lean` (allowed for
offline data-building scripts, CLAUDE.md §7) to dump data/index/corpus_raw.jsonl, then
post-process it into data/index/corpus.jsonl and data/index/corpus_meta.json.
The full extraction takes ~15 min on a laptop (single process).

Stage 2 (Phase 1.3): the full corpus.jsonl is embedded once per model (key from
config/models.yaml → embeddings.candidates), normally on a Kaggle GPU via
scripts/kaggle_job.py + scripts/kaggle_embed.py (CPU takes 3–27 h per model). From those
vectors `--from-vectors DIR` builds two exact cosine indexes (`IndexFlatIP` over
ℓ2-normalised vectors) per model:
    data/index/full/<key>/  the system's index (whole corpus, used by the Retriever)
    data/index/eval/<key>/  evaluation index = corpus minus the queries of Q (the
                            declarations of corpus_eval.jsonl), with the query vectors
`--embed KEY` does the same on the local CPU.

Usage:
    python scripts/build_index.py [--module-prefix Mathlib] [--skip-extract]
    python scripts/build_index.py --from-vectors data/index/kaggle/output
    python scripts/build_index.py --embed bge-small
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas.retrieval.corpus import (  # noqa: E402
    SourceIndex,
    build_corpus,
    corpus_stats,
    load_jsonl,
    read_raw,
    write_jsonl,
)

EXTRACT_SCRIPT = ROOT / "scripts" / "extract_corpus.lean"


def mathlib_commit(lean_project: Path) -> str | None:
    manifest = json.loads((lean_project / "lake-manifest.json").read_text(encoding="utf-8"))
    for pkg in manifest.get("packages", []):
        if pkg.get("name") == "mathlib":
            return pkg.get("rev")
    return None


def run_extraction(lean_project: Path, raw_path: Path, module_prefix: str) -> None:
    """Dump the environment with Lean. Raises CalledProcessError if Lean fails."""
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "FORMAL_MAS_CORPUS_OUT": str(raw_path.resolve()),
        "FORMAL_MAS_MODULE_PREFIX": module_prefix,
    }
    proc = subprocess.run(
        ["lake", "env", "lean", str(EXTRACT_SCRIPT)],
        cwd=lean_project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)


QUERY_FILES = ("queries.json", "queries_no_instr.npy", "queries_instr.npy")


def names_sha(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def write_index(out: Path, vectors, names: list[str], meta: dict) -> None:
    import faiss

    out.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    faiss.write_index(index, str(out / "index.faiss"))
    (out / "names.json").write_text(json.dumps(names), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({**meta, "size": len(names)}, indent=2), encoding="utf-8")


def build_from_vectors(vec_dir: Path, index_root: Path) -> list[Path]:
    """Turn embedded full-corpus folders (Kaggle output layout) into the two indexes per model:
    full/<key> (system index, whole corpus) and eval/<key> (corpus minus the queries of Q,
    i.e. exactly the declarations of corpus_eval.jsonl, plus the precomputed query vectors)."""
    import numpy as np

    names = [r["name"] for r in load_jsonl(index_root / "corpus.jsonl")]
    eval_names = {r["name"] for r in load_jsonl(index_root / "corpus_eval.jsonl")}
    corpus_meta = json.loads((index_root / "corpus_meta.json").read_text(encoding="utf-8"))
    built = []
    for folder in sorted(p for p in vec_dir.iterdir() if (p / "meta.json").exists()):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if meta.get("limit"):
            print(f"skip {folder.name}: speed-test sample (limit={meta['limit']})")
            continue
        if meta["corpus_names_sha256"] != names_sha(names):
            raise ValueError(f"{folder.name}: vectors were embedded from a different corpus.jsonl")
        vectors = np.load(folder / "corpus.npy").astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)  # undo fp16 rounding
        meta = {**meta, "index": "faiss.IndexFlatIP (l2-normalised vectors = cosine)",
                "mathlib_commit": corpus_meta.get("mathlib_commit"),
                "lean_toolchain": corpus_meta.get("lean_toolchain"),
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        key = meta["embedding_key"]
        write_index(index_root / "full" / key, vectors, names, {**meta, "corpus": "corpus.jsonl"})
        keep = np.array([n in eval_names for n in names])
        eval_dir = index_root / "eval" / key
        write_index(eval_dir, vectors[keep], [n for n, k in zip(names, keep) if k],
                    {**meta, "corpus": "corpus_eval.jsonl (corpus.jsonl minus the queries of Q)"})
        for f in QUERY_FILES:
            if (folder / f).exists():
                shutil.copy(folder / f, eval_dir / f)
        built.append(eval_dir)
        print(f"{key}: full {len(names)}, eval {int(keep.sum())}")
    return built


def embed_local(key: str, index_root: Path, vec_dir: Path, chunk: int, batch_size: int) -> Path:
    """CPU fallback producing the same layout as scripts/kaggle_embed.py (resumable chunks)."""
    import numpy as np

    from formal_mas.retrieval.encoders import Encoder, load_specs, model_revision

    spec = load_specs()[key]
    records = load_jsonl(index_root / "corpus.jsonl")
    out = vec_dir / key
    parts = out / "chunks"
    parts.mkdir(parents=True, exist_ok=True)
    encoder = Encoder(spec, batch_size=batch_size)
    for c, s in enumerate(range(0, len(records), chunk)):
        part = parts / f"{c:04d}.npy"
        if not part.exists():
            np.save(part, encoder.encode([spec.doc_text(r) for r in records[s:s + chunk]]).astype(np.float16))
            print(f"chunk {c}: {s + chunk}/{len(records)}", flush=True)
    np.save(out / "corpus.npy", np.concatenate([np.load(p) for p in sorted(parts.glob("*.npy"))]))
    queries = [r for split in ("dev", "test")
               for r in load_jsonl(index_root.parent / "retrieval_eval" / f"q_{split}.jsonl")]
    qtexts = [r["query"] for r in queries]
    np.save(out / "queries_no_instr.npy", encoder.encode([spec.query_text(t, False) for t in qtexts]))
    if spec.query_instruction:
        np.save(out / "queries_instr.npy", encoder.encode([spec.query_text(t, True) for t in qtexts]))
    (out / "queries.json").write_text(json.dumps([r["name"] for r in queries]), encoding="utf-8")
    meta = {
        "embedding_key": key, "hf_id": spec.hf_id, "hf_revision": model_revision(spec.hf_id),
        "backend": spec.backend, "max_length": spec.max_length, "doc_prefix": spec.doc_prefix,
        "doc_template": spec.doc_template, "query_instruction": spec.query_instruction,
        "dim": encoder.dim, "precision": "fp32", "storage": {"corpus": "float16", "queries": "float32"},
        "n_corpus": len(records), "corpus_names_sha256": names_sha([r["name"] for r in records]),
        "limit": None, "n_queries": len(queries), "device": "cpu",
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lean-project", type=Path, default=ROOT / "lean_project")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "index")
    parser.add_argument("--module-prefix", default="Mathlib", help="only modules under this prefix")
    parser.add_argument("--skip-extract", action="store_true", help="reuse an existing corpus_raw.jsonl")
    parser.add_argument("--from-vectors", type=Path, metavar="DIR",
                        help="stage 2 only: build full/ and eval/ indexes from embedded folders in DIR")
    parser.add_argument("--embed", metavar="KEY", help="stage 2 only: embed on CPU, then build the indexes")
    parser.add_argument("--chunk", type=int, default=10_000, help="--embed: texts per resumable chunk")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)

    if args.embed:
        vec_dir = args.out / "vectors"
        embed_local(args.embed, args.out, vec_dir, args.chunk, args.batch_size)
        args.from_vectors = vec_dir
    if args.from_vectors:
        build_from_vectors(args.from_vectors, args.out)
        return

    raw_path = args.out / "corpus_raw.jsonl"
    if not args.skip_extract:
        run_extraction(args.lean_project, raw_path, args.module_prefix)

    lean_meta, entries = read_raw(raw_path)
    sources = SourceIndex(args.lean_project / ".lake" / "packages" / "mathlib")
    decls, excluded = build_corpus(entries, sources)
    corpus_path = args.out / "corpus.jsonl"
    n = write_jsonl(decls, corpus_path)

    stats = corpus_stats(load_jsonl(corpus_path))
    meta = {
        "mathlib_commit": mathlib_commit(args.lean_project),
        "lean_toolchain": (args.lean_project / "lean-toolchain").read_text(encoding="utf-8").strip(),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extraction": lean_meta,
        "postprocess": "whitespace collapsed; leading name and universe lists `.{...}` removed",
        "excluded": dict(excluded),
        "corpus": stats,
    }
    (args.out / "corpus_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {n} declarations to {corpus_path}")
    print(json.dumps({"excluded": dict(excluded), **stats}, indent=2))


if __name__ == "__main__":
    main()
