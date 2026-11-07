# FPR_B manual review (plan §4.2)

**Reviewer:** Andrés Mongui · **Prepared:** 2026-11-07 from `runs/20261104T232056Z_C2_experiment_check/results.jsonl` (read-only) · **Verdicts:** pending

## What to decide

Baseline B (C2) solved each problem in natural language and then judged its own proof; it claimed
all 30 `test` problems. Each claim was handed to the Autoformalizer–Verifier loop (`--check`: up to
3 attempts, same lemma search as C5, its own budget). For 11 claims no attempt was accepted by Lean,
so FPR_B = 11/30 = 36.7 % is an **upper bound**: an unconfirmed claim may be a wrong argument, or a
correct argument that the pipeline failed to formalise.

The plan asks for a manual review of 10 of them. The 10 were drawn with
`random.Random(2026).sample(sorted ids, 10)` (left out: `amc12a_2008_p15`).
For each problem below, read C2's informal solution against the problem and mark **one** verdict:

- **Wrong argument**: the informal solution has a mathematical error or gap that matters (wrong
  answer, missing case, unjustified key step).
- **Formalisation failure**: the informal argument is correct (complete enough for a competition
  grader); the Lean attempts failed for other reasons.
- **Cannot decide**: not enough information, or the argument is too sketchy to judge either way.

Please base the verdict on the informal solution. The Lean errors and the line "proved by other
configurations" are context only: a true statement can still have a wrong proof, and a Lean error
is often about syntax or tactics rather than the mathematics.

Attempt reasons: `lean:error` = Lean compiled the attempt and reported errors; `sanitize:empty` = the Autoformalizer returned no tactics (usually its output was cut at max_tokens);
`sanitize:parse` = Lean's parser rejected the text before compiling.

## Summary (fill in)

| # | Problem | Difficulty | Verdict | Short reason |
|---|---|---|---|---|
| 1 | `amc12b_2002_p3` | medium | | |
| 2 | `imo_1964_p1_2` | hard | | |
| 3 | `imo_1965_p2` | hard | | |
| 4 | `imo_1978_p5` | hard | | |
| 5 | `imo_1979_p1` | hard | | |
| 6 | `imo_1984_p6` | hard | | |
| 7 | `imo_1987_p6` | hard | | |
| 8 | `imo_1993_p5` | hard | | |
| 9 | `putnam_1965_b2` | hard | | |
| 10 | `putnam_1995_b1` | hard | | |

Totals: wrong argument __ · formalisation failure __ · cannot decide __

---

## 1. `amc12b_2002_p3` (number_theory, medium)

### Problem (informal)

For how many positive integers $n$ is $n^2 - 3n + 2$ a [[prime]] number?

$\mathrm{(A)}\ \text{none}
\qquad\mathrm{(B)}\ \text{one}
\qquad\mathrm{(C)}\ \text{two}
\qquad\mathrm{(D)}\ \text{more\ than\ two,\ but\ finitely\ many}
\qquad\mathrm{(E)}\ \text{infinitely\ many}$ Show that it is \mathrm{(B)}\ \text{one}.

### Formal statement (what Lean had to prove)

```lean
theorem amc12b_2002_p3 (S : Finset ℕ)
  (h₀ : ∀ n : ℕ, n ∈ S ↔ 0 < n ∧ Nat.Prime (n ^ 2 + 2 - 3 * n)) :
  S.card = 1 := by
```

### C2's solution (natural language)

Let P(n)=n^2-3n+2. Factor:
\[P(n)=n^2-3n+2=(n-1)(n-2).\]
For positive integers n, check the possible small cases directly:
- If n=1, then P(1)=0, not prime.
- If n=2, then P(2)=0, not prime.
- If n=3, then P(3)=3^2-3\cdot 3+2=2, which is prime.

Now suppose n\ge 4. Then n-2\ge 2 and n-1\ge 3, so both factors in \[(n-1)(n-2)\] are integers greater than 1. Hence P(n) is a product of two integers greater than 1, so it is composite, not prime.

Thus the only positive integer n for which n^2-3n+2 is prime is n=3. Therefore exactly one such positive integer exists, so the answer is (B) one.

**Final answer:** (B) one; the unique value is \(n=3\).

**C2's self-assessment:** verdict `correct`, confidence 1.0; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `tactic_failure`)

```text
line 23: Type mismatch
  hn3
has type
  n = 3
but is expected to have type
  n ∈ {3}

line 32: Application type mismatch: The argument
  hdvd
has type
  m ∣ m * (m + 1)
of sort `Prop` but is expected to have type
  ℕ
of sort `Type` in the application
  Prime.eq_one_or_self_of_dvd hprime hdvd
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
rw [Finset.card_eq_one]
use 3
ext n
rw [h₀ n]
constructor
· rintro ⟨hpos, hprime⟩
  have h12 : n = 1 ∨ n = 2 ∨ 3 ≤ n := by omega
  rcases h12 with rfl | rfl | hge
  · have h2 := hprime.two_le
    norm_num at h2
  · have h2 := hprime.two_le
    norm_num at h2
  · by_cases hn3 : n = 3
    · exact hn3
    · have hn4 : 4 ≤ n := by omega
      obtain ⟨m, rfl⟩ : ∃ m, n = m + 2 := ⟨n - 2, by omega⟩
      have hm2 : 2 ≤ m := by omega
      have h_eq : (m + 2) ^ 2 + 2 - 3 * (m + 2) = m * (m + 1) := by
        apply Nat.sub_eq_of_eq_add
        ring
      rw [h_eq] at hprime
      have hdvd : m ∣ m * (m + 1) := dvd_mul_right m (m + 1)
      have hcases := hprime.eq_one_or_self_of_dvd hdvd
      rcases hcases with h1 | h2
      · omega
      · nlinarith
· intro hn
  simp only [Finset.mem_singleton] at hn
  rw [hn]
  norm_num
```

</details>

**Attempt 2:** `lean:error` (class `tactic_failure`)

```text
line 17: unsolved goals
case «1»
S : Finset ℕ
h₀ : ∀ (n : ℕ), n ∈ S ↔ 0 < n ∧ Nat.Prime (n ^ 2 + 2 - 3 * n)
n : ℕ
hpos : 0 < 1
hprime : Nat.Prime (1 ^ 2 + 2 - 3 * 1)
hlt : ¬3 ≤ 1
hle : 1 ≤ 2
⊢ False

case «2»
S : Finset ℕ
h₀ : ∀ (n : ℕ), n ∈ S ↔ 0 < n ∧ Nat.Prime (n ^ 2 + 2 - 3 * n)
n : ℕ
hpos : 0 < 2
hprime
```

<details><summary>Lean tactics tried in attempt 2</summary>

```lean
rw [Finset.card_eq_one]
use 3
ext n
rw [h₀ n]
simp only [Finset.mem_singleton]
constructor
· rintro ⟨hpos, hprime⟩
  have hge3 : 3 ≤ n := by
    by_contra hlt
    have hle : n ≤ 2 := by omega
    interval_cases n <;> try omega <;> norm_num at hprime
  by_cases hn3 : n = 3
  · exact hn3
  · have hn4 : 4 ≤ n := by omega
    obtain ⟨m, hm⟩ : ∃ m, n = m + 4 := ⟨n - 4, by omega⟩
    rw [hm] at hprime ⊢
    have h_eq : (m + 4) ^ 2 + 2 - 3 * (m + 4) = (m + 2) * (m + 3) := by
      apply Nat.sub_eq_of_eq_add
      ring
    rw [h_eq] at hprime
    have hdvd : (m + 2) ∣ (m + 2) * (m + 3) := dvd_mul_right (m + 2) (m + 3)
    have hcases := hprime.eq_one_or_self_of_dvd (m + 2) hdvd
    rcases hcases with h1 | h1
    · omega
    · have hm2 : 0 < m + 2 := by omega
      nlinarith [mul_pos hm2 hm2]
· intro hn
  rw [hn]
  norm_num
```

</details>

**Attempt 3:** `lean:error` (class `tactic_failure`)

```text
line 41: Application type mismatch: The argument
  fun a => amc12b_2002_p3_sg1._proof_6 n hn3 a
has type
  n - 1 ≠ 0
but is expected to have type
  0 < n - 1
in the application
  Nat.eq_of_mul_eq_mul_left fun a => amc12b_2002_p3_sg1._proof_6 n hn3 a
```

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
rw [Finset.card_eq_one]
use 3
ext n
rw [h₀ n]
simp only [Finset.mem_singleton]
constructor
· rintro ⟨hpos, hprime⟩
  have hn3 : 3 ≤ n := by
    by_contra hlt
    have hle : n ≤ 2 := by omega
    have hn : n = 1 ∨ n = 2 := by omega
    rcases hn with rfl | rfl
    · have h1 : 1 ^ 2 + 2 - 3 * 1 = 0 := by norm_num
      rw [h1] at hprime
      exact Nat.not_prime_zero hprime
    · have h2 : 2 ^ 2 + 2 - 3 * 2 = 0 := by norm_num
      rw [h2] at hprime
      exact Nat.not_prime_zero hprime
  have h1 : 1 ≤ n := by omega
  have h2 : 2 ≤ n := by omega
  have h_eq : n ^ 2 + 2 - 3 * n = (n - 1) * (n - 2) := by
    apply Nat.sub_eq_of_eq_add
    nlinarith [Nat.sub_add_cancel h1, Nat.sub_add_cancel h2]
  rw [h_eq] at hprime
  have hdvd : (n - 1) ∣ (n - 1) * (n - 2) := dvd_mul_right (n - 1) (n - 2)
  have hcases := hprime.eq_one_or_self_of_dvd (n - 1) hdvd
  rcases hcases with h1' | h1'
  · omega
  · have hmul : (n - 1) * 1 = (n - 1) * (n - 2) := by
      rw [Nat.mul_one]
      exact h1'
    have h21 : 1 = n - 2 := Nat.eq_of_mul_eq_mul_left (by omega : n - 1 ≠ 0) hmul
    omega
· intro hn
  rw [hn]
  norm_num
```

</details>

**Context:** formal statement proved by other configurations: C3, C4, C5.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 2. `imo_1964_p1_2` (number_theory, hard)

### Problem (informal)

Show that for any natural number $n$, $7$ does not divide $2^n + 1$.

### Formal statement (what Lean had to prove)

```lean
theorem imo_1964_p1_2 (n : ℕ) : ¬7 ∣ 2 ^ n + 1 := by
```

### C2's solution (natural language)

Work modulo 7. We compute the powers of 2 modulo 7: 2^0 ≡ 1, 2^1 ≡ 2, 2^2 ≡ 4, 2^3 ≡ 8 ≡ 1. Thus the residues of 2^n modulo 7 repeat with period 3, and are always one of 1, 2, or 4. Hence 2^n + 1 is congruent modulo 7 to one of 1+1=2, 2+1=3, or 4+1=5. None of these is congruent to 0 modulo 7. Therefore 7 can never divide 2^n+1 for any natural number n.

**Final answer:** For every natural number n, 7 does not divide 2^n+1.

**C2's self-assessment:** verdict `correct`, confidence 1.0; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `missing_lemma`)

```text
line 13: Unknown constant `ZMod.natCast_zmod_eq_zero_iff_dvd`

line 16: unsolved goals
n : ℕ
h : 7 ∣ 2 ^ n + 1
hz : 2 ^ n + 1 = 0
⊢ 8 = 1

line 22: unsolved goals
n : ℕ
h : 7 ∣ 2 ^ n + 1
hz : 2 ^ n + 1 = 0
h3 : 2 ^ 3 = 1
hn : n = 3 * (n / 3) + n % 3
⊢ 2 ^ (3 * (n / 3) + n % 3) = 2 ^ (3 * ((3 * (n / 3) + n % 3) / 3) + (3 * (n / 3) + n % 3) % 3)
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
intro h
have hz : (2 : ZMod 7) ^ n + 1 = 0 := by
  have h0 : ((2 ^ n + 1 : ℕ) : ZMod 7) = 0 :=
    (ZMod.natCast_zmod_eq_zero_iff_dvd (2 ^ n + 1)).2 h
  push_cast at h0
  exact h0
have h3 : (2 : ZMod 7) ^ 3 = 1 := by norm_num
have hpow : (2 : ZMod 7) ^ n = (2 : ZMod 7) ^ (n % 3) := by
  have hn : n = 3 * (n / 3) + n % 3 := by
    have hdiv := Nat.div_add_mod n 3
    omega
  calc
    (2 : ZMod 7) ^ n = (2 : ZMod 7) ^ (3 * (n / 3) + n % 3) := by rw [hn]
    _ = (2 : ZMod 7) ^ (3 * (n / 3)) * (2 : ZMod 7) ^ (n % 3) := by rw [pow_add]
    _ = ((2 : ZMod 7) ^ 3) ^ (n / 3) * (2 : ZMod 7) ^ (n % 3) := by rw [pow_mul]
    _ = 1 ^ (n / 3) * (2 : ZMod 7) ^ (n % 3) := by rw [h3]
    _ = (2 : ZMod 7) ^ (n % 3) := by simp
rw [hpow] at hz
set k := n % 3 with hk
have hklt : k < 3 := by
  have hlt : n % 3 < 3 := Nat.mod_lt n (by norm_num)
  omega
interval_cases k <;> norm_num at hz
```

</details>

**Attempt 2:** `lean:error` (class `tactic_failure`)

```text
line 37: Tactic `rewrite` failed: Did not find an occurrence of the pattern
  ↑?a = 0
in the target expression
  ↑2 ≠ 0

n : ℕ
h : 7 ∣ 2 ^ n + 1
h0 : 2 ^ (n % 3) + 1 = 0
h3 : 2 ^ 3 = 1
hpow : 2 ^ n = 2 ^ (n % 3)
⊢ ↑2 ≠ 0

line 40: Tactic `rewrite` failed: Did not find an occurrence of the pattern
  ↑?a = 0
in the target expression
  ↑3 ≠ 0

