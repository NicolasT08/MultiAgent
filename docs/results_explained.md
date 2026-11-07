# What was tested and what came out

Plain-language summary of the experiments. For how the system works, see
[how_it_works.md](how_it_works.md). The pre-registered analysis plan is
[analysis_plan.md](analysis_plan.md) (written before any `test` result was seen); the full
statistical tables will be in `docs/results/`.

## 1. The dataset

`data/eval.jsonl` holds **45 problems**, each with its informal text and its Lean statement:

| | easy | medium | hard | total |
|---|---|---|---|---|
| `dev` (for tuning) | 5 | 5 | 5 | 15 |
| `test` (final runs only) | 10 | 10 | 10 | 30 |

- **Sources:** 40 from miniF2F (the Lean 4 port used by DeepSeek-Prover-V1.5) and 5 from
  PutnamBench (3 of them in `test`).
- **Areas in `test`:** algebra 13, number theory 13, combinatorics 4.
- Every statement was checked to compile and reviewed by hand for fidelity to the informal
  text (`data/curation/`). Prompts and settings were tuned only on `dev`; `test` was used once,
  for the final runs.

## 2. The five configurations

All five run on the same code and graph; only flags in `config/ablations/` change.

| | Name | In one line |
|---|---|---|
| **C1** | Baseline A | One LLM asked directly for a whole Lean proof; up to 18 independent tries (Lean's errors are not fed back). No agents. |
| **C2** | Baseline B | One LLM solves the problem in natural language and judges its own answer. No Lean. |
| **C3** | No RAG | Full system without the lemma search. |
| **C4** | No Critic | Full system, but one attempt per subgoal and no replanning. |
| **C5** | Full system | All agents, retries and replanning, up to 40 calls per problem. |

All runs used the same model for every role (DeepSeek `deepseek-flash`, a general-purpose
model, not a specialised prover), the same seed (2026), Lean `v4.36.0-rc1` and Mathlib
`625d891`. The exact settings of each run are in its `runs/<run>/run_config.json`.

## 3. Results on `test` (30 problems)

The C5 and C1 runs were interrupted once and resumed; their pieces are merged into one row per
problem exactly as described in `analysis_plan.md` (Deviations). Run folders:

| Config | Run folder(s) in `runs/` |
|---|---|
| C5 | `20261103T174646Z_C5_experiment` + `…_resume` + `…_resume2` |
| C4 | `20261103T235556Z_C4_experiment` |
| C3 | `20261104T194056Z_C3_experiment` |
| C2 | `20261104T232056Z_C2_experiment_check` |
| C1 | `20261105T131443Z_C1_experiment` + `20261107T200233Z_C1_experiment_resume` (merged: `C1_test_merged_results.jsonl`) |

No run had a `provider_error` or a `budget_stop` row in the merged results.

### 3.1 Problems solved (proof accepted by Lean)

| Config | easy (10) | medium (10) | hard (10) | **total (30)** |
|---|---|---|---|---|
| C1 Baseline A | 10 | 6 | 4 | **20** |
| C3 No RAG | 9 | 7 | 4 | **20** |
| C4 No Critic | 9 | 4 | 1 | **14** |
| C5 Full system | 10 | 10 | 4 | **24** |

By area (descriptive only): algebra / number theory / combinatorics solved out of 13 / 13 / 4:
C1 10 / 8 / 2, C3 12 / 7 / 1, C4 5 / 7 / 2, C5 11 / 10 / 3.

**The 10 hard problems** (post hoc, descriptive: this look was not in the analysis plan; the
per-configuration table is Table 11b in `docs/results/results.md`). Three things stand out.
C1 and C5 solve exactly the same 4 hard problems (imo_1964_p1_2, imo_1965_p2, putnam_1964_b2,
putnam_1965_b2), so on the hard tier the whole C5 gain over C1 disappears. Five hard problems
(imo_1978_p5, imo_1979_p1, imo_1984_p6, imo_1987_p6, putnam_1995_b1) are solved by no
configuration. C3 and C5 also solve 4 each but differ by one problem in each direction: only
C3 solves imo_1993_p5, only C5 solves putnam_1964_b2. Since C5 has everything C3 has plus
retrieval, a hard problem that C3 solves and C5 does not shows how much a single run can vary
from one to the next; each configuration was run once, so differences of one problem should
not be read as an effect.

### 3.2 C2: what the LLM claimed vs what Lean confirmed

C2 never uses Lean, so it cannot "solve" anything in the sense above. Instead it reports
whether it believes its own proof is correct. To see how often that belief is wrong, each
claimed proof was handed to the Lean pipeline (Autoformalizer + Verifier, up to 3 attempts,
same lemma search as C5; `--check` in `scripts/run_baseline_b.py`).

| | easy | medium | hard | total |
|---|---|---|---|---|
| Claimed correct by the LLM | 10 | 10 | 10 | **30** |
| Confirmed by Lean | 10 | 8 | 1 | **19** |

C2 claimed to have solved every problem. Lean confirmed 19; for the other 11 the claim could
not be turned into an accepted proof. That is the rate the plan calls FPR_B (Eq. 16):
11 / 30 = 0.37. Almost all of the gap is on hard problems, where the model was sure of 10
answers and only 1 could be confirmed. Note that "not confirmed" means "the pipeline could not
formalise it within the budget"; it does not always mean the informal argument was wrong.
This is the main reason for the project: a confident LLM is not a reliable judge of its own
mathematics.

### 3.3 Cost and LLM calls

| Config | Real cost of the run(s), USD | LLM calls (kept rows) | Calls per solved | Nominal cost per solved, USD |
|---|---|---|---|---|
| C1 | 2.5679 | 257 | 12.85 | 0.1285 |
| C2 (+ check) | 0.7083 (0.0814 C2, 0.6269 check) | 65 (C2 only) | n/a | n/a |
| C3 | 1.3500 | 530 | 26.50 | 0.0675 |
| C4 | 0.0000 (all 132 calls from cache) | 132 | 9.43 | 0.0278 |
| C5 | 1.3585 | 406 | 16.92 | 0.0566 |

How to read this table:

- **Real cost** is what the provider charged (`data/spend_ledger.jsonl`). It includes the
  attempts discarded when a run was interrupted (C1: 0.0985 USD; C5: 0.18 USD, the part of
  its real cost not in any kept row).
- **Nominal cost** is what each kept row would cost with no cache hits. The plan compares
  configurations on nominal cost, because the cache makes later runs cheaper for reasons that
  have nothing to do with the method.
- **C4 cost 0 USD** because every request it made had already been made, word for word, by C5
  (C4 is C5 with retries removed, so its first attempts are identical); the answers came from
  the cache.
- C1 has a budget of 18 calls per problem (the average C5 used on `dev`, by a rule fixed before
  `test`), the full system 40.
- **Project spend** (all runs, `dev` included): 7.3596 USD of the 9 USD cap.

### 3.4 Time per problem

Wall-clock minutes per problem, mean / median (from `docs/results/results.md`, Table 5;
descriptive, not part of the planned tests):

| Config | easy | medium | hard |
|---|---|---|---|
| C1 | 0.7 / 0.4 | 14.3 / 16.2 | 20.3 / 25.3 |
| C2 (without the check) | 0.1 / 0.1 | 0.1 / 0.1 | 1.0 / 0.5 |
| C3 | 0.8 / 0.1 | 4.0 / 2.6 | 13.6 / 12.3 |
| C4 | 0.1 / 0.0 | 0.1 / 0.1 | 0.1 / 0.1 |
| C5 | 0.4 / 0.0 | 2.7 / 2.6 | 15.1 / 14.1 |

Times are dominated by waiting for the model. C4's times are not comparable: all its LLM
answers came from the cache, so they measure only Lean. Many easy problems take seconds in
C3–C5 because they need no LLM-written subgoal proof at all (8–9 solved problems per
configuration are closed by the fixed tactics or by the plan alone; `results.md`, Table 8).

## 4. Statistical significance

Computed in `docs/results/` (script `docs/results/make_tables.py`) exactly as fixed in the
analysis plan before `test` was run.

**Success rate with 95 % Wilson confidence intervals:**

| Config | Solved | SR | 95 % CI |
|---|---|---|---|
| C1 | 20/30 | 66.7 % | 48.8 – 80.8 % |
| C3 | 20/30 | 66.7 % | 48.8 – 80.8 % |
| C4 | 14/30 | 46.7 % | 30.2 – 63.9 % |
| C5 | 24/30 | 80.0 % | 62.7 – 90.5 % |
| C2 | FPR_B = 11/30 | 36.7 % | 21.9 – 54.5 % |

The intervals are wide and overlap: that is what 30 problems allow.

**The three planned comparisons** (exact McNemar test on the same 30 problems; Bonferroni
correction for 3 tests, so a difference counts only if the adjusted p is below 0.05). *b* =
problems only C5 solved, *c* = problems only the other configuration solved.

| | Comparison | b | c | Difference | Adjusted p | Significant? |
|---|---|---|---|---|---|---|
| H1 | C5 vs C1 (full system vs direct prompting) | 4 | 0 | +13.3 pp | 0.375 | no |
| H2 | C5 vs C3 (contribution of retrieval) | 5 | 1 | +13.3 pp | 0.656 | no |
| H3 | C5 vs C4 (contribution of the Critic) | 10 | 0 | +33.3 pp | 0.006 | **yes** |

In plain words:

- **The Critic (retry and replan) makes a significant difference.** C5 solved 10 problems
  that C4 did not, and C4 solved none that C5 missed (+33.3 points, paired bootstrap
  95 % CI +16.7 to +50.0).
- **Retrieval (RAG) and the full system vs C1 point in C5's favour, but the difference is not
  significant.** With 30 problems a difference of 4 or 5 problems is compatible with chance.
  "Not significant" here means *no evidence of a difference*, not evidence that there is
  none: the test has little power at this sample size.
- **FPR_B = 36.7 %** is an upper bound on how often C2's self-assessment is wrong: an
  unconfirmed claim may be a formalisation failure rather than a wrong argument. For 5 of the
  11, C5 has a Lean-verified proof of the same statement, so at least the claimed result is
  true there (whether C2's own argument was sound is a separate question, left to the manual
  review the plan asks for).

The full tables (per-cell grid, efficiency with confidence intervals, error taxonomy,
refinement curve SR(k)) and every interpretation of the plan are in `docs/results/results.md`.

## 5. Limitations

- **Small sample.** 30 test problems (10 per difficulty, only 4 in combinatorics). Confidence
  intervals are wide, and per-area numbers are descriptive only.
- **One general-purpose model.** Every role used the same general model (`deepseek-flash`), not
  a model trained for Lean. Results may differ with other models. `deepseek-flash` is an
  alias with no versioned id; the run configs record what was used.
- **Possible contamination.** miniF2F and the competition problems are public, and Lean proofs
  of many miniF2F problems are online. The model may have seen some of them in training, which
  would make every configuration look better than it would on new problems. This affects all
  configurations, but not necessarily equally (C1 asks for a Lean proof directly, which is
  where a memorised proof might help most).
- **Formal vs informal statement.** Lean checks the formal statement, not the original text.
  Every statement was reviewed for fidelity, and every solved problem with a curation note was
  reviewed again after the runs (`data/curation/fidelity_review.yaml`, `post_test_review`). All
  four count as solved. One needs a caveat: **`amc12b_2003_p6`** (solved by C1, C3, C4, C5 and
  confirmed for C2) asks which option is a possible first term. The formalisation proves that
  the first term must be ±2/√3 (a necessary condition), not that −2√3/3 is actually attained;
  that is enough to rule out the other options.
- **One run per configuration.** Each configuration ran once with one seed; LLM output varies
  between runs, so the counts above are a single sample.
- **Interrupted runs.** C5 and C1 were stopped by operational incidents and resumed with the
  same frozen configuration; the details are recorded in `analysis_plan.md` (Deviations).
