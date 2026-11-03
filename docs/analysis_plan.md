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

(None yet.)
