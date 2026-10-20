# formal-mas

A generative-AI multi-agent system that solves olympiad-style mathematics problems
with chains of reasoning, retrieval-augmented generation (RAG) over Mathlib, and
**formal verification in Lean 4**. Final project of the Artificial Intelligence
course, UPTC Sogamoso.

Team: Andrés Felipe Mongui Torres and Nicolás Samuel Tinjacá Topia.

---

## 1. How the system works

### 1.1 Target architecture

Given a problem stated as a Lean 4 theorem, the full system will run the loop

```
PLAN → RETRIEVE → GENERATE → FORMALIZE → VERIFY → (CRITIC: retry / replan / give up) → DONE | FAIL
```

- **Planner** proposes subgoals; the code (never an LLM) builds every Lean
  statement and skeleton, so no model can weaken the theorem it must prove.
- **Retriever** returns the *k* most useful Mathlib lemmas for each subgoal.
- **Generator** writes *N* proof sketches (self-consistency) conditioned on those lemmas.
- **Autoformalizer** turns a sketch into Lean tactics.
- **Verifier** is deterministic: only the Lean kernel decides. A problem is solved
  only if the proof compiles, contains no `sorry`, and `#print axioms` reports
  nothing beyond `propext`, `Classical.choice` and `Quot.sound`.
- **Critic** classifies errors with fixed rules and applies a deterministic policy,
  under a hard budget of `b_max` LLM calls per problem.

Five configurations (C1–C5: two baselines, no-RAG, no-Critic, full system) will share
the same graph and differ only by flags in `config/ablations/`.

### 1.2 What works today: the Retriever (Phase 1)

`Retriever.retrieve(subgoal)` maps a Lean subgoal to the top-*k* Mathlib lemmas:

1. **Query rewriting.** The subgoal (a goal state `hyps ⊢ goal` or a statement) is
   rewritten as `binders : type` by `goal_to_query`, the same format as the
   evaluation queries.
2. **Lexical ranking.** Okapi BM25 over `name ∥ signature ∥ docstring`; qualified
   names are split on `.`, `_` and camelCase, and mathematical symbols (`ℕ`, `≤`, `∣`)
   are kept as tokens.
3. **Dense ranking.** `intfloat/e5-base-v2` embeddings with its query instruction,
   exact cosine search with FAISS (`IndexFlatIP` over ℓ2-normalised vectors).
4. **Generic second query.** If the query binds no type variable, concrete carriers
   (ℝ, ℚ, ℤ, ℂ) are replaced by a type variable `R`, so generic lemmas such as
   `two_mul_le_add_sq` are not buried behind `Real.*` lemmas.
5. **Fusion.** Reciprocal rank fusion (RRF, constant 60) of all rankings; the
   top *k* = 16 are returned as `LemmaHit(name, signature, score)`
   (`src/formal_mas/agents/contracts.py`).

It also offers `suggest_names(ident)` to repair `unknown identifier` errors
(`nat.succ_le_iff` → `Nat.succ_le_iff`), searching Mathlib plus the theorem names of
Init/Std/Batteries.

The Retriever **never raises**: without torch, FAISS or the dense index it falls
back to BM25 only; without a lexical index it returns `[]`. Every degradation is
logged as a JSON `retriever_warning` event.

**Corpus.** Extracted from the elaborated Lean environment after `import Mathlib`
(`scripts/extract_corpus.lean`): 212 879 public, non-deprecated theorems, including
`@[to_additive]` and `@[to_dual]` variants.

**Evaluation (Recall@k and MRR).** On a reference set Q of 200 Mathlib theorems
(algebra, number theory, combinatorics) whose gold lemmas are read from their
compiled proof terms, split 134 dev / 66 test. Method and *k* were chosen on dev only;
test was evaluated once:

