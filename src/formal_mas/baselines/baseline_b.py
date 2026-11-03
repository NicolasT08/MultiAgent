"""Baseline B (C2): natural-language reasoning with self-assessment, never verified.

Per round (pipeline.yaml → baselines.baseline_b.attempts), both calls by the `baseline_b`
role (same parameters as the Generator):
    1. solve   → {"solution", "final_answer"}        (prompts/baseline_b_solve.md)
    2. assess  → {"verdict", "confidence", "issues"}  (prompts/baseline_b_assess.md)
The baseline *claims* success when its own verdict is "correct"; Lean never checks it here
(`solved` is None). Never raises.

FPR_B (Eq. 16; docs/analysis_plan.md §4.2) is measured by `check_claim` (scripts/run_baseline_b.py
--check): each claimed proof goes through the Autoformalizer–Verifier loop of the full system with
the same limits as C5, and only Lean's acceptance of the assembled theorem (Eq. 2) counts:
    - the trivial plan (the whole goal as the only subgoal; no Planner, no Generator, no pre-pass,
      so no fixed tactic can prove the theorem in place of the claimed proof);
    - the claimed solution is the Autoformalizer's sketch; the Retriever's lemmas as in C5;
    - up to `attempts` (= critic.k_max) Autoformalizer attempts; after a failure κ classifies it and the
      Critic's deterministic feedback goes into the next attempt (missing_lemma: expanded query and
      real names), no LLM advice;
    - its own Budget of B_max calls (counted apart from C2's own calls), the same spend guard; in practice
      at most k_max × 2 calls (one Autoformalizer call and at most one JSON repair per attempt) = 6.
"""

from __future__ import annotations

from formal_mas.agents.autoformalizer import run_formalize, run_verify
from formal_mas.agents.contracts import NLSolution, SelfAssessment
from formal_mas.agents.critic import retry_feedback
from formal_mas.agents.planner import trivial_plan
from formal_mas.agents.prompting import fragment, render
from formal_mas.lean.runner import accepted
from formal_mas.lean.statement import assemble, parse_theorem, subgoal_statement
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, SpendLimitReached
from formal_mas.orchestrator.policy import Trace, classify, missing_identifier
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

ROLE = "baseline_b"
CHECK = "baseline_b_check"
_STOPS = ("budget_exhausted", "budget_stop", "provider_error", "error")


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


def check_claim(problem: dict, solution: str, client: LLMClient, runner, retriever, budget: Budget, tracer: Tracer, *,
                attempts: int = 3, syntax=None, rag: bool = True, k_retry: int = 32) -> dict:
    """Formalize one claimed proof (see the module docstring) → {status, accepted, proof, attempts: [...],
    error_classes}. status ∈ verified | not_verified | budget_exhausted | budget_stop | provider_error |
    invalid_statement | error; only `verified` means Acc (Eq. 2) of the assembled theorem."""
    out = {"status": "not_verified", "accepted": False, "proof": None, "attempts": [], "error_classes": []}
    try:
        _check(problem, solution, client, runner, retriever, budget, tracer, attempts, syntax, rag, k_retry, out)
    except Exception as exc:  # noqa: BLE001 — a node never raises to the caller
        tracer.event("node_error", level="error", node=CHECK, error=repr(exc)[:1000])
        out.update(status="error", accepted=False, proof=None, detail=repr(exc)[:1000])
    tracer.event("claim_check_end", node=CHECK, status=out["status"], accepted=out["accepted"],
                 attempts=len(out["attempts"]), error_classes=out["error_classes"])
    return out


def _check(problem, solution, client, runner, retriever, budget, tracer, attempts, syntax, rag, k_retry, out) -> None:
    try:
        thm = parse_theorem(problem["statement"])
    except (ValueError, KeyError) as exc:
        out.update(status="invalid_statement", detail=str(exc)[:500])
        return
    plan = trivial_plan(thm, CHECK)
    _, decl = subgoal_statement(thm, plan.subgoals, 1)
    sketch = {"strategy": fragment("check_strategy"), "sketch": solution}
    feedback, expand = "", None
    for a in range(attempts):
        hits = []
        if rag and retriever is not None:
            hits = retriever.retrieve(f"{decl} {expand}", k=k_retry) if expand else retriever.retrieve(decl)
        res = run_formalize(problem, plan, 1, sketch, client, retriever, budget, tracer, retrieve=False,
                            syntax=syntax, hits=hits, feedback=feedback, sample=a)
        if res["status"] == "candidate":
            res = run_verify(res, runner, tracer)
        rec = {"attempt": a + 1, "status": res["status"], "reason": res["reason"], "tactics": res["tactics"],
               "lemmas": res["lemmas"], "errors": res["errors"]}
        out["attempts"].append(rec)
        if res["status"] in _STOPS:
            out.update(status=res["status"], detail=res["detail"])
            return
        if res["status"] == "ok":  # the lemma is the whole goal: assemble the theorem and check Eq. 2 on it
            code = assemble(thm, plan.subgoals, [res["tactics"]], plan.closing)
            result = runner.verify(code, thm.name)
            ok, why = accepted(result)
            rec["assembled"] = why
            if ok:
                out.update(status="verified", accepted=True, proof=code)
            else:  # nothing left to retry: the block itself was accepted
                out["error_classes"].append(classify(Trace(f"assemble:{why}")))
            return
        trace = Trace(str(res["reason"]), tuple((m[0], m[1], m[2]) for m in res["messages"]),
                      tuple(res["statement_end"]) if res["statement_end"] else None)
        error_class = classify(trace)
        rec["error_class"] = error_class
        out["error_classes"].append(error_class)
        tracer.event("claim_check_attempt", node=CHECK, attempt=a + 1, reason=res["reason"], error_class=error_class)
        names, expand = [], None
        if error_class == "missing_lemma":
            expand = missing_identifier(trace)
            if expand and rag and retriever is not None:
                names = retriever.suggest_names(expand) or retriever.suggest_names(expand.rpartition(".")[0] or expand)
        feedback = retry_feedback(error_class, trace, attempt=a + 1, tactics=res["tactics"], detail=res["detail"],
                                  suggestions=names)
