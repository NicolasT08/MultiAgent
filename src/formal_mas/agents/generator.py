"""Generator (Phase 4): N natural-language sketches per subgoal + strategy vote (Eqs. 8-9).

    out = run_generator(problem, plan, client, retriever, budget, tracer, samples=3)

For each subgoal i of the plan (in order):
1. Retriever query = the subgoal as a lemma statement (`statement.subgoal_statement`: the
   theorem's binders, h1 … h(i-1), and Pᵢ), top-k lemmas (pipeline.yaml → generator.retrieve).
2. `samples` calls of the `generator` role (self-consistency; sample j is a distinct cache
   entry), each → `SketchProposal` {strategy ∈ STRATEGIES, sketch, lemmas}. Every sample
   is one LLM call for b_max (plus a JSON repair call when needed).
3. Vote (Eq. 9): ŝ = argmax_s Σ_j 1[strategy_j = s]; ties broken by the order of
   contracts.STRATEGIES (independent of the sampling order). The representative sketch is
   the lowest-index sample with strategy ŝ.
Stops early on budget exhaustion (the sketches obtained so far are still voted), a spend
stop or a provider error (status of that subgoal; the remaining subgoals are not sketched).
Never raises: {status, subgoals: [SubgoalSketches…], detail}. status ∈ ok | partial |
budget_exhausted | budget_stop | provider_error | invalid_statement | error.
"""

from __future__ import annotations

from collections import Counter

from formal_mas.agents.contracts import STRATEGIES, Plan, Sketch, SketchProposal, SubgoalSketches
from formal_mas.agents.planner import lemma_block
from formal_mas.agents.prompting import fragment, render
from formal_mas.lean.statement import Theorem, UnsafeLean, parse_theorem, subgoal_statement
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, SpendLimitReached
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

ROLE = "generator"
STOPS = ("budget_exhausted", "budget_stop", "provider_error")
__all__ = ["run_generator", "vote"]


def vote(samples: list[Sketch]) -> tuple[dict[str, int], str | None, Sketch | None]:
    """Eq. 9 → (votes per strategy, winning strategy, representative sketch). Deterministic:
    ties go to the strategy listed first in STRATEGIES; the representative is the
    lowest-index sample of the winner."""
    if not samples:
        return {}, None, None
    counts = Counter(s.strategy for s in samples)
    best = max(counts.values())
    winner = next(s for s in STRATEGIES if counts.get(s) == best)
    rep = min((s for s in samples if s.strategy == winner), key=lambda s: s.sample)
    return {s: counts[s] for s in STRATEGIES if s in counts}, winner, rep


def _plan_text(plan: Plan) -> str:
    lines = [f"{i}. {h} : {p}" for i, (h, p) in enumerate(zip(plan.hypotheses, plan.subgoals), start=1)]
    lines.append(fragment("closing_step", closing=plan.closing))
    return "\n".join(lines)


def _sketch_subgoal(problem: dict, thm: Theorem, plan: Plan, i: int, client: LLMClient, retriever, budget: Budget,
                    tracer: Tracer, samples: int, retrieve: bool, hits=None, feedback: str = "",
                    sample_offset: int = 0) -> SubgoalSketches:
    res = SubgoalSketches(index=i, subgoal=plan.subgoals[i - 1])
    _, target = subgoal_statement(thm, plan.subgoals, i)
    if hits is None:
        hits = retriever.retrieve(target) if (retrieve and retriever is not None) else []
    res.lemmas = [h.name for h in hits]
    messages = render(ROLE, informal=problem.get("informal", "") or fragment("no_informal"), theorem=thm.declaration(),
                      plan=_plan_text(plan), index=i, count=len(plan.subgoals), target=target,
                      lemmas=lemma_block(hits), strategies=", ".join(STRATEGIES), feedback=feedback)
    for j in range(samples):
        try:
            prop, _ = client.complete_json(ROLE, messages, SketchProposal, budget, sample=sample_offset + j)
        except BudgetExhausted:
            res.status = "budget_exhausted"
            break
        except SpendLimitReached as exc:
            res.status = "budget_stop"
            res.failures.append({"sample": j, "reason": "budget_stop", "detail": str(exc)[:500]})
            break
        except ProviderError as exc:
            res.status = "provider_error"
            res.failures.append({"sample": j, "reason": "provider_error", "detail": str(exc)[:500]})
            break
        except LLMOutputError as exc:
            res.failures.append({"sample": j, "reason": "invalid_output", "detail": str(exc)[:500]})
            tracer.event("sketch_failed", node=ROLE, subgoal=i, sample=j, reason="invalid_output")
            continue
        res.samples.append(Sketch(sample=j, **prop.model_dump()))
    res.votes, res.strategy, res.representative = vote(res.samples)
    if res.status == "ok" and not res.samples:
        res.status = "no_sketch"
    tracer.event("sketch_vote", node=ROLE, subgoal=i, votes=res.votes, strategy=res.strategy,
                 representative=res.representative.sample if res.representative else None,
                 samples=len(res.samples), status=res.status)
    return res


def run_generator(problem: dict, plan: Plan | dict | None, client: LLMClient, retriever, budget: Budget,
                  tracer: Tracer, *, samples: int = 3, retrieve: bool = True, indices=None, hits=None,
                  feedback: str = "", sample_offset: int = 0) -> dict:
    """`indices`: only these subgoals (1-based; e.g. those the pre-pass did not close).
    The graph's retries also pass `hits` (the RETRIEVE node's lemmas, instead of querying here),
    `feedback` (the Critic's f of Eq. 8) and `sample_offset` (a new cache entry per attempt)."""
    out = {"status": "error", "subgoals": [], "detail": None}
    try:
        _run(problem, plan, client, retriever, budget, tracer, samples, retrieve, indices, hits, feedback,
             sample_offset, out)
    except Exception as exc:  # noqa: BLE001 — a node never raises to the caller
        tracer.event("node_error", level="error", node=ROLE, error=repr(exc)[:1000])
        out.update(status="error", detail=repr(exc)[:1000])
    return out


def _run(problem, plan, client, retriever, budget, tracer, samples, retrieve, indices, hits, feedback, sample_offset,
         out) -> None:
    try:
        thm = parse_theorem(problem["statement"])
        plan = plan if isinstance(plan, Plan) else Plan.model_validate(plan)
    except (ValueError, KeyError, TypeError) as exc:  # pydantic.ValidationError is a ValueError
        tracer.event("invalid_statement", level="error", node=ROLE, error=str(exc)[:500])
        out.update(status="invalid_statement", detail=str(exc)[:500])
        return
    for i in range(1, len(plan.subgoals) + 1):
        if indices is not None and i not in indices:
            continue
        try:
            res = _sketch_subgoal(problem, thm, plan, i, client, retriever, budget, tracer, samples, retrieve, hits, feedback,
                                  sample_offset)
        except (UnsafeLean, ValueError, IndexError) as exc:  # a plan that skipped the Planner's checks
            res = SubgoalSketches(index=i, subgoal=plan.subgoals[i - 1], status="error",
                                  failures=[{"reason": "build_failed", "detail": str(exc)[:500]}])
            tracer.event("sketch_failed", level="error", node=ROLE, subgoal=i, reason="build_failed",
                         error=str(exc)[:500])
        out["subgoals"].append(res.model_dump())
        if res.status in STOPS:
            out.update(status=res.status)
            return
    statuses = {s["status"] for s in out["subgoals"]}
    out["status"] = "ok" if statuses <= {"ok"} else "partial"
