"""κ (Eq. 11) against real Lean output and real run failures, π (Eq. 12) against its definition."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from formal_mas.agents.autoformalizer import statement_end
from formal_mas.lean.runner import parse_repl_response
from formal_mas.orchestrator.policy import (ERROR_CLASSES, Limits, Trace, classify, decide, escalate, missing_identifier,
                                            structural_bound)

FIXTURES = Path(__file__).parent / "fixtures" / "lean"
REPL = {c["case"]: c for c in json.loads((FIXTURES / "repl_cases.json").read_text(encoding="utf-8"))["cases"]}
REAL = json.loads((FIXTURES / "kappa_real_failures.json").read_text(encoding="utf-8"))


def lean_trace(case: str, reason: str = "lean:error") -> Trace:
    c = REPL[case]
    messages, *_ = parse_repl_response(c["raw"], c["axioms_line"], c["decls_line"], c["theorem"])
    return Trace(reason, tuple((m.line, m.column, m.text) for m in messages if m.severity == "error"))


@pytest.mark.parametrize("case, expected", [
    ("type_mismatch", "tactic_failure"),
    ("unsolved_goals", "tactic_failure"),
    ("linarith_failed", "tactic_failure"),
    ("unknown_identifier", "missing_lemma"),
    ("lean3_syntax", "syntax"),
    ("heartbeats", "timeout"),
])
def test_kappa_on_recorded_lean_output(case, expected):
    assert classify(lean_trace(case)) == expected


def test_missing_identifier_from_recorded_output():
    assert missing_identifier(lean_trace("unknown_identifier")) == "nat.succ_le_iff.mp"
    assert missing_identifier(lean_trace("linarith_failed")) is None
    text = "Invalid field `foo`: The environment does not contain `Nat.foo`"
    assert missing_identifier(Trace("lean:error", ((3, 0, text),))) == "Nat.foo"
    assert classify(Trace("lean:error", ((3, 0, text),))) == "missing_lemma"


def test_kappa_on_every_real_failure_of_the_dev_and_calibration_runs():
    expected = {"refuted:sg1": "planning", "sanitize:syntax": "syntax", "lean:error": "tactic_failure"}
    assert len(REAL) >= 8 and {c["reason"] for c in REAL} == set(expected)
    seen = set()
    runs = {c["run"] for c in REAL}
    assert any("experiment" in r for r in runs) and any("dev" in r for r in runs)
    for c in REAL:
        messages = tuple((int(m.group(1)), 2, m.group(2)) for e in c["errors"]
                         if (m := re.match(r"line (\d+): (.*)", e, re.S)))
        # amc12b_2004_p3 (calibration): an instance stuck, then `pow_le_pow_right` does not exist
        unknown = any("Unknown identifier" in e for e in c["errors"])
        got = classify(Trace(c["reason"], messages))
        assert got == ("missing_lemma" if unknown else expected[c["reason"]]), c
        seen.add(got)
    assert seen == {"planning", "syntax", "tactic_failure", "missing_lemma"}


@pytest.mark.parametrize("reason, expected", [
    ("sanitize:sorry", "syntax"), ("sanitize:parse", "syntax"), ("invalid_output", "syntax"), ("no_tactics", "syntax"),
    ("lean:sorry", "syntax"), ("lean:nonstandard_axioms: cheat", "syntax"), ("lean:extra_declarations: helper", "syntax"),
    ("lean:timeout", "timeout"), ("lean:memory_limit", "timeout"), ("lean:crash", "timeout"),
    ("refuted:sg2", "planning"), ("assemble:error", "planning"), ("no_sketch", "tactic_failure"),
])
def test_kappa_on_reasons_without_messages(reason, expected):
    assert classify(Trace(reason)) == expected


def test_misformalization_is_an_error_before_the_end_of_the_statement():
    code = "import Mathlib\n\ntheorem t_sg1 (x : ℝ) (h1 : x = y) :\n    x = 3 := by\n  linarith\n"
    end = statement_end(code)
    assert end == (4, 10)
    in_statement = Trace("lean:error", ((3, 28, "Unknown identifier `y`"),), end)
    assert classify(in_statement) == "misformalization"
    # `unsolved goals` is reported at the `by`, on the statement's last line: still the proof's fault
    at_by = Trace("lean:error", ((4, 13, "unsolved goals\n⊢ x = 3"),), end)
    assert classify(at_by) == "tactic_failure"
    in_proof = Trace("lean:error", ((5, 2, "Unknown identifier `foo`"),), end)
    assert classify(in_proof) == "missing_lemma"


def reference_pi(e, a, r, b, k_max, r_max, b_max):
    """Eq. 12 as written in the report."""
    if b >= b_max or (a >= k_max and r >= r_max):
        return "abandon", None
    if (a >= k_max or e == "planning") and r < r_max:
        return "replan", None
    if e == "syntax":
        return "retry", "FORMALIZE"
    if e == "missing_lemma":
        return "retry", "RETRIEVE"
    return "retry", "GENERATE"


def test_pi_is_exactly_eq_12_on_the_whole_grid():
    for lim, e, a, r, b in itertools.product([(1, 0, 16), (3, 1, 40), (5, 2, 8)], ERROR_CLASSES, range(7), range(4),
                                             (0, 7, 8, 16, 39, 40, 41)):
        action = decide(e, a, r, b, Limits(*lim))
        assert (action.kind, action.route) == reference_pi(e, a, r, b, *lim)


def test_pi_decides_the_same_on_the_same_input():
    lim = Limits(3, 1, 40)
    for e, a, r, b in itertools.product(ERROR_CLASSES, range(5), range(3), (0, 39, 40)):
        assert len({decide(e, a, r, b, lim) for _ in range(5)}) == 1
    with pytest.raises(ValueError):
        decide("nope", 0, 0, 0, lim)


def test_kappa_decides_the_same_on_the_same_input():
    for c in REAL:
        t = Trace(c["reason"], tuple((1, 0, e) for e in c["errors"]))
        assert len({classify(t) for _ in range(5)}) == 1


def test_structural_bound_is_eq_13():
    assert structural_bound(Limits(3, 1, 40), n_max=3, samples=3) == 40
    assert structural_bound(Limits(3, 1, 200), n_max=3, samples=3) == 92  # the report's 2(1 + 45)
    assert structural_bound(Limits(1, 0, 40), n_max=3, samples=3) == 16  # C4


def test_escalate_eq_11():
    assert escalate("tactic_failure", 3, 3) == "planning"
    assert escalate("tactic_failure", 2, 3) == "tactic_failure"
    assert escalate("tactic_failure", 1, 1) == "tactic_failure"  # C4: one failure exhausts nothing


def test_review_regressions():
    assert missing_identifier(Trace("lean:error", ((3, 0, "Unknown identifier `Nat.le_of_lt_succ'`"),))) == "Nat.le_of_lt_succ'"
    end = (4, 10)
    assert classify(Trace("lean:error", ((0, 0, "aborted"),), end)) == "tactic_failure"  # no position: not the statement
    assert classify(Trace("lean:error", ((1, 0, "(kernel) deep recursion detected"),), end)) == "timeout"
