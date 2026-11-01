"""Error classifier κ (Eq. 11) and control policy π (Eq. 12): pure rules, no LLM (CLAUDE.md §1.4).

κ : T → E, with E = {syntax, missing_lemma, tactic_failure, misformalization, timeout, planning}.
The first rule that matches decides:
    planning          the Planner's `refuted:sg<i>`, or an assembled proof that was not accepted
                      although all its pieces were (`assemble:…`, the side condition of Lemma 1);
    syntax            a sanitize / parser rejection, unusable model output (`invalid_output`,
                      `no_tactics`), or a block Lean compiled but Eq. 2 forbids (`sorry`,
                      non-standard axioms, extra declarations);
    timeout           the runner's `timeout` / `memory_limit` / `crash`, or a Lean message about
                      heartbeats or recursion depth;
    misformalization  a Lean error located inside the lemma's statement, strictly before the
                      `:=` that ends it: the statement does not elaborate, so no tactic block can
                      help. (`unsolved goals` is reported at the `by`, after the `:=`: not this.);
    syntax            a parse error in the message;
    missing_lemma     an unknown identifier, constant or namespace, or an invalid field;
    tactic_failure    anything else (unsolved goals, type mismatch, linarith / omega failed…).
Across the errors of one compilation the classes are tried in that order. Eq. 11 also maps a
subgoal that failed with k_max distinct strategies to planning (`escalate`, which needs the history
the trace does not carry). With k_max = 1 (C4) one failure is not a strategy exhausted, so it stays.

π(e, a_i, r, b), exactly Eq. 12:
    abandon  if b ≥ B_max or (a_i ≥ k_max and r ≥ R_max);
    replan   if (a_i ≥ k_max or e = planning) and r < R_max;
    retry(e) otherwise: syntax → FORMALIZE (the sketch is kept), missing_lemma → RETRIEVE
             (query expanded with the identifier, k increased), anything else → GENERATE.
Every back edge of the graph consumes an attempt or a replan, or ends: Proposition 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ERROR_CLASSES = ("syntax", "missing_lemma", "tactic_failure", "misformalization", "timeout", "planning")
__all__ = ["Action", "ERROR_CLASSES", "Limits", "Trace", "classify", "decide", "escalate", "missing_identifier",
           "structural_bound"]

_RESOURCE_STATUS = ("timeout", "memory_limit", "crash")
_FORBIDDEN_PREFIXES = ("sanitize:", "invalid_output", "no_tactics", "sorry", "nonstandard_axioms",
                       "extra_declarations", "axioms_unknown", "declarations_unknown")
_TIMEOUT_RE = re.compile(r"deterministic\) timeout|maximum number of heartbeats|maximum recursion depth|deep recursion", re.I)
_SYNTAX_RE = re.compile(r"\bunexpected (?:token|identifier|end of input)\b|(?:^|; )expected\b|\bunknown tactic\b", re.I)
_MISSING_RE = re.compile(r"\bunknown (?:identifier|constant|namespace)\b|\binvalid field\b", re.I)
_NAME_RE = re.compile(r"(?:unknown (?:identifier|constant|namespace)\s+|does not contain\s+)[`‘]([^`’\s]+)[`’]", re.I)
_FIELD_RE = re.compile(r"invalid field\s+[`‘]([^`’\s]+)[`’]", re.I)


@dataclass(frozen=True)
class Trace:
    """What κ reads. `reason`: the node's failure reason, with `lean:` for `runner.accepted`'s.
    `messages`: Lean's errors as (line, column, text). `statement_end`: position of the `:=` that
    ends the lemma statement, or None when the code built no lemma."""

    reason: str
    messages: tuple[tuple[int, int, str], ...] = ()
    statement_end: tuple[int, int] | None = None


@dataclass(frozen=True)
class Limits:
    k_max: int
    r_max: int
    b_max: int


@dataclass(frozen=True)
class Action:
    kind: str  # abandon | replan | retry
    route: str | None = None  # FORMALIZE | RETRIEVE | GENERATE when kind == retry


def classify(trace: Trace) -> str:
    """κ: deterministic, a function of `trace` only."""
    reason = trace.reason.removeprefix("lean:")
    if reason.startswith(("refuted:", "assemble")):
        return "planning"
    if reason.startswith(_FORBIDDEN_PREFIXES):
        return "syntax"
    if reason.startswith(_RESOURCE_STATUS):
        return "timeout"
    texts = [m[2] for m in trace.messages]
    if any(_TIMEOUT_RE.search(t) for t in texts):
        return "timeout"
    if trace.statement_end is not None and any(1 <= m[0] and (m[0], m[1]) < tuple(trace.statement_end)
                                               for m in trace.messages):
        return "misformalization"
    if any(_SYNTAX_RE.search(t) for t in texts):
        return "syntax"
    if any(_MISSING_RE.search(t) for t in texts):
        return "missing_lemma"
    return "tactic_failure"


def escalate(error_class: str, strategies: int, k_max: int) -> str:
    """Eq. 11: a subgoal that failed with k_max distinct strategies is a planning error."""
    return "planning" if k_max > 1 and strategies >= k_max else error_class


def missing_identifier(trace: Trace) -> str | None:
    """The first name Lean says does not exist (for the RETRIEVE query and the suggestions)."""
    for _, _, text in trace.messages:
        if _MISSING_RE.search(text):
            m = _NAME_RE.search(text) or _FIELD_RE.search(text)
            if m:
                return m.group(1)
    return None


def decide(error_class: str, attempts: int, replans: int, calls: int, limits: Limits) -> Action:
    """π (Eq. 12): deterministic, a function of its arguments only."""
    if error_class not in ERROR_CLASSES:
        raise ValueError(f"unknown error class {error_class!r}")
    if calls >= limits.b_max or (attempts >= limits.k_max and replans >= limits.r_max):
        return Action("abandon")
    if (attempts >= limits.k_max or error_class == "planning") and replans < limits.r_max:
        return Action("replan")
    route = {"syntax": "FORMALIZE", "missing_lemma": "RETRIEVE"}.get(error_class, "GENERATE")
    return Action("retry", route)


def structural_bound(limits: Limits, *, n_max: int, samples: int, b_p: int = 1) -> int:
    """Proposition 1 (Eq. 13): C ≤ min{B_max, (R_max + 1)(b_P + n_max · k_max · (N + 2))}."""
    return min(limits.b_max, (limits.r_max + 1) * (b_p + n_max * limits.k_max * (samples + 2)))
