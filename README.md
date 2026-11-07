# formal-mas

A generative-AI multi-agent system that solves olympiad-style mathematics problems
with chains of reasoning, retrieval-augmented generation (RAG) over Mathlib, and
**formal verification in Lean 4**. Final project of the Artificial Intelligence
course, UPTC Sogamoso.

Team: Andrés Felipe Mongui Torres and Nicolás Samuel Tinjacá Topia.

## Start here

New to Lean or to this project? Read these two pages first; they assume programming
experience but no Lean:

- [How it works](docs/how_it_works.md): why Lean is needed, the agents, the full flow with a
  diagram, and one real problem traced from start to finish.
- [What was tested and what came out](docs/results_explained.md): the dataset, the five
  configurations C1–C5, the results on `test` and their limitations.

The rest of this README covers architecture details, installation, usage and reproduction.

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

Five configurations (C1–C5: two baselines, no-RAG, no-Critic, full system) share the same
compiled graph and differ only by flags in `config/ablations/`.

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

### 1.3 LLM client and baselines (Phase 3)

Every LLM call goes through `formal_mas.llm.client.LLMClient`, one OpenAI-compatible
client configured only by `config/models.yaml`:

- **Profiles.** `dev` (default, free: Gemini `gemini-3.5-flash-lite`, falling back to
  `gemini-3.8-flash`) is only for debugging code. `experiment` (paid: DeepSeek
  `deepseek-flash` for every role) has no fallback model: after the retries with
  backoff a problem is marked `provider_error`, which is not a system failure.
- **Thinking.** On, with `reasoning_effort: low`, for planner, generator and
  autoformalizer; off for the critic. Baseline A uses the Autoformalizer's settings and
  Baseline B the Generator's (`same_as`), so the comparisons are fair.
- **Budget.** At most `b_max` LLM calls per problem (`config/pipeline.yaml → budget`);
  cache hits and the JSON repair call count, transport retries do not.
- **Cache.** On disk (`data/llm_cache/`). The key covers the endpoint, the model, the full
  request and the sample index, so the *N* self-consistency samples are different entries.
- **JSON.** Outputs are parsed tolerantly (code fences, prose, trailing commas) and
  validated against the Pydantic contracts. If parsing fails, the client makes one repair call.
- **Trace.** One `llm_call` event per call in `trace.jsonl`. It records role, requested and
  returned model, `system_fingerprint`, tokens (cache hit/miss, reasoning), cost at the
  tariff in force at the call's UTC start (peak/off-peak), latency, timestamp, cache hit
  and `finish_reason`.

Baselines:

- **C1 (Baseline A)** asks the LLM for a tactic block only. `statement.proof_file` builds
  the file, `sanitize.py` checks the block (a rejection is a failed attempt, logged with
  its reason) and the Lean runner decides. There are up to 4 independent samples.
- **C2 (Baseline B)** asks for a natural-language proof and then a self-assessment by the
  same model. It *claims* success when its own verdict is "correct". Lean never checks it.

### 1.4 Planner and Generator (Phase 4)

- **Planner** (`agents/planner.py`, `prompts/planner.md`). Input: the formal statement,
  the informal one, and the Retriever's top-*k* lemmas for the theorem. The LLM returns
  `PlanProposal` (`contracts.py`): at most `n_max` one-line Lean propositions (subgoals),
  a closing tactic block and a short rationale. The code checks every subgoal with
  `sanitize.check_term` and the closing block with `check_tactics`. Then, with Lean and no
  further LLM calls:
  1. The skeleton (`statement.skeleton`: `have h<i> : P<i> := by sorry` …, closing `sorry`)
     must compile, so every subgoal is a well-typed proposition.
  2. **Refutation** (`planner.refute`): one file with `<name>_consistent : False` and, per
     subgoal, `<name>_ref<i> (hr : P<i>) : False` (`statement.refutation_file`), using the
     theorem's hypotheses only and closed by a fixed list of automatic tactics under a
     heartbeat limit. A proved `_ref<i>` means the subgoal is false wherever the hypotheses
     hold: the plan is rejected (`refuted:sg<i>`, event `plan_refuted`). If `_consistent` is
     proved, the hypotheses are contradictory and the check is skipped. A timeout refutes
     nothing. The check can only reject a plan.
  3. The closing step alone, as `<name>_close` with the subgoals as hypotheses
     (`statement.closing_lemma`), must be *accepted* (no hidden `sorry`). If it is not, the
     tactics of `planner.closing_auto` are tried in one file
     (`statement.closing_candidates_file`); the first that is accepted replaces the closing
     (`closing_source: auto:<tactic>`, reason `closing_auto:<tactic>`).
  4. The skeleton with the final closing must compile.

  A proposal with **no subgoals** is a *direct plan*: its closing block must be an accepted
  proof of the theorem itself (`source: direct`). If anything fails (bad JSON, too many
  subgoals, unsafe text, a refuted subgoal, a closing that nothing repairs, budget or
  provider errors), the Planner uses the **trivial plan**: the whole goal as the only
  subgoal, closed by `exact h1`. The reason goes to the trace (`plan_fallback`).
