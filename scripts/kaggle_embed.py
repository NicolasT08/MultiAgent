"""Kaggle GPU job: embed the full Mathlib corpus and the queries of Q (Phase 1.3).

Runs on Kaggle, not locally. `scripts/kaggle_job.py kernel` copies this file into a
kernel folder, filling in the JOB block, and pushes it. The attached private dataset
(built by `scripts/kaggle_job.py dataset`) holds:
    corpus.jsonl    name, signature, text of every declaration in data/index/corpus.jsonl
    queries.jsonl   name, split, query of every item in Q (no gold sets)
    models.yaml     copy of config/models.yaml (model ids are never written in code)
    encoders.py     copy of src/formal_mas/retrieval/encoders.py

Output in /kaggle/working/<key>/: corpus.npy (fp16 storage, ℓ2-normalised), queries.json,
queries_no_instr.npy and queries_instr.npy (fp32 storage), meta.json.
With LIMIT set, only a seeded random sample of LIMIT corpus texts is embedded to measure speed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
import time
from pathlib import Path

# --- JOB (rewritten by scripts/kaggle_job.py) ---
KEYS = ["bge-base"]
LIMIT = None
PRECISION = "fp16"
BATCH_SIZE = 128
# --- end JOB ---

OUT = Path("/kaggle/working")


def find_input(name: str) -> Path:
    hits = sorted(Path("/kaggle/input").rglob(name))
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def names_sha(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def main() -> None:
    import numpy as np
    import sentence_transformers
    import torch
    import transformers

    spec_mod = importlib.util.spec_from_file_location("encoders", find_input("encoders.py"))
    encoders = importlib.util.module_from_spec(spec_mod)
    sys.modules["encoders"] = encoders
    spec_mod.loader.exec_module(encoders)

    specs = encoders.load_specs(find_input("models.yaml"))
    corpus = load_jsonl(find_input("corpus.jsonl"))
    queries = load_jsonl(find_input("queries.jsonl"))
    total = len(corpus)
    if LIMIT:
        corpus = random.Random(0).sample(corpus, LIMIT)
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"gpu={gpu} corpus={len(corpus)}/{total} queries={len(queries)}", flush=True)

    for key in KEYS:
        spec = specs[key]
        out = OUT / key
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        enc = encoders.Encoder(spec, batch_size=BATCH_SIZE, device=device, precision=PRECISION)
        load_s = time.perf_counter() - t0

        enc.encode([spec.doc_text(r) for r in corpus[:BATCH_SIZE]])  # warm-up
        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        vecs = enc.encode([spec.doc_text(r) for r in corpus])
        if device == "cuda":
            torch.cuda.synchronize()
        corpus_s = time.perf_counter() - t0
        np.save(out / "corpus.npy", vecs.astype(np.float16))

        qtexts = [r["query"] for r in queries]
        np.save(out / "queries_no_instr.npy", enc.encode([spec.query_text(t, False) for t in qtexts]))
        if spec.query_instruction:
            np.save(out / "queries_instr.npy", enc.encode([spec.query_text(t, True) for t in qtexts]))
        (out / "queries.json").write_text(json.dumps([r["name"] for r in queries]), encoding="utf-8")

        rate = len(corpus) / corpus_s
        meta = {
            "embedding_key": key,
            "hf_id": spec.hf_id,
            "hf_revision": encoders.model_revision(spec.hf_id),
            "backend": spec.backend,
            "max_length": spec.max_length,
            "doc_prefix": spec.doc_prefix,
            "doc_template": spec.doc_template,
            "query_instruction": spec.query_instruction,
            "dim": enc.dim,
            "precision": PRECISION,
            "storage": {"corpus": "float16", "queries": "float32"},
            "batch_size": BATCH_SIZE,
            "n_corpus": len(corpus),
            "corpus_names_sha256": names_sha([r["name"] for r in corpus]),
            "limit": LIMIT,
            "n_queries": len(queries),
            "device": gpu,
            "versions": {"torch": torch.__version__, "transformers": transformers.__version__,
                         "sentence_transformers": sentence_transformers.__version__},
            "model_load_s": round(load_s, 1),
            "corpus_embed_s": round(corpus_s, 1),
            "texts_per_s": round(rate, 1),
            "est_full_corpus_min": round(total / rate / 60, 1) if LIMIT else None,
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(json.dumps({k: meta[k] for k in ("embedding_key", "texts_per_s", "corpus_embed_s",
                                              "est_full_corpus_min", "dim", "precision")}), flush=True)
        del enc
        if device == "cuda":
            torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
