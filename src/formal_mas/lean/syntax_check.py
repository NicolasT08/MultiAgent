"""Second line of defence for generated Lean (option C, team decision 2026-10-29).

lean/sanitize.py (a blacklist over the text, offline) runs first. Text that passes it is
then parsed by Lean's own parser in the REPL, without elaborating anything:

    tactic block  `by\n  <block>` parsed as a `term` (so the whole block must be one
                  `by` tactic sequence: text that would end the proof is a parse error)
    proposition   parsed as a `term`

and every syntax node kind of the tree must be on the whitelist below:
- explicit kinds: structural nodes, the tactics olympiad proofs use (collected from ~300
  real tactic blocks on 2026-10-29, Lean v4.36.0-rc1 + Mathlib), and the binders/notation
  helpers they need;
- core term syntax `Lean.Parser.Term.*`, except quotations, `do` blocks, `unsafe`, `sorry`
  and other program-only syntax (DENIED_TERM);
- notation terms (`«term_+_»`, `Real.«term√_»`, `BigOperators.bigsum`…) and binder predicates
  (`Lean.«binderPred∈_»`), outside the elaborator / quotation namespaces.
Anything else is rejected with reason `syntax` (detail: the node kind), or `parse` if Lean
cannot parse the text. Identifiers are not node kinds: names such as `sorryAx`, `IO.*`,
`Lean.ofReduceBool` are caught by sanitize.py, which is why both layers are kept.

The LLM text is embedded in a raw string literal `r#"…"#`, only parsed, never elaborated.
It cannot leave the literal: sanitize.py rejects every `"` in the whole text, comments
included, and the embedded text has its comments removed (`_source`); a `"` left anyway
raises before anything is sent to Lean. (The invariant review of 2026-10-29 found that
`skip -- "# …` escaped when only comment-free code was checked for `"`.) One REPL command
checks a whole batch (~3 s for 70 blocks, mostly fixed cost). If the command does not
finish or an answer is missing, the text is rejected (`syntax_unavailable`).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from formal_mas.lean.sanitize import SanitizeResult, check_tactics, check_term, strip_comments

STRUCTURAL = frozenset({
    "null", "group", "choice", "num", "scientific", "ident", "hygieneInfo", "patternIgnore", "fieldIdx",
    "coeNotation", "Lean.cdot", "Lean.cdotTk", "Lean.binderIdent", "Lean.explicitBinders",
    "Lean.unbracketedExplicitBinders", "Lean.calcTactic", "Lean.calcSteps", "Lean.calcFirstStep", "Lean.calcStep",
    "Lean.modCast", "Mathlib.Meta.setBuilder", "Batteries.ExtendedBinder.extBinder",
    "Batteries.ExtendedBinder.extBinders", "BigOperators.bigsum", "BigOperators.bigprod",
    "BigOperators.bigOpBinder", "BigOperators.bigOpBinders",
})

TACTICS = frozenset({
    # sequencing and structure
    "Lean.Parser.Term.byTactic", "Lean.Parser.Term.byTactic'", "Lean.Parser.Tactic.tacticSeq",
    "Lean.Parser.Tactic.tacticSeq1Indented", "Lean.Parser.Tactic.tacticSeqBracketed", "Lean.Parser.Tactic.«tactic_<;>_»",
    "Lean.Parser.Tactic.first", "Lean.Parser.Tactic.tacticTry_", "Lean.Parser.Tactic.tacticRepeat_",
    "Lean.Parser.Tactic.tacticIterate____", "Lean.Parser.Tactic.allGoals", "Lean.Parser.Tactic.anyGoals",
    "Lean.Parser.Tactic.focus", "Lean.Parser.Tactic.case", "Lean.Parser.Tactic.caseArg",
    "Lean.Parser.Tactic.«tacticNext_=>_»", "Lean.Parser.Tactic.paren", "Lean.Parser.Tactic.done",
    "Lean.Parser.Tactic.skip", "Lean.solveTactic", "Lean.Parser.Tactic.repeat'", "Lean.Parser.Tactic.withReducible",
    "Batteries.Tactic.«tacticOn_goal_=>_»", "Batteries.Tactic.goalNum", "Batteries.Tactic.tacticSwap",
    "Batteries.Tactic.tacticPick_goal_", "Lean.Parser.Tactic.rotateLeft", "Lean.Parser.Tactic.rotateRight",
    "Lean.Parser.Tactic.tacticNofun", "Mathlib.Tactic.rename'", "Mathlib.Tactic.renameArg",
    "Lean.Parser.Tactic.optConfig", "Lean.Parser.Tactic.configItem",
    "Lean.Parser.Tactic.posConfigItem", "Lean.Parser.Tactic.negConfigItem", "Lean.Parser.Tactic.valConfigItem",
    "Lean.Parser.Tactic.location", "Lean.Parser.Tactic.locationHyp", "Lean.Parser.Tactic.locationType",
    "Lean.Parser.Tactic.locationWildcard", "token.«⊢»", "Lean.Parser.Tactic.set_option", "Lean.Parser.Tactic.open",
    "Lean.Parser.Command.openSimple", "Lean.Parser.Command.openOnly", "Lean.Parser.Command.openScoped",
    # introduction, cases, induction
    "Lean.Parser.Tactic.intro", "Lean.Parser.Tactic.intros", "Lean.Parser.Tactic.rintro",
    "Lean.Parser.Tactic.rintroPat.one", "Lean.Parser.Tactic.rintroPat.binder", "Lean.Parser.Tactic.obtain",
    "Lean.Parser.Tactic.rcases", "Lean.Parser.Tactic.elimTarget", "Lean.Parser.Tactic.rcasesPatLo",
    "Lean.Parser.Tactic.rcasesPatMed", "Lean.Parser.Tactic.rcasesPat.one", "Lean.Parser.Tactic.rcasesPat.tuple",
    "Lean.Parser.Tactic.rcasesPat.paren", "Lean.Parser.Tactic.rcasesPat.clear", "Lean.Parser.Tactic.rcasesPat.ignore",
    "Lean.Parser.Tactic.rcasesPat.explicit", "Lean.Parser.Tactic.rcasesPat.typed", "Lean.Parser.Tactic.cases",
    "Lean.Parser.Tactic.induction", "Lean.Parser.Tactic.inductionAlts", "Lean.Parser.Tactic.inductionAlt",
    "Lean.Parser.Tactic.inductionAltLHS", "Mathlib.Tactic.cases'", "Mathlib.Tactic.induction'",
    "Mathlib.Tactic.casesM", "Mathlib.Tactic.constructorM", "Lean.Parser.Tactic.match", "Lean.Parser.Tactic.injection",
    "Lean.Parser.Tactic.constructor", "Lean.Parser.Tactic.left", "Lean.Parser.Tactic.right",
    "Lean.Parser.Tactic.«tacticExists_,,»", "Mathlib.Tactic.useSyntax", "Mathlib.Tactic.«tacticUse!___,,»",
    "Lean.Parser.Tactic.tacticAnd_intros", "Batteries.Tactic.tacticSplit_ands", "Mathlib.Tactic.Choose.choose",
    "Mathlib.Tactic.Choose.chooseBinder", "Mathlib.Tactic.lift", "Mathlib.Tactic.wlog",
    "Mathlib.Tactic.ModCases.«tacticMod_cases_:_%_»", "Lean.Elab.Tactic.finCases", "Mathlib.Tactic.intervalCases",
    "«tacticBy_cases_:_»", "Batteries.Tactic.byContra", "Mathlib.Tactic.ByContra.byContra!",
    "Mathlib.Tactic.Contrapose.contrapose!", "Mathlib.Tactic.Contrapose.contrapose", "Lean.Parser.Tactic.tacticExfalso",
    "Lean.Parser.Tactic.split", "Mathlib.Tactic.splitIfs",
    # closing and applying
    "Lean.Parser.Tactic.exact", "Batteries.Tactic.exacts", "Lean.Parser.Tactic.apply", "Lean.Parser.Tactic.refine",
    "Lean.Parser.Tactic.refine'", "Lean.Parser.Tactic.assumption", "Lean.Parser.Tactic.contradiction",
    "Lean.Parser.Tactic.tacticRfl", "Lean.Parser.Tactic.tacticTrivial", "Lean.Parser.Tactic.symm",
    "Batteries.Tactic.tacticTrans___", "Lean.Parser.Tactic.decide", "Lean.Parser.Tactic.omega",
    "Lean.Parser.Tactic.tacticExact_mod_cast_", "Lean.Parser.Tactic.tacticApply_mod_cast_",
    "Lean.Parser.Tactic.tacticAssumption_mod_cast_", "Lean.Parser.Tactic.tacticRw_mod_cast___",
    "Lean.Parser.Tactic.tacticNorm_cast__", "Lean.Parser.Tactic.pushCast", "Mathlib.Tactic.Zify.zify",
    "Mathlib.Tactic.Qify.qify", "Mathlib.Tactic.Rify.rify",
    # hypotheses and goals
    "Lean.Parser.Tactic.tacticHave__", "Lean.Parser.Tactic.tacticHave_", "Lean.Parser.Tactic.tacticLet__",
    "Mathlib.Linter.HaveILetI.tacticHaveI__", "Lean.Parser.Tactic.tacticSuffices_", "Lean.Parser.Tactic.specialize",
    "Lean.Parser.Tactic.generalize", "Lean.Parser.Tactic.generalizeArg", "Mathlib.Tactic.setTactic",
    "Mathlib.Tactic.setArgsRest", "Lean.Parser.Tactic.change", "Lean.Parser.Tactic.clear", "Lean.Parser.Tactic.revert",
    "Lean.Parser.Tactic.renameI", "Lean.Parser.Tactic.subst", "Lean.Parser.Tactic.substVars",
    "Lean.Parser.Tactic.unfold", "Mathlib.Linter.Style.show", "Lean.Parser.Tactic.show",
    # rewriting and simplification
    "Lean.Parser.Tactic.rwSeq", "Lean.Parser.Tactic.rwRuleSeq", "Lean.Parser.Tactic.rwRule", "Lean.Parser.Tactic.rwaAt", "Lean.Parser.Tactic.rwa", "Lean.calc",
    "Lean.Parser.Tactic.tacticErw___", "Mathlib.Tactic.tacticNth_rewrite_____", "Mathlib.Tactic.tacticNth_rw_____",
    "Mathlib.Tactic.tacticSimp_rw___", "Lean.Parser.Tactic.simp", "Lean.Parser.Tactic.simpAll",
    "Lean.Parser.Tactic.simpArgs", "Lean.Parser.Tactic.simpLemma", "Lean.Parser.Tactic.simpErase",
    "Lean.Parser.Tactic.simpStar", "Lean.Parser.Tactic.simpPre", "Lean.Parser.Tactic.simpPost",
    "Lean.Parser.Tactic.dsimp", "Lean.Parser.Tactic.simpa", "Lean.Parser.Tactic.simpaArgsRest",
    "Lean.Parser.Tactic.simpArith", "Mathlib.Tactic.FieldSimp.fieldSimp", "Mathlib.Tactic.normNum",
    "Mathlib.Tactic.normNum1", "Mathlib.Tactic.RingNF.ring", "Mathlib.Tactic.RingNF.ringNF", "Mathlib.Tactic.Ring.ring1",
    "Mathlib.Tactic.Abel.abel", "Mathlib.Tactic.Abel.abelNF", "Mathlib.Tactic.NoncommRing.noncomm_ring",
    "Mathlib.Tactic.Push.push_neg", "Mathlib.Tactic.tacticCancel_denoms_", "Mathlib.Tactic.cancelDenoms",
    "Lean.Parser.Tactic.Conv.conv", "Lean.Parser.Tactic.Conv.convSeq", "Lean.Parser.Tactic.Conv.convSeq1Indented",
    "Lean.Parser.Tactic.Conv.convRw__", "Lean.Parser.Tactic.Conv.lhs", "Lean.Parser.Tactic.Conv.rhs",
    "Mathlib.Tactic.Conv.convLHS", "Mathlib.Tactic.Conv.convRHS",
    # arithmetic deciders
    "Mathlib.Tactic.linarith", "Mathlib.Tactic.nlinarith", "Mathlib.Tactic.tacticLinarith!_",
    "Mathlib.Tactic.tacticNlinarith!_", "Mathlib.Tactic.linarithArgsRest", "Mathlib.Tactic.Positivity.positivity",
    "Mathlib.Tactic.LinearCombination.linearCombination", "Mathlib.Tactic.LinearCombination.expStx",
    "Mathlib.Tactic.GCongr.gcongr", "Mathlib.Tactic.GCongr.«tacticRel[_]»", "«tacticBound[_]»",
    "Mathlib.Tactic.Monotonicity.mono", "Mathlib.Tactic.ComputeDegree.computeDegree",
    # logic and congruence
    "Mathlib.Tactic.Tauto.tauto", "Mathlib.Tactic.ITauto.itauto", "Aesop.Frontend.Parser.aesopTactic",
    "Lean.Parser.Tactic.congr", "Congr!.congr!", "Mathlib.Tactic.convert", "Mathlib.Tactic.convertTo",
    "Lean.Elab.Tactic.Ext.ext", "tacticFunext___", "Mathlib.Tactic.applyFun",
})

# Core term syntax that is never needed in a proof: programs, quotations, escape hatches.
DENIED_TERM = frozenset({
    "Lean.Parser.Term.unsafe", "Lean.Parser.Term.sorry", "Lean.Parser.Term.dbgTrace", "Lean.Parser.Term.panic",
    "Lean.Parser.Term.unreachable", "Lean.Parser.Term.declName", "Lean.Parser.Term.withDeclName",
    "Lean.Parser.Term.stateRefT", "Lean.Parser.Term.macroDollarArg", "Lean.Parser.Term.runElab",
    "Lean.Parser.Term.runMeta", "Lean.Parser.Term.binop", "Lean.Parser.Term.binop_lazy", "Lean.Parser.Term.unop",
    "Lean.Parser.Term.leftact", "Lean.Parser.Term.rightact", "Lean.Parser.Term.binrel", "Lean.Parser.Term.binrel_no_prop",
})
# Known forbidden kinds, for a precise reason (they would be rejected anyway: not whitelisted).
FORBIDDEN = {
    "Lean.Parser.Tactic.tacticSorry": "sorry", "Lean.Parser.Tactic.tacticAdmit": "sorry",
    "Lean.Parser.Tactic.tacticStop_": "sorry", "Lean.Parser.Term.sorry": "sorry",
    "Lean.Parser.Tactic.exact?": "search", "Lean.Parser.Tactic.apply?": "search", "Lean.Parser.Tactic.rewrites?": "search",
    "Lean.Parser.Tactic.simpTrace": "search", "Aesop.Frontend.Parser.aesopTactic?": "search",
    "Mathlib.Tactic.Hint.hintStx": "search", "Mathlib.Tactic.Says.says": "search",
    "Mathlib.Tactic.Polyrith.«tacticPolyrithOnly[_]»": "search",  # calls an external web service
    "Lean.Parser.Tactic.nativeDecide": "native", "Lean.Parser.Tactic.runTac": "metaprogramming",
    "Lean.byElab": "metaprogramming", "Lean.Parser.Term.unsafe": "metaprogramming",
}
_DO_RE = re.compile(r"do(?:[A-Z]|$)")  # do, doSeqIndent, doExpr… (not dotIdent)
_NOTATION_DENIED_NS = ("Qq", "Lean.Elab", "Lean.Meta", "Lean.Parser.Command", "Lean.Parser.Syntax",
                       "Lean.Parser.Tactic", "Lean.Parser.Term")


def _last(kind: str) -> str:
    """Last component of a kind name, ignoring dots inside «…»."""
    depth, start = 0, 0
    for i, ch in enumerate(kind):
        depth += ch == "«"
        depth -= ch == "»"
        if ch == "." and depth == 0:
            start = i + 1
    return kind[start:].strip("«»")


def kind_allowed(kind: str) -> tuple[bool, str]:
    """(allowed, reason if not)."""
    if kind in FORBIDDEN:
        return False, FORBIDDEN[kind]
    if kind in STRUCTURAL or kind in TACTICS or kind.startswith("token."):
        return True, ""
    if kind.startswith("Lean.Parser.Term."):
        last = _last(kind)
        if kind in DENIED_TERM or _DO_RE.match(last) or "uot" in last:  # do-blocks, (double)quot(ed)…
            return False, "syntax"
        return True, ""
    last = _last(kind)
    if (last.startswith("term") or last.startswith("binderPred")) and \
            not any(kind == ns or kind.startswith(ns + ".") for ns in _NOTATION_DENIED_NS):
        return True, ""
    return False, "syntax"


def check_kinds(kinds: list[str]) -> SanitizeResult:
    for kind in kinds:
        ok, reason = kind_allowed(kind)
        if not ok:
            return SanitizeResult(False, reason, f"syntax node `{kind}` is not allowed")
    return SanitizeResult(True)


@dataclass(frozen=True)
class Item:
    kind: str  # "tactics" | "term"
    text: str


def _source(item: Item) -> str:
    """The text to parse, without comments (strip_comments keeps lines and columns): even if a
    `"` slipped past sanitize.py, a comment could not hide it."""
    text = strip_comments(item.text)
    if text is None or '"' in text:
        raise ValueError("unsafe text reached the parser check")  # sanitize.py rejects both first
    if item.kind == "term":
        return " ".join(text.split())
    body = textwrap.dedent(text.strip("\n"))
    return "by\n" + "\n".join(("  " + ln) if ln.strip() else "" for ln in body.split("\n"))


_ANSWER_RE = re.compile(r"^SYNTAX (\d+) (OK|ERROR)\n(.*)$", re.S)


def program(header: str, items: list[Item]) -> tuple[str, dict[int, int]]:
    """One file that parses every item → (code, {index: first line of its command})."""
    code, lines = header.rstrip("\n") + "\n\n", {}
    for i, item in enumerate(items):
        lines[i] = code.count("\n") + 1
        code += (
            "open Lean in\n"
            "#eval show Elab.Command.CommandElabM Unit from do\n"
            f"  match Parser.runParserCategory (← getEnv) `term r#\"{_source(item)}\"# with\n"
            f"  | .error e => logInfo m!\"SYNTAX {i} ERROR\\n{{e}}\"\n"
            "  | .ok stx =>\n"
            "    let mut ks : Array String := #[]\n"
            "    for s in stx.topDown do\n"
            "      if let .node _ k _ := s then\n"
            "        unless ks.contains k.toString do ks := ks.push k.toString\n"
            f"    logInfo m!\"SYNTAX {i} OK\\n{{String.intercalate \"\\n\" ks.toList}}\"\n\n"
        )
    return code, lines


class SyntaxChecker:
    """`check(header, tactics=[…], terms=[…])` → one SanitizeResult per item (tactics first),
    after sanitize.py and then Lean's parser + the whitelist. One REPL command per call."""

    def __init__(self, runner, timeout_s: float = 30.0):
        self.runner = runner
        self.timeout_s = timeout_s

    def check(self, header: str, tactics: list[str] = (), terms: list[str] = ()) -> list[SanitizeResult]:
        items = [Item("tactics", t) for t in tactics] + [Item("term", t) for t in terms]
        out: list[SanitizeResult | None] = []
        for item in items:
            res = check_tactics(item.text) if item.kind == "tactics" else check_term(item.text)
            out.append(None if res.ok else res)
        todo = [i for i, r in enumerate(out) if r is None]
        if todo:
            try:
                code, lines = program(header, [items[i] for i in todo])
            except ValueError:  # unreachable after sanitize.py; fail closed, send nothing to Lean
                code, lines = None, {}
            result = self.runner.verify(code, None, timeout=self.timeout_s) if code is not None else None
            answers = self._answers(result, lines) if result is not None and result.status == "finished" else {}
            for j, i in enumerate(todo):
                ans = answers.get(j)
                if ans is None:
                    out[i] = SanitizeResult(False, "syntax_unavailable",
                                            "parser check did not answer (lean status "
                                            f"{result.status if result is not None else 'not run'})")
                elif ans[0] == "ERROR":
                    out[i] = SanitizeResult(False, "parse", ans[1][:300])
                else:
                    out[i] = check_kinds(ans[1])
        return out

    @staticmethod
    def _answers(result, lines: dict[int, int]) -> dict[int, tuple[str, object]]:
        found: dict[int, list] = {}
        for m in result.messages:
            match = _ANSWER_RE.match(m.text) if m.severity == "info" else None
            first = lines.get(int(match.group(1))) if match else None
            if match and first is not None and m.line in (first, first + 1):  # `open Lean in` / `#eval`
                body = match.group(3)
                value = [k for k in body.split("\n") if k] if match.group(2) == "OK" else body
                found.setdefault(int(match.group(1)), []).append((match.group(2), value))
        return {i: v[0] for i, v in found.items() if len(v) == 1}  # a missing or repeated answer → rejected
