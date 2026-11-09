# formal-mas

A generative-AI multi-agent system that solves olympiad-style mathematics problems
with chains of reasoning, retrieval-augmented generation (RAG) over Mathlib, and
**formal verification in Lean 4**. Final project of the Artificial Intelligence
course, UPTC Sogamoso.

Team: Andrés Felipe Mongui Torres and Nicolás Samuel Tinjacá Topia. License: MIT (`LICENSE`).

**On Hugging Face:**
- Code: https://huggingface.co/NicolasT08/formal-mas
- Evaluation set, curation files and study results: https://huggingface.co/datasets/NicolasT08/formal-mas-eval
  (the informal text of the 5 PutnamBench problems is not redistributed there; see its `LICENSE.md`)

## Start here

New to Lean or to this project? Read these two pages first; they assume programming
experience but no Lean:

- [How it works](docs/how_it_works.md): why Lean is needed, the agents, the full flow with a
  diagram, and one real problem traced from start to finish.
- [What was tested and what came out](docs/results_explained.md): the dataset, the five
  configurations C1–C5, the results on `test` and their limitations.

The rest of this README covers the architecture, the results, installation, usage and how to
reproduce the experiments.

---

## 1. How the system works

### 1.1 Overview

Given a problem stated as a Lean 4 theorem (with its informal text), the system runs the loop

```
PRE-PASS → PLAN → RETRIEVE → GENERATE → FORMALIZE → VERIFY → ASSEMBLE → DONE | FAIL
                                   ↑                   │
                                   └──── CRITIC ───────┘  (retry / replan / give up)
```

- **Pre-pass** tries a fixed list of standard tactics on the theorem, with no LLM call.
- **Planner** proposes subgoals; the code (never an LLM) builds every Lean statement and
  skeleton, so no model can weaken the theorem it must prove.
- **Retriever** returns the *k* = 16 most useful Mathlib lemmas for each subgoal.
- **Generator** writes *N* = 3 proof sketches per subgoal and votes on the strategy
  (self-consistency).
- **Autoformalizer** turns the winning sketch into Lean tactics.
- **Verifier** is deterministic: only the Lean kernel decides. A problem is solved only if the
  assembled proof compiles, contains no `sorry`, and `#print axioms` reports nothing beyond
  `propext`, `Classical.choice` and `Quot.sound` (Eq. 2).
- **Critic** classifies each failure with fixed rules (κ) and applies a deterministic policy
  (π), under a hard budget of `b_max` = 40 LLM calls per problem.

The five configurations (C1–C5: two baselines, no-RAG, no-Critic, full system) share one
compiled graph and differ only by flags in `config/ablations/`. Diagrams of the components,
states and one retry are in [`docs/architecture.md`](docs/architecture.md).

### 1.2 Retriever

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
   (`src/formal_mas/agents/contracts.py`). After a `missing_lemma` failure the Critic asks
   for `k_retry` = 32.

It also offers `suggest_names(ident)` to repair `unknown identifier` errors
(`nat.succ_le_iff` → `Nat.succ_le_iff`), searching Mathlib plus the theorem names of
Init/Std/Batteries. The Retriever **never raises**: without torch, FAISS or the dense index
it falls back to BM25 only; without a lexical index it returns `[]`. Every degradation is
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

### 1.3 Planner and Generator

- **Planner** (`agents/planner.py`, `prompts/planner.md`). Input: the formal statement,
  the informal one, and the Retriever's top-*k* lemmas for the theorem. The LLM returns
  `PlanProposal` (`contracts.py`): at most `n_max` = 3 one-line Lean propositions (subgoals),
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

### 1.4 Pre-pass, Autoformalizer and the sanitizers

- **Two-layer sanitizer.** `lean/sanitize.py` (a blacklist over the text, offline) runs first:
  it rejects `sorry`/`admit`, text that could escape its slot (command keywords, a line
  indented to close the proof), metaprogramming, unsafe options, `IO`, `native_decide` and
  Lean 3 syntax. Text that passes is then parsed by Lean's own parser in the REPL
  (`lean/syntax_check.py`): a tactic block as `by` + block, a proposition as a term, nothing
  elaborated. Every syntax node kind must be on a whitelist. Anything else is rejected
  (`parse`, `syntax`, `search`, …) before compiling. Identifiers are not node kinds, so names
  such as `sorryAx` or `IO.*` are still the blacklist's job.
