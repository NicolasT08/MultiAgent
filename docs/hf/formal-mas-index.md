---
license: apache-2.0
language:
- en
pretty_name: Formal-MAS retrieval index (Mathlib)
tags:
- lean4
- mathlib
- theorem-proving
- retrieval
- faiss
- bm25
size_categories:
- 100K<n<1M
viewer: false
---

# Formal-MAS retrieval index (Mathlib)

The lemma index used by the Retriever of [Formal-MAS](https://huggingface.co/NicolasT08/formal-mas),
a multi-agent prover for olympiad problems with Lean 4 verification. It is a prebuilt copy of the
repository's `data/index/` folder, so that a new installation does not have to extract the corpus
from Lean (~15 min) and embed it (~7 min on a GPU, hours on a CPU). `scripts/setup.py` of the code
repository downloads a pinned revision of this dataset and checks every file's SHA-256
(`data/index_manifest.json`).

## Where it comes from

| | |
|---|---|
| Lean | `leanprover/lean4:v4.36.0-rc1` |
| Mathlib | tag `v4.36.0-rc1`, commit `625d8914199bc567cca9b91f90e8e5afafa5ae51` |
| Extraction | `scripts/extract_corpus.lean` run with `lake env lean` over every module under `Mathlib`; signatures printed with `Lean.PrettyPrinter.ppSignature` (`format.width` 100000, no universes) |
| Corpus | 212 879 public, non-deprecated Mathlib theorems; auto-generated declarations removed (9 247 auto-named, 25 106 other generated) |
| Embedding model | [`intfloat/e5-base-v2`](https://huggingface.co/intfloat/e5-base-v2), revision `f52bf8ec8c7124536f0efb74aca902b2995e5bcd`, 768 dimensions, fp16, documents prefixed with `passage: ` (queries use `query: `) |
| Dense index | `faiss.IndexFlatIP` over l2-normalised vectors (exact cosine search) |
| Lexical index | BM25 (k1 = 1.2, b = 0.75) with the tokenizer `formal_mas.retrieval.bm25.tokenize` |
| Built | 2026-10-17 (corpus and BM25 locally, embeddings on a Kaggle T4 GPU) |

Every component records the same corpus fingerprint (`corpus_names_sha256`
`45649f2284fdb0382bb3a3c80d6ddde470c20e8179e29b76237ce89417da0d45`), so the Retriever refuses an
index built from a different corpus.

## Files

| Path | What it is |
|---|---|
| `corpus.jsonl` | one theorem per line: `name`, `signature`, `docstring`, `module`, `area`, `origin`, `line`, `text` (the indexed text) |
| `corpus_meta.json` | extraction settings, Lean/Mathlib versions, counts per area and origin |
| `full/e5-base/index.faiss` | dense index of the whole corpus (e5-base-v2) |
| `full/e5-base/names.json`, `meta.json` | theorem names in index order; model, revision, precision and build details |
| `full/bm25/` | BM25 matrix (`bm25_matrix.npz`), idf, vocabulary, the lemma table `lemmas.json` (names and signatures, corpus order) and `meta.json` |
| `full/names_extra.json` | 46 236 theorem names of Lean core `Init`, `Std` and Batteries, used only to suggest names after an `unknown identifier` error (not searched) |

The `full/` folder is the system's index (whole corpus). The evaluation indexes of the RAG study
(corpus without the reference queries) are not included; `scripts/build_reference_set.py` and
`scripts/build_index.py` rebuild them.

## How it is used

```python
from formal_mas.retrieval.retriever import Retriever

r = Retriever()   # reads data/index/full; hybrid = RRF(BM25, e5-base-v2), k = 16
for hit in r.retrieve("(a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2"):
    print(hit.score, hit.name)
```

To rebuild it from scratch instead, see README §4.5 of the code repository.

## Licenses

- **Corpus, lemma table and names** (`corpus.jsonl`, `full/bm25/`, `names.json`,
  `names_extra.json`): theorem names, signatures and docstrings extracted from
  [Mathlib](https://github.com/leanprover-community/mathlib4) and from Lean core / Batteries, all
  released under the **Apache License 2.0**; this derived data keeps that license.
- **Vectors** (`full/e5-base/index.faiss`): computed with
  [`intfloat/e5-base-v2`](https://huggingface.co/intfloat/e5-base-v2), released under the **MIT
  License**, from the Apache-2.0 corpus above.
- Build scripts and metadata: MIT, as the code repository (Copyright (c) 2026 Andrés Felipe Mongui
  Torres and Nicolás Samuel Tinjacá Topia).