- **Generator** (`agents/generator.py`, `prompts/generator.md`). For each subgoal it
  queries the Retriever with the subgoal as a lemma statement (theorem binders, earlier
  subgoals as `h1 … h(i-1)`, then the subgoal). It then draws *N* natural-language sketches
  (self-consistency), each labelled with one strategy from the fixed list
  `contracts.STRATEGIES`. The vote (Eq. 9) picks the most frequent label. Ties go to the
  label listed first in `STRATEGIES`. The representative sketch is the lowest-index
  sample with the winning label. Every sample is one LLM call for `b_max`.
- Neither node raises: unsafe Lean, invalid JSON, provider errors, budget exhaustion and
  spend stops become statuses in the result and events in the trace.

### 1.5 Autoformalizer (Phase 5)

- **Two-layer sanitizer.** `lean/sanitize.py` (a blacklist over the text, offline) runs first.
  Text that passes is then parsed by Lean's own parser in the REPL (`lean/syntax_check.py`):
  a tactic block as `by` + block, a proposition as a term, nothing elaborated. Every syntax
  node kind must be on a whitelist (the tactics olympiad proofs use, core term syntax except
  quotations / `do` / `unsafe` / `sorry`, and notation terms). Anything else is rejected
  (`parse`, `syntax`, `search`, …) before compiling. Identifiers are not node kinds, so names
  such as `sorryAx` or `IO.*` are still the blacklist's job. One REPL command per check.
- **Pre-pass** (`agents/prepass.py`). Before any LLM call, every tactic of
  `pipeline.yaml → prepass.tactics` is tried alone on the theorem, in one compilation (one
  lemma per tactic, heartbeat limit each); the first that compiles is confirmed with
  `accepted`. After the plan, the same is done for every subgoal lemma. `prepass.enabled:
  false` (or `run_eval.py --no-prepass`) turns it off for the ablation.
- **Autoformalizer** (`agents/autoformalizer.py`). Input: the subgoal lemma `<name>_sg<i>`
  (built by `statement.py`), the Generator's winning sketch and the Retriever's lemmas for
  that lemma. Output: only the tactic block. The prompt depends on the role's model
  (`models.yaml → prompt_style`): `chat` (`prompts/autoformalizer.md`, with the Lean 4 cheat
  sheet `prompts/lean4_cheatsheet.md`, JSON `{"tactics"}`) or `prover`
  (`prompts/autoformalizer_prover.md`, "Complete the following Lean 4 code" for whole-proof
  models such as Goedel-Prover; only the tactics after `:= by` of the lemma are kept). The
  block passes both sanitizer layers and the lemma must be *accepted*.

### 1.6 Critic and the state graph (Phase 6)

- **Graph** (`orchestrator/graph.py`, LangGraph, one compiled `GRAPH`): pre-pass on the theorem →
  PLAN → pre-pass on the subgoals → per subgoal RETRIEVE → GENERATE → FORMALIZE → VERIFY → ASSEMBLE →
  DONE, with CRITIQUE after any failure. `statement.assemble` pastes every block, unchanged, into the
  skeleton with the closing step, and only `accepted` (Eq. 2) on that file sets `solved`. The result
  records the `stage` where it was solved or stopped (`prepass`, `plan`, `generate:sg<i>`,
  `formalize:sg<i>`, `verify:sg<i>`, `assemble`). No node raises.
