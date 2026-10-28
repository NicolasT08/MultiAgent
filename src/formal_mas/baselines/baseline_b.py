"""Baseline B (C2): natural-language reasoning with self-assessment, never verified.

Per round (pipeline.yaml → baselines.baseline_b.attempts), both calls by the `baseline_b`
role (same parameters as the Generator):
    1. solve   → {"solution", "final_answer"}        (prompts/baseline_b_solve.md)
    2. assess  → {"verdict", "confidence", "issues"}  (prompts/baseline_b_assess.md)
The baseline *claims* success when its own verdict is "correct"; Lean never checks it here
(`solved` is None). The false-positive rate FPR_B (Eq. 16) is measured in Phase 8 by
formalizing the claimed proofs. Never raises.
"""

from __future__ import annotations

from formal_mas.agents.contracts import NLSolution, SelfAssessment
from formal_mas.agents.prompting import render
from formal_mas.lean.statement import parse_theorem
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, SpendLimitReached
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

ROLE = "baseline_b"


def run_baseline_b(problem: dict, client: LLMClient, budget: Budget, tracer: Tracer, attempts: int = 2) -> dict:
    """→ {status, solved: None, claimed_solved, rounds: [...], solution}. status ∈
    claimed_solved | claimed_failed | provider_error | budget_exhausted | budget_stop (spend cap) |
    invalid_statement | error."""
    out = {"status": "claimed_failed", "solved": None, "claimed_solved": False, "rounds": [], "solution": None}
    try:
        _run(problem, client, budget, tracer, attempts, out)
    except Exception as exc:  # noqa: BLE001 — a node never raises to the caller
        tracer.event("node_error", level="error", node=ROLE, error=repr(exc)[:1000])
        out.update(status="error", claimed_solved=False, detail=repr(exc)[:1000])
    return out


def _run(problem: dict, client: LLMClient, budget: Budget, tracer: Tracer, attempts: int, out: dict) -> None:
    try:
        thm = parse_theorem(problem["statement"])
    except (ValueError, KeyError) as exc:
        tracer.event("invalid_statement", level="error", error=str(exc)[:500])
        out.update(status="invalid_statement", detail=str(exc)[:500])
        return
    informal = problem.get("informal", "")
    for i in range(attempts):
        rnd: dict = {"sample": i}
        out["rounds"].append(rnd)
        try:
            sol, _ = client.complete_json(ROLE, render("baseline_b_solve", informal=informal,
                                                       declaration=thm.declaration()), NLSolution, budget, sample=i)
            rnd.update(solution=sol.solution, final_answer=sol.final_answer)
            verdict, _ = client.complete_json(ROLE, render("baseline_b_assess", informal=informal,
                                                           solution=sol.solution), SelfAssessment, budget, sample=i)
        except BudgetExhausted:
            out["status"] = "budget_exhausted"
            return
        except SpendLimitReached as exc:
            out.update(status="budget_stop", detail=str(exc)[:1000])
            return
        except ProviderError as exc:
            out.update(status="provider_error", detail=str(exc)[:1000])
            return
        except LLMOutputError as exc:
            rnd.update(reason="invalid_output", detail=str(exc)[:500])
            tracer.event("round_failed", node=ROLE, **rnd)
            continue
        rnd.update(verdict=verdict.verdict, confidence=verdict.confidence, issues=verdict.issues)
        tracer.event("self_assessment", node=ROLE, sample=i, verdict=verdict.verdict, confidence=verdict.confidence)
        if verdict.verdict == "correct":
            out.update(status="claimed_solved", claimed_solved=True, solution=sol.solution,
                       final_answer=sol.final_answer)
            return
