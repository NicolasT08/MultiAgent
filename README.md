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
│   ├── lean/
│   │   ├── runner.py            # compiles Lean (REPL, fallback per file), acceptance (Eq. 2)
│   │   ├── sanitize.py          # rejects unsafe generated Lean (escapes, metaprogramming, IO…)
│   │   └── statement.py         # parses theorems; builds proof files, skeletons, subgoal lemmas
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
│   ├── build_repl.sh            # builds the Lean REPL (pinned tag/commit) in lean_repl/
│   ├── capture_lean_fixtures.py # records real Lean outputs for the runner tests
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
├── lean_repl/                   # Lean REPL clone + build (generated, not in git)
├── data/
│   ├── retrieval_eval/          # Q (q_dev.jsonl, q_test.jsonl) and evaluation results
│   └── index/                   # generated corpus and indexes (not in git)
└── tests/                       # offline unit tests (+ one slow Lean integration test)
```

`data/index/`, `runs/`, `lean_project/.lake/`, `lean_repl/` and `.env` are git-ignored.

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

The ~30 s assumes the Mathlib `.olean` files (~6 GB) are in the OS file cache. Cold,
on a hard disk, the first `import Mathlib` can take ~30 min (random reads of memory-
mapped files); keep `lean_project/` on an SSD if possible.

### 3.4 Lean REPL (proof checking back-end)

```bash
bash scripts/build_repl.sh
```

Clones [leanprover-community/repl](https://github.com/leanprover-community/repl) at tag
`v4.36.0-rc1` (commit `a85a1d2f4bdeab19c3d34be979ea336c9ee9b3ed`, same toolchain as
`lean_project/`) into `lean_repl/`, checks the pinned commit and builds it (~1 min).
Without it the runner falls back to `lake env lean` per file (~32 s per check).

### 3.5 Retrieval data

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

### 4.2 Checking proofs with Lean

```python
from formal_mas.lean.runner import LeanRunner, accepted

code = "import Mathlib\n\ntheorem t (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x\n"
with LeanRunner() as lean:            # reads config/pipeline.yaml → lean
    res = lean.verify(code, theorem="t")
    print(res.status, res.backend, res.axioms)   # finished repl ['propext', 'Classical.choice', 'Quot.sound']
    print(accepted(res))                         # (True, 'accepted')
    for m in res.errors:
        print(m.line, m.column, m.text)
```

The first call starts the REPL and loads the base imports once (~33 s warm); later
checks take ~0.01–0.5 s. `accepted` implements Eq. 2: Lean finished, no errors, no
`sorry`, the theorem's axioms ⊆ {`propext`, `Classical.choice`, `Quot.sound`}, and the
code declared nothing but the theorem (and Lean's auxiliaries for it).
Hangs (`timeout`, final), memory blow-ups (`memory_limit`) and REPL crashes (`crash`) kill
the process tree and restart the REPL before the next check; if the REPL cannot start, the
runner uses `lake env lean --json` per file, logs a `lean_runner_warning` event and marks
the run as degraded (`lean.run_info()`, to be stored in `run_config.json`). Identical
checks are answered from an in-memory cache.

Generated Lean never touches the statement: `statement.py` builds every file and pastes
only model-written tactic blocks and subgoal propositions, after `sanitize.py` checks them.

```python
from formal_mas.lean.statement import assemble, parse_theorem, skeleton, subgoal_lemma

thm = parse_theorem("theorem amgm (a b : ℝ) (h₁ : a + b = 10) : a * b ≤ 25 := by sorry")
subgoals = ["0 ≤ (a - b) ^ 2", "(a + b) ^ 2 = 100"]
plan = skeleton(thm, subgoals, "nlinarith [h1, h2]")          # compile; `compiles(res)` → plan type-checks
name, code = subgoal_lemma(thm, subgoals, 2, "rw [h₁]\nnorm_num")   # amgm_sg2 with h1 as hypothesis
final = assemble(thm, subgoals, ["positivity", "rw [h₁]\nnorm_num"], "nlinarith [h1, h2]")
# unsafe text (e.g. "exact h\ntheorem evil : False := sorry") raises statement.UnsafeLean
```

### 4.3 Evaluating retrieval

```bash
python scripts/eval_retrieval.py                     # dev split, every built index
python scripts/eval_retrieval.py --models e5-base    # a subset of dense models
python scripts/eval_retrieval.py --split test --final   # test split: final report only
```

Results are written to `data/retrieval_eval/results_<split>.{json,md,tex}`.
Model and *k* selection must use the dev split only; the test split is reserved for
the final numbers.

### 4.4 Configuration

- **Models** are defined only in `config/models.yaml`. To change the embedding model,
  edit `embeddings.selected` (and rebuild its index); never write model names in code.
- **Retrieval hyperparameters** live in `config/pipeline.yaml` → `retrieval`:
  `method` (`rrf` | `bm25` | `dense`), `k`, `depth`, `rrf_k`, `query_instruction`,
  `generic_query`, `index_dir`, `suggestions`.
- **Lean runner** settings live in `config/pipeline.yaml` → `lean`: `backend`
  (`repl` | `file`), `base_imports`, `timeout_s`, `startup_timeout_s`, `restart_every`,
  `restart_memory_mb`, `kill_memory_mb`, `cache_size`.

### 4.5 Tests

```bash
pytest              # offline unit tests (toy corpus, fake encoder, fake REPL, recorded Lean outputs)
pytest -m slow      # integration tests with real Lean + Mathlib (~2.5 min warm)
```

The runner tests parse real Lean outputs stored in `tests/fixtures/lean/`; re-record
them with `python scripts/capture_lean_fixtures.py` when the Lean version changes.

---

## 5. Reproducibility

- Lean and Mathlib versions are pinned in `lean_project/`; the Lean REPL tag and commit
  are pinned in `scripts/build_repl.sh` (`runner.repl_env_info()` reports them).
- Index metadata (`data/index/*/<model>/meta.json`, `data/retrieval_eval/meta.json`)
  records the model, Hugging Face revision, precision, corpus hash and Mathlib commit.
- The reference set split uses seed 2026, stratified by area.
- Every decision taken during Phase 1, with its evidence, is logged in `PLAN.md`.
