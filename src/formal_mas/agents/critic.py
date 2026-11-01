"""Critic (Phase 6): the feedback that goes back into the prompts after a failed attempt.

The decision (κ, π) is made by orchestrator/policy.py, before anything here runs, and nothing here
can change it. By default the feedback is deterministic: Lean's message (which prints the goal
state), the failed block and, for a missing lemma, real names from Retriever.suggest_names. With
pipeline.yaml → critic.llm_feedback, one `critic` LLM call adds advice to that text; it counts for
b_max, it is the "+1" of the N + 2 calls per attempt in Eq. 13, and if it fails the deterministic
text is used alone. Never raises.
"""

from __future__ import annotations

from formal_mas.agents.contracts import CriticFeedback
from formal_mas.agents.prompting import fragment, render
from formal_mas.orchestrator.policy import Trace
from formal_mas.tracing import Budget, Tracer

ROLE = "critic"
__all__ = ["llm_advice", "replan_feedback", "report", "retry_feedback"]


def report(trace: Trace, detail: str | None = None) -> str:
    """Lean's messages (goal states included) or, without any, the rejection reason."""
    if trace.messages:
        return "\n".join(f"line {line}: {text}" for line, _, text in trace.messages[:3])
    return f"{trace.reason}: {detail}" if detail else trace.reason


def _slot(text: str) -> str:
    """A prompt's feedback slot sits right before its last instruction."""
    return text.strip() + "\n\n"


def retry_feedback(error_class: str, trace: Trace, *, attempt: int, tactics: str | None, detail: str | None = None,
                   suggestions: list[str] | None = None, advice: str | None = None) -> str:
    extra = fragment("real_names", names=", ".join(suggestions)) if suggestions else ""
    extra += fragment("advice", advice=advice) if advice else ""
    return _slot(render("feedback_retry", attempt=attempt, error_class=error_class,
                        hint=fragment(f"hint_{error_class}"), tactics=tactics or fragment("no_tactics"),
                        report=report(trace, detail), suggestions=extra)[0]["content"])


def replan_feedback(failure: Trace, *, subgoals: list[str], stage: str, attempts: int, error_class: str,
                    detail: str | None = None) -> str:
    if attempts:
        reason = fragment("replan_subgoal", stage=stage, attempts=attempts, error_class=error_class,
                          report=report(failure, detail))
    elif failure.messages:
        reason = fragment("replan_plan", reason=failure.reason, report=report(failure))
    else:
        reason = failure.reason
    listed = "\n".join(f"{i}. {s}" for i, s in enumerate(subgoals, start=1)) or fragment("no_subgoals")
    return _slot(render("feedback_replan", reason=reason, subgoals=listed)[0]["content"])


def llm_advice(client, budget: Budget, tracer: Tracer, *, lemma: str, error_class: str, text: str,
               sample: int = 0) -> str | None:
    """Optional advice from the `critic` role; None on any failure (the budget and spend checks of
    the next call stop the run if those were the cause)."""
    try:
        messages = render(ROLE, lemma=lemma, error_class=error_class, report=text)
        advice, _ = client.complete_json(ROLE, messages, CriticFeedback, budget, sample=sample)
        return " ".join(advice.feedback.split())[:1000] or None
    except Exception as exc:  # noqa: BLE001 — the feedback is optional, a node never raises
        tracer.event("critic_llm_failed", level="warning", node=ROLE, error=repr(exc)[:500])
        return None
