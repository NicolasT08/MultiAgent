# Generic olympiad lemmas: qualitative retrieval check (2026-10-19)

Qualitative evidence for the `generic_query` method (Phase 1.5); details and the
diagnosis in `olympiad_probes.json`. **Not a statistical evaluation**: 7 hand-picked
probes, and the type variable `R` was chosen looking at the first two.

- **V0**: RRF(BM25, E5-base with instruction), the Phase 1.4 choice.
- **V1**: V0 plus a second query with concrete carriers replaced by `R`
  (`generalize_query`), four rankings fused by RRF.
- Index: `data/index/full`. Rank of the first gold lemma; "—" = not in the fused list (beyond ~100).

| Query (concrete) | Gold lemma | V0 | V1 |
|---|---|---|---|
| `(x : ℝ) : 0 ≤ x ^ 2` | `sq_nonneg` | — | — |
| `(a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2` | `two_mul_le_add_sq` | 68 | **13** |
| `(a b : ℝ) : \|a + b\| ≤ \|a\| + \|b\|` | `abs_add_le` | 6 | 3 |
| `(a b : ℝ) (ha : 0 ≤ a) (hab : a ≤ b) : a ^ 2 ≤ b ^ 2` | `pow_le_pow_left₀` | 16 | 15 |
| `(a b c : ℤ) (h₁ : a ∣ b) (h₂ : b ∣ c) : a ∣ c` | `dvd_trans` / `Dvd.dvd.trans` | — | — |
| `(a b : ℤ) (n : ℕ) : a - b ∣ a ^ n - b ^ n` | `sub_dvd_pow_sub_pow` | — | **15** |
| `(n : ℕ) (f : ℕ → ℝ) : ∑ i ∈ range (n+1), f i = …` | `Finset.sum_range_succ` | 1 | 1 |

In the top 16 (the system's k): V0 3/7, V1 5/7.

**Diagnosis (original query, each ranker alone, whole index).** `sq_nonneg`: BM25 2255,
E5 658. `two_mul_le_add_sq`: BM25 151, E5 33. The token ℝ (BM25 idf 2.56) favours every
`Real.*` lemma; generic signatures are long (typeclass binders), which BM25 length
normalisation penalises; and the E5 embedding of a query with ℝ lands among `Real.*` lemmas.

**Limitations.**
- `sq_nonneg` is still missed: the query shares no words with its name (`sq`, `nonneg`)
  and uses `x` where the lemma uses `a`.
- `dvd_trans` is still missed: the lemma states its hypotheses as implications
  (`a ∣ b → b ∣ c → a ∣ c`), the query as named binders, and the signature is short and generic.
- Future work (not adopted): adding Mathlib naming-convention words (`0 ≤` → nonneg,
  `^ 2` → sq, `∣` → dvd) reached `sq_nonneg` at 16 and `dvd_trans` at 43 in an exploratory
  run, but lowered Q-dev R@16 (0.627 vs 0.634) and its rules were written after seeing these probes.

**Reference set Q.** Q-dev (n = 134, 40 generalised queries): R@16 0.634 → 0.642,
MRR 0.319 → 0.323 (McNemar on R@16: 1 win / 0 losses, p = 1.0). Single Q-test
re-evaluation after adoption (n = 66, 20 generalised): R@k unchanged for every k,
MRR 0.333 → 0.322 (ΔMRR 95 % bootstrap CI [−0.032, +0.001]; 3 ℕ-only queries moved,
e.g. `Nat.clog_zero_right` 1 → 3). Q consists of Mathlib theorems, 70 % already generic,
so it under-represents the concrete-type subgoals this method targets.

**Latency** (CPU, Intel Core family 6 model 158, 8 threads; full index; 141 queries × 3 rounds):
median 165 → 206 ms overall (p90 229 → 295); on generalised queries 128 → 256 ms (p90 164 → 333);
unchanged on the rest.