n : ℕ
h : 7 ∣ 2 ^ n + 1
h0 : 2 ^ (n % 3) + 1 = 0
h3 : 2 ^ 3 = 1
hpow : 2 ^ n = 2 ^ (n % 3)
h2 : ↑2 ≠ 0
⊢ ↑3 ≠ 0

line 43: Tactic `rewrite` failed: Did not find an occurrence of the pattern
  ↑?a = 0
in the target expression
  ↑5 ≠ 0

n : ℕ
h : 7 ∣ 2 ^ n + 1
h0 : 2 ^ (n % 3) + 1 = 0
h3 : 2 ^ 3 = 1
hpow : 2 ^ n = 2 ^ (n % 3)
h2 : ↑2 ≠ 0
h3n : ↑3 ≠ 0
⊢ ↑5 ≠ 0
```

<details><summary>Lean tactics tried in attempt 2</summary>

```lean
intro h
have h0 : ((2 ^ n + 1 : ℕ) : ZMod 7) = 0 := by
  have hz : (((2 ^ n + 1 : ℕ) : ℤ) : ZMod 7) = 0 := by
    rw [ZMod.intCast_zmod_eq_zero_iff_dvd]
    exact_mod_cast h
  simpa using hz
push_cast at h0
have h3 : (2 : ZMod 7) ^ 3 = 1 := by
  have h8 : ((8 : ℤ) : ZMod 7) = ((1 : ℤ) : ZMod 7) := by
    rw [ZMod.intCast_eq_intCast_iff_dvd_sub]
    norm_num
  calc
    (2 : ZMod 7) ^ 3 = ((8 : ℤ) : ZMod 7) := by
      push_cast
      ring
    _ = ((1 : ℤ) : ZMod 7) := h8
    _ = 1 := by
      push_cast
      rfl
have hpow : (2 : ZMod 7) ^ n = (2 : ZMod 7) ^ (n % 3) := by
  have hn : n = 3 * (n / 3) + n % 3 := by
    have := Nat.div_add_mod n 3
    omega
  conv_lhs => rw [hn]
  rw [pow_add, pow_mul, h3, one_pow, one_mul]
rw [hpow] at h0
have h2 : ((2 : ℕ) : ZMod 7) ≠ 0 := by
  rw [ZMod.natCast_eq_zero_iff]
  norm_num
have h3n : ((3 : ℕ) : ZMod 7) ≠ 0 := by
  rw [ZMod.natCast_eq_zero_iff]
  norm_num
have h5n : ((5 : ℕ) : ZMod 7) ≠ 0 := by
  rw [ZMod.natCast_eq_zero_iff]
  norm_num
have hcases : n % 3 = 0 ∨ n % 3 = 1 ∨ n % 3 = 2 := by
  have hlt : n % 3 < 3 := Nat.mod_lt n (by norm_num)
  omega
rcases hcases with hc | hc | hc
· rw [hc] at h0
  norm_num at h0
  exact h2 (by simpa using h0)
· rw [hc] at h0
  norm_num at h0
  exact h3n (by simpa using h0)
· rw [hc] at h0
  norm_num at h0
  exact h5n (by simpa using h0)
```

</details>

**Attempt 3:** `lean:error` (class `tactic_failure`)

```text
line 11: Invalid projection: Projections cannot be used on functions, and
  ZMod.natCast_eq_zero_iff
has function type `∀ (a b : ℕ), ↑a = 0 ↔ b ∣ a`

line 13: unsolved goals
n : ℕ
h : 7 ∣ 2 ^ n + 1
h0 : 2 ^ n + 1 = 0
⊢ 8 = 1

line 23: unsolved goals
n : ℕ
h : 7 ∣ 2 ^ n + 1
h0 : 2 ^ 0 + 1 = 0
h3 : 2 ^ 3 = 1
hpow : 2 ^ n = 2 ^ (n % 3)
hc : n % 3 = 0
⊢ ¬2 = 0
```

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
intro h
have h0 : ((2 ^ n + 1 : ℕ) : ZMod 7) = 0 := (ZMod.natCast_eq_zero_iff).2 h
push_cast at h0
have h3 : (2 : ZMod 7) ^ 3 = 1 := by norm_num
have hpow : (2 : ZMod 7) ^ n = (2 : ZMod 7) ^ (n % 3) := by
  conv_lhs => rw [← Nat.div_add_mod n 3]
  rw [pow_add, pow_mul, h3, one_pow, one_mul]
rw [hpow] at h0
have hcases : n % 3 = 0 ∨ n % 3 = 1 ∨ n % 3 = 2 := by
  have hlt : n % 3 < 3 := Nat.mod_lt _ (by norm_num)
  omega
rcases hcases with hc | hc | hc
· rw [hc] at h0
  exact absurd h0 (by norm_num)
· rw [hc] at h0
  exact absurd h0 (by norm_num)
· rw [hc] at h0
  exact absurd h0 (by norm_num)
```

</details>

**Context:** formal statement proved by other configurations: C1, C3, C5.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 3. `imo_1965_p2` (algebra, hard)

### Problem (informal)

Consider the system of equations
$a_{11}x_1 + a_{12}x_2 + a_{13}x_3 = 0$
$a_{21}x_1 + a_{22}x_2 + a_{23}x_3 = 0$
$a_{31}x_1 + a_{32}x_2 + a_{33}x_3 = 0$
with unknowns $x_1$, $x_2$, $x_3$. The coefficients satisfy the conditions:

(a) $a_{11}$, $a_{22}$, $a_{33}$ are positive numbers;

(b) the remaining coefficients are negative numbers;

(c) in each equation, the sum of the coefficients is positive.

Prove that the given system has only the solution $x_1 = x_2 = x_3 = 0$.

### Formal statement (what Lean had to prove)

```lean
theorem imo_1965_p2 (x y z : ℝ) (a : ℕ → ℝ) (h₀ : 0 < a 0 ∧ 0 < a 4 ∧ 0 < a 8)
    (h₁ : a 1 < 0 ∧ a 2 < 0) (h₂ : a 3 < 0 ∧ a 5 < 0) (h₃ : a 6 < 0 ∧ a 7 < 0)
    (h₄ : 0 < a 0 + a 1 + a 2) (h₅ : 0 < a 3 + a 4 + a 5) (h₆ : 0 < a 6 + a 7 + a 8)
    (h₇ : a 0 * x + a 1 * y + a 2 * z = 0) (h₈ : a 3 * x + a 4 * y + a 5 * z = 0)
    (h₉ : a 6 * x + a 7 * y + a 8 * z = 0) : x = 0 ∧ y = 0 ∧ z = 0 := by
```

### C2's solution (natural language)

Suppose, for contradiction, that the system has a nonzero solution \((x_1,x_2,x_3)\). Let \(M=\max\{|x_1|,|x_2|,|x_3|\}>0\), and choose an index \(k\in\{1,2,3\}\) such that \(|x_k|=M\). Write the \(k\)-th equation as
\[
a_{kk}x_k=-\sum_{j\ne k} a_{kj}x_j.
\]
Taking absolute values gives
\[
a_{kk}|x_k|=\left|\sum_{j\ne k} a_{kj}x_j\right|\le \sum_{j\ne k}|a_{kj}|\,|x_j|.
\]
Because the off-diagonal coefficients are negative, \(|a_{kj}|=-a_{kj}\) for \(j\ne k\). Also, since \(|x_j|\le M=|x_k|\), we obtain
\[
a_{kk}|x_k|\le \left(\sum_{j\ne k}(-a_{kj})\right)|x_k|.
\]
However, condition (c) for the \(k\)-th equation says
\[
a_{k1}+a_{k2}+a_{k3}>0.
\]
Since \(a_{kk}>0\) and the other two coefficients are negative, this is equivalent to
\[
a_{kk}>-\sum_{j\ne k}a_{kj}=\sum_{j\ne k}|a_{kj}|.
\]
Multiplying by \(|x_k|=M>0\) gives
\[
a_{kk}|x_k|> \left(\sum_{j\ne k}|a_{kj}|\right)|x_k|,
\]
contradicting the previous inequality. Therefore no nonzero solution can exist, so necessarily \(x_1=x_2=x_3=0\).

**Final answer:** The system has only the trivial solution \(x_1=x_2=x_3=0\).

**C2's self-assessment:** verdict `correct`, confidence 0.99; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `missing_lemma`)

```text
line 19: Unknown identifier `abs_add`

line 22: No goals to be solved
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
have key : ∀ (p q r u v w : ℝ), 0 < p → q < 0 → r < 0 →
    0 < p + q + r → p * u + q * v + r * w = 0 →
    |v| ≤ |u| → |w| ≤ |u| → u = 0 := by
  intro p q r u v w hp hq hr hsum heq hv hw
  have hM_nonneg : 0 ≤ |u| := abs_nonneg u
  have htri : p * |u| ≤ (-q) * |v| + (-r) * |w| := by
    rw [show p * |u| = |p * u| by rw [abs_mul, abs_of_pos hp]]
    have hpu : p * u = -(q * v + r * w) := by linarith
    rw [hpu, abs_neg]
    calc |q * v + r * w| ≤ |q * v| + |r * w| := abs_add _ _
      _ = (-q) * |v| + (-r) * |w| := by
        rw [abs_mul, abs_mul, abs_of_neg hq, abs_of_neg hr]
        ring
  have hmono : (-q) * |v| + (-r) * |w| ≤ (-q + -r) * |u| := by
    have hq' : 0 ≤ -q := by linarith
    have hr' : 0 ≤ -r := by linarith
    have hv' : (-q) * |v| ≤ (-q) * |u| := mul_le_mul_of_nonneg_left hv hq'
    have hw' : (-r) * |w| ≤ (-r) * |u| := mul_le_mul_of_nonneg_left hw hr'
    linarith
  have hkey : p * |u| ≤ (-q + -r) * |u| := by linarith
  have hlt : (-q + -r) < p := by linarith
  have hMzero : |u| = 0 := by
    have hpos : 0 < p - (-q + -r) := by linarith
    nlinarith [hkey, hpos, hM_nonneg]
  exact abs_eq_zero.mp hMzero
rcases le_total |x| |y| with hxy | hyx
· rcases le_total |y| |z| with hyz | hzy
  · have hxz : |x| ≤ |z| := le_trans hxy hyz
    have hz0 : z = 0 := key (a 8) (a 6) (a 7) z x y h₀.2.2 h₃.1 h₃.2 (by linarith [h₆]) (by linarith [h₉]) hxz hyz
    have hzabs : |z| = 0 := by rw [hz0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxz, hzabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyz, hzabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
  · have hy0 : y = 0 := key (a 4) (a 3) (a 5) y x z h₀.2.1 h₂.1 h₂.2 (by linarith [h₅]) (by linarith [h₈]) hxy hzy
    have hyabs : |y| = 0 := by rw [hy0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxy, hyabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hz0 : z = 0 := by
      have : |z| = 0 := le_antisymm (by linarith [hzy, hyabs]) (abs_nonneg z)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
· rcases le_total |x| |z| with hxz | hzx
  · have hyz : |y| ≤ |z| := le_trans hyx hxz
    have hz0 : z = 0 := key (a 8) (a 6) (a 7) z x y h₀.2.2 h₃.1 h₃.2 (by linarith [h₆]) (by linarith [h₉]) hxz hyz
    have hzabs : |z| = 0 := by rw [hz0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxz, hzabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyz, hzabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
  · have hx0 : x = 0 := key (a 0) (a 1) (a 2) x y z h₀.1 h₁.1 h₁.2 h₄ h₇ hyx hzx
    have hxabs : |x| = 0 := by rw [hx0, abs_zero]
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyx, hxabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    have hz0 : z = 0 := by
      have : |z| = 0 := le_antisymm (by linarith [hzx, hxabs]) (abs_nonneg z)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
```

</details>

**Attempt 2:** `lean:error` (class `tactic_failure`)

```text
line 23: Type mismatch
  abs_add' ?m.573 ?m.574
has type
  |?m.573| ≤ |?m.574| + |?m.574 + ?m.573|
but is expected to have type
  |q * v + r * w| ≤ |q * v| + |r * w|
```

<details><summary>Lean tactics tried in attempt 2</summary>