- **Classifier κ** (Eq. 11, `orchestrator/policy.py`): maps a failure to one of `syntax`,
  `missing_lemma`, `tactic_failure`, `misformalization`, `timeout`, `planning`, by fixed rules.
  `misformalization` is a Lean error positioned strictly before the `:=` that ends the lemma's
  statement: the statement itself does not elaborate. A subgoal that failed with `k_max` distinct
  strategies is `planning` (Eq. 11), except with `k_max = 1` (C4); `message_class` keeps the original class.
- **Policy π** (Eq. 12, same file): abandon if `b ≥ b_max` or (`a_i ≥ k_max` and `r ≥ r_max`); replan
  if (`a_i ≥ k_max` or the class is `planning`) and `r < r_max`; otherwise retry: `syntax` → FORMALIZE
  (sketch kept), `missing_lemma` → RETRIEVE (identifier added to the query, `retrieval.k_retry`), any
  other class → GENERATE. Both functions are pure, with no LLM.
- **Feedback** (`agents/critic.py`): deterministic by default (Lean's message with the goal state, the
  failed block, `Retriever.suggest_names` for a missing name). `critic.llm_feedback: true` (C3, C5) adds
  one LLM call of advice per retry; it counts for `b_max` and cannot change the action.
- **Bound** (Proposition 1, Eq. 13): `C ≤ min{b_max, (r_max + 1)(1 + n_max · k_max · (N + 2))}`, tested with
  random LLM and Lean behaviour (`tests/test_graph.py`). A JSON repair call counts for `b_max` but is
  not in the structural term, so with repairs only `b_max` bounds the calls.
- **Configurations** (`config/ablations/`): C1 `flow: baseline_a`, `b_max: 18` (C5's mean calls per problem on the
  15 dev problems, final configuration); C2 `flow: baseline_b`; C3
  `retrieval.enabled: false` (no lemmas, no name suggestions), Critic advice on; C4 `k_max: 1, r_max: 0`;
  C5 the full system, Critic advice on.
- **FPR_B** (Eq. 16): `python scripts/run_baseline_b.py --split dev --check` runs C2 and formalizes every
  proof it claims (trivial plan, the claimed proof as the Autoformalizer's sketch, `k_max` attempts with the
  Critic's deterministic feedback, no pre-pass; its own budget, apart from C2's calls, at most `k_max` × 2 = 6
  calls per claim); FPR_B = claims Lean did not accept / claims checked. Lean start-up is checked with probes (`pipeline.yaml → lean.probes`); without Mathlib a
  run stops before any LLM call (`lean_unavailable`).

---

## 2. Repository structure

Only files that exist today are listed.

