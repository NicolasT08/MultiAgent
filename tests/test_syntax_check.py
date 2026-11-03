"""lean/syntax_check.py offline: the node-kind whitelist, the generated parser program and
how answers are read (fake Lean). The real parser runs in tests/test_syntax_check_lean.py."""

from __future__ import annotations

import pytest

from formal_mas.lean.runner import LeanMessage, LeanResult
from formal_mas.lean.statement import DEFAULT_HEADER
from formal_mas.lean.syntax_check import SyntaxChecker, _last, check_kinds, kind_allowed, program


@pytest.mark.parametrize("kind", [
    "null", "num", "token.«⊢»", "Lean.Parser.Tactic.omega", "Mathlib.Tactic.nlinarith", "Lean.Parser.Term.app",
    "Lean.Parser.Term.dotIdent", "Lean.Parser.Term.anonymousCtor", "«term_+_»", "Real.«term√_»", "Nat.term_!",
    "Lean.«binderPred∈_»", "Lean.«term∀__,_»", "BigOperators.bigsum", "termℝ", "Mathlib.Notation.«term∃!_,_»",
    # rejected in the C5 dev run of 2026-11-03 although legitimate
    "Lean.Parser.Tactic.classical", "Mathlib.Linter.HaveILetI.tacticLetI__", "Mathlib.Linter.HaveILetI.tacticHaveI__",
    "Mathlib.Meta.SetNotationForOrder.subsetStx'", "Mathlib.Meta.SetNotationForOrder.ssubsetStx'",
    "Mathlib.Meta.SetNotationForOrder.supsetStx'", "Mathlib.Meta.SetNotationForOrder.ssupsetStx'",
])
def test_allowed_kinds(kind):
    assert kind_allowed(kind) == (True, ""), kind


@pytest.mark.parametrize("kind, reason", [
    ("Lean.Parser.Tactic.tacticSorry", "sorry"), ("Lean.Parser.Tactic.tacticStop_", "sorry"),
    ("Lean.Parser.Term.sorry", "sorry"), ("Lean.Parser.Tactic.exact?", "search"),
    ("Lean.Parser.Tactic.simpTrace", "search"), ("Mathlib.Tactic.Polyrith.«tacticPolyrithOnly[_]»", "search"),
    ("Lean.Parser.Tactic.nativeDecide", "native"), ("Lean.Parser.Tactic.runTac", "metaprogramming"),
    ("Lean.byElab", "metaprogramming"), ("Lean.Parser.Term.quot", "syntax"),
    ("Lean.Parser.Term.doubleQuotedName", "syntax"), ("Lean.Parser.Term.do", "syntax"),
    ("Lean.Parser.Term.doSeqIndent", "syntax"), ("Lean.Parser.Term.unsafe", "metaprogramming"),
    ("Lean.Parser.Tactic.traceState", "syntax"), ("Mathlib.Tactic.ExtractGoal.extractGoal", "syntax"),
    ("Some.Unknown.tactic", "syntax"), ("Qq.«term_q(_)»", "syntax"), ("Lean.Parser.Command.declaration", "syntax"),
])
def test_rejected_kinds(kind, reason):
    assert kind_allowed(kind) == (False, reason), kind


def test_last_component_ignores_dots_in_guillemets():
    assert _last("Mathlib.Tactic.GCongr.«tacticRel[_]»") == "tacticRel[_]"
    assert _last("Lean.«term∀__,_»") == "term∀__,_"
    assert _last("«term_._»") == "term_._"
    assert check_kinds(["null", "Lean.Parser.Tactic.exact?"]).reason == "search"


def test_program_embeds_text_in_raw_strings():
    code, lines = program(DEFAULT_HEADER, [__import__("formal_mas.lean.syntax_check", fromlist=["Item"]).Item(
        "tactics", "  constructor\n  · simp")])
    assert 'r#"by\n  constructor\n  · simp"#' in code and "runParserCategory" in code
    assert code.splitlines()[lines[0] - 1] == "open Lean in"


class FakeParser:
    """Answers every `SYNTAX i` command; `kinds[i]` (list) or a parse error string."""

    def __init__(self, answers, status="finished", shift=1, duplicate=False):
        self.answers, self.status, self.shift, self.duplicate = answers, status, shift, duplicate
        self.codes = []

    def verify(self, code, theorem=None, timeout=None):
        self.codes.append(code)
        lines = [no for no, ln in enumerate(code.split("\n"), start=1) if ln == "open Lean in"]
        msgs = []
        for i, (line, ans) in enumerate(zip(lines, self.answers)):
            text = f"SYNTAX {i} ERROR\n{ans}" if isinstance(ans, str) else f"SYNTAX {i} OK\n" + "\n".join(ans)
            msgs.append(LeanMessage("info", line + self.shift, 0, text))
            if self.duplicate:
                msgs.append(LeanMessage("info", line + self.shift, 0, text))
        return LeanResult(self.status, "repl", msgs)


OK_KINDS = ["Lean.Parser.Term.byTactic", "Lean.Parser.Tactic.tacticSeq", "null", "Lean.Parser.Tactic.omega"]


def test_checker_offline_layer_first_then_parser():
    fake = FakeParser([OK_KINDS, OK_KINDS + ["Lean.Parser.Tactic.exact?"], "<input>:2:3: unknown tactic",
                       ["Lean.Parser.Term.app", "«term_=_»"]])
    res = SyntaxChecker(fake).check(DEFAULT_HEADER, tactics=["omega", "sorry", "omega\nfoo", "begin_x"],
                                    terms=["x = 1"])
    assert [r.reason for r in res] == ["", "sorry", "search", "parse", ""]
    assert res[0].ok and res[4].ok and len(fake.codes) == 1
    assert "sorry" not in fake.codes[0]  # rejected offline, never sent to Lean


def test_comments_never_reach_the_raw_string():
    from formal_mas.lean.syntax_check import Item

    code, _ = program(DEFAULT_HEADER, [Item("tactics", "skip -- a comment #\nomega /- b -/")])
    assert "comment" not in code and "/-" not in code and 'r#"by\n  skip' in code
    code, _ = program(DEFAULT_HEADER, [Item("tactics", 'skip -- "# x')])
    assert '"# x' not in code  # the comment, quote included, is gone
    with pytest.raises(ValueError):
        program(DEFAULT_HEADER, [Item("tactics", 'skip "# x')])  # sanitize.py rejects it first anyway
    fake = FakeParser([OK_KINDS])
    (res,) = SyntaxChecker(fake).check(DEFAULT_HEADER, tactics=['skip -- "# #eval IO.Process.exit 0'])
    assert res.reason == "string" and fake.codes == []


def test_checker_fails_closed():
    for fake in (FakeParser([OK_KINDS], status="timeout"), FakeParser([OK_KINDS], shift=5),
                 FakeParser([OK_KINDS], duplicate=True), FakeParser([])):
        (res,) = SyntaxChecker(fake).check(DEFAULT_HEADER, tactics=["omega"])
        assert not res.ok and res.reason == "syntax_unavailable"
    assert SyntaxChecker(FakeParser([OK_KINDS], shift=0)).check(DEFAULT_HEADER, tactics=["omega"])[0].ok
    assert SyntaxChecker(FakeParser([])).check(DEFAULT_HEADER) == []
