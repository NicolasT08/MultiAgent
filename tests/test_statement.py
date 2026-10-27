"""lean/statement.py: parsing curated statements and building the Lean files (offline)."""

from __future__ import annotations

import pytest

from formal_mas.lean.statement import (
    DEFAULT_HEADER,
    Theorem,
    UnsafeLean,
    assemble,
    indent,
    parse_theorem,
    proof_file,
    skeleton,
    subgoal_lemma,
)

MINIF2F = """import Mathlib
import Aesop

set_option maxHeartbeats 400000

open BigOperators Real Nat Topology Rat

/-- The sum of two positive reals. -/
theorem mathd_algebra_10 (a b : ℝ) (h₀ : 0 < a ∧ 0 < b)
    (h₁ : a + b = 10) :
    a * b ≤ 25 := by
  sorry
"""


def test_parse_minif2f_statement():
    thm = parse_theorem(MINIF2F)
    assert thm.name == "mathd_algebra_10"
    assert thm.binders == ("(a b : ℝ)", "(h₀ : 0 < a ∧ 0 < b)", "(h₁ : a + b = 10)")
    assert thm.goal == "a * b ≤ 25"
    assert thm.header.startswith("import Mathlib\nimport Aesop") and "open BigOperators" in thm.header
    assert thm.binder_names == ["a", "b", "h₀", "h₁"]


@pytest.mark.parametrize("src,goal", [
    ("theorem t : 2 + 2 = 4 := by sorry", "2 + 2 = 4"),
    ("theorem t : 2 + 2 = 4 := sorry", "2 + 2 = 4"),
    ("theorem t : 2 + 2 = 4", "2 + 2 = 4"),
    ("theorem t (f : ℕ → ℕ) (h : ∀ x, f x = x) : f (f 2) = 2 := by\n  sorry", "f (f 2) = 2"),
    ("theorem t : (fun x : ℕ => x) 1 = 1 ∧ (∃ y : ℕ, y = 1) := by sorry", "(fun x : ℕ => x) 1 = 1 ∧ (∃ y : ℕ, y = 1)"),
    ("lemma t {n : ℕ} [NeZero n] (h : 0 < n) : n ≠ 0 := by sorry", "n ≠ 0"),
    ("theorem t (x : ℝ) : -- a comment\n  x ^ 2 ≥ 0 := by sorry", "x ^ 2 ≥ 0"),
])
def test_parse_variants(src, goal):
    thm = parse_theorem(src)
    assert thm.goal == goal and thm.header == DEFAULT_HEADER


def test_implicit_and_instance_binders():
    thm = parse_theorem("theorem t {n m : ℕ} [inst : NeZero n] [Fact (1 < m)] (h : n < m) : True := by sorry")
    assert thm.binder_names == ["n", "m", "inst", "h"]


@pytest.mark.parametrize("src", ["def f := 1", "theorem : True := trivial", "theorem t (x : ℝ := 1", "theorem t (x : ℝ) := rfl"])
def test_parse_errors(src):
    with pytest.raises(ValueError):
        parse_theorem(src)


def test_hypothesis_names_avoid_collisions():
    assert Theorem("t", ("(x : ℝ)",), "True").hypothesis_names(2) == ["h1", "h2"]
    assert Theorem("t", ("(h1 : 0 < x)",), "True").hypothesis_names(2) == ["hsg1", "hsg2"]


THM = parse_theorem(MINIF2F)
SUBGOALS = ["0 ≤ (a - b) ^ 2", "(a + b) ^ 2 = 100"]


def test_proof_file():
    code = proof_file(THM, "    nlinarith [sq_nonneg (a - b)]\n")
    assert code.startswith(THM.header)
    assert "theorem mathd_algebra_10 (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) (h₁ : a + b = 10) :\n    a * b ≤ 25 := by\n" \
           "  nlinarith [sq_nonneg (a - b)]\n" in code


def test_skeleton_and_assemble_share_the_layout():
    sk = skeleton(THM, SUBGOALS, "nlinarith [h1, h2]")
    assert "  have h1 : 0 ≤ (a - b) ^ 2 := by\n    sorry\n  have h2 : (a + b) ^ 2 = 100 := by\n    sorry\n" \
           "  nlinarith [h1, h2]\n" in sk
    assert skeleton(THM, SUBGOALS).rstrip().endswith("  sorry")
    final = assemble(THM, SUBGOALS, ["positivity", "rw [h₁]\nnorm_num"], "nlinarith [h1, h2]")
    assert "  have h2 : (a + b) ^ 2 = 100 := by\n    rw [h₁]\n    norm_num\n  nlinarith [h1, h2]\n" in final
    assert "sorry" not in final


def test_subgoal_lemma_has_previous_subgoals_as_hypotheses():
    name, code = subgoal_lemma(THM, SUBGOALS, 2, "rw [h₁]\nnorm_num")
    assert name == "mathd_algebra_10_sg2"
    assert "theorem mathd_algebra_10_sg2 (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) (h₁ : a + b = 10) " \
           "(h1 : 0 ≤ (a - b) ^ 2) :\n    (a + b) ^ 2 = 100 := by\n  rw [h₁]\n  norm_num\n" in code
    name1, code1 = subgoal_lemma(THM, SUBGOALS, 1, "positivity")
    assert name1 == "mathd_algebra_10_sg1" and "(h1 :" not in code1
    with pytest.raises(IndexError):
        subgoal_lemma(THM, SUBGOALS, 3, "simp")


def test_the_statement_is_never_changed_by_generated_text():
    # the builders only paste blocks after `:= by`; the declaration line is always ours
    decl = THM.declaration()
    for code in (proof_file(THM, "simp"), skeleton(THM, SUBGOALS), assemble(THM, SUBGOALS, ["simp", "simp"], "simp")):
        assert f"{decl} := by\n" in code


@pytest.mark.parametrize("build", [
    lambda: proof_file(THM, "exact h\ntheorem evil : False := sorry"),
    lambda: proof_file(THM, "sorry"),
    lambda: skeleton(THM, ["0 < a := by sorry"]),
    lambda: skeleton(THM, SUBGOALS, "run_tac pure ()"),
    lambda: subgoal_lemma(THM, SUBGOALS, 1, "native_decide"),
    lambda: assemble(THM, SUBGOALS, ["simp", "  simp\nexact h"], "simp"),
    lambda: assemble(THM, ["x = 1\naxiom cheat : False"], ["simp"], "simp"),
])
def test_unsafe_text_is_refused(build):
    with pytest.raises(UnsafeLean):
        build()


def test_assemble_needs_one_block_per_subgoal():
    with pytest.raises(ValueError):
        assemble(THM, SUBGOALS, ["simp"], "simp")


def test_indent():
    assert indent("\n    a\n      b\n\n    c\n", 2) == "  a\n    b\n\n  c"


def test_statement_with_a_real_proof_is_refused():
    with pytest.raises(ValueError):
        parse_theorem("theorem t (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x")


def test_hypothesis_names_try_further_prefixes():
    assert Theorem("t", ("(h1 hsg1 : True)",), "True").hypothesis_names(1) == ["hsg_1"]
    assert Theorem("t", ("⦃h1 h2⦄",), "True").hypothesis_names(1) == ["hsg1"]
