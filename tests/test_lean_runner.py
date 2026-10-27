"""Parser and acceptance predicate (Eq. 2) on REAL Lean outputs.

Fixtures in tests/fixtures/lean/ were recorded by scripts/capture_lean_fixtures.py with the
Lean REPL v4.36.0-rc1 (repl_cases.json) and `lake env lean --json` (file_cases.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from formal_mas.lean.runner import (
    ALLOWED_AXIOMS,
    LeanResult,
    accepted,
    compiles,
    extra_declarations,
    file_result,
    parse_repl_response,
    split_imports,
    with_checks,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lean"
REPL = {c["case"]: c for c in json.loads((FIXTURES / "repl_cases.json").read_text(encoding="utf-8"))["cases"]}
FILE = {c["case"]: c for c in json.loads((FIXTURES / "file_cases.json").read_text(encoding="utf-8"))["cases"]}


def repl_result(case: str) -> LeanResult:
    c = REPL[case]
    messages, sorries, axioms, decls = parse_repl_response(c["raw"], c["axioms_line"], c["decls_line"], c["theorem"])
    return LeanResult("finished", "repl", messages, sorries, axioms, decls, theorem=c["theorem"])


def file_case_result(case: str) -> LeanResult:
    c = FILE[case]
    return file_result(c["returncode"], c["stdout"], c["stderr"], c["axioms_line"], c["decls_line"], c["theorem"])


# -- acceptance on every recorded case ---------------------------------------

EXPECTED = {
    "correct": (True, "accepted"),
    "correct_tactics": (True, "accepted"),
    "no_axioms": (True, "accepted"),
    "warning_only": (True, "accepted"),
    "aux_match": (True, "accepted"),
    "minif2f_header": (True, "accepted"),
    "type_mismatch": (False, "error"),
    "sorry": (False, "sorry"),
    "native_decide": (False, "nonstandard_axioms: t._native.native_decide.ax_1"),
    "decide_native_flag": (False, "nonstandard_axioms: t._native.decide.ax_1"),
    "unknown_identifier": (False, "error"),
    "lean3_syntax": (False, "error"),
    "unsolved_goals": (False, "error"),
    "linarith_failed": (False, "error"),
    "heartbeats": (False, "error"),
    "custom_axiom": (False, "nonstandard_axioms: cheat"),
    "forged_axioms_message": (False, "nonstandard_axioms: cheat"),
    "unknown_theorem_name": (False, "error"),
    "no_theorem": (False, "axioms_unknown"),
    "extra_def": (False, "extra_declarations: helper"),
    "extra_in_namespace": (False, "extra_declarations: t.sneaky"),
    "forged_decls_message": (False, "extra_declarations: helper"),
}


def test_every_fixture_has_an_expectation():
    assert set(REPL) == set(EXPECTED)


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_acceptance_repl(case):
    assert accepted(repl_result(case)) == EXPECTED[case]


@pytest.mark.parametrize("case", ["correct", "type_mismatch", "sorry", "extra_def"])
def test_acceptance_file_backend_agrees(case):
    assert accepted(file_case_result(case)) == EXPECTED[case]


def test_file_backend_native_decide_axiom_name():
    ok, reason = accepted(file_case_result("native_decide"))
    assert not ok and reason == "nonstandard_axioms: t._native.native_decide.ax_1_1"


# -- parser details ------------------------------------------------------------


def test_checks_are_parsed_and_removed_from_messages():
    res = repl_result("correct")
    assert res.axioms == ["propext", "Classical.choice", "Quot.sound"]
    assert set(res.axioms) == ALLOWED_AXIOMS
    assert res.declarations == ["t"]
    assert res.messages == []
    assert repl_result("no_axioms").axioms == []


def test_positions_are_those_of_the_full_file():
    err = repl_result("type_mismatch").errors[0]
    line = REPL["type_mismatch"]["code"].split("\n")[err.line - 1]
    assert line.startswith("theorem t (x : ℝ) : 0 < x ^ 2")
    assert line[err.column:err.end_column] == "sq_nonneg x"
    assert err.text.startswith("Type mismatch\n  sq_nonneg x")
    assert file_case_result("type_mismatch").errors[0] == err  # same position and text in both back-ends


def test_sorry_goal_and_warning():
    res = repl_result("sorry")
    assert res.sorries == [{"line": 5, "column": 2, "goal": "n : ℕ\n⊢ n + 0 = n"}]
    assert res.has_sorry and res.axioms == ["sorryAx"]
    assert file_case_result("sorry").has_sorry  # the file back-end only has the warning


def test_error_texts_used_by_the_classifier():
    first = {c: repl_result(c).errors[0].text for c in
             ["unknown_identifier", "lean3_syntax", "unsolved_goals", "linarith_failed", "heartbeats"]}
    assert first["unknown_identifier"] == "Unknown identifier `nat.succ_le_iff.mp`"
    assert first["lean3_syntax"] == "Unknown identifier `begin`"
    assert "unexpected token" in repl_result("lean3_syntax").errors[1].text
    assert first["unsolved_goals"].startswith("unsolved goals\ncase right")
    assert first["linarith_failed"].startswith("linarith failed")
    assert first["heartbeats"].startswith("(deterministic) timeout")


def test_errors_carry_sorry_axiom():
    # an elaboration error inserts sorryAx: rejected as `error` before axioms are looked at
    assert "sorryAx" in repl_result("type_mismatch").axioms


def test_forged_axioms_message_is_ignored():
    res = repl_result("forged_axioms_message")
    assert res.axioms == ["cheat"]
    assert any("depends on axioms: [propext]" in m.text for m in res.messages)  # kept as an ordinary message


def test_forged_decls_message_is_ignored():
    res = repl_result("forged_decls_message")
    assert sorted(res.declarations) == ["helper", "t"]
    assert any(m.text.startswith("FORMAL_MAS_DECLS") for m in res.messages)


def test_axioms_answer_must_name_the_theorem():
    c = REPL["correct"]
    _, _, axioms, _ = parse_repl_response(c["raw"], c["axioms_line"], c["decls_line"], "other_theorem")
    assert axioms is None  # → axioms_unknown, never accepted


def test_duplicate_answers_are_not_trusted():
    c = REPL["correct"]
    raw = {**c["raw"], "messages": c["raw"]["messages"] * 2}  # e.g. forged answers at the same lines
    messages, _, axioms, decls = parse_repl_response(raw, c["axioms_line"], c["decls_line"], "t")
    assert axioms is None and decls is None and len(messages) == 4


def test_lean_auxiliaries_are_allowed():
    assert sorted(repl_result("aux_match").declarations) == ["t", "t._proof_1", "t.match_1"]
    assert extra_declarations(["t", "t._proof_2", "t.match_3", "t.eq_1", "t.sneaky", "u"], "t") == ["t.sneaky", "u"]


def test_checks_work_under_minif2f_header():
    res = repl_result("minif2f_header")
    assert res.declarations == ["t"] and set(res.axioms) <= ALLOWED_AXIOMS


def test_missing_declarations_listing_is_not_accepted():
    res = repl_result("correct")
    res.declarations = None
    assert accepted(res) == (False, "declarations_unknown")


def test_skeleton_predicate():
    assert compiles(repl_result("sorry")) and not compiles(repl_result("type_mismatch"))


def test_severity_names_are_normalised():
    sev = {m.severity for c in FILE for m in file_case_result(c).messages}
    assert sev <= {"error", "warning", "info"}


def test_file_backend_memory_limit():
    res = file_case_result("memory_limit")
    assert res.status == "memory_limit" and "excessive memory consumption" in res.detail
    assert accepted(res) == (False, "memory_limit")


def test_file_backend_crash():
    res = file_result(3221225477, "", "Segmentation fault", None)
    assert res.status == "crash"


@pytest.mark.parametrize("status", ["timeout", "memory_limit", "crash"])
def test_unfinished_runs_are_rejected(status):
    assert accepted(LeanResult(status, "repl", axioms=["propext"], declarations=["t"], theorem="t")) == (False, status)


# -- code preparation ----------------------------------------------------------


def test_split_imports_blanks_lines():
    code = "-- header\nimport Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\nopen Real\n\ntheorem t : True := trivial\n"
    modules, body = split_imports(code)
    assert modules == ["Mathlib", "Aesop"]
    assert body.split("\n")[1:3] == ["", ""]
    assert body.count("\n") == code.count("\n")
    assert "set_option maxHeartbeats 400000" in body and "import" not in body


def test_split_imports_stops_at_first_command():
    modules, body = split_imports("theorem t : True := trivial\nimport Mathlib\n")
    assert modules == [] and "import Mathlib" in body


def test_with_checks_lines():
    code, axioms_line, decls_line = with_checks("import Mathlib\n\ntheorem t : True := trivial\n", "t")
    lines = code.split("\n")
    assert lines[axioms_line - 1] == "#print axioms t"
    assert lines[decls_line - 1].startswith("#eval show Lean.CoreM")
    assert with_checks("x", None) == ("x", None, None)
