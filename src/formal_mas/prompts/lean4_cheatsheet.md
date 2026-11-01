<!-- Lean 4 / Mathlib cheat sheet for the Autoformalizer (inserted into prompts/autoformalizer.md
     by prompting.document). Every Lean example below was compiled with the lean-lsp MCP on
     2026-10-29 (`lean_run_code`, Lean v4.36.0-rc1 + Mathlib v4.36.0-rc1, miniF2F header): no
     errors. The amc12b_2004_p3 variants were also checked in the REPL with `accepted` (PLAN.md,
     Phase 5). The two sections from the calibration (implicit arguments, `omega` with truncated
     subtraction) were compiled in the REPL with `accepted` on 2026-10-31, lean-lsp not connecting;
     their wrong variants fail with the messages quoted. Change an example only after compiling it
     again (skill prompt-tuning). -->
Syntax (Lean 4, never Lean 3):
- `fun x => …` (not `λ x, …`); `intro x` (not `assume`); `obtain ⟨x, hx⟩ := h` or `rcases h with ⟨x, hx⟩` (not `cases h with x hx`); `·` to focus a goal; `Nat.succ_le_iff` (namespaces are capitalized).
- Hypotheses of the lemma are used by name: `linarith [h₁]`, `rw [h1] at h₂`, `exact h₀.1`.

Closing tactics and when they work:
- `linarith`: linear (in)equalities over ordered fields, ℤ, ℕ: `example (x : ℝ) (h₀ : 2 * x + 3 = 7) : x = 2 := by linarith`
- `nlinarith [hints]`: polynomial inequalities; give squares as hints: `example (a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by nlinarith [sq_nonneg (a - b)]`
- `positivity`: goals `0 < e`, `0 ≤ e`, `e ≠ 0`: `example (x y : ℝ) (hx : 0 < x) (hy : 0 < y) : 0 < x * y + x ^ 2 := by positivity`
- `norm_num`: numeric computation: `example : (2 : ℝ) ^ 10 / 4 = 256 := by norm_num`
- `omega`: linear arithmetic over ℕ and ℤ with `%`, `/` by numerals and truncated `-`: `example (n : ℕ) (h₀ : n % 4 = 3) : n % 2 = 1 := by omega`, `example (a b : ℕ) (h : a ≤ b) : b - a + a = b := by omega`, `example (n : ℕ) (h : 6 ∣ n) : 3 ∣ n := by omega`
- `decide`: small closed statements: `example : (2 ^ 10) % 7 = 2 := by decide`, `example : Nat.choose 5 2 = 10 := by decide`
- `ring`: polynomial identities in commutative rings: `example (a b : ℝ) : (a + b) ^ 2 = a ^ 2 + 2 * a * b + b ^ 2 := by ring`
- `field_simp` then `ring`, for divisions (needs `≠ 0` facts in context):
  ```
  example (x : ℝ) (hx : x ≠ 0) : x / x + 1 = 2 := by
    field_simp
    ring
  ```
- Casts: `exact_mod_cast h` (`example (n : ℕ) (h : (n : ℝ) = 3) : n = 3 := by exact_mod_cast h`); `push_cast`.

Case analysis:
- `rcases h with h | h <;> rw [h] <;> norm_num` (a disjunction), `obtain ⟨k, hk⟩ := h` (an existential, then `omega`).
- Bounded variables: `interval_cases x` once hypotheses bound `x` (`example (x : ℕ) (h₀ : x ≤ 3) (h₁ : x ^ 2 = 4) : x = 2 := by interval_cases x <;> simp_all`).
- Parity: `Nat.even_or_odd n : Even n ∨ Odd n`; `omega` proves `n % 2 = 0 ∨ n % 2 = 1`.

