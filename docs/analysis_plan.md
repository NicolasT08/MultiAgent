# Analysis plan (pre-registered before any run on `test`)

Written 2026-11-03, during Phase 8 Part A, **before any configuration was run on the `test`
split**. It fixes the metrics, tests and handling rules so that the analysis does not depend on
the results. Any later change must be recorded at the end of this file with its date and reason,
and reported in the paper as a deviation.

Notation and equations refer to the report (`docs/report/Proyecto_Inteligencia.pdf`, Section IV-A).

## 1. Data and design

- **Problems:** `data/eval.jsonl`, split `test`: 30 problems, stratified on the 3 × 3 grid
  (difficulty easy/medium/hard × area algebra/number theory/combinatorics, with the documented
  reallocation of Phase 7). The split was fixed with seed 2026 before any prompt tuning.
- **Configurations:** C1–C5 (Section IV-A8), all on the same LangGraph graph, profile
  `experiment` (`deepseek-flash`, `config/models.yaml`), seed 2026, with the configuration frozen
  by the team's commit at the end of Part A (its hash is recorded in each `run_config.json`).
- **Run order on `test`:** C5, C3, C4, C2, C1 (team decision 2026-10-31).
- **One run per (configuration, problem).** No configuration is re-run on `test` to obtain a
  better result. The only exception is the `provider_error` rule of Section 5.
- **Paired design:** every configuration sees the same 30 problems, so comparisons between
  configurations are paired by problem.
- The LLM response cache stays on. Identical requests across configurations (e.g. the first
  attempt of C4 and C5) are answered from the cache; this keeps the paired comparison tighter and is
  stated in the paper. Costs are compared with the **nominal** cost (`cost_usd_nominal`: cache
  hits priced as fresh calls) so that run order does not favour later configurations.

## 2. Primary outcome

- **Verified success** of configuration *m* on problem *x*: Acc(φ_x, c*_x) of Eq. 2 for the final
  assembled proof (status `solved` in `results.jsonl`). Only the Lean kernel decides (RNF-1).
- **Success rate:** SR_m = (number solved) / 30, with a **Wilson 95 % interval**.
- Since each problem is run once, pass@k of Eq. 15 is estimated only for k = 1 (= SR_m). This is
  stated as a limitation; no repeated sampling is run.

## 3. Planned comparisons (confirmatory)

Three comparisons, decided now, each a **two-sided exact McNemar test** on the paired outcomes
(binomial test on the discordant pairs b, c with p = 1/2), **Bonferroni-corrected** for 3 tests
(reject at p < 0.05 / 3 ≈ 0.0167; adjusted p-values = min(1, 3p) are also reported):

| ID | Comparison | Question |
|---|---|---|
| H1 | C5 vs C1 | Does the multi-agent pipeline beat direct proof prompting (at equal mean measured computation, `b_max` of C1 fixed in Part A)? |
| H2 | C5 vs C3 | RAG contribution, Δ_RAG = SR_C5 − SR_C3 (Section IV-A9e). |
| H3 | C5 vs C4 | Contribution of the Critic loop (retries and replanning). |

For each one we report: b and c (discordant counts), ΔSR, a 95 % **paired bootstrap** interval for
ΔSR (10 000 resamples of problems, seed 2026, percentile method), the exact p-value and the
Bonferroni-adjusted p-value. With n = 30 the power is low; a non-significant result is reported as
"no evidence of a difference", never as "no difference".

**Exploratory only** (no correction, labelled exploratory, no claims): C3 vs C4, C1 vs C3, C1 vs C4,
and any per-difficulty or per-area comparison.

C2 is **not** compared with McNemar on verified success: its success is self-assessed, not verified.

## 4. Secondary outcomes

1. **Per cell of the 3 × 3 grid:** for each configuration, solved / n of each (difficulty, area)
   cell, with the cell's n shown. Descriptive only: no tests and no intervals per cell (n ≤ 5).
   Margins by difficulty and by area are reported with Wilson intervals. Empty cells (no easy
   combinatorics) are shown as "—".
2. **Baseline B (Eq. 16):** claimed rate |B⁺| / 30 (Wilson), and FPR_B = |{x ∈ B⁺ : ¬Acc}| / |B⁺|
   (Wilson), where every self-assessed proof is passed to the Autoformalizer–Verifier loop with the
   same budget (`run_baseline_b.py --check`, to be built before the C2 run on `test`). FPR_B is an
   upper bound on the true false-positive rate; a manual review of the B⁺ problems that fail
   formalization (all of them if ≤ 10, otherwise 10 drawn with seed 2026) separates wrong informal
   proofs from formalization failures.
