"""lean/syntax_check.py with the real Lean parser (`pytest -m slow`): every attack is still
rejected, the valid Lean 4 blocks of test_sanitize.py and ~200 olympiad tactic blocks pass,
and the parser layer catches what the text blacklist cannot see."""

from __future__ import annotations

from pathlib import Path

import pytest

from formal_mas.lean.runner import LeanRunner
from formal_mas.lean.statement import DEFAULT_HEADER
from formal_mas.lean.syntax_check import SyntaxChecker
from test_sanitize import ATTACKS, VALID_BLOCKS, VALID_TERMS

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (ROOT / "lean_repl" / ".lake" / "build" / "bin").exists(), reason="Lean REPL not built"),
]

# `#s` card notation needs `open Finset`, which DEFAULT_HEADER lacks: it would not compile either.
NEEDS_OPEN_FINSET = {b for b in VALID_BLOCKS if "#(" in b} | {t for t in VALID_TERMS if "#(" in t}

OLYMPIAD = [
    "intro x y", "rintro ⟨x, hx⟩ (h | h)", "obtain ⟨x, hx, hx'⟩ := h", "rcases h with ⟨a, b⟩ | c",
    "induction' n with n ih", "induction n generalizing m with\n| zero => rfl\n| succ n ih => simp",
    "use 3, 4", "exists 2", "refine ⟨_, ?_⟩", "refine' ⟨_, _⟩", "exact (h 2).le", "apply mul_pos <;> positivity",
    "have h3 : x = 2 := by linarith", "set y := x + 1 with hy", "show x = 2", "suffices h : x = 2 by linarith",
    "calc x = 2 * 1 := by ring\n  _ = 2 := by norm_num", "conv_lhs => rw [h]", "rw [← h, mul_comm] at h2 ⊢",
    "nth_rw 1 [h]", "simp only [h, mul_comm] at *", "simpa [h] using h2", "field_simp [hx.ne'] at h ⊢", "ring_nf",
    "nlinarith [sq_nonneg (x - y), mul_pos hx hy]", "linear_combination 2 * h - h2", "gcongr", "push_cast at h ⊢",
    "push_neg at h", "by_contra! h", "by_cases h : x = 0", "contrapose! h", "exfalso", "interval_cases x",
    "fin_cases h <;> simp", "mod_cases h : n % 3", "exact_mod_cast h", "zify [hx] at h ⊢", "specialize h 2",
    "split_ifs with h", "all_goals linarith", "first | omega | simp", "try simp", "decide", "aesop", "tauto",
    "exact ∑ i ∈ Finset.range 3, i", "exact ⌊x⌋", "exact √x", "exact |x|", "exact x ≡ y [MOD 3]",
    "rcases Nat.even_or_odd n with ⟨k, rfl⟩ | ⟨k, rfl⟩", "obtain ⟨k, hk⟩ : ∃ k, n = 2 * k := ⟨n / 2, by omega⟩",
    "rwa [h] at h2", "rwa [h]", "exact calc x = y := h\n  _ = z := h2", "calc x = y := h\n  _ = z := h2",
    "interval_cases x <;> interval_cases y <;> omega", "constructor <;> intro h <;> linarith",
    "induction n with\n| zero => simp\n| succ n ih =>\n  rw [pow_succ]\n  omega", "exact .inl rfl",
    # parenthesised tactics and goal combinators (missing from the first whitelist: the dev run of
    # 2026-10-29 rejected the cheat sheet's own `(try norm_num at h₀) <;> (try norm_num)` variant)
    "interval_cases x <;> interval_cases y <;> (try norm_num at h₀) <;> (try norm_num)",
    "(simp; omega)", "all_goals (try omega)", "iterate 3 (rw [h])", "done", "skip", "solve | simp | omega",
    "on_goal 2 => simp", "swap", "pick_goal 2", "rotate_left", "repeat' constructor", "rename' h => h'", "nofun",
]
# Pass the text blacklist (no forbidden word or identifier) but are not proofs.
PARSER_ONLY = {
    "polyrith": "search", "simp?": "search", "rw?": "search", "aesop?": "search", "hint": "search",
    "simp? says simp": "search", "trace_state": "syntax", "extract_goal": "syntax", "exact `(1)": "syntax",
    "rwa [(by exact?)]": "search", "exact calc x = y := by exact?\n  _ = z := h": "search",
    "exact ``Nat": "syntax", "exact do pure 1": "syntax", "exact (h": "parse",
}


@pytest.fixture(scope="module")
def checker():
    r = LeanRunner()
    assert r.start(), r.warnings
    yield SyntaxChecker(r)
    r.close()


def test_valid_blocks_and_olympiad_tactics_pass(checker):
    blocks = [b for b in VALID_BLOCKS if b not in NEEDS_OPEN_FINSET] + OLYMPIAD
    bad = [(b, r) for b, r in zip(blocks, checker.check(DEFAULT_HEADER, tactics=blocks)) if not r.ok]
    assert bad == []
    terms = [t for t in VALID_TERMS if t not in NEEDS_OPEN_FINSET]
    assert all(r.ok for r in checker.check(DEFAULT_HEADER, terms=terms))


def test_attacks_are_still_rejected(checker):
    res = checker.check(DEFAULT_HEADER, tactics=[a for a, _ in ATTACKS])
    assert [(a, r.reason) for (a, reasons), r in zip(ATTACKS, res) if r.reason not in reasons] == []


def test_parser_layer_catches_what_the_blacklist_misses(checker):
    res = checker.check(DEFAULT_HEADER, tactics=list(PARSER_ONLY), terms=["x = 1 ∧ (by exact?) = 2"])
    assert [r.reason for r in res] == [*PARSER_ONLY.values(), "search"]