| Method (test, n = 66) | R@4 | R@8 | R@16 | R@32 | MRR |
|---|---|---|---|---|---|
| BM25 | 0.394 | 0.561 | 0.621 | 0.697 | 0.346 |
| e5-base (with instruction) | 0.333 | 0.409 | 0.530 | 0.606 | 0.279 |
| RRF(BM25, e5-base) | 0.409 | 0.530 | 0.621 | 0.667 | 0.333 |
| RRF(BM25, e5-base) + generic query (**selected**) | 0.409 | 0.530 | 0.621 | 0.667 | 0.322 |

Full tables (BGE-base, BGE-M3, both gold definitions, LaTeX versions) are in
`data/retrieval_eval/`.

---

## 2. Repository structure

Only files that exist today are listed.

```
formal-mas/
├── README.md
├── requirements.txt
├── pyproject.toml               # package metadata and pytest settings
│
├── config/
│   ├── models.yaml              # the ONLY place where models are named (embeddings today)
│   └── pipeline.yaml            # hyperparameters (retrieval: method, k, depth, rrf_k, …)
│
├── src/formal_mas/
│   ├── config.py                # loads models.yaml and pipeline.yaml
│   ├── agents/
│   │   └── contracts.py         # Pydantic I/O contracts (LemmaHit)
│   └── retrieval/
│       ├── corpus.py            # turns the Lean environment dump into corpus entries
│       ├── reference_set.py     # builds the reference set Q and its gold lemmas
│       ├── bm25.py              # Okapi BM25 over Lean declarations
│       ├── encoders.py          # embedding encoders built from models.yaml
│       ├── evaluate.py          # Recall@k, MRR, reciprocal rank fusion
│       └── retriever.py         # Retriever: subgoal → [LemmaHit], name suggestions
│
├── scripts/
│   ├── setup_lean.sh            # installs elan/Lean 4 and builds lean_project/
│   ├── extract_corpus.lean      # dumps Mathlib theorems from the Lean environment
│   ├── extract_proof_consts.lean# dumps proof-term constants (gold lemmas of Q)
│   ├── extract_names.lean       # dumps Init/Std/Batteries theorem names
│   ├── build_index.py           # corpus, FAISS and BM25 indexes, extra names
│   ├── build_reference_set.py   # reference set Q (dev/test)
│   ├── kaggle_job.py            # embeds the corpus on a free Kaggle GPU
│   ├── kaggle_embed.py          # the Kaggle kernel itself (runs on Kaggle)
│   └── eval_retrieval.py        # Recall@k / MRR of every method
│
├── lean_project/                # Lean 4 + Mathlib project (pinned versions)
├── data/
│   ├── retrieval_eval/          # Q (q_dev.jsonl, q_test.jsonl) and evaluation results
│   └── index/                   # generated corpus and indexes (not in git)
└── tests/                       # offline unit tests (+ one slow Lean integration test)
```

`data/index/`, `runs/`, `lean_project/.lake/` and `.env` are git-ignored.

---

## 3. Installation

Tested on Windows 11 (Git Bash) with Python 3.12; the scripts also work on Linux and macOS.

### 3.1 Requirements