```lean
have key : ∀ (p q r u v w : ℝ), 0 < p → q < 0 → r < 0 →
    0 < p + q + r → p * u + q * v + r * w = 0 →
    |v| ≤ |u| → |w| ≤ |u| → u = 0 := by
  intro p q r u v w hp hq hr hsum heq hv hw
  have hM_nonneg : 0 ≤ |u| := abs_nonneg u
  have htri : p * |u| ≤ (-q) * |v| + (-r) * |w| := by
    have ha : |p * u| = p * |u| := by rw [abs_mul, abs_of_pos hp]
    have hpu : p * u = -(q * v + r * w) := by linarith
    have h1 : |q * v| = (-q) * |v| := by rw [abs_mul, abs_of_neg hq]
    have h2 : |r * w| = (-r) * |w| := by rw [abs_mul, abs_of_neg hr]
    calc p * |u| = |p * u| := ha.symm
      _ = |-(q * v + r * w)| := by rw [hpu]
      _ = |q * v + r * w| := abs_neg _
      _ ≤ |q * v| + |r * w| := abs_add' _ _
      _ = (-q) * |v| + (-r) * |w| := by rw [h1, h2]
  have hmono : (-q) * |v| + (-r) * |w| ≤ (-q + -r) * |u| := by
    have hq' : 0 ≤ -q := by linarith
    have hr' : 0 ≤ -r := by linarith
    have hv' : (-q) * |v| ≤ (-q) * |u| := mul_le_mul_of_nonneg_left hv hq'
    have hw' : (-r) * |w| ≤ (-r) * |u| := mul_le_mul_of_nonneg_left hw hr'
    linarith
  have hkey : p * |u| ≤ (-q + -r) * |u| := by linarith
  have hMzero : |u| = 0 := by
    have hpos : 0 < p - (-q + -r) := by linarith
    nlinarith [hkey, hpos, hM_nonneg]
  exact abs_eq_zero.mp hMzero
rcases le_total |x| |y| with hxy | hyx
· rcases le_total |y| |z| with hyz | hzy
  · have hxz : |x| ≤ |z| := le_trans hxy hyz
    have hz0 : z = 0 := key (a 8) (a 6) (a 7) z x y h₀.2.2 h₃.1 h₃.2 (by linarith [h₆]) (by linarith [h₉]) hxz hyz
    have hzabs : |z| = 0 := by rw [hz0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxz, hzabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyz, hzabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
  · have hy0 : y = 0 := key (a 4) (a 3) (a 5) y x z h₀.2.1 h₂.1 h₂.2 (by linarith [h₅]) (by linarith [h₈]) hxy hzy
    have hyabs : |y| = 0 := by rw [hy0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxy, hyabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hz0 : z = 0 := by
      have : |z| = 0 := le_antisymm (by linarith [hzy, hyabs]) (abs_nonneg z)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
· rcases le_total |x| |z| with hxz | hzx
  · have hyz : |y| ≤ |z| := le_trans hyx hxz
    have hz0 : z = 0 := key (a 8) (a 6) (a 7) z x y h₀.2.2 h₃.1 h₃.2 (by linarith [h₆]) (by linarith [h₉]) hxz hyz
    have hzabs : |z| = 0 := by rw [hz0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxz, hzabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyz, hzabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
  · have hx0 : x = 0 := key (a 0) (a 1) (a 2) x y z h₀.1 h₁.1 h₁.2 h₄ h₇ hyx hzx
    have hxabs : |x| = 0 := by rw [hx0, abs_zero]
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyx, hxabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    have hz0 : z = 0 := by
      have : |z| = 0 := le_antisymm (by linarith [hzx, hxabs]) (abs_nonneg z)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
```

</details>

**Attempt 3:** `lean:error` (class `missing_lemma`)

```text
line 23: Unknown identifier `abs_add`
```

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
have key : ∀ (p q r u v w : ℝ), 0 < p → q < 0 → r < 0 →
    0 < p + q + r → p * u + q * v + r * w = 0 →
    |v| ≤ |u| → |w| ≤ |u| → u = 0 := by
  intro p q r u v w hp hq hr hsum heq hv hw
  have hM_nonneg : 0 ≤ |u| := abs_nonneg u
  have htri : p * |u| ≤ (-q) * |v| + (-r) * |w| := by
    have ha : |p * u| = p * |u| := by rw [abs_mul, abs_of_pos hp]
    have hpu : p * u = -(q * v + r * w) := by linarith
    have h1 : |q * v| = (-q) * |v| := by rw [abs_mul, abs_of_neg hq]
    have h2 : |r * w| = (-r) * |w| := by rw [abs_mul, abs_of_neg hr]
    calc p * |u| = |p * u| := ha.symm
      _ = |-(q * v + r * w)| := by rw [hpu]
      _ = |q * v + r * w| := abs_neg _
      _ ≤ |q * v| + |r * w| := abs_add _ _
      _ = (-q) * |v| + (-r) * |w| := by rw [h1, h2]
  have hmono : (-q) * |v| + (-r) * |w| ≤ (-q + -r) * |u| := by
    have hq' : 0 ≤ -q := by linarith
    have hr' : 0 ≤ -r := by linarith
    have hv' : (-q) * |v| ≤ (-q) * |u| := mul_le_mul_of_nonneg_left hv hq'
    have hw' : (-r) * |w| ≤ (-r) * |u| := mul_le_mul_of_nonneg_left hw hr'
    linarith
  have hkey : p * |u| ≤ (-q + -r) * |u| := by linarith
  have hMzero : |u| = 0 := by
    have hpos : 0 < p - (-q + -r) := by linarith
    nlinarith [hkey, hpos, hM_nonneg]
  exact abs_eq_zero.mp hMzero