- **Pre-pass** (`agents/prepass.py`). Before any LLM call, every tactic of
  `pipeline.yaml → prepass.tactics` is tried alone on the theorem, in one compilation (one
  lemma per tactic, heartbeat limit each); the first that compiles is confirmed with
  `accepted`. After the plan, the same is done for every subgoal lemma. `prepass.enabled:
  false` (or `run_eval.py --no-prepass`) turns it off.
- **Autoformalizer** (`agents/autoformalizer.py`). Input: the subgoal lemma `<name>_sg<i>`
  (built by `statement.py`), the Generator's winning sketch and the Retriever's lemmas for
  that lemma. Output: only the tactic block. The prompt depends on the role's model
  (`models.yaml → prompt_style`): `chat` (`prompts/autoformalizer.md`, with the Lean 4 cheat
  sheet `prompts/lean4_cheatsheet.md`, JSON `{"tactics"}`; used by every configured model)
  or `prover` (`prompts/autoformalizer_prover.md`, for whole-proof prover models; only the
  tactics after `:= by` of the lemma are kept). The block passes both sanitizer layers and
  the lemma must be *accepted*.

### 1.5 Critic and the state graph

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
  other class → GENERATE. Both functions are pure, with no LLM. Defaults: `k_max` = 3 attempts per
  subgoal, `r_max` = 1 replan per problem.
- **Feedback** (`agents/critic.py`): deterministic by default (Lean's message with the goal state, the
  failed block, `Retriever.suggest_names` for a missing name). `critic.llm_feedback: true` (C3, C5) adds
  one LLM call of advice per retry; it counts for `b_max` and cannot change the action.
- **Bound** (Proposition 1, Eq. 13): `C ≤ min{b_max, (r_max + 1)(b_P + n_max · k_max · (N + 2))}`
  (`policy.structural_bound`; = min{40, 92} = 40 with the defaults), tested with random LLM and Lean
  behaviour (`tests/test_graph.py`). A JSON repair call counts for `b_max` but is not in the
  structural term, so with repairs only `b_max` bounds the calls.

### 1.6 LLM client

Every LLM call goes through `formal_mas.llm.client.LLMClient`, one OpenAI-compatible
client configured only by `config/models.yaml`:

- **Profiles.** `dev` (default, free: Gemini `gemini-3.5-flash-lite`, falling back to
  `gemini-3.8-flash`) is only for debugging code. `experiment` (paid: DeepSeek
  `deepseek-flash` for every role) has no fallback model: after the retries with
  backoff a problem is marked `provider_error`, which is not a system failure. All the
  reported results use `experiment`.
- **Thinking** (`experiment`). On, with `reasoning_effort: low` and `max_tokens` 32 768, for
  planner, generator and autoformalizer; off for the critic. Baseline A uses the
  Autoformalizer's settings and Baseline B the Generator's (`same_as`), so the comparisons are fair.
- **Budget.** At most `b_max` LLM calls per problem (`config/pipeline.yaml → budget`);
  cache hits and the JSON repair call count, transport retries do not.
- **Cache.** On disk (`data/llm_cache/`). The key covers the endpoint, the model, the full
  request and the sample index, so the *N* self-consistency samples are different entries.
  An identical request is answered from the cache at no cost.
- **JSON.** Outputs are parsed tolerantly (code fences, prose, trailing commas) and
  validated against the Pydantic contracts. If parsing fails, the client makes one repair call.
- **Trace.** One `llm_call` event per call in `trace.jsonl`. It records role, requested and
  returned model, `system_fingerprint`, tokens (cache hit/miss, reasoning), cost at the
  tariff in force at the call's UTC start (peak/off-peak), latency, timestamp, cache hit
  and `finish_reason`.
- **Spend caps** (`config/pipeline.yaml → spend`): every paid call is appended to
  `data/spend_ledger.jsonl` (outside git). Before each paid request the client compares its
  worst-case cost (every prompt byte as a cache-miss token plus all of `max_tokens` as
  output, at the peak price) with `spend.total_usd` (whole project, 9 USD) and
  `spend.run_usd` (one run, 3.5 USD). If it could pass either cap the request is not sent,
  the problem is `budget_stop` and the run ends cleanly. `--run-usd` can only lower the
  per-run cap.