- Python ≥ 3.10 (the project uses 3.12 in `.venv/`)
- [uv](https://docs.astral.sh/uv/) (needed by the `lean-lsp` MCP server)
- Git and `curl`
- ~7 GB of disk for Lean 4 + Mathlib, plus ~3 GB for the indexes

### 3.2 Python environment

```bash
uv venv --python 3.12 .venv
source .venv/Scripts/activate        # Windows (Git Bash); on Linux/macOS: .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch
pip install -r requirements.txt
pip install -e .
```

On Windows, set `PYTHONUTF8=1` so the console handles Lean symbols (ℝ, ⊢, ≤).

### 3.3 Lean 4 + Mathlib

```bash
bash scripts/setup_lean.sh
```

This installs `elan`, downloads the precompiled Mathlib cache and builds
`lean_project/`. The versions are pinned by `lean_project/lean-toolchain` and
`lake-manifest.json`:

| Component | Version |
|---|---|
| Lean | `v4.36.0-rc1` (commit `c9afcddf514a6772bd4d649f7cfc04eff0b9f15e`) |
| Mathlib | `v4.36.0-rc1` (commit `625d8914199bc567cca9b91f90e8e5afafa5ae51`) |

Smoke test: save this as `lean_project/Smoke.lean` and run
`cd lean_project && lake env lean Smoke.lean` (the first `import Mathlib` takes ~30 s).
`#print axioms` should list only `propext`, `Classical.choice` and `Quot.sound`.

```lean
import Mathlib

theorem smoke (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x

#print axioms smoke
```

### 3.4 Retrieval data

The indexes are not versioned, so they must be rebuilt once. The reference set Q
is already in `data/retrieval_eval/`.

```bash
# 1. Corpus from the Lean environment (~15 min) → data/index/corpus.jsonl
python scripts/build_index.py

# 2. Reference set Q and corpus_eval.jsonl (~8 min); --skip-extract reuses the dump
python scripts/build_reference_set.py

# 3. Dense embeddings. Either on a free Kaggle GPU (~7 min per base model):
python scripts/kaggle_job.py dataset
python scripts/kaggle_job.py kernel --keys e5-base
python scripts/kaggle_job.py status            # wait until it finishes
python scripts/kaggle_job.py fetch
python scripts/build_index.py --from-vectors data/index/kaggle/output
#    …or on the local CPU (several hours):
python scripts/build_index.py --embed e5-base

# 4. BM25 index and the extra names used for suggestions
python scripts/build_index.py --bm25
python scripts/build_index.py --extra-names
```

The Kaggle route needs the Kaggle CLI credentials in `~/.kaggle/kaggle.json`; they
are never stored in this repository. Only names, signatures and query statements
are uploaded.

---

## 4. Usage

### 4.1 Retrieving lemmas

```python
from formal_mas.retrieval.retriever import Retriever

r = Retriever()                       # reads config/pipeline.yaml and config/models.yaml
print(r.mode)                         # "hybrid", "bm25" or "none"

for hit in r.retrieve("(a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2"):
    print(f"{hit.score:.3f}  {hit.name}  {hit.signature}")

r.retrieve("x : ℝ\n⊢ 0 ≤ x ^ 2", k=8)  # goal states are accepted too
r.suggest_names("nat.succ_le_iff")    # ['Nat.succ_le_iff', …]
```

Start-up takes ~14 s on CPU (model and indexes load once); a query takes ~200 ms
median.

### 4.2 Evaluating retrieval

```bash
python scripts/eval_retrieval.py                     # dev split, every built index
python scripts/eval_retrieval.py --models e5-base    # a subset of dense models
python scripts/eval_retrieval.py --split test --final   # test split: final report only
```

Results are written to `data/retrieval_eval/results_<split>.{json,md,tex}`.
Model and *k* selection must use the dev split only; the test split is reserved for
the final numbers.

### 4.3 Configuration

- **Models** are defined only in `config/models.yaml`. To change the embedding model,
  edit `embeddings.selected` (and rebuild its index); never write model names in code.
- **Retrieval hyperparameters** live in `config/pipeline.yaml` → `retrieval`:
  `method` (`rrf` | `bm25` | `dense`), `k`, `depth`, `rrf_k`, `query_instruction`,
  `generic_query`, `index_dir`, `suggestions`.

### 4.4 Tests

```bash
pytest              # offline unit tests (toy corpus, fake encoder, no downloads)
pytest -m slow      # integration test with real Lean + Mathlib (~1 min)
```

---

## 5. Reproducibility

- Lean and Mathlib versions are pinned in `lean_project/`.
- Index metadata (`data/index/*/<model>/meta.json`, `data/retrieval_eval/meta.json`)
  records the model, Hugging Face revision, precision, corpus hash and Mathlib commit.
- The reference set split uses seed 2026, stratified by area.
- Every decision taken during Phase 1, with its evidence, is logged in `PLAN.md`.
