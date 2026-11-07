"""Statistics of the pre-registered analysis plan (docs/analysis_plan.md).

Wilson score interval, exact two-sided McNemar test, Bonferroni correction and the percentile
bootstrap used for paired differences, ratios and means. Pure functions: no I/O, no global
random state (every bootstrap draws from a fresh generator seeded with ``seed``, so every
statistic sees the same resamples of problems).

Exact binomial and Wilson come from ``scipy.stats.binomtest``; the bootstrap uses
``numpy.random.default_rng``.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import binomtest

SEED = 2026        # bootstrap seed (the run seed, pipeline.yaml → run.seed)
N_BOOT = 10_000    # bootstrap resamples
ALPHA = 0.05       # family-wise significance level


def wilson(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for k successes out of n; (nan, nan) when n = 0."""
    if n == 0:
        return float("nan"), float("nan")
    ci = binomtest(k, n).proportion_ci(confidence_level=confidence, method="wilson")
    return float(ci.low), float(ci.high)


def mcnemar_exact(a, b) -> tuple[int, int, float]:
    """Exact two-sided McNemar test on paired booleans (one entry per problem).

    Returns (b, c, p): b = A true and B false, c = A false and B true, and p the two-sided
    exact binomial p-value of b given b + c with probability 1/2 (p = 1 when b + c = 0).
    """
    a, b_ = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    if a.shape != b_.shape:
        raise ValueError(f"paired arrays differ in shape: {a.shape} vs {b_.shape}")
    nb, nc = int(np.sum(a & ~b_)), int(np.sum(~a & b_))
    p = 1.0 if nb + nc == 0 else binomtest(nb, nb + nc, 0.5, alternative="two-sided").pvalue
    return nb, nc, float(p)


def bonferroni(p: float, m: int) -> float:
    """Bonferroni-adjusted p-value for one of m tests: min(1, m·p)."""
    if m < 1:
        raise ValueError("m must be >= 1")
    return min(1.0, m * p)


def bonferroni_reject(p: float, m: int, alpha: float = ALPHA) -> bool:
    """True when the raw p-value is below alpha / m (same as bonferroni(p, m) < alpha)."""
    return p < alpha / m


def bootstrap_index(n: int, n_boot: int = N_BOOT, seed: int = SEED) -> np.ndarray:
    """(n_boot, n) indices of problems drawn with replacement, from a fresh generator."""
    return np.random.default_rng(seed).integers(0, n, size=(n_boot, n))


def _percentile(x: np.ndarray, confidence: float) -> tuple[float, float]:
    tail = round(50 * (1 - confidence), 10)  # 0.95 → exactly 2.5 (no float residue)
    lo, hi = np.percentile(x, [tail, 100 - tail])
    return float(lo), float(hi)


def paired_boot_diff(a, b, n_boot: int = N_BOOT, seed: int = SEED,
                     confidence: float = 0.95) -> tuple[float, float]:
    """Percentile CI of mean(a) − mean(b), resampling problems (pairs kept together)."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    idx = bootstrap_index(len(a), n_boot, seed)
    return _percentile(a[idx].mean(axis=1) - b[idx].mean(axis=1), confidence)


def boot_ratio(num, solved, n_boot: int = N_BOOT, seed: int = SEED,
               confidence: float = 0.95) -> tuple[float, float, int]:
    """Percentile CI of sum(num) / sum(solved), e.g. calls per solved problem.

    Resamples with no solved problem are dropped; their count is the third value.
    """
    num, solved = np.asarray(num, dtype=float), np.asarray(solved, dtype=float)
    idx = bootstrap_index(len(num), n_boot, seed)
    s = solved[idx].sum(axis=1)
    t = num[idx].sum(axis=1)
    ok = s > 0
    if not ok.any():
        return float("nan"), float("nan"), int((~ok).sum())
    lo, hi = _percentile(t[ok] / s[ok], confidence)
    return lo, hi, int((~ok).sum())


def boot_mean(x, n_boot: int = N_BOOT, seed: int = SEED,
              confidence: float = 0.95) -> tuple[float, float]:
    """Percentile CI of the mean of x."""
    x = np.asarray(x, dtype=float)
    idx = bootstrap_index(len(x), n_boot, seed)
    return _percentile(x[idx].mean(axis=1), confidence)


def quartiles(x) -> tuple[float, float, float]:
    """(median, Q1, Q3) with numpy's default (linear) percentile method."""
    q1, med, q3 = np.percentile(np.asarray(x, dtype=float), [25, 50, 75])
    return float(med), float(q1), float(q3)
