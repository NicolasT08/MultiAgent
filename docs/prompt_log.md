# Prompt log

One entry per prompt change (skill `prompt-tuning`): date, file, what changed, why, and the
measured effect on the **dev** split (SR and LLM calls before → after). Prompts are never
tuned on `test`.

| Date | File | Change | Why | Dev SR before → after | Calls before → after |
|---|---|---|---|---|---|
| 2026-10-26 | `baseline_a.md` | Created (C1: tactic block only, JSON `{"tactics"}`) | Phase 3 | — (first version) | — |
| 2026-10-26 | `baseline_b_solve.md` | Created (C2: NL proof, JSON `{"solution","final_answer"}`) | Phase 3 | — | — |
| 2026-10-26 | `baseline_b_assess.md` | Created (C2 self-assessment, JSON `{"verdict","confidence","issues"}`) | Phase 3 | — | — |
| 2026-10-26 | `json_repair.md` | Created (one repair call for unparsable JSON) | Phase 3 | — | — |
| 2026-10-28 | `planner.md` | Created (Planner: ≤ n_max one-line Lean subgoals + closing tactic block + rationale, JSON `{"subgoals","closing","rationale"}`; hypothesis names h1… announced by the code; one worked example) | Phase 4 | — (first version; dev preview on `data/dev_preview.jsonl`) | — |
| 2026-10-28 | `generator.md` | Created (Generator: one NL sketch of one subgoal with a strategy label from `contracts.STRATEGIES`, JSON `{"strategy","sketch","lemmas"}`; called N times per subgoal) | Phase 4 | — | — |
| 2026-10-29 | `planner.md` | Subgoal rule reworded: each subgoal is *only* a proposition (examples `x = 3`, `∃ k, n = 2 * k + 1`), never a proof — no `:=`, `by`, tactics, `have` or name. New closing rule: the closing block must not mention the theorem's own name | Dev run 2026-10-28 (`runs/20261028T223416Z_PG_dev`): `induction_divisibility_3div2tooddnp1` put a proof with `:=` inside a subgoal (`sanitize:escape`) and its closing cited the theorem itself | PG preview (no proofs yet). `sanitize:escape` in subgoals 1/5 → 0/5; LLM plans accepted 2/5 → 2/5 | 26 → 23 (`runs/20261029T201541Z_PG_dev`) |
| 2026-10-29 | `autoformalizer.md` | Created (Autoformalizer, `chat` style: subgoal lemma built by `statement.subgoal_statement` + winning sketch + Retriever lemmas + the cheat sheet → JSON `{"tactics"}` (`TacticProof`); forbids search tactics and `stop` besides `sorry`/`native_decide`) | Phase 5 | dev, `--config LIN`, flash-lite: `smoke.jsonl` with `--no-prepass` 5/5 solved (`runs/20261029T214106Z_LIN_dev_noprepass`); `dev_preview.jsonl` 1/5 (`runs/20261029T215007Z_LIN_dev`) | — (first version); smoke 21 calls, dev_preview 28 |
| 2026-10-29 | `autoformalizer_prover.md` | Created (`prover` style for whole-proof models such as Goedel-Prover-V2: their own "Complete the following Lean 4 code" prompt, the sketch and lemma names as a Lean comment above the lemma; no JSON, the code keeps only the tactics after `:= by` of the lemma) | Phase 5; Goedel is not deployed | — (tested with the fake LLM only) | — |
| 2026-10-29 | `lean4_cheatsheet.md` | Created (reference inserted into `autoformalizer.md`: Lean 4 syntax, closing tactics with examples, case analysis, the `<;>` / `No goals to be solved` case of `amc12b_2004_p3` with the three variants that compile, induction, `have`, divisibility, inequalities) | Phase 5 | (part of the `autoformalizer.md` row) | — |
| 2026-10-31 | `generator.md`, `autoformalizer.md`, `autoformalizer_prover.md`, `planner.md` | `{{feedback}}` slot before the last instruction, empty on a first attempt (those prompts stay byte-identical, except the prover's sketch comment layout, unchanged when empty) | Phase 6: Eq. 8 and 10 condition on the Critic's feedback | no change on first attempts | no change on first attempts |
| 2026-10-31 | `critic.md`, `feedback_retry.md`, `feedback_replan.md`, `fragments.md` | Created (optional LLM advice; deterministic feedback; short prompt texts moved out of the code, same wording) | Phase 6, invariant review | see Phase 6 runs in PLAN.md | see Phase 6 runs in PLAN.md |
| 2026-11-03 | `lean/syntax_check.py` (code, not prompt; Round 1) | Whitelist gains `Lean.Parser.Tactic.classical`, `Mathlib.Linter.HaveILetI.tacticLetI__` and the four `Mathlib.Meta.SetNotationForOrder.{subsetStx',ssubsetStx',supsetStx',ssupsetStx'}` (`⊆ ⊂ ⊇ ⊃` in this Mathlib) | C5 dev baseline (`runs/20261103T130158Z_C5_experiment`): 7 of 18 syntax failures were legitimate Lean rejected by the whitelist (classical ×4, letI ×1, ⊆ ×2) | `sanitize:syntax` 7 → 0, nodes rejected 7 → 0 (`runs/20261103T151038Z_C5_experiment_r1`); new `sanitize:parse` 3 (real parse errors by the model) | see next row (same run) |
| 2026-11-03 | `autoformalizer.md` (Round 1) | Three rules: keep reasoning short (Lean checks the block); split long proofs into `have` steps closed by one tactic; always return a best-attempt tactic block | Same baseline: 9 Autoformalizer answers cut at `max_tokens` 32768 (all in the 4 hard failures, ~$0.18) → 10 `sanitize:empty` | SR 11/15 → 11/15 (gained imo_1987_p4, putnam_1965_a4; lost mathd_numbertheory_353 (budget), induction_divisibility_3div2tooddnp1). `length` 9 → 5, `sanitize:empty` 10 → 4. Cost real $0.509 → $0.414, nominal $0.563 → $0.521 | 225 → 264 |
| 2026-10-31 | `lean4_cheatsheet.md` | Two sections: implicit type arguments (`dvd_mul_right _ _`, `pow_le_pow_right`) and `omega` with truncated subtraction | The two failures of the Phase 5 calibration (`runs/20261029T234139Z_LIN_experiment`) | see Phase 6 runs in PLAN.md | see Phase 6 runs in PLAN.md |

Lean checked with lean-lsp for the 2026-10-29 Phase 5 prompts (`lean_run_code`, Lean v4.36.0-rc1 + Mathlib,
miniF2F header): every example of `lean4_cheatsheet.md` compiles without errors. A first induction example
(`∑ i ∈ Finset.range (n + 1), i = n * (n + 1) / 2` with `omega`) failed and was replaced by
`∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2` and `3 ∣ 4 ^ n + 2`, both checked. Lemma names quoted:
`sq_nonneg`, `Nat.even_or_odd`, `Finset.sum_range_succ`, `pow_succ`, `Int.ModEq`, `Real.sqrt_sq`, `abs_lt`
(all used in the compiled examples). The three `amc12b_2004_p3` variants were also accepted in the REPL.
The first `dev_preview` run (`runs/20261029T214442Z_LIN_dev`) showed that the syntax whitelist lacked
parenthesised tactics (`Lean.Parser.Tactic.paren`), so the cheat sheet's own `(try norm_num at h₀)` variant
was rejected for `aime_1991_p1`; the whitelist was fixed (code, not prompt) and the run repeated.

Note on the 2026-10-29 entry: the same dev run also introduced the Planner's refutation check, automatic
closing and direct plans (code, not prompt), so the accepted-plan count mixes both changes; the prompt's own
effect is read from the fallback reasons: no subgoal contained `:=` and no closing cited the theorem
(`induction_divisibility…` now proposes a plain equation and fails later, at its closing step). Flash-Lite's
plans also vary between runs (aime: 10/61 on 10-28, 88/17 on 10-29, both refuted or wrong), so 5 problems
are a sanity check, not a measurement. No Lean names were added to the prompt.

Lean checked with lean-lsp (2026-10-28, temporary file in `lean_project/.mas_tmp/`, deleted): the example plan
of `planner.md` (`a = 3`, `b = 2`, closing `rw [h1, h2]` / `norm_num`) compiles as a skeleton with
`sorry`, and with `linarith` for both subgoals the proof depends only on
`[propext, Classical.choice, Quot.sound]`; its closing step as the standalone lemma `example_plan_close` (what `statement.closing_lemma` builds) is accepted with the same axioms. Tactic names quoted in the prompts (`linarith`,
`nlinarith`, `omega`, `norm_num`, `rw`, `simp`, `exact`) are core/Mathlib tactics; no lemma
names are quoted.

Lean checked for the 2026-10-31 cheat sheet sections in the REPL with `accepted` (lean-lsp did not connect in
that session; miniF2F header): every right variant accepted, every wrong one rejected with the quoted message.
Names: `dvd_mul_right`, `dvd_mul_left`, `Nat.pow_le_pow_right`, `pow_le_pow_right₀`, `pow_succ`.

Round 1 (2026-11-03): every tactic added to the whitelist was checked with lean-lsp (`lean_run_code`,
miniF2F header): `classical` with `Finset.filter`, `letI`, `haveI`, and `⊆ ⊂ ⊇ ⊃` all compile with only the
standard axioms; the one `sorry` in the baseline was correctly rejected. The prompt rules quote no Lean names.
Both rounds sample fresh answers (the Autoformalizer prompt changed, so its cache entries changed), so the two
regressions on easy/medium problems are run-to-run variance of a 15-problem split, not a measured effect:
`mathd_numbertheory_353` hit `decide` recursion/timeout and then the budget; `induction_divisibility…` failed
the same induction step 5 times. The per-change effects (syntax and `length` counts) are the measurement.

**Tuning closed after Round 1 (team decision, 2026-11-03).** The candidate Round 2 changes were written for
two dev problems (overfitting), and on 15 problems their effect could not be told apart from run-to-run
variance (Round 1 gained 2 and lost 2). Future work, not applied:
- cheat sheet: avoid `decide` on large numbers (use `norm_num` or closed-form sum lemmas), from the `decide`
  recursion timeouts of `mathd_numbertheory_353`;
- cheat sheet: a worked induction step for divisibility of powers (`pow_succ` with `omega` / `Nat.ModEq`),
  from `induction_divisibility_3div2tooddnp1`.
The frozen configuration is the Round 1 one.