```
formal-mas/
├── README.md
├── requirements.txt
├── pyproject.toml               # package metadata and pytest settings
├── .env.example                 # API key variables (copy to .env)
│
├── config/
│   ├── models.yaml              # the ONLY place where models are named (providers, models, profiles)
│   └── pipeline.yaml            # hyperparameters (retrieval, lean, budget, llm cache, baselines)
│
├── src/formal_mas/
│   ├── config.py                # loads models.yaml and pipeline.yaml
│   ├── tracing.py               # trace.jsonl writer and the per-problem budget (b_max)
│   ├── run_config.py            # runs/<run>/run_config.json (RNF-2)
│   ├── llm/
│   │   ├── client.py            # OpenAI-compatible client: budget, cache, retries, JSON, trace
│   │   ├── specs.py             # resolves and validates a profile of models.yaml
│   │   ├── pricing.py           # usage normalisation and cost (peak / off-peak)
│   │   └── spend.py             # spend caps and the ledger of real spend
│   ├── agents/
│   │   ├── contracts.py         # Pydantic I/O contracts (LemmaHit, PlanProposal, SketchProposal, …)
│   │   ├── prompting.py         # loads prompts/*.md (SYSTEM:/USER:, {{placeholders}})
│   │   ├── planner.py           # subgoals + closing step; skeleton, refutation, closing checked, else trivial plan
│   │   ├── generator.py         # N sketches per subgoal, strategy vote (Eq. 9)
│   │   ├── autoformalizer.py    # subgoal lemma + sketch + lemmas → tactic block (chat / prover style)
│   │   └── prepass.py           # fixed automatic tactics before any LLM call
│   ├── orchestrator/
│   │   ├── graph.py             # the LangGraph state graph shared by C1–C5
│   │   └── policy.py            # classifier κ (Eq. 11) and policy π (Eq. 12): rules, no LLM
│   ├── baselines/
│   │   ├── baseline_a.py        # C1: LLM writes the tactic block, Lean verifies
│   │   └── baseline_b.py        # C2: natural-language proof + self-assessment (unverified)
│   ├── prompts/                 # one .md per prompt
│   ├── lean/
│   │   ├── runner.py            # compiles Lean (REPL, fallback per file), acceptance (Eq. 2)
│   │   ├── sanitize.py          # rejects unsafe generated Lean (escapes, metaprogramming, IO…)
│   │   ├── syntax_check.py      # parses generated Lean in the REPL; syntax node whitelist
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
│   ├── eval_retrieval.py        # Recall@k / MRR of every method
│   └── run_eval.py              # runs a configuration (C1–C5; PG = Phase 4 preview) over a dataset
│
├── lean_project/                # Lean 4 + Mathlib project (pinned versions)
├── lean_repl/                   # Lean REPL clone + build (generated, not in git)
├── data/
│   ├── smoke.jsonl              # 5 trivial hand-written problems for smoke runs
│   ├── dev_preview.jsonl        # 5 medium miniF2F problems for Planner/Generator previews (dev only)
│   ├── eval.jsonl               # evaluation set: 45 problems, 15 dev / 30 test (see data/README.md)
│   ├── curation/                # selection criteria, discarded problems, fidelity review
│   ├── raw/                     # pinned sources of eval.jsonl (not in git)
│   ├── retrieval_eval/          # Q (q_dev.jsonl, q_test.jsonl) and evaluation results
│   ├── index/                   # generated corpus and indexes (not in git)
│   └── llm_cache/               # LLM response cache (not in git)
├── runs/                        # one folder per run (not in git)
├── docs/prompt_log.md           # log of prompt changes and their measured effect
└── tests/                       # offline unit tests (+ slow Lean integration tests)
```

`data/index/`, `data/llm_cache/`, `data/spend_ledger.jsonl`, `runs/`, `lean_project/.lake/`, `lean_repl/`
and `.env` are git-ignored.

---

## 3. Installation

Tested on Windows 11 (Git Bash) with Python 3.12; the scripts also work on Linux and macOS.

### 3.1 Requirements

- Python ≥ 3.10 (the project uses 3.12 in `.venv/`)
- [uv](https://docs.astral.sh/uv/) (creates the Python environment, §3.2)
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

API keys go in `.env` (copy `.env.example`; never commit it): `GEMINI_API_KEY` for the
free `dev` profile, `DEEPSEEK_API_KEY` for the paid `experiment` profile.

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

### 4.4 Running the configurations

```bash
MODEL_PROFILE=dev python scripts/run_eval.py --config C1 --data data/smoke.jsonl --echo
MODEL_PROFILE=dev python scripts/run_eval.py --config C2 --data data/smoke.jsonl --limit 3
# Planner + Generator only (plans and sketches, no proof); not an experimental configuration
MODEL_PROFILE=dev python scripts/run_eval.py --config PG --data data/dev_preview.jsonl --echo
# The full system and its ablations; --no-prepass forces the LLM path
MODEL_PROFILE=dev python scripts/run_eval.py --config C5 --data data/dev_preview.jsonl --echo
MODEL_PROFILE=dev python scripts/run_eval.py --config C3 --data data/dev_preview.jsonl --echo
MODEL_PROFILE=dev python scripts/run_eval.py --config C4 --data data/smoke.jsonl --no-prepass --echo
```

Each run writes `runs/<UTC time>_<config>_<profile>/`:

- `run_config.json`: exact role specs, the models and fingerprints the provider returned,
  seed, Lean toolchain and Mathlib revision, `LeanRunner.run_info()` (incl. `degraded`),
  RAG index metadata, dataset hash, git commit and uncommitted changes. It is written at
  the start and rewritten at the end.
- `trace.jsonl`: one event per line, covering LLM calls, retries, attempts, Lean checks and warnings.
- `results.jsonl`: one line per problem with status, stage, `error_classes`, `critiques`, replans,
  LLM calls, tokens, cost and `length` finishes.

`experiment` runs are paid and need the team's explicit permission. `run_eval.py` warns
(without blocking) when a run starts inside DeepSeek's peak-price window (01:00–04:00
and 06:00–10:00 UTC, Monday–Friday). The client warns when the first response's `model`
differs from `expected_version` in `models.yaml`.

