"""Offline tests of formal_mas.analysis.stats against values known in closed form."""

from __future__ import annotations

import math

import numpy as np
import pytest

from formal_mas.analysis import stats


def wilson_closed_form(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z / (1 + z * z / n) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return centre - half, centre + half


# ----------------------------------------------------------------------------- Wilson
@pytest.mark.parametrize("k,n", [(24, 30), (20, 30), (14, 30), (0, 30), (30, 30), (1, 2), (11, 30)])
def test_wilson_matches_closed_form(k, n):
    lo, hi = stats.wilson(k, n)
    elo, ehi = wilson_closed_form(k, n)
    assert lo == pytest.approx(elo, abs=1e-12)
    assert hi == pytest.approx(ehi, abs=1e-12)


def test_wilson_known_values():
    # 24/30 → [0.6269, 0.9049]; 30/30 upper bound is exactly 1; 0/30 lower bound exactly 0.
    lo, hi = stats.wilson(24, 30)
    assert (round(lo, 4), round(hi, 4)) == (0.6269, 0.9049)
    assert stats.wilson(30, 30)[1] == pytest.approx(1.0)
    assert stats.wilson(0, 30)[0] == pytest.approx(0.0, abs=1e-15)


def test_wilson_empty_is_nan():
    lo, hi = stats.wilson(0, 0)
    assert math.isnan(lo) and math.isnan(hi)


# ----------------------------------------------------------------------------- McNemar
def paired(b: int, c: int, both: int = 0, neither: int = 0):
    a = [True] * b + [False] * c + [True] * both + [False] * neither
    o = [False] * b + [True] * c + [True] * both + [False] * neither
    return np.array(a), np.array(o)


@pytest.mark.parametrize("b,c,p", [
    (4, 0, 2 / 16),            # 2 · (1/2)^4
    (10, 0, 2 / 1024),         # 2 · (1/2)^10
    (5, 1, 2 * 7 / 64),        # 2 · (C(6,0) + C(6,1)) / 2^6
    (3, 3, 1.0),               # symmetric
    (9, 3, 2 * 299 / 4096),    # 2 · (1 + 12 + 66 + 220) / 2^12
    (1, 0, 1.0),               # 2 · 1/2, capped at 1
])
def test_mcnemar_exact_known_p(b, c, p):
    a, o = paired(b, c, both=7, neither=5)
    nb, nc, got = stats.mcnemar_exact(a, o)
    assert (nb, nc) == (b, c)
    assert got == pytest.approx(p, rel=1e-12)


def test_mcnemar_no_discordant_pairs():
    a, o = paired(0, 0, both=4, neither=3)
    assert stats.mcnemar_exact(a, o) == (0, 0, 1.0)


def test_mcnemar_is_symmetric_in_p():
    a, o = paired(7, 2, both=10)
    assert stats.mcnemar_exact(a, o)[2] == pytest.approx(stats.mcnemar_exact(o, a)[2])
    assert stats.mcnemar_exact(o, a)[:2] == (2, 7)


def test_mcnemar_rejects_unpaired_input():
    with pytest.raises(ValueError):
        stats.mcnemar_exact([True, False], [True])


# ----------------------------------------------------------------------------- Bonferroni
def test_bonferroni():
    assert stats.bonferroni(0.002, 3) == pytest.approx(0.006)
    assert stats.bonferroni(0.4, 3) == 1.0
    assert stats.bonferroni(0.01, 1) == 0.01
    with pytest.raises(ValueError):
        stats.bonferroni(0.01, 0)


def test_bonferroni_reject_threshold():
    assert stats.bonferroni_reject(0.0166, 3)
    assert not stats.bonferroni_reject(0.0167, 3)
    assert stats.bonferroni_reject(0.049, 1, alpha=0.05)


# ----------------------------------------------------------------------------- bootstrap
def test_bootstrap_index_is_seeded_and_fresh():
    a = stats.bootstrap_index(30, n_boot=50, seed=2026)
    b = stats.bootstrap_index(30, n_boot=50, seed=2026)
    assert a.shape == (50, 30)
    assert (a == b).all()
    assert a.min() >= 0 and a.max() < 30
    assert not (stats.bootstrap_index(30, n_boot=50, seed=1) == a).all()


def test_paired_boot_diff_degenerate_cases():
    x = np.array([1, 0, 1, 1, 0], dtype=float)
    assert stats.paired_boot_diff(x, x) == (0.0, 0.0)
    ones, zeros = np.ones(8), np.zeros(8)
    assert stats.paired_boot_diff(ones, zeros) == (1.0, 1.0)


def test_paired_boot_diff_contains_point_estimate():
    rng = np.random.default_rng(0)
    a = rng.random(30) < 0.8
    b = rng.random(30) < 0.5
    lo, hi = stats.paired_boot_diff(a, b)
    assert lo <= a.mean() - b.mean() <= hi


def test_boot_ratio_constant_ratio():
    calls = np.array([4, 4, 4, 4], dtype=float)
    solved = np.ones(4)
    assert stats.boot_ratio(calls, solved) == (4.0, 4.0, 0)


def test_boot_ratio_counts_dropped_resamples():
    calls = np.array([10.0] + [3.0] * 3)
    solved = np.array([1.0, 0.0, 0.0, 0.0])
    lo, hi, dropped = stats.boot_ratio(calls, solved, n_boot=2000)
    # P(no solved problem in a resample of 4) = (3/4)^4 ≈ 0.316
    assert 0.25 < dropped / 2000 < 0.39
    assert 10.0 <= lo <= hi
    lo, hi, dropped = stats.boot_ratio(np.ones(3), np.zeros(3), n_boot=10)
    assert math.isnan(lo) and math.isnan(hi) and dropped == 10


def test_boot_mean_constant_and_bounds():
    assert stats.boot_mean(np.full(10, 2.5)) == (2.5, 2.5)
    x = np.arange(20, dtype=float)
    lo, hi = stats.boot_mean(x)
    assert x.min() <= lo <= x.mean() <= hi <= x.max()


def test_percentile_tails_are_exact():
    # 0.95 must map to the 2.5 / 97.5 percentiles exactly (results are compared byte for byte).
    x = np.arange(1001, dtype=float)
    assert stats._percentile(x, 0.95) == (25.0, 975.0)


def test_quartiles():
    assert stats.quartiles([1, 2, 3, 4, 5]) == (3.0, 2.0, 4.0)
    assert stats.quartiles([7]) == (7.0, 7.0, 7.0)
