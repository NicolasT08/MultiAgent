"""Integration test: LeanRunner with the real Lean REPL (`pytest -m slow`, ~1.5 min warm;
the first `import Mathlib` after a reboot can take much longer on an HDD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from formal_mas.lean.runner import LeanRunner, accepted

ROOT = Path(__file__).resolve().parents[1]
REPL_BIN = ROOT / "lean_repl" / ".lake" / "build" / "bin"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not REPL_BIN.exists(), reason="Lean REPL not built (bash scripts/build_repl.sh)"),
]

HEADER = "import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\nopen Real\n\n"
GOOD = HEADER + "theorem t (a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2 := two_mul_le_add_sq a b\n"
BAD = HEADER + "theorem t (x : ℝ) : 0 < x ^ 2 := sq_nonneg x\n"
HANG = HEADER + "partial def spin (n : Nat) : Nat := spin (n + 1)\n\n#eval spin 0\n"


@pytest.fixture(scope="module")
def runner():
    r = LeanRunner()
    assert r.start(), r.warnings
    yield r
    r.close()


def test_accepts_and_rejects(runner):
    good = runner.verify(GOOD, theorem="t")
    assert good.backend == "repl" and accepted(good) == (True, "accepted")
    bad = runner.verify(BAD, theorem="t")
    assert accepted(bad) == (False, "error")
    assert bad.errors[0].line == 8 and bad.errors[0].text.startswith("Type mismatch")


def test_hang_times_out_and_repl_restarts(runner):
    starts = runner.starts
    res = runner.verify(HANG, timeout=10)
    assert res.status == "timeout"
    good = runner.verify(GOOD + "-- not in the cache\n", theorem="t")
    assert not good.cached and accepted(good) == (True, "accepted") and runner.starts == starts + 1


def test_statement_pipeline_end_to_end(runner):
    """skeleton (with sorry) compiles → each subgoal lemma is accepted → the assembled
    proof, with the same blocks pasted unchanged, is accepted; a false proof is not."""
    from formal_mas.lean.runner import compiles
    from formal_mas.lean.statement import assemble, parse_theorem, proof_file, skeleton, subgoal_lemma

    thm = parse_theorem("theorem amgm_10 (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) (h₁ : a + b = 10) :\n"
                        "    a * b ≤ 25 := by sorry")
    subgoals = ["0 ≤ (a - b) ^ 2", "(a + b) ^ 2 = 100"]
    blocks = ["positivity", "rw [h₁]\nnorm_num"]
    closing = "nlinarith [h1, h2]"

    sk = runner.verify(skeleton(thm, subgoals, closing), theorem=thm.name)
    assert compiles(sk) and sk.has_sorry
    for i, block in enumerate(blocks, start=1):
        name, code = subgoal_lemma(thm, subgoals, i, block)
        assert accepted(runner.verify(code, theorem=name)) == (True, "accepted"), i
    final = runner.verify(assemble(thm, subgoals, blocks, closing), theorem=thm.name)
    assert accepted(final) == (True, "accepted") and final.declarations == ["amgm_10"]
    wrong = runner.verify(proof_file(thm, "linarith"), theorem=thm.name)
    assert accepted(wrong) == (False, "error")


REFUTE = ["omega", "linarith", "nlinarith", "norm_num at *", "simp_all"]
AIME = ("theorem aime_1991_p1 (x y : ℕ) (h₀ : 0 < x ∧ 0 < y) (h₁ : x * y + (x + y) = 71)\n"
        "  (h₂ : x ^ 2 * y + x * y ^ 2 = 880) : x ^ 2 + y ^ 2 = 146 := by sorry")


def _proved(runner, thm, code, ranges):
    from formal_mas.lean.runner import compiles_between

    res = runner.verify(code, None, timeout=60)
    return {name.removeprefix(thm.name + "_") for name, r in ranges.items() if compiles_between(res, *r)}


def test_refutation_rejects_false_subgoals_only(runner):
    """The plan Flash-Lite proposed for aime_1991_p1 on 2026-10-28 (x*y = 10, x + y = 61) is
    refuted subgoal by subgoal; the true values (55, 16) are not; contradictory hypotheses
    prove `_consistent` (the Planner then skips the check)."""
    from formal_mas.lean.statement import parse_theorem, refutation_file

    thm = parse_theorem("import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 0\n\n" + AIME)
    assert _proved(runner, thm, *refutation_file(thm, ["x * y = 10", "x + y = 61"], REFUTE, 200000)) == \
        {"ref1", "ref2"}
    assert _proved(runner, thm, *refutation_file(thm, ["x * y = 55", "x + y = 16"], REFUTE, 200000)) == set()
    bad = parse_theorem("theorem c (x y : ℕ) (h₁ : x + y = 3) (h₂ : 10 < x) : x = 5 := by sorry")
    assert _proved(runner, bad, *refutation_file(bad, ["x = 5"], REFUTE, 200000)) == {"consistent", "ref1"}
    # a subgoal naming an earlier subgoal's hypothesis is not auto-bound: it fails to elaborate,
    # so it is never "refuted" (without autoImplicit false, `h1` would become a new variable)
    assert _proved(runner, thm, *refutation_file(thm, ["x * y = 55", "h1 = h1 → x = 0"], REFUTE, 200000)) == set()


def test_closing_candidates(runner):
    from formal_mas.lean.statement import closing_candidates_file, parse_theorem

    thm = parse_theorem("theorem s (a b : ℝ) (h₀ : a + b = 5) (h₁ : a - b = 1) : a + 2 * b = 7 := by sorry")
    code, ranges = closing_candidates_file(thm, ["a = 3", "b = 2"], ["omega", "linarith", "nlinarith"], 200000)
    assert _proved(runner, thm, code, ranges) == {"close_auto2", "close_auto3"}  # omega: not over ℝ


def test_probes_pass_on_the_real_repl_and_a_missing_name_is_caught(runner):
    # the module fixture started a REPL whose probes (Real.sqrt, Aesop.BuiltinRules.not_intro) answered
    assert runner.starts >= 1 and not any(w["reason"] == "repl_probe_failed" for w in runner.warnings)
    assert runner.ensure_available() and runner.available
    # a probe name that does not exist stands in for "the import did not bring Mathlib": the REPL is
    # tried twice, then the file back-end runs the same probe and fails too → Lean unavailable
    from formal_mas.config import load_pipeline
    lean = {**load_pipeline()["lean"], "probes": {"Mathlib": "Real.no_such_constant_formal_mas"}}
    broken = LeanRunner({"lean": lean})
    try:
        assert broken.ensure_available() is False and broken.available is False
        reasons = [w["reason"] for w in broken.warnings]
        assert reasons == ["repl_probe_failed", "repl_probe_failed", "file_import_failed"], broken.warnings
        assert "Real.no_such_constant_formal_mas" in broken.warnings[0]["detail"]
        assert broken.verify(GOOD, theorem="t").status == "crash"  # never accepts anything
    finally:
        broken.close()
