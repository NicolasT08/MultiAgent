## Method notes (interpretations of `docs/analysis_plan.md` and data anomalies)

Hand-written; appended verbatim to `results.md` by `make_tables.py`. Each item is a point where the plan
had to be read literally or was silent, or a fact found in the data.

### Analysis code

1. **Analysis code.** Plan §7 places the statistics in `src/formal_mas/analysis/stats.py`. The first version of
   this analysis implemented them inside `docs/results/make_tables.py` (the file did not exist yet); on
   2026-11-07, by team decision, they were moved to `src/formal_mas/analysis/stats.py` (Wilson, exact McNemar,
   Bonferroni, bootstrap, quartiles), with offline tests against closed-form values in `tests/test_stats.py`.
   `make_tables.py` imports them, and the regenerated `results.md` and `tables.tex` were checked to be byte for
   byte identical to the earlier output (before Table 10 was switched to the post-test fidelity review). Exact binomial tests and Wilson intervals come from
   `scipy.stats.binomtest` (Wilson without continuity correction). The bootstrap uses
   `numpy.random.default_rng`. The κ classifier is imported read-only from `src/formal_mas/orchestrator/policy.py`.
   `scripts/compare.py` was not written: `make_tables.py` produces every table of §7.
2. **Dev before test (plan §7).** The script was first run in `--dev` mode on dev runs, using stand-ins on the
   same 15 dev problems, and checked there: Wilson 11/15 = [48.0, 89.1], exact McNemar p values computed by
   hand, and the C1 κ classification on `20261103T161957Z_C1_experiment_c1_cost`. Unit tests of the statistics
   were added later (item 1).
   Disclosure: before the script existed, the analyst had seen the first `test` row of C5 (`mathd_algebra_206`,
   solved) and the summaries stored in the `run_config.json` of the C5 resume runs and of C4, while checking the
   configurations. After the first `test` run, the script changed in three ways: (a) C2 wall time now stops at
   `claim_check_start` (the row's `wall_s` includes the claim check, and the 2026-11-03 Clarification excludes
   the check from C2's figures); (b) a note on responses cut at `max_tokens` was added; (c) LaTeX layout. No test,
   comparison, correction or metric of the plan was changed.

### Merges and integrity

3. **C5 merge** follows the C5 Deviations entry exactly. It keeps 23 rows of `20261103T174646Z_C5_experiment`,
   `imo_1987_p6` and `imo_1979_p1` of `..._resume`, and 5 rows of `..._resume2`. The `budget_stop` row of
   `imo_1984_p6` in resume 1 is discarded; the script asserts its status. The script fails if a row is kept twice
   or if a source row is neither kept nor discarded.
4. **C1** uses `runs/20261107T200233Z_C1_experiment_resume/C1_test_merged_results.jsonl`. Every merged row equals
   (as parsed JSON) exactly one source row: 23 from `20261105T131443Z_C1_experiment` and 7 from the resume.
5. All five configurations have exactly 30 rows with the same 30 `test` ids. There is no `provider_error`,
   `budget_stop`, `error`, `node_error`, `invalid_statement` or `lean_down` row, and no degraded run. The plan's
   re-run rule and sensitivity analysis (§5) therefore do not apply. Area and difficulty in each row match
   `data/eval.jsonl`.
6. All `run_config.json` files agree on profile, seed, split, Lean toolchain, Mathlib revision and model settings.
   `git diff ad70ec2 2c8eae5 -- src config` shows that only `config/pipeline.yaml: spend.run_usd` changed (the
   documented spend-cap deviations). The input sha256 of the resume runs match the Deviations entry.
   **Anomaly:** `run_config.json` records `dirty: true` for C5 resume 1, C5 resume 2, C4 and the C1 resume, with
   `changed_files = [docs/analysis_plan.md]` only. The Deviations entry calls these trees "clean". The only
   uncommitted file was the plan itself.
7. The first C5 run and the first C1 run have run status `running`: they were killed, as documented. Their
   results files hold only finished problems.

### Primary outcome and comparisons

8. **C2 in the SR table.** Following §7.1, C2 has no verified SR. It enters through its claimed rate
   |B⁺|/30 = 30/30 and FPR_B. The 19 claims that the check verified are reported as a count only, because the
   plan defines no SR for them. C2 is not in any McNemar test (§3). In the per-cell and margin tables, C2 shows
   **claimed** counts, labelled as such, because §4.1 says "for each configuration".
9. **McNemar:** b = first configuration solved and second not; c = the reverse; two-sided exact binomial on
   b | b + c with p = 1/2 (p = 1 if b + c = 0). Bonferroni-adjusted p = min(1, 3p); a test is significant if
   p < 0.05/3.
10. **Paired bootstrap** (plan: 10 000 resamples of problems, seed 2026, percentile). The generator is a fresh
    `numpy.random.default_rng(2026)` for each statistic, so all comparisons use the same 10 000 index vectors.
    The plan does not name a generator. For H2 the lower bound is exactly 0.0 pp.
11. Exploratory comparisons (C3 vs C4, C1 vs C3, C1 vs C4) get b, c, ΔSR and an uncorrected exact p only. The
    plan specifies no bootstrap CI for them.

### FPR_B (Eq. 16, §4.2, Clarification 2026-11-03)

12. B⁺ holds all 30 problems (every C2 row is `claimed_solved`). Check status: 19 `verified`, 11 `not_verified`.
    No check ended in `budget_exhausted`, `provider_error`, `budget_stop`, `error` or `lean_down`, so none is
    left out. FPR_B = 11/30.
13. **Manual-review sample.** 11 claims are not verified, which is more than 10, so 10 are drawn "with seed 2026".
    The plan does not give the drawing procedure; `random.Random(2026).sample(sorted(ids), 10)` was used, which
    leaves out `amc12a_2008_p15`. The manual review itself (informal proof wrong vs. formalization failure) is a
    task for the team and has not been done.
14. Factual cross-reference, not a conclusion: for 5 of the 11 not-verified claims (`amc12a_2008_p15`,
    `amc12b_2002_p3`, `imo_1964_p1_2`, `imo_1965_p2`, `putnam_1965_b2`), C5 has a kernel-accepted proof of the
    same formal statement.

### Efficiency (§4.3)

15. C(x) = `llm.llm_calls` of the row. It includes cache hits and JSON-repair calls, as the budget counts them.
    Tokens = prompt + completion (reasoning tokens are part of completion in the ledger). Nominal cost
    (`cost_usd_nominal`) is the compared figure (§1). Real cost is shown as well.
16. **Real cost.** The real cost of every kept row equals the ledger sum for (run_id, problem_id); no mismatch was
    found. The ledger totals include spend that is in no kept row:
    C1 +0.09845 USD (`imo_1987_p6` in the first C1 run: 10 calls, 5 paid and 5 cached JSON repairs), so
    C1 = 2.4695 + 0.0985 = 2.5679 USD.
    C5 +0.18153 USD (`imo_1987_p6` in the first run: 0.02309 USD, 8 paid calls; `imo_1984_p6` in resume 1:
    0.15844 USD, 15 paid calls), so C5 = 1.1770 + 0.1815 = 1.3585 USD.
    C2 = 0.0814 (C2 itself) + 0.6269 (claim check) = 0.7083 USD. C3 = 1.3500 USD. C4 = 0.0000 USD.
    Per-problem and per-solved figures come from kept rows only.
17. **C2 efficiency** leaves out the claim check: its calls, tokens and cost come from the row's `llm`, and its
    wall time runs from `problem_start` to `claim_check_start`. C̄_solved needs a "solved" count, and C2 has no
    verified one, so its ratio uses claimed solutions (30).
18. **C4:** all 132 LLM calls were cache hits (requests identical to C5's), so its real cost is 0 and its LLM
    latency is near zero. Lean verification was real (5 of 259 `lean_verify` events were Lean-cache hits). C4's
    wall time (about 0.1 min per problem) therefore does not measure LLM computation. Nominal cost is the
    comparable figure (§1).
19. Bootstrap CIs for the efficiency ratios: plan §4.3 gives no seed, so the same procedure as note 10 is used
    (seed 2026, percentile). For Σx/#solved, resamples with no solved problem would be dropped; none occurred on
    `test`.
20. The median/IQR table (4d) and wall time by difficulty (Table 5) were requested by the team. They are
    descriptive only and not in the plan.

### Refinement robustness (§4.4, Eq. 14)

21. **Accepted plan.** The trace of a solved C5 problem is split at each `plan_proposed` / `plan_fallback` that
    follows subgoal work (attempt counters restart after a replan). The accepted plan is the last segment.
    "Attempts on a subgoal" is the `attempts` field of `subgoal_solved`; pre-pass subgoals count as 0. Read
    literally ("its accepted proof used at most k attempts on every subgoal"), a replanned problem counts by its
    accepted plan only. This affects `algebra_absapbon1pabsapbleqsumabsaon1pabsa`: C5 replanned once, then
    solved every subgoal on its first attempt, so it counts at k = 1. It is the only problem that differs
    between SR_C5(1) = 15/30 and SR_C4 = 14/30. SR_C5(3) = SR_C5 = 24/30, as expected.
22. **SR(0).** Δ̂(1) = SR(1) − SR(0) needs SR(0), which the plan does not define. The same rule with k = 0 is
    used: the accepted plan has no LLM-attempted subgoal (pre-pass or automatic closing only). This gives 9/30.
23. **Independence curve.** The plan says p_i is "estimated from first-attempt subgoal success in C5". Each
    subgoal has one first attempt, so a per-subgoal p_i would be 0 or 1. A pooled p̂ = 18/44 is used instead,
    counting every LLM-attempted subgoal of every plan (including abandoned plans and failed problems). The curve
    reported is Eq. 14's per-subgoal marginal gain Δ(k) = p̂(1 − p̂)^(k−1). It sits at a different level
    (subgoal) from the problem-level Δ̂(k). The last two columns of Table 6 give the subgoal-level empirical
    counterpart and are supplementary. The figure the plan asks for (§7.5) is
    `docs/results/figures/srk_curve.pdf` (and `.png`), drawn from the same numbers: (a) SR_C5(k), k = 0..3, with
    Wilson CIs and C4 at k = 1; (b) Δ̂(k) against p̂(1 − p̂)^(k−1).

### Error taxonomy (§4.5)

24. For C3–C5 the classes are the rows' `error_classes`, one per Critic event (checked equal to the
    `critique` events), including the k_max escalation to `planning`. Only kept rows count; the discarded
    attempts of the merges are excluded.
25. **C1** attempts are classified offline with `policy.classify(Trace(reason, messages, None))`, using the
    stored `lean_errors` (at most 3 messages of 300 characters, versus 5 × 1500 in the graph). The statement end
    is unknown, so `misformalization` cannot fire, which fits since C1 never writes statements. `escalate` is
    not applied because C1 has no k_max or strategies. The 44 `sanitize:empty` attempts (classed as `syntax`)
    happen alongside 52 C1 responses that ended at `max_tokens`.
26. **C2** has no verified attempts of its own. Its taxonomy row is the claim-check loop (labelled). The plan
    does not say how C2 enters §4.5.
27. Final failure reasons group stages by prefix (`verify:sg2` becomes `verify`).

### Other data facts

28. Responses that ended at `max_tokens` (`finish_reason = length`): C1 52 (9 rows), C3 18 (6 rows),
    C5 14 (5 rows), C4 2 (2 rows), C2 0 (12 in the claim check).
29. C1 `budget_exhausted` rows (7) reached b_max = 18 calls after 9–17 recorded attempts, because JSON-repair
    calls count against the budget. The 3 `failed` C1 rows used all 18 attempts.
30. **Fidelity (§6).** Four solved test problems carry a fidelity note: `amc12b_2003_p6`, `mathd_algebra_234`,
    `mathd_algebra_455` and `putnam_1965_b2`. The team reviewed them on 2026-11-07 (`post_test_review` in
    `data/curation/fidelity_review.yaml`; Table 10): all four count as solved, `amc12b_2003_p6` with a note in the
    report. The verified SR is reported unchanged, as §6 requires.
31. **Supplementary, not in the plan:** Table 8 (pre-pass vs LLM `solved_by`) and Table 4d.

### Illustrative traces (for the appendix)

- `amc12a_2008_p15` (Critic loop). C5 solved it on the third attempt at its only subgoal (two `tactic_failure`
  retries with Critic feedback, 16 calls, first C5 run). C4 abandoned after the first failure. C3 failed after a
  replan (30 calls). C1 failed. The C2 claim was not verified.
- `numbertheory_sumkmulnckeqnmul2pownm1` (RAG). C5 solved it with 5 calls on the first attempt. C3 failed after
  4 `missing_lemma` and 2 `tactic_failure` attempts over two plans (30 calls).
- `imo_1965_p2` (FPR_B as an upper bound). C2 claimed it, and its check failed (`missing_lemma`,
  `tactic_failure`, `missing_lemma`). C5 found a kernel-accepted proof: the pre-pass closed one subgoal and the
  LLM closed the other two (one retry after `missing_lemma`).