After `<;>`, every goal must be closed by the same tactic (error `No goals to be solved`):
- Wrong: `interval_cases x <;> interval_cases y <;> norm_num at h₀ ⊢`. In the impossible cases `norm_num at h₀` turns `h₀` into `False` and closes the goal, then the `⊢` part finds no goal. `norm_num at h₀` alone leaves `4 + 4 = 8` open in the true case.
- Right (all three compile for `(x y : ℕ) (h₀ : 2 ^ x * 3 ^ y = 1296) (h1 : x ≤ 10) (h2 : y ≤ 6) : x + y = 8`):
  `interval_cases x <;> interval_cases y <;> omega`,
  `interval_cases x <;> interval_cases y <;> simp_all`,
  `interval_cases x <;> interval_cases y <;> (try norm_num at h₀) <;> (try norm_num)`.
- Rule: after `<;>` use a closer that handles every goal (`omega`, `simp_all`) or wrap each step in `try`; do not combine `at h ⊢` when `h` can become `False`.

Lemmas with implicit type arguments: give the arguments, and check the name exists:
- Wrong: `rw [← h₀]` then `exact dvd_mul_right _ _`. With `_` the type is still unknown when the instance is sought, so Lean reports `typeclass instance problem is stuck`.
- Right, naming the arguments: `exact dvd_mul_right (2 ^ x) (3 ^ y)` proves `2 ^ x ∣ 2 ^ x * 3 ^ y`, and `exact dvd_mul_left (3 ^ y) (2 ^ x)` proves `3 ^ y ∣ 2 ^ x * 3 ^ y`.
- `pow_le_pow_right` does not exist here (`Unknown identifier`). In ℕ: `Nat.pow_le_pow_right (by norm_num) hx : 2 ^ 11 ≤ 2 ^ x` for `hx : 11 ≤ x`. In an ordered field: `pow_le_pow_right₀ (by norm_num) hx : (2 : ℝ) ^ 11 ≤ 2 ^ x`.

`omega` and subtraction in ℕ (`a - b` is 0 when `b > a`):
- Wrong: `example (a b : ℕ) : a - b + b = a := by omega` fails without `b ≤ a`; right: `example (a b : ℕ) (h : b ≤ a) : a - b + b = a := by omega`.
- `omega` treats `4 ^ k` as an unknown number, and knows nothing about it, so `4 ^ (k + 1) - 3 + 3 = 4 ^ (k + 1)` fails. Give the fact and expose the atom:
  ```
  example (k : ℕ) : 4 ^ (k + 1) - 3 + 3 = 4 ^ (k + 1) := by
    have h : 0 < 4 ^ k := by positivity
    rw [pow_succ]
    omega
  ```
- Do not rewrite a goal into a subtraction when an induction works without it:
  ```
  example (n : ℕ) : 3 ∣ 2 * 4 ^ n + 1 := by
    induction n with
    | zero => norm_num
    | succ k ih =>
      rw [pow_succ]
      omega
  ```

Induction (Lean 4 syntax):
```
example (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2 := by
  induction n with
  | zero => simp
  | succ k ih =>
    rw [Finset.sum_range_succ, ih]
    ring
```
```
example (n : ℕ) : 3 ∣ 4 ^ n + 2 := by
  induction n with
  | zero => norm_num
  | succ k ih =>
    rw [pow_succ]
    omega
```
Sums use `∑ i ∈ Finset.range n, f i` (the old `∑ i in s` is a syntax error).

Intermediate steps with `have`:
```
example (x y : ℝ) (h₀ : x + y = 10) (h₁ : x - y = 2) : x * y = 24 := by
  have hx : x = 6 := by linarith
  have hy : y = 4 := by linarith
  rw [hx, hy]
  norm_num
```

Divisibility and congruences: a witness `exact ⟨k + 2, by ring⟩` proves `3 ∣ 3 * k + 6`; for `h : a ≡ b [ZMOD 5]`, `rw [Int.ModEq] at h` then `omega` gives `(a - b) % 5 = 0`.

Inequalities: `nlinarith [sq_nonneg (a - b), sq_nonneg (b - c), sq_nonneg (c - a)]` proves `a * b + b * c + c * a ≤ a ^ 2 + b ^ 2 + c ^ 2`; `Real.sqrt_sq hx : Real.sqrt (x ^ 2) = x` for `hx : 0 ≤ x`; `rw [abs_lt] at h` turns `|x - 1| < 2` into `-2 < x - 1 ∧ x - 1 < 2`.