**Spend caps** (`config/pipeline.yaml` → `spend`): every paid call is appended to
`data/spend_ledger.jsonl` (outside git) with its date, run, problem, role and computed
cost. Before each paid request the client compares its worst-case cost (every prompt
byte as a cache-miss token plus all of `max_tokens` as output, at the peak price) with `spend.total_usd`
(whole project, sum of the ledger) and `spend.run_usd` (this run). If it could pass
either cap the request is not sent, the problem is `budget_stop` and the run ends
cleanly (`run_config.json` status `budget_stop`). `run_eval.py` prints the project's
spend and what is left at the start and the run's cost at the end; `--run-usd` can only
lower the per-run cap. Because the check uses the worst case, a run stops a little before
its cap is actually reached.

### 4.5 Configuration

- **Models** are defined only in `config/models.yaml`: `providers` (endpoint and the
  environment variable of the key), `models` (id, `expected_version`, JSON mode, how to
  toggle thinking, prices and peak windows), and `profiles` (role → model and request
  parameters; `same_as` copies a role). Never write model names in code. To change the
  embedding model, edit `embeddings.selected` (and rebuild its index).
- **Control flow** lives in `config/pipeline.yaml`: `flow`, `critic.k_max`, `critic.r_max`,
  `critic.llm_feedback`, and each configuration's overrides in `config/ablations/`.
- **Budget, spend, cache and baselines** live in `config/pipeline.yaml`: `budget.b_max`,
  `spend.total_usd`, `spend.run_usd`, `spend.ledger`, `llm.cache`, `llm.cache_dir`,
  `llm.json_repair`, `baselines.*.attempts`, `run.seed`.
- **Planner, Generator, Autoformalizer and pre-pass** live in `config/pipeline.yaml` →
  `planner` (`n_max`, `retrieve`, `check_skeleton`, `refute` and `closing_auto`: `enabled`,
  `tactics`, `heartbeats`, `timeout_s`), `generator` (`samples` = *N*, `retrieve`),
  `autoformalizer` (`retrieve`) and `prepass` (`enabled`, `tactics`, `heartbeats`,
  `timeout_s`). The Autoformalizer's prompt style is the role's model's
  `models.yaml → prompt_style` (`chat` | `prover`).
- **Retrieval hyperparameters** live in `config/pipeline.yaml` → `retrieval`:
  `method` (`rrf` | `bm25` | `dense`), `k`, `depth`, `rrf_k`, `query_instruction`,
  `generic_query`, `index_dir`, `suggestions`.
- **Lean runner** settings live in `config/pipeline.yaml` → `lean`: `backend`
  (`repl` | `file`), `base_imports`, `timeout_s`, `startup_timeout_s`, `restart_every`,
  `restart_memory_mb`, `kill_memory_mb`, `cache_size`, `syntax_check` and
  `syntax_timeout_s` (the parser layer of the sanitizer).

### 4.6 Tests

```bash
pytest              # offline unit tests (toy corpus, fake encoder, fake REPL, fake LLM, recorded Lean outputs)
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
- Every run stores `runs/<run>/run_config.json` (see §4.4). DeepSeek offers no versioned
  model id: `deepseek-flash` is an alias (it has already moved from V4-Flash to
  V4.1-Flash), and the API echoes the alias as `model`, so the `system_fingerprint` of
  every response (recorded with `model`) is the only per-call trace of the underlying model.
