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
