"""Theorem statements and the Lean files built around them (CLAUDE.md §1.3).

Models never write statements or skeletons. They only provide text for three slots, and
each piece is checked by lean/sanitize.py before it is pasted:
    subgoal propositions  `have hᵢ : <P> := by`        (check_term)
    tactic blocks          the proof of a subgoal/goal  (check_tactics)
    closing tactic         after the `have`s            (check_tactics)

Files built here (all start with the problem's header, default DEFAULT_HEADER):
    proof_file      theorem <name> <binders> : <goal> := by <block>
    skeleton        the theorem with `have hᵢ : Pᵢ := by sorry` and the closing tactic
                    (or `sorry`): compiled to check that the plan type-checks
    subgoal_lemma   theorem <name>_sg<i> <binders> (h1 : P1) … (h(i-1) : P(i-1)) : Pᵢ := by
                    <block> — the block, unchanged, is later pasted into the skeleton
    assemble        the skeleton with every block pasted and the closing tactic
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from formal_mas.lean.sanitize import SanitizeResult, check_tactics, check_term, strip_comments

# miniF2F's header (the evaluation set comes from miniF2F, PLAN.md Phase 7).
DEFAULT_HEADER = (
    "import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\n"
    "open BigOperators Real Nat Topology Rat\n"
)

_OPEN, _CLOSE = "([{⦃⟨", ")]}⦄⟩"
_NAME_RE = re.compile(r"[^\W\d][\w'.!?]*")
_DECL_RE = re.compile(r"(?<![\w.'])(theorem|lemma)\s+")


class UnsafeLean(ValueError):
    """Generated text rejected by lean/sanitize.py; `.result` says why."""

    def __init__(self, slot: str, result: SanitizeResult):
        super().__init__(f"{slot}: {result.reason}: {result.detail}")
        self.slot = slot
        self.result = result


@dataclass(frozen=True)
class Theorem:
    name: str
    binders: tuple[str, ...]  # one bracket group each, e.g. "(x : ℝ)", "(h₀ : 0 < x)"
    goal: str  # the type after the top-level colon
    header: str = DEFAULT_HEADER

    def declaration(self, name: str | None = None, extra: tuple[str, ...] = (), goal: str | None = None) -> str:
        """`theorem <name> <binders> <extra> : <goal>` (no proof)."""
        parts = [f"theorem {name or self.name}", *self.binders, *extra]
        return " ".join(parts) + f" :\n    {goal if goal is not None else self.goal}"

    @property
    def binder_names(self) -> list[str]:
        names = []
        for group in self.binders:
            inner = group[1:-1]
            depth, colon = 0, None
            for i, ch in enumerate(inner):
                depth += ch in _OPEN
                depth -= ch in _CLOSE
                if ch == ":" and depth == 0 and inner[i + 1:i + 2] != "=":
                    colon = i
                    break
            if colon is None:
                if group[0] in "{⦃":  # {x y} / ⦃x y⦄ (type inferred)
                    names += inner.split()
                continue
            names += [n for n in inner[:colon].split() if _NAME_RE.fullmatch(n)]
        return names

    def hypothesis_names(self, n: int) -> list[str]:
        """Names of the subgoal hypotheses h1 … hn (hsg1 …, hsg_1 … if a binder already uses them)."""
        taken = set(self.binder_names)
        for prefix in ("h", "hsg", "hsg_"):
            names = [f"{prefix}{i}" for i in range(1, n + 1)]
            if not taken.intersection(names):
                return names
        raise ValueError(f"{self.name}: binders already use h/hsg/hsg_ hypothesis names")


def _read_group(text: str, i: int) -> int:
    """Index just after the bracket group starting at text[i]."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] in _OPEN:
            depth += 1
        elif text[j] in _CLOSE:
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError(f"unbalanced brackets in binders: {text[i:i + 60]!r}")


def _top_level(text: str, token: str) -> list[int]:
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and text.startswith(token, i):
            out.append(i)
    return out


