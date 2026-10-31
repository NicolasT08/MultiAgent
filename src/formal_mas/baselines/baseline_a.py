"""Baseline A (C1): the LLM writes the tactic proof directly; only Lean decides.

Per attempt (independent samples, no error feedback; pipeline.yaml → baselines.baseline_a):
    1. the `baseline_a` role (same parameters as the Autoformalizer) returns {"tactics": …};
    2. statement.proof_file builds the file around it (the model never writes the theorem)
       and lean/sanitize.py checks the block, then (if given) lean/syntax_check.py parses it
       with Lean's parser against the node whitelist: a rejection is a failed attempt, logged
       with its reason (`sanitize:<reason>`);
    3. LeanRunner.verify + runner.accepted (Eq. 2).
Stops at the first accepted proof, when the attempts or the budget run out, or on a
provider error. Never raises (CLAUDE.md §7): every failure becomes a result status.

Phase 6 moves C1 onto the shared graph as config/ablations/C1_baseline_a.
"""

from __future__ import annotations

from formal_mas.agents.contracts import TacticProof
from formal_mas.agents.prompting import render
from formal_mas.lean.runner import accepted
from formal_mas.lean.statement import UnsafeLean, clean_tactics, parse_theorem, proof_file
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, SpendLimitReached
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

ROLE = "baseline_a"
__all__ = ["run_baseline_a", "clean_tactics"]


def run_baseline_a(problem: dict, client: LLMClient, runner, budget: Budget, tracer: Tracer,
                   attempts: int = 4, syntax=None) -> dict:
    """→ {status, solved, attempts: [...], proof}. status ∈ solved | failed | provider_error |
    budget_exhausted | budget_stop (spend cap) | invalid_statement | error."""
    out = {"status": "failed", "solved": False, "attempts": [], "proof": None}
    try:
        _run(problem, client, runner, budget, tracer, attempts, syntax, out)
    except Exception as exc:  # noqa: BLE001 — a node never raises to the caller
        tracer.event("node_error", level="error", node=ROLE, error=repr(exc)[:1000])
        out.update(status="error", solved=False, detail=repr(exc)[:1000])
    return out


def _run(problem: dict, client: LLMClient, runner, budget: Budget, tracer: Tracer, attempts: int, syntax,
         out: dict) -> None:
    try:
        thm = parse_theorem(problem["statement"])
    except (ValueError, KeyError) as exc:
        tracer.event("invalid_statement", level="error", error=str(exc)[:500])
        out.update(status="invalid_statement", detail=str(exc)[:500])
        return
    messages = render("baseline_a", header=thm.header.strip(), declaration=thm.declaration(),
                      informal=problem.get("informal", ""))
    for i in range(attempts):
        record = {"sample": i}
        out["attempts"].append(record)
        try:
            proof, _ = client.complete_json(ROLE, messages, TacticProof, budget, sample=i)
        except BudgetExhausted:
            out["attempts"].pop()
            out["status"] = "budget_exhausted"
            return
        except SpendLimitReached as exc:
            out["attempts"].pop()
            out.update(status="budget_stop", detail=str(exc)[:1000])
            return
        except ProviderError as exc:
            out["attempts"].pop()
            out.update(status="provider_error", detail=str(exc)[:1000])
            return
        except LLMOutputError as exc:
            record.update(reason="invalid_output", detail=str(exc)[:500])
            tracer.event("attempt_failed", node=ROLE, **record)
            continue
        tactics = clean_tactics(proof.tactics)
        record["tactics"] = tactics
        try:
            code = proof_file(thm, tactics)
        except UnsafeLean as exc:
            record.update(reason=f"sanitize:{exc.result.reason}", detail=exc.result.detail)
            tracer.event("attempt_failed", node=ROLE, **record)
            continue
        except ValueError as exc:
            record.update(reason="build_failed", detail=str(exc)[:500])
            tracer.event("attempt_failed", node=ROLE, **record)
            continue
        if syntax is not None and not (res := syntax.check(thm.header, tactics=[tactics])[0]).ok:
            record.update(reason=f"sanitize:{res.reason}", detail=res.detail)
            tracer.event("attempt_failed", node=ROLE, **record)
            continue
        result = runner.verify(code, thm.name)
        ok, why = accepted(result)
        record.update(reason=why, lean_status=result.status, lean_errors=[m.text[:300] for m in result.errors[:3]],
                      cached_lean=result.cached)
        tracer.event("attempt_verified" if ok else "attempt_failed", node=ROLE, **record)
        if ok:
            out.update(status="solved", solved=True, proof=tactics)
            return