3. **Efficiency (Section IV-A9c, RNF-3):** per configuration, C̄_solved = Σ_x C(x) / #solved
   (failed problems' calls included in the numerator), and the same ratio for tokens, nominal USD
   cost and wall-clock time; also the means per problem. 95 % bootstrap intervals (resampling
   problems). RNF-3 (C̄_solved ≤ 20) is checked for C5 and reported as met or not; it is not tuned.
4. **Refinement robustness (Eqs. 14, IV-A9d):** SR(k) for k = 1, 2, 3, where a C5 problem counts
   as solved at k if its accepted proof used at most k attempts on every subgoal (read from
   `trace.jsonl`); k = 1 is also reported from C4. Δ̂(k) = SR(k) − SR(k−1) is plotted against the
   independence curve of Eq. 14 with p_i estimated from first-attempt subgoal success in C5. The
   paper states that k = 1 from C4 has no retries or advice while k = 2, 3 come from C5.
5. **Error taxonomy (Section IV-A9f):** relative frequency of each κ class of Eq. 11 over **all
   failed attempts**, per configuration and per area; separately, the final failure reason per
   problem (`stage` and final status). C1 attempts are classified with the same κ.

## 5. Status handling

| Status in `results.jsonl` | Meaning | Primary analysis |
|---|---|---|
| `solved` | Acc holds on the assembled proof | success |
| `failed` (any `stage`) | the method did not find an accepted proof | failure |
| `budget_exhausted` | `b_max` LLM calls reached | failure (the budget is part of the method) |
| `timeout` / memory limit | Lean verification limits | failure |
| `error` / `node_error` / `invalid_statement` | a bug in the system or the statement | failure; counted and listed |
| `provider_error` | the API refused or failed after all transport retries | see below |
| `budget_stop` | the project or run spend cap stopped the run | see below |
| run status `lean_unavailable`; row with `lean_down: true` | Lean could not load Mathlib (start-up probes) | as `provider_error`: infrastructure; re-run once, see below |

- **`provider_error`** is an infrastructure failure, not a decision of the model. After a run, the
  problems with `provider_error` are re-run **once**, in a new run folder with the same
  configuration and cache (the original run is kept untouched). The re-run's result replaces the
  provider error in the analysis. If it is again `provider_error`, it counts as a **failure** in the
  primary analysis, and a sensitivity analysis excludes those problems from that configuration
  (and, for McNemar, the pairs where either side is excluded). Counts are reported per configuration.
- **`budget_stop`**: the problem and the rest of the run were not attempted. The team decides
  whether to raise the spend cap; if raised, only the problems not attempted are run with the same
  frozen configuration and merged. If not, that configuration is reported as **incomplete**: SR on
  the attempted problems (with n stated), a conservative SR counting not-attempted problems as
  failures, and McNemar only on the problems attempted by both configurations.
- **Lean unavailable**: a run that stops with `lean_unavailable` made no LLM call for the problems
  not run; those problems, and a row marked `lean_down: true` (Lean was lost during it, so its
  failure may be Lean's), are re-run once with the same configuration, exactly like
  `provider_error`. A success in a `lean_down` row stands (the kernel accepted it).
- Runs marked `degraded` (Lean file back-end used) are valid and are flagged in the tables.

## 6. Fidelity notes (manual check before reporting success)

`data/curation/fidelity_review.yaml` lists, under `notes`, problems whose formal statement differs
from the informal text in a way judged harmless at curation time. Every problem **solved on `test`
by any configuration** that has a note is reviewed by hand before it is counted as a success in the
paper (PLAN.md, Phase 9). The review records whether the accepted proof establishes what the
informal problem asks. The verified SR (Section 2) is never changed by this review: the kernel's
verdict is what it is. If a review concludes that the formal statement does not capture the
informal problem, the paper reports it next to the table (problem, configuration, reason) and gives
the SR both with and without that problem.

## 7. What is reported, in order

1. Table: SR with Wilson 95 % CI for C1, C3, C4, C5; claimed rate and FPR_B for C2.
2. Table: H1–H3 with b, c, ΔSR, bootstrap CI, exact p and Bonferroni-adjusted p.
3. Table: per-cell solved/n for each configuration, with difficulty and area margins.
4. Table: efficiency (calls, tokens, nominal cost, time per problem and per solved problem).
5. Figure: SR(k), k = 1…3, vs the independence curve of Eq. 14.
6. Figure or table: κ taxonomy per configuration and area.
7. Counts of `provider_error`, `budget_stop`, `error`, degraded runs and the fidelity reviews.

Analysis code (`src/formal_mas/analysis/stats.py`, `scripts/compare.py`) is written in Phase 9 to
implement exactly this plan; it is tested on `dev` runs before it reads any `test` result.

## Deviations

No deviation from the analysis itself. Infrastructure incidents during the `test` runs:

- **2026-11-03, C5 on `test` interrupted by low memory.** Run `20261103T174646Z_C5_experiment`
  (commit `ad70ec2`, clean tree) was killed at about 19:16 UTC by the host (Claude Code stops
  background jobs when the machine runs critically low on memory; the Lean REPL, ~3.5 GB, was
  sharing RAM with an idle Lean language server, ~2.7 GB). It is an infrastructure failure, not a
  decision of the method. 23 of 30 problems had finished and are kept unchanged; the 24th
  (`imo_1987_p6`) was cut mid-problem and left no row in `results.jsonl`. No row was `lean_down`,
  and there was no `provider_error` or `budget_stop`.
  The 7 problems without a row were completed in two resume runs. Both used the same frozen
  configuration (the analysis is the same at either commit), the same seed and cache, and the
  language server was stopped first. The input of each is a file holding exactly the needed lines of
  `data/eval.jsonl`, unchanged and in the original order. Its sha256 and the id list are in that
  run's `run_config.json`, and a copy is kept as `test_remaining.jsonl` in that run's folder.
  1. `20261103T212359Z_C5_experiment_resume` (commit `ad70ec2`, clean tree; input sha256
     `53284f8f…086fc4`) ran all 7 from scratch: `imo_1987_p6`, `imo_1979_p1`, `imo_1984_p6`,
     `imo_1964_p1_2`, `putnam_1964_b2`, `putnam_1995_b1`, `putnam_1965_b2`. It reached its spend
     cap (`--run-usd 0.50`) during the third problem. `imo_1987_p6` and `imo_1979_p1` finished
     (both `failed`). `imo_1984_p6` got a `budget_stop` row after 19 calls, and the last 4 were
     not attempted.
  2. `20261103T225140Z_C5_experiment_resume2` (commit `93bc701`, which changes only this file; clean
     tree; input sha256 `f3da0f6d…6d77f91`) ran `imo_1984_p6` from scratch,
     plus the 4 problems not attempted, with `--run-usd 1.00` (the cap raised by team decision, §5).
     The requests that `imo_1984_p6` repeats from step 1 are answered from the cache.
  **Merge:** the C5 result on `test` has one row per problem (30 in all), taken from the run where
  the problem finished:
  - 23 rows from the first run;
  - `imo_1987_p6` and `imo_1979_p1` from resume 1;
  - `imo_1984_p6` and the other 4 from resume 2.
  The `budget_stop` row of `imo_1984_p6` in resume 1 is discarded; only its complete row counts. The
  spend of the discarded attempts (`imo_1987_p6` in the first run and `imo_1984_p6` in resume 1)
  counts towards the project's real cost. It is not in any kept row, so each problem's efficiency
  figures come from its kept row only. Cache hits lower the real cost of the resume runs but not
  their nominal cost, and nominal cost is the figure compared (§1).

- **2026-11-03, run order C4 before C3.** C4 was run right after C5, and C3 moved to the next
  off-peak morning, so the order is C5, C4, C3, C2, C1 instead of C5, C3, C4, C2, C1 (§1). The
  reason is schedule and cost only. C3 takes about 3 h and would have run into the provider's peak
  window (01:00 UTC), while C4 is short and is answered mostly from the cache of C5. This has no
  effect on the analysis. Each configuration is still run once on the same 30 problems with the
  same frozen configuration, and the comparisons are paired by problem. C4 shares its cached
  requests with C5, not with C3, so its cache hits are the same in either order. Costs are compared
  at nominal prices (§1), so run order does not favour either configuration.

- **2026-11-04, per-run spend cap raised from 1.0 to 2.0 USD before C3 on `test`.** Team decision.
  Only `config/pipeline.yaml → spend.run_usd` changed (1.0 → 2.0); `run_eval.py --run-usd` can only
  lower that cap, so C3 (about 3 h, no cache shared with C5 because its prompts carry no lemmas;
  C5 on `test` cost 1.36 USD real in total) would otherwise very likely end in `budget_stop`
  before the 30th problem. This is a spend-control change, not a method change: the cap is
  checked before each paid request and only decides whether the request is sent, so it does not
  change any prompt, model, budget `B_max`/`b_max`, policy or result of a request that is sent. The
  project cap `spend.total_usd` stays at 9.0 USD. C5 and C4 ran under the 1.0 USD cap (or a lower
  `--run-usd`); C3 runs with `--run-usd 2.00`. The offline test that pins the configured caps
  (`tests/test_spend.py::test_real_pipeline_yaml_has_the_caps`) was updated to the new value.

- **2026-11-05, per-run spend cap raised from 2.0 to 3.5 USD before C1 on `test`.** Team decision.
  Only `config/pipeline.yaml → spend.run_usd` changed (2.0 → 3.5). C1 costs more per call than C5
  (the cost run `20261103T161957Z_C1_experiment_c1_cost` spent 0.21 USD real on 3 `dev` problems,
  about 0.07 USD per problem), so 30 problems would likely end in `budget_stop` under 2.0 USD. As
  before, this is a spend-control change, not a method change: the cap is checked before each paid
  request and only decides whether the request is sent, so it does not change any prompt, model,
  budget `B_max`/`b_max`, policy or result of a request that is sent. The project cap
  `spend.total_usd` stays at 9.0 USD. C1 runs with `--run-usd 3.50`. The offline test that pins
  the configured caps was updated to the new value.

- **2026-11-05, C1 on `test` stopped by the team.** Run `20261105T131443Z_C1_experiment` (commit
  `5ed7507`, clean tree, `--run-usd 3.50`) was stopped on purpose at about 17:43 UTC, together with
  its whole process tree (REPL, `lake`), to free the machine for other work. It is an operational
  incident, not a decision of the method: no code, prompt, model or configuration changed. 23 of 30
  problems had finished and are kept unchanged; the 24th (`imo_1987_p6`) was cut mid-problem
  (after 10 calls, 0.10 USD real) and left no row in `results.jsonl`. There was no `provider_error`
  or `budget_stop`. The 7 problems without a row will be completed in a resume run, following the
  same procedure as for C5 (same frozen configuration, seed and cache; input file with exactly the
  needed lines of `data/eval.jsonl`; `imo_1987_p6` run from scratch, its earlier spend counted in the
  project's real cost but in no kept row).
  The resume run is `20261107T200233Z_C1_experiment_resume` (commit `2c8eae5`, clean tree; input
  sha256 `009bece56dc0fdde3c814c9e81aeda6ee7bd8dd677d0fa29bab5dd723c907b8b`, the 7 problems in
  dataset order; `--run-usd 1.65` = 3.50 minus the 1.8407 USD C1 had spent, rounded down, so that
  C1 as a whole stays under 3.50).

- **2026-11-07, `dirty: true` in four `run_config.json` files (recording artefact, found in the
  analysis).** `run_eval.py` rewrites `run_config.json` when a run ends, and the git state (and
  `created_utc`) were read again at that moment instead of at launch. In
  `20261103T212359Z_C5_experiment_resume`, `20261103T225140Z_C5_experiment_resume2`,
  `20261103T235556Z_C4_experiment` and `20261107T200233Z_C1_experiment_resume` the only changed file
  is `docs/analysis_plan.md`, edited while the run was going to log these very incidents; every
  tree was clean at launch (checked before each run, as the Phase 8 rules require). No code,
  prompt, configuration or data file changed, so the method is not affected. In those files
  `created_utc` is also the end time; the start is the timestamp in the run name and the
  `run_start` event of `trace.jsonl`. The two interrupted runs (`20261103T174646Z_C5_experiment`,
  `20261105T131443Z_C1_experiment`) never reached the final rewrite and show their launch state.
  Fixed after all `test` runs (`run_config.run_start_info`): `git` and `created_utc` now describe
  the launch, and the state when the file is written goes to `git_end` and `written_utc`. Runs
  already made are not rewritten.

Clarifications written before any run on `test`:

- **2026-11-03, FPR_B check (§4.2), how "the Autoformalizer–Verifier loop with the same budget" is
  implemented** (`scripts/run_baseline_b.py --check`, `baselines/baseline_b.check_claim`): each claimed
  proof (B⁺) is the sketch of the Autoformalizer on the trivial plan (the whole theorem as the only
  subgoal), with the Retriever's lemmas as in C5; up to `critic.k_max` = 3 attempts, each failure
  classified by κ and followed by the Critic's deterministic feedback (no LLM advice, no Planner, no
  Generator, no automation pre-pass, so no fixed tactic can stand in for the claimed proof); its own
  budget of `B_max` = 40 calls, counted apart from C2's own calls (C2's efficiency figures exclude the
  check; in practice the check makes at most k_max × 2 = 6 calls: one Autoformalizer call and at most
  one JSON repair per attempt). Acc is Eq. 2 on the assembled theorem. FPR_B = not_verified /
  (verified + not_verified), where `budget_exhausted` counts as not_verified (the budget is part of the
  method). A check that ended in `provider_error`, `budget_stop` or `error`, or a row marked `lean_down`,
  is left out of FPR_B, counted apart and re-run once like a `provider_error` (§5); if it fails again
  the same way it is reported as not checked. Problems C2 did not claim are not checked.