### 1.7 Baselines and the five configurations

| | Flow and flags (`config/ablations/`) | What it is |
|---|---|---|
| **C1** Baseline A | `flow: baseline_a`, `b_max: 18`, `attempts: 18` | One LLM asked for the whole tactic block of the theorem; up to 18 independent attempts (no error feedback). `statement.proof_file` builds the file, both sanitizer layers check the block (a rejection is a failed attempt) and Lean decides. 18 = C5's mean calls per problem on the 15 `dev` problems, fixed before `test`. |
| **C2** Baseline B | `flow: baseline_b` | Natural-language proof and a self-assessment by the same model; it *claims* success when its own verdict is "correct". Lean never checks it. |
| **C3** No RAG | `retrieval.enabled: false`, `critic.llm_feedback: true` | Full system without lemmas or name suggestions. |
| **C4** No Critic | `critic.k_max: 1`, `critic.r_max: 0` | Full system with one attempt per subgoal and no replanning. |
| **C5** Full system | `critic.k_max: 3`, `critic.llm_feedback: true` | Every agent, retries and replanning. |

**FPR_B** (Eq. 16): `python scripts/run_baseline_b.py --check` runs C2 and formalizes every
proof it claims (trivial plan, the claimed proof as the Autoformalizer's sketch, `k_max`
attempts with the Critic's deterministic feedback, the Retriever's lemmas as in C5, no
pre-pass; its own budget, apart from C2's calls). FPR_B = claims Lean did not accept /
claims checked. An unconfirmed claim may also be a formalization failure, so FPR_B is an
upper bound; a manual review of 10 unconfirmed claims is in
[`docs/results/fprb_review.md`](docs/results/fprb_review.md).

Lean start-up is checked with probes (`pipeline.yaml → lean.probes`); without Mathlib a run
stops before any LLM call (`lean_unavailable`).

---

## 2. Results at a glance

30 `test` problems (10 easy, 10 medium, 10 hard; algebra, number theory, combinatorics from
miniF2F and PutnamBench), profile `experiment` (DeepSeek `deepseek-flash` for every role),
seed 2026, one run per configuration. Analysis exactly as pre-registered in
[`docs/analysis_plan.md`](docs/analysis_plan.md) (written before `test` was run). Source:
[`docs/results/results.md`](docs/results/results.md), Tables 1 and 2.

**Success rate** (proof accepted by Lean; 95 % Wilson intervals):

| Config | Solved | SR | 95 % CI |
|---|---|---|---|
| C1 Baseline A | 20/30 | 66.7 % | [48.8, 80.8] |
| C3 No RAG | 20/30 | 66.7 % | [48.8, 80.8] |
| C4 No Critic | 14/30 | 46.7 % | [30.2, 63.9] |
| C5 Full system | 24/30 | 80.0 % | [62.7, 90.5] |
| C2 Baseline B | claimed 30/30; Lean confirmed 19 | FPR_B 36.7 % | [21.9, 54.5] |

**Planned comparisons** (exact McNemar on the paired problems, Bonferroni × 3; *b* = solved
only by C5, *c* = solved only by the other configuration):

| | Comparison | b | c | ΔSR (pp) | Bootstrap 95 % CI (pp) | p | Adjusted p |
|---|---|---|---|---|---|---|---|
| H1 | C5 vs C1 | 4 | 0 | +13.3 | [+3.3, +26.7] | 0.125 | 0.375 |
| H2 | C5 vs C3 (RAG) | 5 | 1 | +13.3 | [+0.0, +30.0] | 0.219 | 0.656 |
| H3 | C5 vs C4 (Critic) | 10 | 0 | +33.3 | [+16.7, +50.0] | 0.002 | **0.006** |

Only H3 is significant: the Critic's retries and replanning add 10 problems. With n = 30,
H1 and H2 show no evidence of a difference, which is not evidence that there is none. C5
used 16.9 LLM calls and 0.057 USD (nominal) per solved problem.

More: [`docs/results_explained.md`](docs/results_explained.md) (plain-language summary and
limitations) and [`docs/results/results.md`](docs/results/results.md) (every table: per-cell
grid, efficiency, time, refinement curve SR(k), κ error taxonomy, method notes). LaTeX tables:
`docs/results/tables.tex`; figure: `docs/results/figures/srk_curve.{pdf,png}`.

---

## 3. Repository structure

```
formal-mas/
├── README.md
├── requirements.txt
├── pyproject.toml               # package metadata and pytest settings
├── .env.example                 # API key variables (copy to .env)
│
├── config/
│   ├── models.yaml              # the ONLY place where models are named (providers, models, profiles)
│   ├── pipeline.yaml            # hyperparameters (retrieval, lean, budget, spend, critic, agents)
│   └── ablations/               # C1_baseline_a, C2_baseline_b, C3_no_rag, C4_no_critic, C5_full (flags only)
│
├── src/formal_mas/
│   ├── config.py                # loads models.yaml and pipeline.yaml (+ ablation)
│   ├── tracing.py               # trace.jsonl writer and the per-problem budget (b_max)
│   ├── run_config.py            # runs/<run>/run_config.json (RNF-2)
│   ├── evalset.py               # pools, seeded draw and stratified split of data/eval.jsonl
│   ├── llm/
│   │   ├── client.py            # OpenAI-compatible client: budget, cache, retries, JSON, trace
│   │   ├── specs.py             # resolves and validates a profile of models.yaml
│   │   ├── pricing.py           # usage normalisation and cost (peak / off-peak)
│   │   └── spend.py             # spend caps and the ledger of real spend
│   ├── agents/
│   │   ├── contracts.py         # Pydantic I/O contracts (→ docs/schemas/)
│   │   ├── prompting.py         # loads prompts/*.md (SYSTEM:/USER:, {{placeholders}})
│   │   ├── prepass.py           # fixed automatic tactics before any LLM call
│   │   ├── planner.py           # subgoals + closing step; skeleton, refutation, closing checked, else trivial plan
│   │   ├── generator.py         # N sketches per subgoal, strategy vote (Eq. 9)
│   │   ├── autoformalizer.py    # subgoal lemma + sketch + lemmas → tactic block
│   │   └── critic.py            # feedback for retry / replan (deterministic, optional LLM advice)
│   ├── orchestrator/
│   │   ├── graph.py             # the LangGraph state graph shared by C1–C5
│   │   └── policy.py            # classifier κ (Eq. 11), policy π (Eq. 12), call bound (Eq. 13)
│   ├── baselines/
│   │   ├── baseline_a.py        # C1: LLM writes the tactic block, Lean verifies
│   │   └── baseline_b.py        # C2: natural-language proof + self-assessment; FPR_B check
│   ├── analysis/
│   │   └── stats.py             # Wilson, exact McNemar, Bonferroni, bootstrap (analysis plan)
│   ├── prompts/                 # one .md per prompt + lean4_cheatsheet.md
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
│   ├── fetch_eval_sources.sh    # downloads the pinned miniF2F port and PutnamBench → data/raw/
│   ├── build_eval_set.py        # builds data/eval.jsonl (seeded draw, compile with sorry)
│   ├── run_eval.py              # runs a configuration (C1–C5; PG = Planner/Generator preview) over a dataset
│   └── run_baseline_b.py        # C2 with --check (FPR_B)
│
├── lean_project/                # Lean 4 + Mathlib project (pinned versions)
├── lean_repl/                   # Lean REPL clone + build (generated, not in git)
├── data/
│   ├── README.md                # how eval.jsonl was built
│   ├── smoke.jsonl              # 5 trivial hand-written problems for smoke runs
│   ├── dev_preview.jsonl        # 5 medium miniF2F problems for previews (dev only)
│   ├── eval.jsonl               # evaluation set: 45 problems, 15 dev / 30 test
│   ├── curation/                # selection criteria, discarded problems, fidelity review (+ post-test review)
│   ├── raw/                     # pinned sources of eval.jsonl (not in git)
│   ├── retrieval_eval/          # Q (q_dev.jsonl, q_test.jsonl) and evaluation results
│   ├── index/                   # generated corpus and indexes (not in git)
│   ├── llm_cache/               # LLM response cache (not in git)
│   └── spend_ledger.jsonl       # every paid call (not in git)
├── runs/                        # one folder per run (not in git)
├── docs/
│   ├── how_it_works.md          # plain-language explanation with a real example
│   ├── results_explained.md     # plain-language results and limitations
│   ├── architecture.md          # component, state and sequence diagrams
│   ├── analysis_plan.md         # pre-registered analysis plan + Deviations
│   ├── prompt_log.md            # log of prompt changes and their measured effect (dev only)
│   ├── schemas/                 # JSON Schemas of the agent contracts
│   ├── results/                 # tables (results.md, tables.tex), figures/, make_tables.py, fprb_review.md
│   └── report/                  # the report (PDF)
└── tests/                       # offline unit tests (+ slow Lean integration tests)
```

`data/index/`, `data/llm_cache/`, `data/spend_ledger.jsonl`, `data/raw/`, `runs/`,
`lean_project/.lake/`, `lean_repl/` and `.env` are git-ignored.

---

## 4. Installation

Tested on Windows 11 (Git Bash) with Python 3.12; the scripts also work on Linux and macOS.

### 4.1 Requirements

- Python ≥ 3.10 (the project uses 3.12 in `.venv/`)
- [uv](https://docs.astral.sh/uv/) (creates the Python environment, §4.2)
- Git and `curl`
- ~7 GB of disk for Lean 4 + Mathlib, plus ~3 GB for the indexes
- ~4 GB of free RAM for the Lean REPL (Mathlib loaded)

### 4.2 Python environment

```bash
uv venv --python 3.12 .venv
source .venv/Scripts/activate        # Windows (Git Bash); on Linux/macOS: .venv/bin/activate
uv pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch
uv pip install -r requirements.txt
uv pip install -e .
```

On Windows, set `PYTHONUTF8=1` so the console handles Lean symbols (ℝ, ⊢, ≤).

API keys go in `.env` (copy `.env.example`; never commit it): `GEMINI_API_KEY` for the
free `dev` profile, `DEEPSEEK_API_KEY` for the paid `experiment` profile.

### 4.3 Lean 4 + Mathlib

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

### 4.4 Lean REPL (proof checking back-end)

```bash
bash scripts/build_repl.sh
```

Clones [leanprover-community/repl](https://github.com/leanprover-community/repl) at tag
`v4.36.0-rc1` (commit `a85a1d2f4bdeab19c3d34be979ea336c9ee9b3ed`, same toolchain as
`lean_project/`) into `lean_repl/`, checks the pinned commit and builds it (~1 min).
Without it the runner falls back to `lake env lean` per file (~32 s per check).

### 4.5 Retrieval data

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

## 5. Usage

### 5.1 Retrieving lemmas

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

### 5.2 Checking proofs with Lean

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
code declared nothing but the theorem (and Lean's auxiliaries for it). After the import
the REPL must answer `#check` probes of Mathlib and Aesop (`lean.probes`), so a REPL that
started without Mathlib is never used.
Hangs (`timeout`), memory blow-ups (`memory_limit`) and REPL crashes (`crash`) kill
the process tree and restart the REPL before the next check; if the REPL cannot start, the
runner uses `lake env lean --json` per file, logs a `lean_runner_warning` event and marks
the run as degraded (`run_config.json`). Identical checks are answered from an in-memory cache.

Generated Lean never touches the statement: `statement.py` builds every file and pastes
only model-written tactic blocks and subgoal propositions, after the sanitizers check them.

```python
from formal_mas.lean.statement import assemble, parse_theorem, skeleton, subgoal_lemma

thm = parse_theorem("theorem amgm (a b : ℝ) (h₁ : a + b = 10) : a * b ≤ 25 := by sorry")
subgoals = ["0 ≤ (a - b) ^ 2", "(a + b) ^ 2 = 100"]
plan = skeleton(thm, subgoals, "nlinarith [h1, h2]")          # compile; `compiles(res)` → plan type-checks
name, code = subgoal_lemma(thm, subgoals, 2, "rw [h₁]\nnorm_num")   # amgm_sg2 with h1 as hypothesis
final = assemble(thm, subgoals, ["positivity", "rw [h₁]\nnorm_num"], "nlinarith [h1, h2]")
# unsafe text (e.g. "exact h\ntheorem evil : False := sorry") raises statement.UnsafeLean
```

### 5.3 Evaluating retrieval

```bash
python scripts/eval_retrieval.py                     # dev split, every built index
python scripts/eval_retrieval.py --models e5-base    # a subset of dense models
python scripts/eval_retrieval.py --split test --final   # test split: final report only
```

Results are written to `data/retrieval_eval/results_<split>.{json,md,tex}`.
Model and *k* selection must use the dev split only; the test split is reserved for
the final numbers.

### 5.4 Running a configuration

```bash
# free profile (Gemini), for debugging
MODEL_PROFILE=dev python scripts/run_eval.py --config C5 --split dev --limit 3 --echo
MODEL_PROFILE=dev python scripts/run_eval.py --config C1 --data data/smoke.jsonl --echo
MODEL_PROFILE=dev python scripts/run_baseline_b.py --split dev --limit 3 --check --echo
# Planner + Generator only (plans and sketches, no proof); not an experimental configuration
MODEL_PROFILE=dev python scripts/run_eval.py --config PG --data data/dev_preview.jsonl --echo
# --no-prepass forces the LLM path
MODEL_PROFILE=dev python scripts/run_eval.py --config C4 --data data/smoke.jsonl --no-prepass --echo
```

Options: `--config` (C1–C5, PG), `--data` (default `data/eval.jsonl`), `--split`
(`dev` | `test` | `all`), `--limit`, `--seed`, `--run-name` (suffix of the run folder),
`--echo`, `--run-usd` (lower the per-run spend cap), `--no-prepass`, `--check` (C2 only).

Each run writes `runs/<UTC time>_<config>_<profile>[_<run-name>]/`:

- `run_config.json`: exact role specs, the models and fingerprints the provider returned,
  seed, Lean toolchain and Mathlib revision, Lean runner info (incl. `degraded`), RAG index
  metadata, dataset path and sha256, package versions, and the git commit and uncommitted
  changes **at launch** (`git`, `created_utc`). It is written at the start and rewritten at
  the end with the status and summary; the git state at that moment goes to `git_end`
  (`written_utc`).
- `trace.jsonl`: one event per line, covering LLM calls, retries, attempts, Lean checks and warnings.
- `results.jsonl`: one line per problem with status, stage, `error_classes`, `critiques`, replans,
  the accepted proof, LLM calls, tokens, cost (real and nominal) and wall time.
- `console.log`: everything the run printed.

`experiment` runs are paid. `run_eval.py` warns (without blocking) when a run starts inside
DeepSeek's peak-price window (01:00–04:00 and 06:00–10:00 UTC, Monday–Friday), and the
client warns when the first response's `model` differs from `expected_version` in
`models.yaml`. It prints the project's spend and what is left at the start and the run's
cost at the end.

### 5.5 Configuration

- **Models** are defined only in `config/models.yaml`: `providers` (endpoint and the
  environment variable of the key), `models` (id, `expected_version`, JSON mode, how to
  toggle thinking, prices and peak windows, `prompt_style`), and `profiles` (role → model
  and request parameters; `same_as` copies a role). Never write model names in code. To
  change the embedding model, edit `embeddings.selected` (and rebuild its index).
- **Control flow** lives in `config/pipeline.yaml`: `flow`, `critic.k_max`, `critic.r_max`,
  `critic.llm_feedback`, and each configuration's overrides in `config/ablations/`.
- **Budget, spend, cache and baselines**: `budget.b_max`, `spend.total_usd`, `spend.run_usd`,
  `spend.ledger`, `llm.cache`, `llm.cache_dir`, `llm.json_repair`, `baselines.*.attempts`,
  `run.seed`.
- **Agents**: `planner` (`n_max`, `retrieve`, `check_skeleton`, `refute` and `closing_auto`:
  `enabled`, `tactics`, `heartbeats`, `timeout_s`), `generator` (`samples` = *N*,
  `retrieve`), `autoformalizer` (`retrieve`) and `prepass` (`enabled`, `tactics`,
  `heartbeats`, `timeout_s`).
- **Retrieval**: `retrieval` → `enabled`, `method` (`rrf` | `bm25` | `dense`), `k`, `k_retry`,
  `depth`, `rrf_k`, `query_instruction`, `generic_query`, `index_dir`, `suggestions`.
- **Lean runner**: `lean` → `backend` (`repl` | `file`), `probes`, `base_imports`,
  `timeout_s`, `startup_timeout_s`, `restart_every`, `restart_memory_mb`, `kill_memory_mb`,
  `cache_size`, `syntax_check` and `syntax_timeout_s`.

### 5.6 Tests

```bash
pytest              # offline unit tests (toy corpus, fake encoder, fake REPL, fake LLM, recorded Lean outputs)
pytest -m slow      # integration tests with real Lean + Mathlib (~2.5 min warm)
```

The runner tests parse real Lean outputs stored in `tests/fixtures/lean/`; re-record
them with `python scripts/capture_lean_fixtures.py` when the Lean version changes.

### 5.7 Demo (Gradio)

From the repository root in **PowerShell** (Windows):

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-demo.txt            # gradio, pinned
$env:PYTHONUTF8 = "1"
python demo/app.py                              # open http://127.0.0.1:7860 (Lean loads in ~1 min warm)

# other port, if 7860 is taken
$env:GRADIO_SERVER_PORT = "7861"; python demo/app.py

# recorded examples and results only (no Lean, no API key; what the public Space runs)
$env:DEMO_MODE = "replay"; python demo/app.py
Remove-Item Env:DEMO_MODE                       # back to the full demo

python scripts/export_replays.py                # runs/ -> demo/replays/*.json + results (--candidates, --dry-run)
python scripts/build_demo_set.py --dry-run      # the "New" problems draw (without --dry-run: compile with sorry, write)
```

The page is in Spanish by default, with an ES/EN switch; every visible text lives in
`demo/i18n.py`. It is written for a reader who does not know Lean: technical details are
always behind a closed "technical details" toggle.

- **Solve** (local only: shown with `.env`, `lean_project/` and `lean_repl/` present and
  `DEMO_MODE` not `replay`). The header shows "Preparing the verifier… (step X of 3)" while the
  Lean REPL (Mathlib), the Retriever and a known-proof check load; the Solve button stays disabled
  until "Ready". Pick a problem (filters New / Easy / Medium / Hard; **New** are the six miniF2F
  `valid` problems in `data/demo_problems.jsonl`, outside the study and never run before, so a
  solve is fully live) and press Solve. The model is **DeepSeek (`experiment` profile, paid) by
  default, with no confirmation step**; the line under the button gives the typical cost, and the
  spend guard caps each demo problem at **0.20 USD** (`--run-usd 0.20`; `b_max` is unchanged).
  "Advanced options" switches to the free Gemini profile (`dev`) or takes a Lean statement typed by
  the user (it must compile with `sorry`; no LLM ever writes or edits it). The frozen C5
  configuration runs exactly as `scripts/run_eval.py --config C5 --run-name demo`, and the run is
  saved in `runs/<time>_C5_<profile>_demo/` (its one-problem input in `runs/demo_inputs/`).
  The steps (Plan, Search, Reason, Translate to Lean, Verify, Correct) update live; the result
  comes only from the run's `status` (the Lean kernel), with time, model queries, cost and how
  many queries came from the cache.
- **Recorded examples** plays real study runs from `demo/replays/` (no Lean, no API key) with a
  speed control, plus "Without the Critic" / "With the Critic" side by side (C4 vs C5). The
  export keeps a whitelist of trace fields: no raw model output, prompts, cache keys or local
  paths, and no PutnamBench informal text.
- **Study results**: Tables 1 and 2 of `docs/results/results.md` with plain titles and one
  reading sentence each.

Demo runs are not part of the study: `docs/results/make_tables.py` leaves `*_demo` runs out of
the project spend, and the demo's typical-cost line ignores them. They do count toward the
project-wide spend cap (`spend.total_usd` in `config/pipeline.yaml`), like any paid call.

---

## 6. Reproducing the experiments

### 6.1 Regenerating the tables and the figure from `runs/`

The run folders are not in git (`runs/` is git-ignored); place the eight `test` run folders
listed in `docs/results/results.md` ("Runs used") under `runs/`, plus the exploratory run
`20261108T130803Z_C5_experiment_pro_pro_hard` (Table 12, post hoc) and
`data/spend_ledger.jsonl` for the real-cost columns. Then, from the repository root:

```bash
pytest tests/test_stats.py                            # the statistics against known values
python docs/results/make_tables.py                    # → docs/results/results.md, tables.tex, figures/srk_curve.{pdf,png}
python docs/results/check_explained.py                # every number in docs/results_explained.md matches results.md
python docs/results/make_tables.py --dev /tmp/devout  # optional: the same code on dev runs (plan §7)
```

`check_explained.py` fails (exit code 1, one line per mismatch) if a table copied into
`docs/results_explained.md` is not verbatim, a note differs, or a number in the text does not
appear in `results.md` at the precision shown. Run it after every `make_tables.py` run and
every edit of `results_explained.md`.

`make_tables.py` only reads `runs/`, `data/eval.jsonl`, the ledger and
`data/curation/fidelity_review.yaml`. The run folders and how interrupted runs are merged
(one row per problem) are fixed in `TEST_SOURCES`, `C1_MERGED` and `DISCARDED` at the top of
the script, following the Deviations section of `docs/analysis_plan.md`. Two runs of the
script produce byte-identical files.

### 6.2 Running C1–C5 again

These are the commands used for the reported runs (`argv` in each `run_config.json`), in
the order they were run. Before any paid run: a clean tree, `pytest` green, nothing else
loading Lean, and a free `dev` smoke run.

```bash
# 0. checks
pytest
MODEL_PROFILE=dev python scripts/run_eval.py --config C5 --split dev --limit 3 --echo

# 1–5. the five configurations on test (paid; DEEPSEEK_API_KEY in .env), off-peak
MODEL_PROFILE=experiment python scripts/run_eval.py --config C5 --split test --echo --run-usd 1.50
MODEL_PROFILE=experiment python scripts/run_eval.py --config C4 --split test --echo --run-usd 0.40
MODEL_PROFILE=experiment python scripts/run_eval.py --config C3 --split test --echo --run-usd 2.00
MODEL_PROFILE=experiment python scripts/run_baseline_b.py --split test --check --echo --run-usd 1.00
MODEL_PROFILE=experiment python scripts/run_eval.py --config C1 --split test --echo --run-usd 3.50

# 6. tables: point TEST_SOURCES / C1_MERGED in docs/results/make_tables.py to the new run folders
python docs/results/make_tables.py
```

Nominal cost of the reported runs (no cache): C5 1.36, C4 0.39, C3 1.35, C2 with the check
0.71, C1 2.57 USD, about 6.4 USD in all, under the 9 USD project cap. C4 is C5 with retries
removed, so right after C5 its requests are answered from the LLM cache (its real cost was
0); without that cache, raise C4's `--run-usd` above its nominal cost. Without
`data/llm_cache/` the model samples again and the results will differ from run to run: the
reported numbers are one sample of each configuration. With the cache, every request is
answered from disk and the runs replay at no cost.

If a run is interrupted, follow the procedure in `docs/analysis_plan.md` (Deviations): write
the missing lines of `data/eval.jsonl`, unchanged and in order, to a new file and run only
those with `--data <file> --run-name resume` and the same configuration; the problem that
was cut runs from scratch.

---

## 7. Reproducibility notes

- Lean and Mathlib versions are pinned in `lean_project/`; the Lean REPL tag and commit
  are pinned in `scripts/build_repl.sh` (`runner.repl_env_info()` reports them).
- Index metadata (`data/index/*/<model>/meta.json`, `data/retrieval_eval/meta.json`)
  records the model, Hugging Face revision, precision, corpus hash and Mathlib commit.
- `data/eval.jsonl` is rebuilt by `scripts/fetch_eval_sources.sh` (pinned commits) and
  `scripts/build_eval_set.py` (seeded draw); see `data/README.md`.
- The reference set split, the run seed and the bootstrap use seed 2026.
- Every run stores `runs/<run>/run_config.json` (see §5.4). DeepSeek offers no versioned
  model id: `deepseek-flash` is an alias (it has already moved from V4-Flash to
  V4.1-Flash), and the API echoes the alias as `model`, so the `system_fingerprint` of
  every response (recorded with `model`) is the only per-call trace of the underlying model.
- Operational incidents during the `test` runs (two interrupted runs and their resumes, spend
  caps raised, the order C4 before C3, `dirty: true` in four `run_config.json` files) are
  recorded in `docs/analysis_plan.md` → Deviations. None changed code, prompts or configuration.