rcases le_total |x| |y| with hxy | hyx
· rcases le_total |y| |z| with hyz | hzy
  · have hxz : |x| ≤ |z| := le_trans hxy hyz
    have hz0 : z = 0 := key (a 8) (a 6) (a 7) z x y h₀.2.2 h₃.1 h₃.2 (by linarith [h₆]) (by linarith [h₉]) hxz hyz
    have hzabs : |z| = 0 := by rw [hz0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxz, hzabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyz, hzabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
  · have hy0 : y = 0 := key (a 4) (a 3) (a 5) y x z h₀.2.1 h₂.1 h₂.2 (by linarith [h₅]) (by linarith [h₈]) hxy hzy
    have hyabs : |y| = 0 := by rw [hy0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxy, hyabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hz0 : z = 0 := by
      have : |z| = 0 := le_antisymm (by linarith [hzy, hyabs]) (abs_nonneg z)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
· rcases le_total |x| |z| with hxz | hzx
  · have hyz : |y| ≤ |z| := le_trans hyx hxz
    have hz0 : z = 0 := key (a 8) (a 6) (a 7) z x y h₀.2.2 h₃.1 h₃.2 (by linarith [h₆]) (by linarith [h₉]) hxz hyz
    have hzabs : |z| = 0 := by rw [hz0, abs_zero]
    have hx0 : x = 0 := by
      have : |x| = 0 := le_antisymm (by linarith [hxz, hzabs]) (abs_nonneg x)
      exact abs_eq_zero.mp this
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyz, hzabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
  · have hx0 : x = 0 := key (a 0) (a 1) (a 2) x y z h₀.1 h₁.1 h₁.2 h₄ h₇ hyx hzx
    have hxabs : |x| = 0 := by rw [hx0, abs_zero]
    have hy0 : y = 0 := by
      have : |y| = 0 := le_antisymm (by linarith [hyx, hxabs]) (abs_nonneg y)
      exact abs_eq_zero.mp this
    have hz0 : z = 0 := by
      have : |z| = 0 := le_antisymm (by linarith [hzx, hxabs]) (abs_nonneg z)
      exact abs_eq_zero.mp this
    exact ⟨hx0, hy0, hz0⟩
```

</details>

**Context:** formal statement proved by other configurations: C1, C3, C5.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 4. `imo_1978_p5` (algebra, hard)

### Problem (informal)

Let $f$ be an injective function from ${1,2,3,\ldots}$ in itself. Prove that for any $n$ we have: $\sum_{k=1}^{n} f(k)k^{-2} \geq \sum_{k=1}^{n} k^{-1}.$

### Formal statement (what Lean had to prove)

```lean
theorem imo_1978_p5 (n : ℕ) (a : ℕ → ℕ) (h₀ : Function.Injective a) (h₁ : a 0 = 0) (h₂ : 0 < n) :
  (∑ k ∈ Finset.Icc 1 n, (1 : ℝ) / k) ≤ ∑ k ∈ Finset.Icc 1 n, (a k : ℝ) / k ^ 2 := by
```

### C2's solution (natural language)

Let n be a positive integer. The numbers f(1),...,f(n) are distinct positive integers. Arrange them in increasing order as
\[b_1<b_2<\cdots<b_n.\]
Then b_i\ge i for every i, since b_1,...,b_i are i distinct positive integers, so their maximum is at least i.

We use the rearrangement inequality. Since the sequence 1/k^2 is decreasing, the minimum possible value of
\[\sum_{k=1}^n \frac{x_k}{k^2}\]
over all rearrangements x_1,\ldots,x_n of b_1,\ldots,b_n is obtained by placing the larger b_i's against the smaller weights, i.e. by taking x_k=b_k. Indeed, if in some arrangement a smaller b is paired with a smaller weight and a larger b with a larger weight, swapping them decreases the sum; repeating removes all such inversions.

More explicitly, there is a permutation \sigma of \{1,\ldots,n\} such that
\[\sum_{k=1}^n \frac{f(k)}{k^2}=\sum_{i=1}^n \frac{b_i}{\sigma(i)^2}.\]
By the rearrangement inequality,
\[\sum_{i=1}^n \frac{b_i}{\sigma(i)^2}\ge \sum_{i=1}^n \frac{b_i}{i^2}.\]
Since b_i\ge i, we obtain
\[\sum_{i=1}^n \frac{b_i}{i^2}\ge \sum_{i=1}^n \frac{i}{i^2}=\sum_{i=1}^n \frac1i.\]
Therefore
\[\sum_{k=1}^n \frac{f(k)}{k^2}\ge \sum_{k=1}^n \frac1k.\]
This proves the claim.

**Final answer:** For every positive integer n, \(\displaystyle \sum_{k=1}^{n} \frac{f(k)}{k^2}\ge \sum_{k=1}^{n}\frac1k.\)

**C2's self-assessment:** verdict `correct`, confidence 0.99; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 2:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 3:** `sanitize:empty` (class `syntax`) — no Lean messages

**Context:** formal statement proved by other configurations: none.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 5. `imo_1979_p1` (number_theory, hard)

### Problem (informal)

If $p$ and $q$ are natural numbers so that$ \frac{p}{q}=1-\frac{1}{2}+\frac{1}{3}-\frac{1}{4}+ \ldots -\frac{1}{1318}+\frac{1}{1319}, $prove that $p$ is divisible with $1979$.

### Formal statement (what Lean had to prove)

```lean
theorem imo_1979_p1 (p q : ℕ) (h₀ : 0 < q)
  (h₁ : (∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1) ^ (k + 1) * ((1 : ℝ) / k)) = p / q) : 1979 ∣ p := by
```

### C2's solution (natural language)

Let
\[S=\sum_{k=1}^{1319}\frac{(-1)^{k+1}}{k}
=1-\frac12+\frac13-\cdots-\frac1{1318}+\frac1{1319}.\]
Since the even terms are $k=2j$ with $1\le j\le 659$, we have
\[S=H_{1319}-H_{659},
\]
where $H_n=\sum_{k=1}^n\frac1k$.

First note that $1979$ is prime; indeed its square root is less than $45$, and it is not divisible by any prime $2,3,5,7,11,13,17,19,23,29,31,37,41,43$.

Work modulo the prime $1979$. In $\mathbb F_{1979}$,
\[H_{1978}=\sum_{k=1}^{1978}\frac1k\equiv \sum_{k=1}^{1978}k=\frac{1978\cdot1979}{2}\equiv0\pmod{1979},
\]
because inversion permutes the nonzero residues modulo $1979$.

Thus
\[H_{1319}\equiv -\sum_{k=1320}^{1978}\frac1k\pmod{1979}.
\]
Put $j=1979-k$. As $k$ runs from $1320$ to $1978$, $j$ runs from $659$ down to $1$, and
\[\frac1k=\frac1{1979-j}\equiv -\frac1j\pmod{1979}.
\]
Therefore
\[\sum_{k=1320}^{1978}\frac1k\equiv -\sum_{j=1}^{659}\frac1j=-H_{659}\pmod{1979}.
\]
Hence
\[H_{1319}\equiv H_{659}\pmod{1979},
\]
so
\[S=H_{1319}-H_{659}\equiv0\pmod{1979}.
\]

Now $S=p/q$ with $0<q$. All denominators occurring in $S$ are less than $1979$, so when $S$ is written in lowest terms, its denominator is not divisible by $1979$. Equivalently, if $S=N/D$ with $1979\nmid D$, then $S\equiv0\pmod{1979}$ implies $1979\mid N$. Since $p/q=N/D$, we have $pD=Nq$. Because $1979\nmid D$ and $1979\mid N$, it follows that $1979\mid p$.

Therefore $1979$ divides $p$.

**Final answer:** 1979 divides p.

**C2's self-assessment:** verdict `correct`, confidence 0.99; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `missing_lemma`)

```text
line 10: Unknown identifier `IMO1979P1.imo_1979_p1`
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
exact IMO1979P1.imo_1979_p1 p q h₀ h₁
```

</details>

**Attempt 2:** `lean:error` (class `syntax`)

```text
line 22: Tactic `rewrite` failed: Did not find an occurrence of the pattern
  ∏ x ∈ ?m.168, ?m.167 x ≠ 0
in the target expression
  ¬∏ i ∈ Finset.Icc 1 1319, ↑i = 0

p q : ℕ
h₀ : 0 < q
h₁ : ∑ k ∈ Finset.Icc 1 1319, (-1) ^ (k + 1) * (1 / ↑k) = ↑p / ↑q
D : ℕ := ∏ k ∈ Finset.Icc 1 1319, k
N : ℤ := ∑ k ∈ Finset.

line 29: failed to synthesize instance of type class
  Preorder (ZMod 1979)

Hint: Type class instance resolution failures can be inspected with the `set_option trace.Meta.synthInstance true` command.

line 29: failed to synthesize instance of type class
  LocallyFiniteOrder (ZMod 1979)

Hint: Type class instance resolution failures can be inspected with the `set_option trace.Meta.synthInstance true` command.
```

<details><summary>Lean tactics tried in attempt 2</summary>

```lean
let D : ℕ := ∏ k ∈ Finset.Icc 1 1319, k
let N : ℤ := ∑ k ∈ Finset.Icc 1 1319, (-1 : ℤ) ^ (k + 1) * (D / k : ℕ)
have hDpos : 0 < D := by
  dsimp [D]
  rw [Nat.pos_iff_ne_zero]
  rw [Finset.prod_ne_zero_iff]
  intro k hk
  rw [Finset.mem_Icc] at hk
  omega
have hDmod : (D : ZMod 1979) ≠ 0 := by
  dsimp [D]
  rw [Nat.cast_prod]
  rw [Finset.prod_ne_zero_iff]
  intro k hk
  rw [Finset.mem_Icc] at hk
  rw [ZMod.natCast_zmod_eq_zero_iff_dvd]
  intro hdvd
  have hle : 1979 ≤ k := Nat.le_of_dvd (by omega) hdvd
  omega
have hsum : (∑ k ∈ Finset.Icc 1 1319, (-1 : ZMod 1979) ^ (k + 1) * (k : ZMod 1979)⁻¹) = 0 := by
  decide
have hNmod : (N : ZMod 1979) = 0 := by
  dsimp [N]
  push_cast
  have hDk : ∀ k ∈ Finset.Icc 1 1319, ((D / k : ℕ) : ZMod 1979) = (D : ZMod 1979) * (k : ZMod 1979)⁻¹ := by
    intro k hk
    rw [Finset.mem_Icc] at hk
    have hkpos : 0 < k := by omega
    have hkdvd : k ∣ D := by
      dsimp [D]
      exact Finset.dvd_prod_of_mem (fun x => x) hk
    have hk0 : (k : ZMod 1979) ≠ 0 := by
      rw [ZMod.natCast_zmod_eq_zero_iff_dvd]
      intro hdvd
      have hle : 1979 ≤ k := Nat.le_of_dvd hkpos hdvd
      omega
    have hDeq : k * (D / k) = D := by
      rw [mul_comm]
      exact Nat.div_mul_cancel hkdvd
    have hcastDeq : (D : ZMod 1979) = (k : ZMod 1979) * ((D / k : ℕ) : ZMod 1979) := by
      rw [← hDeq]
      push_cast
      ring
    calc
      ((D / k : ℕ) : ZMod 1979) = (k : ZMod 1979)⁻¹ * ((k : ZMod 1979) * ((D / k : ℕ) : ZMod 1979)) := by
```

</details>

**Attempt 3:** `lean:error` (class `syntax`)

```text
line 24: Unknown constant `ZMod.natCast_zmod_eq_zero_iff_dvd`

line 40: Unknown constant `ZMod.natCast_zmod_eq_zero_iff_dvd`

line 47: unsolved goals
p q : ℕ
h₀ : 0 < q
h₁ : ∑ k ∈ Finset.Icc 1 1319, (-1) ^ (k + 1) * (1 / ↑k) = ↑p / ↑q
hprime_fact : Fact (Nat.Prime 1979)
D : ℕ := ∏ k ∈ Finset.Icc 1 1319, k
N : ℤ := ∑ k ∈ Finset.Icc 1 1319, (-1) ^ (k + 1) * ↑(D / k)
hDpos : 0 < D
hDmod : ↑D ≠ 0
k : ℕ
hk : k ∈ Finset.Icc 1 1319
hkdvd
```

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
haveI hprime_fact : Fact (Nat.Prime 1979) := ⟨by norm_num⟩
let D : ℕ := ∏ k ∈ Finset.Icc (1 : ℕ) 1319, k
let N : ℤ := ∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℤ) ^ (k + 1) * ((D / k : ℕ) : ℤ)
have hDpos : 0 < D := by
  dsimp [D]
  apply Finset.prod_pos
  intro k hk
  have hk1 := (Finset.mem_Icc.mp hk).1
  omega
have hDmod : (D : ZMod 1979) ≠ 0 := by
  dsimp [D]
  push_cast
  apply Finset.prod_ne_zero_iff.mpr
  intro k hk
  rw [ZMod.natCast_zmod_eq_zero_iff_dvd]
  intro hdvd
  have hk1 := (Finset.mem_Icc.mp hk).1
  have hk2 := (Finset.mem_Icc.mp hk).2
  have hle : 1979 ≤ k := Nat.le_of_dvd (by omega) hdvd
  omega
have hDkZ : ∀ k ∈ Finset.Icc (1 : ℕ) 1319,
    ((D / k : ℕ) : ZMod 1979) = (D : ZMod 1979) * (k : ZMod 1979)⁻¹ := by
  intro k hk
  have hkdvd : k ∣ D := by
    dsimp [D]
    exact Finset.dvd_prod_of_mem (fun x => x) hk
  have hkpos : 0 < k := by
    have hk1 := (Finset.mem_Icc.mp hk).1
    omega
  have hk0 : (k : ZMod 1979) ≠ 0 := by
    rw [ZMod.natCast_zmod_eq_zero_iff_dvd]
    intro hdvd
    have hle : 1979 ≤ k := Nat.le_of_dvd hkpos hdvd
    have hk2 := (Finset.mem_Icc.mp hk).2
    omega
  have hDeq : k * (D / k) = D := by
    rw [mul_comm, Nat.div_mul_cancel hkdvd]
  have hcast : (k : ZMod 1979) * ((D / k : ℕ) : ZMod 1979) = (D : ZMod 1979) := by
    rw [← hDeq]
    push_cast
    ring
  calc
    ((D / k : ℕ) : ZMod 1979)
        = (k : ZMod 1979)⁻¹ * ((k : ZMod 1979) * ((D / k : ℕ) : ZMod 1979)) := by
            rw [inv_mul_cancel_left₀ hk0]
    _ = (k : ZMod 1979)⁻¹ * (D : ZMod 1979) := by rw [hcast]
    _ = (D : ZMod 1979) * (k : ZMod 1979)⁻¹ := by ring
have hsum : (∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ZMod 1979) ^ (k + 1) * (k : ZMod 1979)⁻¹) = 0 := by
  decide
have hNmod : (N : ZMod 1979) = 0 := by
  dsimp [N]
  push_cast
  calc
    (∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ZMod 1979) ^ (k + 1) * ((D / k : ℕ) : ZMod 1979))
        = ∑ k ∈ Finset.Icc (1 : ℕ) 1319, (D : ZMod 1979) * ((-1 : ZMod 1979) ^ (k + 1) * (k : ZMod 1979)⁻¹) := by
          apply Finset.sum_congr rfl
          intro k hk
          rw [hDkZ k hk]
          ring
    _ = (D : ZMod 1979) * (∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ZMod 1979) ^ (k + 1) * (k : ZMod 1979)⁻¹) := by
          rw [Finset.mul_sum]
    _ = (D : ZMod 1979) * 0 := by rw [hsum]
    _ = 0 := by rw [mul_zero]
have hDkQ : ∀ k ∈ Finset.Icc (1 : ℕ) 1319,
    (1 / (k : ℚ)) * (D : ℚ) = ((D / k : ℕ) : ℚ) := by
  intro k hk
  have hkdvd : k ∣ D := by
    dsimp [D]
    exact Finset.dvd_prod_of_mem (fun x => x) hk
  have hkpos : 0 < k := by
    have hk1 := (Finset.mem_Icc.mp hk).1
    omega
  have hDeq : k * (D / k) = D := by
    rw [mul_comm, Nat.div_mul_cancel hkdvd]
  have hcast : (k : ℚ) * ((D / k : ℕ) : ℚ) = (D : ℚ) := by
    rw [← hDeq]
    push_cast
    ring
  rw [← hcast]
  field_simp [show (k : ℚ) ≠ 0 by positivity]
  ring
have hrD : (∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) * (D : ℚ) = (N : ℚ) := by
  dsimp [N]
  push_cast
  rw [Finset.sum_mul]
  apply Finset.sum_congr rfl
  intro k hk
  calc
    ((-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) * (D : ℚ)
        = (-1 : ℚ) ^ (k + 1) * ((1 / (k : ℚ)) * (D : ℚ)) := by ring
    _ = (-1 : ℚ) ^ (k + 1) * ((D / k : ℕ) : ℚ) := by rw [hDkQ k hk]
have hrat : (∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) = (p : ℚ) / (q : ℚ) := by
  have hcast : ((∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) : ℝ)
      = (((p : ℚ) / (q : ℚ)) : ℝ) := by
    calc
      ((∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) : ℝ)
          = ∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℝ) ^ (k + 1) * (1 / (k : ℝ)) := by
              push_cast
              rfl
      _ = (p : ℝ) / (q : ℝ) := h₁
      _ = (((p : ℚ) / (q : ℚ)) : ℝ) := by
              push_cast
              rfl
  exact_mod_cast hcast
have hq0 : (q : ℚ) ≠ 0 := by positivity
have hpDqN : (p : ℚ) * (D : ℚ) = (q : ℚ) * (N : ℚ) := by
  calc
    (p : ℚ) * (D : ℚ)
        = (((p : ℚ) / (q : ℚ)) * (q : ℚ)) * (D : ℚ) := by
            field_simp [hq0]
            ring
    _ = ((∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) * (q : ℚ)) * (D : ℚ) := by
            rw [← hrat]
    _ = (q : ℚ) * ((∑ k ∈ Finset.Icc (1 : ℕ) 1319, (-1 : ℚ) ^ (k + 1) * (1 / (k : ℚ))) * (D : ℚ)) := by
            ring
    _ = (q : ℚ) * (N : ℚ) := by
            rw [hrD]
have hpDqN_int : (p : ℤ) * (D : ℤ) = (q : ℤ) * N := by
  exact_mod_cast hpDqN
have hdvdN : (1979 : ℤ) ∣ N := by
  rw [← ZMod.intCast_zmod_eq_zero_iff_dvd]
  exact hNmod
have hmodp : (1979 : ℤ) ∣ (p : ℤ) * (D : ℤ) := by
  rw [hpDqN_int]
  exact dvd_mul_of_dvd_right hdvdN (q : ℤ)
have hDnotdvd : ¬ (1979 ∣ D) := by
  intro hdiv
  apply hDmod
  exact (ZMod.natCast_zmod_eq_zero_iff_dvd).mpr hdiv
have hpdvd : 1979 ∣ p * D := by
  rw [← Int.natCast_dvd_natCast]
  push_cast
  exact hmodp
have hprime_nat : Nat.Prime 1979 := hprime_fact.out
rcases (Nat.Prime.dvd_mul hprime_nat).mp hpdvd with hp | hD
· exact hp
· exact False.elim (hDnotdvd hD)
```

</details>

**Context:** formal statement proved by other configurations: none.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 6. `imo_1984_p6` (number_theory, hard)

### Problem (informal)

Let $a,b,c,d$ be odd integers such that $0<a<b<c<d$ and $ad=bc$. Prove that if $a+d=2^k$ and $b+c=2^m$ for some integers $k$ and $m$, then $a=1$.

### Formal statement (what Lean had to prove)

```lean
theorem imo_1984_p6 (a b c d k m : ℕ) (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 < d)
    (h₁ : Odd a ∧ Odd b ∧ Odd c ∧ Odd d) (h₂ : a < b ∧ b < c ∧ c < d) (h₃ : a * d = b * c)
    (h₄ : a + d = 2 ^ k) (h₅ : b + c = 2 ^ m) : a = 1 := by
```

### C2's solution (natural language)

Since \(ad=bc\), we have \(d/c=b/a>1\). Hence
\[
d-c=c\left(\frac ba-1\right)=\frac{c(b-a)}a>b-a,
\]
because \(c>a\). Therefore \(a+d>b+c\), so \(2^k>2^m\), and hence \(k>m\).

Also \(m\ge 3\): indeed \(a,b,c\) are positive odd integers with \(0<a<b<c\), so \(b\ge 3\) and \(c\ge 5\), whence \(b+c\ge 8\), i.e. \(2^m\ge 8\).

Because \(k>m\), the congruence \(a+d=2^k\) gives
\[
d\equiv -a \pmod{2^m},
\]
and \(b+c=2^m\) gives
\[
c\equiv -b \pmod{2^m}.
\]
Using \(ad=bc\), we obtain
\[
-a^2\equiv -b^2 \pmod{2^m},
\]
so
\[
a^2\equiv b^2 \pmod{2^m}.
\]

We now use a standard fact: if \(x,y\) are odd and \(m\ge 3\), then from \(x^2\equiv y^2\pmod{2^m}\) it follows that \(x\equiv \pm y\pmod{2^{m-1}}\). Indeed,
\[
x^2-y^2=(x-y)(x+y),
\]
and for odd \(x,y\), one of \(x-y,x+y\) is divisible by exactly one factor of \(2\), so the other must be divisible by \(2^{m-1}\).

Applying this to \(a,b\), we get
\[
b\equiv \pm a \pmod{2^{m-1}}.
\]
Since \(b<c\) and \(b+c=2^m\), we have \(0<b<2^{m-1}\). Also \(0<a<b\), so \(0<b-a<2^{m-1}\). Thus \(b\not\equiv a\pmod{2^{m-1}}\), and therefore
\[
a+b\equiv 0\pmod{2^{m-1}}.
\]
But \(0<a+b<2^m\), so necessarily
\[
a+b=2^{m-1}.
\]
Thus
\[
b=2^{m-1}-a,\qquad c=2^m-b=2^{m-1}+a.
\]
Hence
\[
bc=(2^{m-1}-a)(2^{m-1}+a)=2^{2m-2}-a^2.
\]
On the other hand, from \(a+d=2^k\), we have \(d=2^k-a\), so
\[
ad=a(2^k-a)=a2^k-a^2.
\]
Since \(ad=bc\), we get
\[
a2^k-a^2=2^{2m-2}-a^2,
\]
so
\[
a2^k=2^{2m-2}.
\]
Therefore \(a\) is a power of \(2\). But \(a\) is odd, so \(a=1\).

**Final answer:** a=1

**C2's self-assessment:** verdict `correct`, confidence 0.99; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 2:** `lean:error` (class `missing_lemma`)

```text
line 10: Unknown identifier `imo_1984_p6`
```

<details><summary>Lean tactics tried in attempt 2</summary>

```lean
exact imo_1984_p6 a b c d k m h₀ h₁ h₂ h₃ h₄ h₅
```

</details>

**Attempt 3:** `sanitize:empty` (class `syntax`) — no Lean messages

**Context:** formal statement proved by other configurations: none.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 7. `imo_1987_p6` (number_theory, hard)

### Problem (informal)

Let $n $ be an integer greater than or equal to 2.  Prove that if $k^2 + k + n $ is prime for all integers $k $ such that $ 0 \leq k \leq \sqrt{n/3} $, then $k^2 + k + n $ is prime for all integers $k $ such that $0 \leq k \leq n - 2 $.

### Formal statement (what Lean had to prove)

```lean
theorem imo_1987_p6 (p : ℕ) (f : ℕ → ℕ) (h₀ : ∀ x, f x = x ^ 2 + x + p)
  (h₀ : ∀ k : ℕ, k ≤ Nat.floor (Real.sqrt (p / 3)) → Nat.Prime (f k)) :
   ∀ i ≤ p - 2, Nat.Prime (f i) := by
```

### C2's solution (natural language)

Let f(x)=x^2+x+n. We prove the contrapositive. Suppose there is an integer k with 0≤k≤n−2 such that f(k) is composite, and choose the least such k. Since 0≤√(n/3), the hypothesis gives f(0)=n prime, so k≥1.

Let q be the smallest prime divisor of f(k). Because f(k) is composite, q≤√(f(k)). We first show q>k. If q≤k, then k−q≥0, and
f(k−q)=f(k)−q(2k−q+1),
so q divides f(k−q). Also f(k−q)≥n≥k+2>q, since k≤n−2 and q≤k. Hence f(k−q) is composite and k−q<k, contradicting the minimality of k. Thus q>k.

Set r=q−k−1. Then r≥0. Since f(−k−1)=f(k) and r≡−k−1 (mod q), we have q|f(r). We claim r≥k. Suppose instead r<k. By minimality of k, f(r) is prime. Since q|f(r), we get q=f(r). Then q divides
f(k)−f(r)=(k−r)(k+r+1).
But 0<k−r<q, and also 0<k+r+1<q because
q−(k+r+1)=r^2+r+n−(k+r+1)=r^2+n−k−1≥r^2+1>0,
using n≥k+2. A prime q cannot divide the product of two positive integers each smaller than q, contradiction. Hence r≥k.

Therefore q=r+k+1≥2k+1. Since q≤√(f(k)), we obtain
(2k+1)^2≤q^2≤f(k)=k^2+k+n.
Thus
4k^2+4k+1≤k^2+k+n,
so n≥3k^2+3k+1>3k^2. Hence k<√(n/3). By the hypothesis, f(k) must be prime, contradicting the choice of k as a composite value.

This contradiction shows that no such k exists. Therefore f(k)=k^2+k+n is prime for every integer k with 0≤k≤n−2.

**Final answer:** If k^2+k+n is prime for all 0≤k≤√(n/3), then k^2+k+n is prime for all 0≤k≤n−2.

**C2's self-assessment:** verdict `correct`, confidence 0.99; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `missing_lemma`)

```text
line 63: Unknown constant `ZMod.natCast_zmod_eq_zero_iff_dvd`

line 79: Unknown constant `ZMod.natCast_zmod_eq_zero_iff_dvd`

line 88: Invalid field `dvd_prime_iff_eq`: The environment does not contain `Irreducible.dvd_prime_iff_eq`, so it is not possible to project the field `dvd_prime_iff_eq` from an expression
  hq_prime
of type `Irreducible q`
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
intro i hi
by_contra hprime
have hf : ∀ x, f x = x ^ 2 + x + p := by assumption
have hp : ∀ k, k ≤ Nat.floor (Real.sqrt (p / 3)) → Nat.Prime (f k) := by assumption
let P : ℕ → Prop := fun n => n ≤ p - 2 ∧ ¬ Nat.Prime (f n)
have hex : ∃ n, P n := ⟨i, hi, hprime⟩
set k := Nat.find hex with hk_def
have hkP : P k := by
  rw [hk_def]
  exact Nat.find_spec hex
have hk_le : k ≤ p - 2 := hkP.1
have hk_not : ¬ Nat.Prime (f k) := hkP.2
have hk_min : ∀ j < k, ¬ P j := by
  intro j hj
  rw [hk_def] at hj
  exact Nat.find_min hex hj
have hk_min_prime : ∀ j < k, j ≤ p - 2 → Nat.Prime (f j) := by
  intro j hj hle
  by_contra hjp
  exact hk_min j hj ⟨hle, hjp⟩
have hk_pos : 0 < k := by
  rcases Nat.eq_zero_or_pos k with hk0 | hkpos
  · exfalso
    have hprime0 : Nat.Prime (f 0) := hp 0 (Nat.zero_le _)
    exact hk_not (by simpa [hk0] using hprime0)
  · exact hkpos
have hp0 : Nat.Prime p := by
  have h := hp 0 (Nat.zero_le _)
  have hf0 : f 0 = p := by simpa using hf 0
  simpa [hf0] using h
have hp_ge2 : 2 ≤ p := hp0.two_le
have hfk_ge2 : 2 ≤ f k := by
  rw [hf k]
  have hk2 : 0 ≤ k ^ 2 := Nat.zero_le _
  omega
have h_exists_div : ∃ m, 2 ≤ m ∧ m ≤ Nat.sqrt (f k) ∧ m ∣ f k := by
  by_contra hno
  push_neg at hno
  have hprimefk : Nat.Prime (f k) := by
    rw [Nat.prime_def_le_sqrt]
    refine ⟨hfk_ge2, ?_⟩
    intro m hm1 hm2 hm3
    exact hno m hm1 hm2 hm3
  exact hk_not hprimefk
obtain ⟨m, hm_ge2, hm_le_sqrt, hm_dvd⟩ := h_exists_div
have hm_ne_one : m ≠ 1 := by omega
obtain ⟨q, hq_prime, hq_dvd_m⟩ := Nat.exists_prime_and_dvd hm_ne_one
have hq_dvd : q ∣ f k := dvd_trans hq_dvd_m hm_dvd
have hm_pos : 0 < m := by omega
have hq_le_m : q ≤ m := Nat.le_of_dvd hm_pos hq_dvd_m
have hq_le_sqrt : q ≤ Nat.sqrt (f k) := le_trans hq_le_m hm_le_sqrt
have hq_pos : 0 < q := hq_prime.pos
have hfk_mod : (f k : ZMod q) = 0 := by
  exact (ZMod.natCast_zmod_eq_zero_iff_dvd (f k) q).mpr hq_dvd
have hq_gt_k : k < q := by
  by_contra hcon
  have hq_le_k : q ≤ k := by omega
  have hfkq_mod : (f (k - q) : ZMod q) = 0 := by
    have hkq_cast : ((k - q : ℕ) : ZMod q) = (k : ZMod q) := by
      rw [Nat.cast_sub hq_le_k]
      simp
    calc
      (f (k - q) : ZMod q) = (((k - q) ^ 2 + (k - q) + p : ℕ) : ZMod q) := by rw [hf (k - q)]
      _ = ((k - q : ℕ) : ZMod q) ^ 2 + ((k - q : ℕ) : ZMod q) + (p : ZMod q) := by push_cast; ring
      _ = (k : ZMod q) ^ 2 + (k : ZMod q) + (p : ZMod q) := by rw [hkq_cast]
      _ = ((k ^ 2 + k + p : ℕ) : ZMod q) := by push_cast; ring
      _ = (f k : ZMod q) := by rw [hf k]
      _ = 0 := hfk_mod
  have hq_dvd_fkq : q ∣ f (k - q) := by
    exact (ZMod.natCast_zmod_eq_zero_iff_dvd (f (k - q)) q).mp hfkq_mod
  have hkp2 : k + 2 ≤ p := by omega
  have hpq : q < p := by omega
  have hfkq_gt_q : q < f (k - q) := by
    rw [hf (k - q)]
    have hnonneg : 0 ≤ (k - q) ^ 2 + (k - q) := by positivity
    nlinarith
  have hfkq_not_prime : ¬ Nat.Prime (f (k - q)) := by
    intro hprime
    have hq_eq : q = f (k - q) := (hq_prime.dvd_prime_iff_eq hprime).mp hq_dvd_fkq
    omega
  have hkq_lt : k - q < k := by omega
  have hkq_le : k - q ≤ p - 2 := by omega
  have hprimefkq : Nat.Prime (f (k - q)) := hk_min_prime (k - q) hkq_lt hkq_le
  exact hfkq_not_prime hprimefkq
set r := q - k - 1 with hr_def
have hq_eq_r : q = r + k + 1 := by
  rw [hr_def]
  omega
have hr_cast : ((r : ℕ) : ZMod q) = -((k : ZMod q) + 1) := by
  have hrq : r + k + 1 = q := hq_eq_r.symm
  have hzero : ((r + k + 1 : ℕ) : ZMod q) = 0 := by
    rw [hrq]
    simp
  push_cast at hzero
  linear_combination hzero
have hfr_mod : (f r : ZMod q) = 0 := by
  calc
    (f r : ZMod q) = (((r) ^ 2 + r + p : ℕ) : ZMod q) := by rw [hf r]
    _ = (r : ZMod q) ^ 2 + (r : ZMod q) + (p : ZMod q) := by push_cast; ring
    _ = (-((k : ZMod q) + 1)) ^ 2 + (-((k : ZMod q) + 1)) + (p : ZMod q) := by rw [hr_cast]
    _ = (k : ZMod q) ^ 2 + (k : ZMod q) + (p : ZMod q) := by ring
    _ = ((k ^ 2 + k + p : ℕ) : ZMod q) := by push_cast; ring
    _ = (f k : ZMod q) := by rw [hf k]
    _ = 0 := hfk_mod
have hq_dvd_fr : q ∣ f r := by
  exact (ZMod.natCast_zmod_eq_zero_iff_dvd (f r) q).mp hfr_mod
have hr_ge_k : k ≤ r := by
  by_contra hcon
  have hr_lt : r < k := by omega
  have hr_prime : Nat.Prime (f r) := hk_min_prime r hr_lt (by omega)
  have hq_eq_fr : q = f r := (hq_prime.dvd_prime_iff_eq hr_prime).mp hq_dvd_fr
  have hprod_mod : (((k - r) * (k + r + 1) : ℕ) : ZMod q) = 0 := by
    have hkr : r ≤ k := le_of_lt hr_lt
    have hk_sub : ((k - r : ℕ) : ZMod q) = (k : ZMod q) - (r : ZMod q) := by
      rw [Nat.cast_sub hkr]
    calc
      (((k - r) * (k + r + 1) : ℕ) : ZMod q)
          = ((k - r : ℕ) : ZMod q) * ((k + r + 1 : ℕ) : ZMod q) := by push_cast; ring
      _ = ((k : ZMod q) - (r : ZMod q)) * ((k : ZMod q) + (r : ZMod q) + 1) := by
            rw [hk_sub]
            push_cast
      _ = ((k : ZMod q) ^ 2 + (k : ZMod q) + (p : ZMod q)) - ((r : ZMod q) ^ 2 + (r : ZMod q) + (p : ZMod q)) := by ring
      _ = (f k : ZMod q) - (f r : ZMod q) := by
            rw [hf k, hf r]
            push_cast
            ring
      _ = 0 := by rw [hfk_mod, hfr_mod]; simp
  have hq_dvd_prod : q ∣ (k - r) * (k + r + 1) := by
    exact (ZMod.natCast_zmod_eq_zero_iff_dvd ((k - r) * (k + r + 1)) q).mp hprod_mod
  have hq_dvd_or : q ∣ (k - r) ∨ q ∣ (k + r + 1) := (hq_prime.dvd_mul).mp hq_dvd_prod
  have hkr_pos : 0 < k - r := by omega
  have hkr_lt_q : k - r < q := by omega
  have hkr1_pos : 0 < k + r + 1 := by omega
  have hkr1_lt_q : k + r + 1 < q := by
    rw [hq_eq_fr, hf r]
    have hkp2 : k + 2 ≤ p := by omega
    have hr2 : 0 ≤ r ^ 2 := Nat.zero_le _
    nlinarith
  rcases hq_dvd_or with h | h
  · have hle : q ≤ k - r := Nat.le_of_dvd hkr_pos h
    omega
  · have hle : q ≤ k + r + 1 := Nat.le_of_dvd hkr1_pos h
    omega
have hq_ge : 2 * k + 1 ≤ q := by
  rw [hq_eq_r]
  omega
have hq_sq_le : q ^ 2 ≤ f k := by
  have := hq_le_sqrt
  rwa [Nat.le_sqrt] at this
have hineq : (2 * k + 1) ^ 2 ≤ f k := by
  have hsq : (2 * k + 1) ^ 2 ≤ q ^ 2 := by
    nlinarith [hq_ge, Nat.zero_le (2 * k + 1), Nat.zero_le q]
  exact le_trans hsq hq_sq_le
have hp_gt : 3 * k ^ 2 < p := by
  rw [hf k] at hineq
  nlinarith
have hk_le_floor : k ≤ Nat.floor (Real.sqrt (p / 3)) := by
  apply Nat.le_floor.mpr
  have hp_gtR : (3 : ℝ) * (k : ℝ) ^ 2 < (p : ℝ) := by
    exact_mod_cast hp_gt
  have hk_sq_le : (k : ℝ) ^ 2 ≤ (p : ℝ) / 3 := by
    nlinarith
  calc
    (k : ℝ) = Real.sqrt ((k : ℝ) ^ 2) := by rw [Real.sqrt_sq (by positivity)]
    _ ≤ Real.sqrt ((p : ℝ) / 3) := Real.sqrt_le_sqrt hk_sq_le
exact hk_not (hp k hk_le_floor)
```

</details>

**Attempt 2:** `lean:error` (class `tactic_failure`)

```text
line 102: omega could not prove the goal:
a possible counterexample may satisfy the constraints
  j ≥ 0
  j ≤ 0
  h ≥ 0
  h - j ≥ 0
  g ≥ 2
  g - h ≤ 0
  g - j ≥ 0
  e ≥ 2
  d ≥ 2
  d - j ≥ 1
  c ≥ 1
  c - d ≤ -2
  c - j ≥ 0
  b ≥ 0
  b - d ≤ -2
  a ≥ 0
  a - d ≤ -2
where
 a := ↑i
 b := ↑hex.choose
 c := ↑(Na

line 103: Application type mismatch: The argument
  hq_dvd_fkq
has type
  q ∣ f (k - q)
but is expected to have type
  f (k - q) ∣ q
in the application
  (Prime.dvd_iff_eq hq_prime hne1).mp hq_dvd_fkq

line 105: omega could not prove the goal:
a possible counterexample may satisfy the constraints
  l ≥ 0
  j ≥ 0
  h ≥ 0
  h - j ≤ -1
  h ≤ 0
  h + l ≥ 1
  g ≥ 0
  g - h ≥ 0
  e ≥ 2
  e - g ≤ 0
  e - h ≥ 0
  d ≥ 2
  c ≥ 2
  c - h ≥ 1
  c - h - l ≥ 2
  b ≥ 0
  b - c ≤ -2
  a ≥ 0
  a - c ≤ -2
where
 a := ↑i
 b :
```

<details><summary>Lean tactics tried in attempt 2</summary>

```lean
intro i hi
by_contra hprime
have hf : ∀ x, f x = x ^ 2 + x + p := by assumption
have hp : ∀ k, k ≤ Nat.floor (Real.sqrt (p / 3)) → Nat.Prime (f k) := by assumption
let P : ℕ → Prop := fun n => n ≤ p - 2 ∧ ¬ Nat.Prime (f n)
have hex : ∃ n, P n := ⟨i, hi, hprime⟩
set k := Nat.find hex with hk_def
have hkP : P k := by
  rw [hk_def]
  exact Nat.find_spec hex
have hk_le : k ≤ p - 2 := hkP.1
have hk_not : ¬ Nat.Prime (f k) := hkP.2
have hk_min : ∀ j < k, ¬ P j := by
  intro j hj
  rw [hk_def] at hj
  exact Nat.find_min hex hj
have hk_min_prime : ∀ j < k, j ≤ p - 2 → Nat.Prime (f j) := by
  intro j hj hle
  by_contra hjp
  exact hk_min j hj ⟨hle, hjp⟩
have hk_pos : 0 < k := by
  rcases Nat.eq_zero_or_pos k with hk0 | hkpos
  · exfalso
    have hprime0 : Nat.Prime (f 0) := hp 0 (Nat.zero_le _)
    exact hk_not (by simpa [hk0] using hprime0)
  · exact hkpos
have hp0 : Nat.Prime p := by
  have h := hp 0 (Nat.zero_le _)
  have hf0 : f 0 = p := by simpa using hf 0
  simpa [hf0] using h
have hp_ge2 : 2 ≤ p := hp0.two_le
have hfk_ge2 : 2 ≤ f k := by
  rw [hf k]
  have hk2 : 0 ≤ k ^ 2 := Nat.zero_le _
  omega
have h_exists_div : ∃ m, 2 ≤ m ∧ m ≤ Nat.sqrt (f k) ∧ m ∣ f k := by
  by_contra hno
  push_neg at hno
  have hprimefk : Nat.Prime (f k) := by
    rw [Nat.prime_def_le_sqrt]
    refine ⟨hfk_ge2, ?_⟩
    intro m hm1 hm2 hm3
    exact hno m hm1 hm2 hm3
  exact hk_not hprimefk
obtain ⟨m, hm_ge2, hm_le_sqrt, hm_dvd⟩ := h_exists_div
have hm_ne_one : m ≠ 1 := by omega
obtain ⟨q, hq_prime, hq_dvd_m⟩ := Nat.exists_prime_and_dvd hm_ne_one
have hq_dvd : q ∣ f k := dvd_trans hq_dvd_m hm_dvd
have hm_pos : 0 < m := by omega
have hq_le_m : q ≤ m := Nat.le_of_dvd hm_pos hq_dvd_m
have hq_le_sqrt : q ≤ Nat.sqrt (f k) := le_trans hq_le_m hm_le_sqrt
have hzmod_natCast_eq_zero_iff_dvd : ∀ n : ℕ, (n : ZMod q) = 0 ↔ q ∣ n := by
  intro n
  constructor
  · intro h
    have h' : ((n : ℤ) : ZMod q) = 0 := by
      push_cast
      exact h
    rw [ZMod.intCast_zmod_eq_zero_iff_dvd] at h'
    exact_mod_cast h'
  · intro h
    have h' : ((n : ℤ) : ZMod q) = 0 := by
      rw [ZMod.intCast_zmod_eq_zero_iff_dvd]
      exact_mod_cast h
    push_cast at h'
    exact h'
have hfk_mod : (f k : ZMod q) = 0 := by
  exact (hzmod_natCast_eq_zero_iff_dvd (f k)).mpr hq_dvd
have hq_gt_k : k < q := by
  by_contra hcon
  have hq_le_k : q ≤ k := by omega
  have hfkq_mod : (f (k - q) : ZMod q) = 0 := by
    have hkq_cast : ((k - q : ℕ) : ZMod q) = (k : ZMod q) := by
      rw [Nat.cast_sub hq_le_k]
      simp
    calc
      (f (k - q) : ZMod q) = (((k - q) ^ 2 + (k - q) + p : ℕ) : ZMod q) := by rw [hf (k - q)]
      _ = ((k - q : ℕ) : ZMod q) ^ 2 + ((k - q : ℕ) : ZMod q) + (p : ZMod q) := by push_cast; ring
      _ = (k : ZMod q) ^ 2 + (k : ZMod q) + (p : ZMod q) := by rw [hkq_cast]
      _ = ((k ^ 2 + k + p : ℕ) : ZMod q) := by push_cast; ring
      _ = (f k : ZMod q) := by rw [hf k]
      _ = 0 := hfk_mod
  have hq_dvd_fkq : q ∣ f (k - q) := by
    exact (hzmod_natCast_eq_zero_iff_dvd (f (k - q))).mp hfkq_mod
  have hkp2 : k + 2 ≤ p := by omega
  have hpq : q < p := by omega
  have hfkq_gt_q : q < f (k - q) := by
    rw [hf (k - q)]
    have hnonneg : 0 ≤ (k - q) ^ 2 + (k - q) := by positivity
    nlinarith
  have hfkq_not_prime : ¬ Nat.Prime (f (k - q)) := by
    intro hprime
    have hne1 : f (k - q) ≠ 1 := by omega
    have hq_eq : q = f (k - q) := (Nat.Prime.dvd_iff_eq hq_prime hne1).mp hq_dvd_fkq
    omega
  have hkq_lt : k - q < k := by omega
  have hkq_le : k - q ≤ p - 2 := by omega
  have hprimefkq : Nat.Prime (f (k - q)) := hk_min_prime (k - q) hkq_lt hkq_le
  exact hfkq_not_prime hprimefkq
set r := q - k - 1 with hr_def
have hq_eq_r : q = r + k + 1 := by
  rw [hr_def]
  omega
have hr_cast : ((r : ℕ) : ZMod q) = -((k : ZMod q) + 1) := by
  have hrq : r + k + 1 = q := hq_eq_r.symm
  have hzero : ((r + k + 1 : ℕ) : ZMod q) = 0 := by
    rw [hrq]
    simp
  push_cast at hzero
  linear_combination hzero
have hfr_mod : (f r : ZMod q) = 0 := by
  calc
    (f r : ZMod q) = (((r) ^ 2 + r + p : ℕ) : ZMod q) := by rw [hf r]
    _ = (r : ZMod q) ^ 2 + (r : ZMod q) + (p : ZMod q) := by push_cast; ring
    _ = (-((k : ZMod q) + 1)) ^ 2 + (-((k : ZMod q) + 1)) + (p : ZMod q) := by rw [hr_cast]
    _ = (k : ZMod q) ^ 2 + (k : ZMod q) + (p : ZMod q) := by ring
    _ = ((k ^ 2 + k + p : ℕ) : ZMod q) := by push_cast; ring
    _ = (f k : ZMod q) := by rw [hf k]
    _ = 0 := hfk_mod
have hq_dvd_fr : q ∣ f r := by
  exact (hzmod_natCast_eq_zero_iff_dvd (f r)).mp hfr_mod
have hr_ge_k : k ≤ r := by
  by_contra hcon
  have hr_lt : r < k := by omega
  have hr_prime : Nat.Prime (f r) := hk_min_prime r hr_lt (by omega)
  have hfr_ne1 : f r ≠ 1 := by
    rw [hf r]
    have h1 : 0 ≤ r ^ 2 := Nat.zero_le _
    omega
  have hq_eq_fr : q = f r := (Nat.Prime.dvd_iff_eq hq_prime hfr_ne1).mp hq_dvd_fr
  have hprod_mod : (((k - r) * (k + r + 1) : ℕ) : ZMod q) = 0 := by
    have hkr : r ≤ k := le_of_lt hr_lt
    have hk_sub : ((k - r : ℕ) : ZMod q) = (k : ZMod q) - (r : ZMod q) := by
      rw [Nat.cast_sub hkr]
    calc
      (((k - r) * (k + r + 1) : ℕ) : ZMod q)
          = ((k - r : ℕ) : ZMod q) * ((k + r + 1 : ℕ) : ZMod q) := by push_cast; ring
      _ = ((k : ZMod q) - (r : ZMod q)) * ((k : ZMod q) + (r : ZMod q) + 1) := by
            rw [hk_sub]
            push_cast
      _ = ((k : ZMod q) ^ 2 + (k : ZMod q) + (p : ZMod q)) - ((r : ZMod q) ^ 2 + (r : ZMod q) + (p : ZMod q)) := by ring
      _ = (f k : ZMod q) - (f r : ZMod q) := by
            rw [hf k, hf r]
            push_cast
            ring
      _ = 0 := by rw [hfk_mod, hfr_mod]; simp
  have hq_dvd_prod : q ∣ (k - r) * (k + r + 1) := by
    exact (hzmod_natCast_eq_zero_iff_dvd ((k - r) * (k + r + 1))).mp hprod_mod
  have hq_dvd_or : q ∣ (k - r) ∨ q ∣ (k + r + 1) := (hq_prime.dvd_mul).mp hq_dvd_prod
  have hkr_pos : 0 < k - r := by omega
  have hkr_lt_q : k - r < q := by omega
  have hkr1_pos : 0 < k + r + 1 := by omega
  have hkr1_lt_q : k + r + 1 < q := by
    rw [hq_eq_fr, hf r]
    have hkp2 : k + 2 ≤ p := by omega
    have hr2 : 0 ≤ r ^ 2 := Nat.zero_le _
    nlinarith
  rcases hq_dvd_or with h | h
  · have hle : q ≤ k - r := Nat.le_of_dvd hkr_pos h
    omega
  · have hle : q ≤ k + r + 1 := Nat.le_of_dvd hkr1_pos h
    omega
have hq_ge : 2 * k + 1 ≤ q := by
  rw [hq_eq_r]
  omega
have hq_sq_le : q ^ 2 ≤ f k := by
  have := hq_le_sqrt
  rwa [Nat.le_sqrt] at this
have hineq : (2 * k + 1) ^ 2 ≤ f k := by
  have hsq : (2 * k + 1) ^ 2 ≤ q ^ 2 := by
    nlinarith [hq_ge, Nat.zero_le (2 * k + 1), Nat.zero_le q]
  exact le_trans hsq hq_sq_le
have hp_gt : 3 * k ^ 2 < p := by
  rw [hf k] at hineq
  nlinarith
have hk_le_floor : k ≤ Nat.floor (Real.sqrt (p / 3)) := by
  apply Nat.le_floor.mpr
  have hp_gtR : (3 : ℝ) * (k : ℝ) ^ 2 < (p : ℝ) := by
    exact_mod_cast hp_gt
  have hk_sq_le : (k : ℝ) ^ 2 ≤ (p : ℝ) / 3 := by
    nlinarith
  calc
    (k : ℝ) = Real.sqrt ((k : ℝ) ^ 2) := by rw [Real.sqrt_sq (by positivity)]
    _ ≤ Real.sqrt ((p : ℝ) / 3) := Real.sqrt_le_sqrt hk_sq_le
exact hk_not (hp k hk_le_floor)
```

</details>

**Attempt 3:** `sanitize:syntax` (class `syntax`) — no Lean messages

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
intro i hi
by_contra hprime
have hf : ∀ x, f x = x ^ 2 + x + p := by assumption
have hp : ∀ k, k ≤ Nat.floor (Real.sqrt (p / 3)) → Nat.Prime (f k) := by assumption
let P : ℕ → Prop := fun n => n ≤ p - 2 ∧ ¬ Nat.Prime (f n)
have hex : ∃ n, P n := ⟨i, hi, hprime⟩
set k := Nat.find hex with hk_def
have hkP : P k := by
  rw [hk_def]
  exact Nat.find_spec hex
have hk_le : k ≤ p - 2 := hkP.1
have hk_not : ¬ Nat.Prime (f k) := hkP.2
have hk_min : ∀ j < k, ¬ P j := by
  intro j hj
  rw [hk_def] at hj
  exact Nat.find_min hex hj
have hk_min_prime : ∀ j < k, j ≤ p - 2 → Nat.Prime (f j) := by
  intro j hj hle
  by_contra hjp
  exact hk_min j hj ⟨hle, hjp⟩
have hk_pos : 0 < k := by
  rcases Nat.eq_zero_or_pos k with hk0 | hkpos
  · exfalso
    have hprime0 : Nat.Prime (f 0) := hp 0 (Nat.zero_le _)
    exact hk_not (by simpa [hk0] using hprime0)
  · exact hkpos
have hp0 : Nat.Prime p := by
  have h := hp 0 (Nat.zero_le _)
  have hf0 : f 0 = p := by simpa using hf 0
  simpa [hf0] using h
have hp_ge2 : 2 ≤ p := hp0.two_le
have hkp2 : k + 2 ≤ p := by
  have h := Nat.add_le_add_right hk_le 2
  simpa [Nat.sub_add_cancel hp_ge2] using h
have hk_lt_p : k < p := by omega
have hfk_ge2 : 2 ≤ f k := by
  rw [hf k]
  have hk2 : 0 ≤ k ^ 2 := Nat.zero_le _
  omega
have h_exists_div : ∃ m, 2 ≤ m ∧ m ≤ Nat.sqrt (f k) ∧ m ∣ f k := by
  by_contra hno
  push_neg at hno
  have hprimefk : Nat.Prime (f k) := by
    rw [Nat.prime_def_le_sqrt]
    refine ⟨hfk_ge2, ?_⟩
    intro m hm1 hm2 hm3
    exact hno m hm1 hm2 hm3
  exact hk_not hprimefk
obtain ⟨m, hm_ge2, hm_le_sqrt, hm_dvd⟩ := h_exists_div
have hm_ne_one : m ≠ 1 := by omega
obtain ⟨q, hq_prime, hq_dvd_m⟩ := Nat.exists_prime_and_dvd hm_ne_one
have hq_dvd : q ∣ f k := dvd_trans hq_dvd_m hm_dvd
have hm
```

</details>

**Context:** formal statement proved by other configurations: none.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 8. `imo_1993_p5` (algebra, hard)

### Problem (informal)

Let $\mathbb{N} = \{1,2,3, \ldots\}$. Determine if there exists a strictly increasing function $f: \mathbb{N} \mapsto \mathbb{N}$ with the following properties:

(i) $f(1) = 2$;

(ii) $f(f(n)) = f(n) + n, (n \in \mathbb{N})$.

### Formal statement (what Lean had to prove)

```lean
theorem imo_1993_p5 : ∃ f : ℕ → ℕ, f 1 = 2 ∧ ∀ n, f (f n) = f n + n ∧ ∀ n, f n < f (n + 1) := by
```

### C2's solution (natural language)

Yes, such a function exists. Let \[ \varphi=\frac{1+\sqrt5}{2}, \] so that \varphi^2=\varphi+1 and \varphi-1=1/\varphi. Define \[ f(n)=\left\lfloor \varphi n+\frac12\right\rfloor \qquad(n\in\mathbb N). \]

First, \[ f(1)=\left\lfloor \varphi+\frac12\right\rfloor=2, \] since \varphi=1.618\ldots, so \varphi+1/2\in(2,3).

Next, f is strictly increasing. For any n, write y=\varphi n+1/2. Since \varphi>1, we have y+\varphi>y+1, hence \[ \left\lfloor y+\varphi\right\rfloor\ge \left\lfloor y\right\rfloor+1. \] Therefore \[ f(n+1)=\left\lfloor \varphi(n+1)+\frac12\right\rfloor \ge \left\lfloor \varphi n+\frac12\right\rfloor+1=f(n)+1, \] so f is strictly increasing.

It remains to prove the identity. Fix n\in\mathbb N and set m=f(n). Then m is the nearest integer to \varphi n, so \[ m-\frac12<\varphi n<m+\frac12, \] because \varphi n is irrational. Thus \[ |m-\varphi n|<\frac12. \] Now compute \[ \varphi m-(m+n)=(\varphi-1)m-n=\frac{m}{\varphi}-n=\frac{m-\varphi n}{\varphi}. \] Hence \[ |\varphi m-(m+n)|<\frac{1}{2\varphi}<\frac12. \] Therefore \[ (m+n)-\frac12<\varphi m<(m+n)+\frac12, \] so adding 1/2 gives \[ m+n<\varphi m+\frac12<m+n+1. \] Taking floors, \[ \left\lfloor \varphi m+\frac12\right\rfloor=m+n. \] Since m=f(n), this is exactly \[ f(f(n))=f(m)=m+n=f(n)+n. \] Thus the required function exists.

**Final answer:** Yes. An example is \(f(n)=\left\lfloor \frac{1+\sqrt5}{2}\,n+\frac12\right\rfloor\), which satisfies \(f(1)=2\), is strictly increasing, and obeys \(f(f(n))=f(n)+n\).

**C2's self-assessment:** verdict `correct`, confidence 0.99; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `tactic_failure`)

```text
line 14: Invalid pattern variable: Variable name must be atomic, but `Nat.totient._@.3120910823._hygCtx._hyg.219` has multiple components

line 15: No goals to be solved
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
have hsqrt_gt2 : (2 : ℝ) < Real.sqrt 5 := by
  exact (Real.lt_sqrt (by norm_num : (0 : ℝ) ≤ 2)).mpr (by norm_num)
have hsqrt_lt3 : Real.sqrt 5 < 3 := by
  nlinarith [Real.sq_sqrt (show (0 : ℝ) ≤ 5 by norm_num), Real.sqrt_nonneg 5, sq_nonneg (Real.sqrt 5 - 3)]
let φ : ℝ := (1 + Real.sqrt 5) / 2
let f : ℕ → ℕ := fun n => Nat.floor (φ * (n : ℝ) + 1 / 2)
have hφ_sq : φ ^ 2 = φ + 1 := by
  have hsq : (Real.sqrt 5) ^ 2 = 5 := Real.sq_sqrt (by norm_num)
  dsimp [φ]
  nlinarith [hsq]
have hφ_gt1 : 1 < φ := by
  dsimp [φ]
  linarith [hsqrt_gt2]
have hφ_lt2 : φ < 2 := by
  dsimp [φ]
  linarith [hsqrt_lt3]
have hφpos : 0 < φ := lt_trans zero_lt_one hφ_gt1
refine ⟨f, ?_, ?_, ?_⟩
· dsimp [f]
  have hxpos : (0 : ℝ) ≤ φ * (1 : ℝ) + 1 / 2 := by nlinarith [hφpos]
  rw [Nat.floor_eq_iff hxpos]
  constructor
  · dsimp [φ]
    linarith [hsqrt_gt2]
  · linarith [hφ_lt2]
· intro n
  have hfloor_le : (f n : ℝ) ≤ φ * (n : ℝ) + 1 / 2 := by
    dsimp [f]
    apply Nat.floor_le
    have hnnonneg : (0 : ℝ) ≤ n := by exact_mod_cast Nat.zero_le n
    nlinarith [mul_nonneg (le_of_lt hφpos) hnnonneg]
  have hlt : (f n : ℝ) + 1 < φ * ((n : ℝ) + 1) + 1 / 2 := by
    nlinarith [hfloor_le, hφ_gt1]
  have hle : f n + 1 ≤ f (n + 1) := by
    dsimp [f]
    apply Nat.le_floor
    exact_mod_cast le_of_lt hlt
  omega
· intro n
  let m : ℕ := f n
  have hmlow : (m : ℝ) ≤ φ * (n : ℝ) + 1 / 2 := by
    dsimp [m, f]
    apply Nat.floor_le
    have hnnonneg : (0 : ℝ) ≤ n := by exact_mod_cast Nat.zero_le n
    nlinarith [mul_nonneg (le_of_lt hφpos) hnnonneg]
  have hmup : φ * (n : ℝ) + 1 / 2 < (m : ℝ) + 1 := by
    dsimp [m, f]
    exact Nat.lt_floor_add_one _
  have hnum : 0 < (m : ℝ) - φ * (n : ℝ) + φ / 2 := by
    nlinarith [hmup, hφ_gt1]
  have hDmul : φ * (φ * (m : ℝ) + 1 / 2 - ((m : ℝ) + (n : ℝ))) = (m : ℝ) - φ * (n : ℝ) + φ / 2 := by
    nlinarith [hφ_sq]
  have hDpos : 0 < φ * (m : ℝ) + 1 / 2 - ((m : ℝ) + (n : ℝ)) := by
    have hmulpos : 0 < φ * (φ * (m : ℝ) + 1 / 2 - ((m : ℝ) + (n : ℝ))) := by
      rw [hDmul]
      exact hnum
    exact pos_of_mul_pos_left hmulpos (le_of_lt hφpos)
  have hnum_le : (m : ℝ) - φ * (n : ℝ) + φ / 2 ≤ (1 + φ) / 2 := by
    linarith [hmlow]
  have hDlt1 : φ * (m : ℝ) + 1 / 2 - ((m : ℝ) + (n : ℝ)) < 1 := by
    have hmul_lt : φ * (φ * (m : ℝ) + 1 / 2 - ((m : ℝ) + (n : ℝ))) < φ * 1 := by
      rw [hDmul]
      nlinarith [hnum_le, hφ_gt1]
    exact (mul_lt_mul_left hφpos).mp hmul_lt
  have hfloor : Nat.floor (φ * (m : ℝ) + 1 / 2) = m + n := by
    have hmnonneg : (0 : ℝ) ≤ m := by exact_mod_cast Nat.zero_le m
    have hxpos : (0 : ℝ) ≤ φ * (m : ℝ) + 1 / 2 := by
      nlinarith [mul_nonneg (le_of_lt hφpos) hmnonneg]
    rw [Nat.floor_eq_iff hxpos]
    constructor
    · push_cast
      linarith [hDpos]
    · push_cast
      linarith [hDlt1]
  simpa [f, m] using hfloor
```

</details>

**Attempt 2:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 3:** `lean:error` (class `tactic_failure`)

```text
line 10: Invalid pattern variable: Variable name must be atomic, but `Nat.totient._@.3120910823._hygCtx._hyg.95` has multiple components

line 11: No goals to be solved
```

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
let φ : ℝ := (1 + Real.sqrt 5) / 2
have hsqrt5 : (Real.sqrt 5) ^ 2 = 5 := by
  rw [Real.sq_sqrt]
  norm_num
have hsqrt5_gt2 : 2 < Real.sqrt 5 := by
  rw [Real.lt_sqrt (by norm_num)]
  norm_num
have hsqrt5_lt3 : Real.sqrt 5 < 3 := by
  rw [Real.sqrt_lt (by norm_num)]
  norm_num
have hφsq : φ ^ 2 = φ + 1 := by
  dsimp [φ]
  field_simp
  nlinarith [hsqrt5]
have hφpos : 0 < φ := by
  dsimp [φ]
  positivity
have hφ32 : (3 : ℝ) / 2 < φ := by
  dsimp [φ]
  nlinarith [hsqrt5_gt2]
have hφ1 : 1 < φ := by linarith
have hφ2 : φ < 2 := by
  dsimp [φ]
  nlinarith [hsqrt5_lt3]
let f : ℕ → ℕ := fun n => Nat.floor (φ * n + 1 / 2)
refine ⟨f, ?_, ?_, ?_⟩
· dsimp [f]
  exact (Nat.floor_eq_iff (by positivity)).2 ⟨by nlinarith [hφ32], by nlinarith [hφ2]⟩
· intro n
  dsimp [f]
  let m : ℕ := Nat.floor (φ * (n : ℝ) + 1 / 2)
  have hmn_nonneg : 0 ≤ φ * (n : ℝ) + 1 / 2 := by positivity
  have hm_le : (m : ℝ) ≤ φ * (n : ℝ) + 1 / 2 := by
    dsimp [m]
    exact Nat.floor_le hmn_nonneg
  have hm_lt : φ * (n : ℝ) + 1 / 2 < (m : ℝ) + 1 := by
    dsimp [m]
    exact Nat.lt_floor_add_one _
  have hd_le : (m : ℝ) - φ * (n : ℝ) ≤ 1 / 2 := by linarith
  have hd_gt : -1 / 2 < (m : ℝ) - φ * (n : ℝ) := by linarith
  have hmul : φ * (φ * (m : ℝ) - ((m : ℝ) + (n : ℝ))) = (m : ℝ) - φ * (n : ℝ) := by
    linear_combination (m : ℝ) * hφsq
  have hφne : φ ≠ 0 := ne_of_gt hφpos
  have he_eq : φ * (m : ℝ) - ((m : ℝ) + (n : ℝ)) = ((m : ℝ) - φ * (n : ℝ)) / φ := by
    rw [eq_div_iff hφne]
    linarith [hmul]
  have hlower : -1 / 2 < φ * (m : ℝ) - ((m : ℝ) + (n : ℝ)) := by
    rw [he_eq, lt_div_iff hφpos]
    nlinarith [hd_gt, hφ1]
  have hupper : φ * (m : ℝ) - ((m : ℝ) + (n : ℝ)) < 1 / 2 := by
    rw [he_eq, div_lt_iff hφpos]
    nlinarith [hd_le, hφ1]
  have hfloor : Nat.floor (φ * (m : ℝ) + 1 / 2) = m + n := by
    exact (Nat.floor_eq_iff (by positivity)).2 ⟨by push_cast; linarith, by push_cast; linarith⟩
  change Nat.floor (φ * (m : ℝ) + 1 / 2) = m + n
  exact hfloor
· intro n
  dsimp [f]
  have hle : Nat.floor (φ * (n : ℝ) + 1 / 2) + 1 ≤ Nat.floor (φ * (n + 1 : ℕ) + 1 / 2) := by
    apply (Nat.le_floor (by positivity)).2
    have hfl : (Nat.floor (φ * (n : ℝ) + 1 / 2) : ℝ) ≤ φ * (n : ℝ) + 1 / 2 := by
      exact Nat.floor_le (by positivity)
    push_cast
    nlinarith [hfl, hφ1]
  exact Nat.lt_of_succ_le hle
```

</details>

**Context:** formal statement proved by other configurations: C3.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 9. `putnam_1965_b2` (combinatorics, hard)

### Problem (informal)

A round-robin tournament has $n > 1$ players $P_1, P_2, \dots, P_n$, who each play one game with each other player. Each game results in a win for one player and a loss for the other. If $w_r$ and $l_r$ denote the number of games won and lost, respectively, by $P_r$, prove that $$\sum_{r=1}^{n} w_r^2 = \sum_{r=1}^{n} l_r^2.$$

### Formal statement (what Lean had to prove)

```lean
theorem putnam_1965_b2
(n : ℕ)
(hn : n > 1)
(won : Fin n → Fin n → Bool)
(hirrefl : ∀ i : Fin n, won i i = false)
(hantisymm : ∀ i j : Fin n, i ≠ j → won i j = ¬won j i)
(w l : Fin n → ℤ)
(hw : w = fun r : Fin n => ∑ j : Fin n, (if won r j then 1 else 0))
(hl : l = fun r : Fin n => n - 1 - w r)
: ∑ r : Fin n, (w r)^2 = ∑ r : Fin n, (l r)^2 := by
```

### C2's solution (natural language)

In a round-robin tournament with n players, every player plays exactly n-1 games, so for each r,

w_r + l_r = n-1.

Also, each game produces exactly one win and exactly one loss. Therefore the total number of wins equals the total number of losses, and both equal the total number of games, namely n(n-1)/2. Hence

∑_{r=1}^n w_r = ∑_{r=1}^n l_r.

Now compute the difference of the two desired sums:

∑_{r=1}^n w_r^2 - ∑_{r=1}^n l_r^2
= ∑_{r=1}^n (w_r^2 - l_r^2)
= ∑_{r=1}^n (w_r-l_r)(w_r+l_r).

Using w_r+l_r=n-1, this becomes

∑_{r=1}^n (w_r-l_r)(n-1)
= (n-1)∑_{r=1}^n (w_r-l_r)
= (n-1)\(∑_{r=1}^n w_r - ∑_{r=1}^n l_r\).

Since ∑ w_r = ∑ l_r, the last expression is 0. Therefore

∑_{r=1}^n w_r^2 = ∑_{r=1}^n l_r^2.

**Final answer:** For every round-robin tournament, \(\sum_{r=1}^{n} w_r^2 = \sum_{r=1}^{n} l_r^2\).

**C2's self-assessment:** verdict `correct`, confidence 0.99; issues it noted: Minor typographical issue: there is a stray backslash before a parenthesis in the line involving (n-1)(sum w_r - sum l_r), but it does not affect the mathematical argument. (1 round(s)).

### Lean check attempts

**Attempt 1:** `lean:error` (class `tactic_failure`)

```text
line 11: No goals to be solved
```

<details><summary>Lean tactics tried in attempt 1</summary>

```lean
let f : Fin n → Fin n → ℤ := fun r j => if won r j then 1 else 0
have hw' : ∀ r : Fin n, w r = ∑ j : Fin n, f r j := by
  intro r
  rw [hw]
  simp [f]
have hl' : ∀ r : Fin n, l r = (n : ℤ) - 1 - w r := by
  intro r
  simpa using congrFun hl r
have hpoint : ∀ r j : Fin n, f r j + f j r = if r = j then 0 else 1 := by
  intro r j
  by_cases h : r = j
  · rw [h]
    simp [f, hirrefl]
  · rw [if_neg h]
    have hh := hantisymm r j h
    cases h1 : won r j <;> cases h2 : won j r <;> simp_all [f]
have hinner : ∀ r : Fin n, ∑ j : Fin n, (f r j + f j r) = (n : ℤ) - 1 := by
  intro r
  calc
    ∑ j : Fin n, (f r j + f j r) = ∑ j : Fin n, (if r = j then (0 : ℤ) else 1) := by
      apply Finset.sum_congr rfl
      intro j _
      exact hpoint r j
    _ = ∑ j : Fin n, (1 - (if r = j then (1 : ℤ) else 0)) := by
      apply Finset.sum_congr rfl
      intro j _
      by_cases h : r = j <;> simp [h]
    _ = ∑ j : Fin n, (1 : ℤ) - ∑ j : Fin n, (if r = j then (1 : ℤ) else 0) := by
      rw [Finset.sum_sub_distrib]
    _ = (n : ℤ) - 1 := by
      rw [Finset.sum_const, Finset.card_univ]
      rw [Finset.sum_ite_eq]
      simp [Finset.mem_univ]
have hsumpt : ∑ r : Fin n, ∑ j : Fin n, (f r j + f j r) = (n : ℤ) * ((n : ℤ) - 1) := by
  calc
    ∑ r : Fin n, ∑ j : Fin n, (f r j + f j r) = ∑ r : Fin n, ((n : ℤ) - 1) := by
      apply Finset.sum_congr rfl
      intro r _
      exact hinner r
    _ = (n : ℤ) * ((n : ℤ) - 1) := by
      rw [Finset.sum_const, Finset.card_univ]
      simp [nsmul_eq_mul]
have hswap : (∑ r : Fin n, ∑ j : Fin n, f r j) = ∑ r : Fin n, ∑ j : Fin n, f j r := by
  simpa using (Finset.sum_comm : (∑ r : Fin n, ∑ j : Fin n, f r j) = ∑ j : Fin n, ∑ r : Fin n, f r j)
have hdouble : 2 * ∑ r : Fin n, w r = ∑ r : Fin n, ∑ j : Fin n, (f r j + f j r) := by
  calc
    2 * ∑ r : Fin n, w r = 2 * ∑ r : Fin n, ∑ j : Fin n, f r j := by
      congr 1
      apply Finset.sum_congr rfl
      intro r _
      exact hw' r
    _ = (∑ r : Fin n, ∑ j : Fin n, f r j) + (∑ r : Fin n, ∑ j : Fin n, f r j) := by
      ring
    _ = (∑ r : Fin n, ∑ j : Fin n, f r j) + (∑ r : Fin n, ∑ j : Fin n, f j r) := by
      rw [hswap]
    _ = ∑ r : Fin n, ∑ j : Fin n, (f r j + f j r) := by
      rw [← Finset.sum_add_distrib]
      apply Finset.sum_congr rfl
      intro r _
      rw [← Finset.sum_add_distrib]
have hdouble2 : 2 * ∑ r : Fin n, w r = (n : ℤ) * ((n : ℤ) - 1) := by
  rw [hdouble, hsumpt]
have hsum : ∑ r : Fin n, w r = ∑ r : Fin n, l r := by
  simp only [hl']
  rw [Finset.sum_sub_distrib]
  have hconst : ∑ r : Fin n, ((n : ℤ) - 1) = (n : ℤ) * ((n : ℤ) - 1) := by
    rw [Finset.sum_const, Finset.card_univ]
    simp [nsmul_eq_mul]
  rw [hconst]
  linarith [hdouble2]
have hdiff : ∑ r : Fin n, ((w r)^2 - (l r)^2) = 0 := by
  calc
    ∑ r : Fin n, ((w r)^2 - (l r)^2) = ∑ r : Fin n, ((w r - l r) * (w r + l r)) := by
      apply Finset.sum_congr rfl
      intro r _
      ring
    _ = ∑ r : Fin n, ((w r - l r) * ((n : ℤ) - 1)) := by
      apply Finset.sum_congr rfl
      intro r _
      rw [hl' r]
      ring
    _ = ((n : ℤ) - 1) * ∑ r : Fin n, (w r - l r) := by
      rw [Finset.mul_sum]
      apply Finset.sum_congr rfl
      intro r _
      ring
    _ = ((n : ℤ) - 1) * (∑ r : Fin n, w r - ∑ r : Fin n, l r) := by
      rw [Finset.sum_sub_distrib]
    _ = 0 := by
      rw [hsum]
      ring
have hzero : ∑ r : Fin n, (w r)^2 - ∑ r : Fin n, (l r)^2 = 0 := by
  rw [← Finset.sum_sub_distrib]
  exact hdiff
linarith
```

</details>

**Attempt 2:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 3:** `lean:error` (class `tactic_failure`)

```text
line 33: Application type mismatch: The argument
  h
has type
  ¬j = r
but is expected to have type
  r ≠ j
in the application
  hantisymm r j h
```

<details><summary>Lean tactics tried in attempt 3</summary>

```lean
have hl_eq : ∀ r : Fin n, l r = ∑ j : Fin n, (if won j r then (1 : ℤ) else 0) := by
  intro r
  rw [hl, hw]
  have hsum_diag : ∑ j : Fin n, (if j = r then (0 : ℤ) else 1) = (n : ℤ) - 1 := by
    calc
      ∑ j : Fin n, (if j = r then (0 : ℤ) else 1)
          = ∑ j : Fin n, ((1 : ℤ) - (if j = r then (1 : ℤ) else 0)) := by
            apply Finset.sum_congr rfl
            intro j hj
            by_cases h : j = r <;> simp [h]
      _ = ∑ j : Fin n, (1 : ℤ) - ∑ j : Fin n, (if j = r then (1 : ℤ) else 0) := by
            rw [Finset.sum_sub_distrib]
      _ = (n : ℤ) - 1 := by
            simp [Finset.sum_ite_eq', Finset.card_univ]
  calc
    (n : ℤ) - 1 - ∑ j : Fin n, (if won r j then (1 : ℤ) else 0)
        = ∑ j : Fin n, (if j = r then (0 : ℤ) else 1) - ∑ j : Fin n, (if won r j then (1 : ℤ) else 0) := by
          rw [hsum_diag]
    _ = ∑ j : Fin n, ((if j = r then (0 : ℤ) else 1) - (if won r j then (1 : ℤ) else 0)) := by
          rw [Finset.sum_sub_distrib]
    _ = ∑ j : Fin n, (if won j r then (1 : ℤ) else 0) := by
          apply Finset.sum_congr rfl
          intro j hj
          by_cases h : j = r
          · rw [h]
            simp [hirrefl]
          · have hb := hantisymm r j h
            cases hwj : won j r <;> simp [h, hb, hwj]
have hsum : ∑ r : Fin n, l r = ∑ r : Fin n, w r := by
  calc
    ∑ r : Fin n, l r = ∑ r : Fin n, ∑ j : Fin n, (if won j r then (1 : ℤ) else 0) := by
      apply Finset.sum_congr rfl
      intro r hr
      exact hl_eq r
    _ = ∑ j : Fin n, ∑ r : Fin n, (if won j r then (1 : ℤ) else 0) := by
      rw [Finset.sum_comm]
    _ = ∑ r : Fin n, w r := by
      simpa [hw]
have hfac : ∀ r : Fin n, w r ^ 2 - l r ^ 2 = (w r - l r) * ((n : ℤ) - 1) := by
  intro r
  have hwl : w r + l r = (n : ℤ) - 1 := by
    rw [hl]
    ring
  calc
    w r ^ 2 - l r ^ 2 = (w r - l r) * (w r + l r) := by ring
    _ = (w r - l r) * ((n : ℤ) - 1) := by rw [hwl]
have hmain : ∑ r : Fin n, (w r ^ 2 - l r ^ 2) = 0 := by
  calc
    ∑ r : Fin n, (w r ^ 2 - l r ^ 2) = ∑ r : Fin n, ((w r - l r) * ((n : ℤ) - 1)) := by
      apply Finset.sum_congr rfl
      intro r hr
      exact hfac r
    _ = (∑ r : Fin n, (w r - l r)) * ((n : ℤ) - 1) := by
      rw [Finset.sum_mul]
    _ = 0 := by
      have hs : ∑ r : Fin n, (w r - l r) = 0 := by
        rw [Finset.sum_sub_distrib, hsum]
        ring
      rw [hs]
      ring
have hsub : ∑ r : Fin n, w r ^ 2 - ∑ r : Fin n, l r ^ 2 = 0 := by
  rw [← Finset.sum_sub_distrib]
  exact hmain
exact sub_eq_zero.mp hsub
```

</details>

**Context:** formal statement proved by other configurations: C1, C3, C5.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**


---

## 10. `putnam_1995_b1` (combinatorics, hard)

### Problem (informal)

For a partition $\pi$ of $\{1, 2, 3, 4, 5, 6, 7, 8, 9\}$, let $\pi(x)$ be the number of elements in the part containing $x$. Prove that for any two partitions $\pi$ and $\pi'$, there are two distinct numbers $x$ and $y$ in $\{1, 2, 3, 4, 5, 6, 7, 8, 9\}$ such that $\pi(x) = \pi(y)$ and $\pi'(x) = \pi'(y)$. [A {\em partition} of a set $S$ is a collection of disjoint subsets (parts) whose union is $S$.]

### Formal statement (what Lean had to prove)

```lean
theorem putnam_1995_b1
  (Pi : Finpartition (Finset.range 9) → (Finset.range 9) → ℕ)
  (Pi_def : ∀ partition k, Pi partition k = (Exists.choose (Finpartition.exists_mem partition k.2)).card) :
  ∀ Pt1 Pt2,
    ∃ x y : Finset.range 9,
      x ≠ y ∧
      Pi Pt1 x = Pi Pt1 y ∧
      Pi Pt2 x = Pi Pt2 y := by
```

### C2's solution (natural language)

Proof. Let S={1,2,3,4,5,6,7,8,9}. For each x∈S, consider the ordered pair P(x)=(π(x),π'(x)). Suppose, for contradiction, that no two distinct elements have the same ordered pair. Then P is injective. Let R be the set of distinct part sizes occurring in π, and let C be the set of distinct part sizes occurring in π'. Then P(x)∈R×C for every x, so 9=|S|≤|R||C|.

We claim |R|≤3. Indeed, if R contained four distinct positive integers, their sum would be at least 1+2+3+4=10, but the sum of all part sizes in π is 9, so this is impossible. Thus |R|≤3. Similarly, |C|≤3.

Hence 9≤|R||C|≤9, so |R|=|C|=3 and P is a bijection from S onto R×C.

Now fix a∈R. Let n_a be the number of parts of π of size a. The number of elements x with π(x)=a is a n_a, because these elements are exactly the union of those n_a parts. On the other hand, since P is a bijection onto R×C, for each b∈C there is exactly one x with P(x)=(a,b). Therefore exactly |C|=3 elements satisfy π(x)=a. Hence a n_a=3 for every a∈R. Thus every a∈R divides 3, so R⊆{1,3}; in particular |R|≤2, contradicting |R|=3.

This contradiction shows that P is not injective. Therefore there exist distinct x,y∈S such that P(x)=P(y), i.e. π(x)=π(y) and π'(x)=π'(y).

**Final answer:** For any two partitions π and π' of {1,2,3,4,5,6,7,8,9}, there exist distinct x,y such that π(x)=π(y) and π'(x)=π'(y).

**C2's self-assessment:** verdict `correct`, confidence 1.0; no issues noted (1 round(s)).

### Lean check attempts

**Attempt 1:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 2:** `sanitize:empty` (class `syntax`) — no Lean messages

**Attempt 3:** `sanitize:empty` (class `syntax`) — no Lean messages

**Context:** formal statement proved by other configurations: none.

### Verdict

- [ ] Wrong argument
- [ ] Formalisation failure
- [ ] Cannot decide

**Reason:**