def parse_theorem(src: str, header: str | None = None) -> Theorem:
    """Parse a curated statement such as miniF2F's
    `theorem mathd_algebra_10 (x : ℝ) (h₀ : 0 < x) : x / x = 1 := by sorry`.
    Text before `theorem` (imports, `open`, `set_option`) becomes the header unless
    `header` is given; an empty one falls back to DEFAULT_HEADER."""
    code = strip_comments(src)
    if code is None:
        raise ValueError("unterminated block comment in the statement")
    m = _DECL_RE.search(code)
    if not m:
        raise ValueError("no `theorem` or `lemma` found")
    if header is None:
        header = src[:m.start()].strip("\n")
        header = header + "\n" if header.strip() else DEFAULT_HEADER
    rest = code[m.end():]
    name_m = _NAME_RE.match(rest)
    if not name_m:
        raise ValueError(f"no theorem name in {rest[:60]!r}")
    name, i = name_m.group(0), name_m.end()
    binders = []
    while True:
        while i < len(rest) and rest[i].isspace():
            i += 1
        if i < len(rest) and rest[i] in "([{⦃":
            j = _read_group(rest, i)
            binders.append(" ".join(rest[i:j].split()))
            i = j
            continue
        break
    if not rest.startswith(":", i) or rest.startswith(":=", i):
        raise ValueError(f"expected `:` after the binders of {name}")
    tail = rest[i + 1:]
    ends = _top_level(tail, ":=")
    goal = tail
    if ends:  # the proof must be a placeholder: never guess where a real proof starts
        placeholder = [e for e in ends if re.fullmatch(r"\s*(?:by\s+)?(?:sorry)?\s*", tail[e + 2:])]
        if not placeholder:
            raise ValueError(f"{name}: the statement must end with `:= by sorry` (or have no proof)")
        goal = tail[:placeholder[0]]
    goal = " ".join(goal.split())
    if not goal:
        raise ValueError(f"empty goal in {name}")
    return Theorem(name=name, binders=tuple(binders), goal=goal, header=header)


def indent(block: str, spaces: int) -> str:
    """Dedent a block and indent every non-empty line by `spaces`."""
    lines = textwrap.dedent(block.strip("\n")).split("\n")
    return "\n".join((" " * spaces + line) if line.strip() else "" for line in lines)


def _checked_tactics(slot: str, block: str) -> str:
    res = check_tactics(block)
    if not res.ok:
        raise UnsafeLean(slot, res)
    return block


def _checked_terms(subgoals: list[str]) -> list[str]:
    for i, p in enumerate(subgoals, start=1):
        res = check_term(p)
        if not res.ok:
            raise UnsafeLean(f"subgoal {i}", res)
    return [" ".join(p.split()) for p in subgoals]


def proof_file(thm: Theorem, block: str) -> str:
    """The theorem proved by one tactic block. Raises UnsafeLean."""
    block = _checked_tactics("proof", block)
    return f"{thm.header}\n{thm.declaration()} := by\n{indent(block, 2)}\n"


def skeleton(thm: Theorem, subgoals: list[str], closing: str | None = None) -> str:
    """The plan with every proof replaced by `sorry` (compile it and test `compiles`)."""
    subgoals = _checked_terms(subgoals)
    names = thm.hypothesis_names(len(subgoals))
    body = [f"  have {h} : {p} := by\n    sorry" for h, p in zip(names, subgoals)]
    body.append(indent(_checked_tactics("closing", closing), 2) if closing else "  sorry")
    return f"{thm.header}\n{thm.declaration()} := by\n" + "\n".join(body) + "\n"


def subgoal_lemma(thm: Theorem, subgoals: list[str], i: int, block: str) -> tuple[str, str]:
    """Subgoal i (1-based) as a standalone lemma → (lemma name, file)."""
    subgoals = _checked_terms(subgoals)
    if not 1 <= i <= len(subgoals):
        raise IndexError(f"subgoal {i} of {len(subgoals)}")
    block = _checked_tactics(f"subgoal {i} proof", block)
    names = thm.hypothesis_names(len(subgoals))
    extra = tuple(f"({h} : {p})" for h, p in zip(names[:i - 1], subgoals[:i - 1]))
    name = f"{thm.name}_sg{i}"
    decl = thm.declaration(name=name, extra=extra, goal=subgoals[i - 1])
    return name, f"{thm.header}\n{decl} := by\n{indent(block, 2)}\n"


def assemble(thm: Theorem, subgoals: list[str], blocks: list[str], closing: str) -> str:
    """The final proof: every subgoal block pasted into the skeleton, then `closing`."""
    subgoals = _checked_terms(subgoals)
    if len(blocks) != len(subgoals):
        raise ValueError(f"{len(blocks)} proofs for {len(subgoals)} subgoals")
    names = thm.hypothesis_names(len(subgoals))
    body = [f"  have {h} : {p} := by\n{indent(_checked_tactics(f'subgoal {k} proof', b), 4)}"
            for k, (h, p, b) in enumerate(zip(names, subgoals, blocks), start=1)]
    body.append(indent(_checked_tactics("closing", closing), 2))
    return f"{thm.header}\n{thm.declaration()} := by\n" + "\n".join(body) + "\n"
