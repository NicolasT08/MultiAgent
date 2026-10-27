"""Reject unsafe or hopeless generated Lean before it is compiled.

Models produce two kinds of text (CLAUDE.md §1.3): tactic blocks, pasted after `:= by`
under a statement built by lean/statement.py, and subgoal propositions, pasted as
`have hᵢ : P := by`. Neither may leave its slot. `check_tactics` / `check_term` reject:

    empty           nothing to compile
    tab             tab characters (Lean rejects them; they also break the column rule)
    string          string literals (also inside char literals such as '"') and «quoted»
                    names: they could hide tokens from this scanner (e.g. `"--"`), and
                    proofs do not need them
    brackets        unbalanced ()[]{}⦃⦄⟨⟩ in a proposition (it could close the binder it is
                    pasted into and add hypotheses to a subgoal lemma)
    comment         an unterminated block comment (it would swallow the runner's checks)
    escape          text that could end the proof and start a new command: a line indented
                    less than the block's first line, command keywords (`theorem`, `def`,
                    `namespace`, `end`, `where`, …), `#` commands, `@[…]`, `open` without `in`
    option          `set_option` without `in`, or an option outside SAFE_OPTIONS (`debug.*`
                    could skip the kernel)
    metaprogramming `macro`, `syntax`, `elab`, `notation`, `run_cmd`, `run_tac`, `unsafe`,
                    `implemented_by`, `extern`, `axiom`, the `Lean.*` namespace, …
    io              `IO` and friends in any form (generated code must never run programs)
    sorry           `sorry`, `admit`
    native          `native_decide`, `decide +native`, `ofReduceBool` (extra axioms)
    lean3           Lean 3 syntax (`begin`, `assume`, `cases h with x`, `nat.succ_le_iff`)

This is a first line of defence, not the judge: the runner still requires that Lean
accepts the proof, that `#print axioms` is standard and that the only new declarations
are the theorem and its auxiliaries (lean/runner.py). The scanner errs on the side of
rejecting; the Critic sees the reason and asks for a new attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SAFE_OPTIONS = frozenset({
    "maxHeartbeats", "maxRecDepth", "synthInstance.maxHeartbeats", "synthInstance.maxSize",
    "exponentiation.threshold",
})

# Keywords that start (or belong only to) top-level commands: inside a tactic block they
# would end the proof and run a new command.
COMMAND_KEYWORDS = frozenset({
    "theorem", "lemma", "def", "example", "abbrev", "instance", "structure", "class", "inductive",
    "opaque", "noncomputable", "private", "protected", "partial", "mutual", "namespace", "section",
    "end", "variable", "universe", "export", "import", "attribute", "deriving", "omit", "include",
    "where", "local", "scoped", "initialize", "builtin_initialize", "alias", "irreducible_def",
    "add_decl_doc", "library_note", "register_option", "register_simp_attr", "compile_inductive",
    "suppress_compilation", "unseal", "seal", "recall", "assert_not_exists", "proof_wanted",
    "initialize_simps_projections", "unif_hint",
})
METAPROGRAMMING = frozenset({
    "macro", "macro_rules", "syntax", "elab", "elab_rules", "notation", "notation3", "infix", "infixl", "infixr",
    "simproc", "dsimproc", "simproc_decl", "dsimproc_decl", "add_aesop_rules", "declare_aesop_rule_sets",
    # namespaces whose APIs reach the elaborator / environment (also blocked in `open … in`)
    "Mathlib", "Batteries", "Aesop", "Std", "Qq",
    "prefix", "postfix", "declare_syntax_cat", "run_cmd", "run_tac", "run_elab", "run_meta", "by_elab",
    "unsafe", "implemented_by", "extern", "axiom", "Lean", "Elab", "Meta", "Environment", "addDecl",
    "addAndCompile", "modifyEnv", "setEnv", "evalTactic", "liftMetaTactic", "debug",
})
IO_NAMES = frozenset({"IO", "EIO", "BaseIO", "unsafeIO", "unsafeEIO", "unsafeBaseIO", "System", "FilePath"})
SORRY = frozenset({"sorry", "admit"})
NATIVE = frozenset({"native_decide", "native", "ofReduceBool", "ofReduceNat"})

_PAIRS = {"(": ")", "[": "]", "{": "}", "⦃": "⦄", "⟨": "⟩"}
_IDENT_RE = re.compile(r"(?<![\w'!?])[^\W\d][\w'!?]*(?:\.[^\W\d][\w'!?]*)*")
_HASH_COMMAND_RE = re.compile(r"#[A-Za-z_]\w{2,}")  # #eval, #print, #exit… (card `#s` is allowed)
_OPEN_RE = re.compile(r"\bopen\s+((?:[^\W\d][\w.']*\s+)+?)in\b")
_SET_OPTION_RE = re.compile(r"\bset_option\s+([\w.]+)\s+\S+\s+in\b")
_LEAN3_PATTERNS = [
    (re.compile(r"(?<![\w.'])begin(?![\w'])"), "`begin … end` block"),
    (re.compile(r"(?<![\w.'])assume(?![\w'])"), "`assume` (use `intro`)"),
    # Lean 4 `cases h with` / `induction n with` continue with `| alt`; `cases'` is Mathlib
    (re.compile(r"(?<![\w.'])(?:cases|induction)(?![\w'])[^\n|]*?\bwith[ ]+[^\W\d]"),
     "`cases h with x` (use `obtain ⟨x, hx⟩ := h` or `| …` alternatives)"),
    (re.compile(r"(?<![\w.'])(?:nat|int|real|rat|complex|finset|set|list|multiset|polynomial)\.[a-z_]"),
     "lowercase Lean 3 namespace (e.g. `nat.` → `Nat.`)"),
]


@dataclass(frozen=True)
class SanitizeResult:
    ok: bool
    reason: str = ""
    detail: str = ""


OK = SanitizeResult(True)


def _reject(reason: str, detail: str) -> SanitizeResult:
    return SanitizeResult(False, reason, detail)


def strip_comments(text: str) -> str | None:
    """Blank out `--` and (nested) `/- … -/` comments, keeping newlines and columns.
    None if a block comment is not closed. Assumes no string literals (rejected first)."""
    out, i, depth, n = [], 0, 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if depth:
            if two == "/-":
                depth, step = depth + 1, 2
            elif two == "-/":
                depth, step = depth - 1, 2
            else:
                step = 1
            out.append("".join(c if c == "\n" else " " for c in text[i:i + step]))
            i += step
        elif two == "--":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/-":
            depth = 1
            out.append("  ")
            i += 2
        else:
            out.append(text[i])
            i += 1
    return None if depth else "".join(out)


def _identifiers(code: str) -> list[str]:
    return _IDENT_RE.findall(code)


def _check_tokens(code: str) -> SanitizeResult:
    """Checks shared by tactic blocks and terms, on comment-free code."""
    for pattern, what in _LEAN3_PATTERNS:
        if m := pattern.search(code):
            return _reject("lean3", f"{what}: {m.group(0)!r}")
    if m := _HASH_COMMAND_RE.search(code):
        return _reject("escape", f"`#` command {m.group(0)!r}")
    if "@[" in code:
        return _reject("escape", "attribute `@[…]`")
    for ident in _identifiers(code):
        parts = ident.split(".")
        if parts[0] in SORRY:
            return _reject("sorry", ident)
        if any(p in NATIVE for p in parts):
            return _reject("native", ident)
        if any(p in IO_NAMES for p in parts):
            return _reject("io", ident)
        if parts[0] in METAPROGRAMMING or "debug" in parts:
            return _reject("metaprogramming", ident)
        if parts[0] in COMMAND_KEYWORDS and len(parts) == 1:
            return _reject("escape", f"command keyword `{ident}`")
    for m in re.finditer(r"\bopen\b", code):
        opened = _OPEN_RE.match(code, m.start())
        if not opened:
            return _reject("escape", "`open` without `… in`")
        if any(n.split(".")[0] in METAPROGRAMMING | IO_NAMES for n in opened.group(1).split()):
            return _reject("metaprogramming", f"open {opened.group(1).strip()}")
    for m in re.finditer(r"\bset_option\b", code):
        opt = _SET_OPTION_RE.match(code, m.start())
        if not opt:
            return _reject("option", "`set_option` without `… in`")
        if opt.group(1) not in SAFE_OPTIONS:
            return _reject("option", f"option `{opt.group(1)}` is not allowed")
    return OK


def _prepare(text: str) -> tuple[str | None, SanitizeResult]:
    if not text.strip():
        return None, _reject("empty", "nothing to compile")
    if "\t" in text:
        return None, _reject("tab", "tab character")
    code = strip_comments(text)
    if code is None:
        return None, _reject("comment", "unterminated block comment `/-`")
    for ch, what in (('"', "string literal"), ("«", "quoted name «…»"), ("»", "quoted name «…»")):
        if ch in code:
            return None, _reject("string", what)
    return code, OK


def check_tactics(block: str) -> SanitizeResult:
    """A tactic block to paste after `:= by` (any common indentation; statement.py
    re-indents it). Every line must be indented at least as much as the first one."""
    code, res = _prepare(block)
    if code is None:
        return res
    lines = block.split("\n")
    first = next(line for line in lines if line.strip())
    base = len(first) - len(first.lstrip(" "))
    for no, line in enumerate(lines, start=1):
        if line.strip() and len(line) - len(line.lstrip(" ")) < base:
            return _reject("escape", f"line {no} is indented less than the block: {line.strip()[:60]!r}")
    return _check_tokens(code)


def check_term(term: str) -> SanitizeResult:
    """A proposition to paste as `have hᵢ : <term> := by` (one line, no `:=`)."""
    code, res = _prepare(term)
    if code is None:
        return res
    if "\n" in term.strip():
        return _reject("escape", "a proposition must fit on one line")
    if ":=" in code:
        return _reject("escape", "`:=` in a proposition")
    stack = []
    for ch in code:
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif ch in _PAIRS.values():
            if not stack or stack.pop() != ch:
                return _reject("brackets", f"unbalanced `{ch}`")
    if stack:
        return _reject("brackets", f"unclosed bracket (expected `{stack[-1]}`)")
    return _check_tokens(code)
