"""Linear pipeline without Critic (Phase 5):
PRE-PASS → PLAN → PRE-PASS → RETRIEVE → GENERATE → FORMALIZE → VERIFY → ASSEMBLE.

    out = run_linear(problem, client, runner, retriever, budget, tracer, pipeline_cfg, syntax=checker)

1. prepass (theorem): the fixed tactics of pipeline.yaml → prepass on the theorem itself,
   before any LLM call. Accepted → solved (stage `prepass`, 0 LLM calls).
2. PLAN (agents/planner.py, with refutation, closing_auto and the syntax layer). A direct plan
   (no subgoals, closing accepted as the whole proof) → assembled and verified (stage `plan`).
3. prepass (subgoals): the same tactics on every subgoal lemma `<name>_sg<i>`, one compilation.
4. GENERATE (agents/generator.py) only for the subgoals the pre-pass did not close; each
   subgoal's Retriever query is its lemma statement (RETRIEVE happens inside the nodes).
5. FORMALIZE + VERIFY (agents/autoformalizer.py): one attempt per remaining subgoal, with
   the winning sketch; the subgoal lemma must be accepted. Stops at the first failure.
6. ASSEMBLE: statement.assemble pastes every block, unchanged, into the skeleton with the
   plan's closing step; the final file must be *accepted* (Eq. 2). Only this decides
   `solved` (CLAUDE.md §1.1).
Retries, name repair and replanning are Phase 6 (Critic, policy π). Every LLM call counts for
the problem's b_max (`Budget`). Never raises: {status, solved, stage, proof, …};
status ∈ solved | failed | budget_exhausted | budget_stop | provider_error |
invalid_statement | error; `stage` is where it was solved or where it stopped:
prepass | plan | generate:sg<i> | formalize:sg<i> | verify:sg<i> | assemble.
"""

from __future__ import annotations

from formal_mas.agents.autoformalizer import run_autoformalizer
from formal_mas.agents.contracts import Plan
from formal_mas.agents.generator import run_generator
from formal_mas.agents.planner import run_planner
from formal_mas.agents.prepass import prepass, subgoal_targets, theorem_target
from formal_mas.lean.runner import accepted
from formal_mas.lean.statement import assemble, parse_theorem
from formal_mas.tracing import Budget, Tracer

NODE = "linear"
STOPS = ("budget_exhausted", "budget_stop", "provider_error")
__all__ = ["run_linear"]


def run_linear(problem: dict, client, runner, retriever, budget: Budget, tracer: Tracer, pipeline_cfg: dict, *,
               syntax=None) -> dict:
    out = {"status": "error", "solved": False, "stage": None, "proof": None, "prepass": {}, "planner": None,
           "generator": None, "formalizer": [], "final": None, "detail": None}
    try:
        _run(problem, client, runner, retriever, budget, tracer, pipeline_cfg, syntax, out)
    except Exception as exc:  # noqa: BLE001 — never raises to the caller
        tracer.event("node_error", level="error", node=NODE, error=repr(exc)[:1000])
        out.update(status="error", solved=False, detail=repr(exc)[:1000])
    tracer.event("pipeline_end", node=NODE, status=out["status"], solved=out["solved"], stage=out["stage"])
    return out


def _final(thm, subgoals: list[str], blocks: list[str], closing: str, runner, tracer, out: dict, stage: str) -> None:
    """ASSEMBLE + the acceptance predicate on the whole proof."""
    code = assemble(thm, subgoals, blocks, closing)
    result = runner.verify(code, thm.name)
    ok, why = accepted(result)
    errors = [f"line {m.line}: {m.text[:300]}" for m in result.errors[:3]]
    out["final"] = {"accepted": ok, "reason": why, "errors": errors, "lean_status": result.status}
    tracer.event("assemble", node=NODE, accepted=ok, reason=why, errors=errors, cached_lean=result.cached)
    out.update(status="solved" if ok else "failed", solved=ok, stage=stage if ok else "assemble",
               proof=code if ok else None, detail=None if ok else f"{why}: {errors[0] if errors else ''}")


def _run(problem, client, runner, retriever, budget, tracer, cfg, syntax, out) -> None:
    try:
        thm = parse_theorem(problem["statement"])
    except (ValueError, KeyError) as exc:
        tracer.event("invalid_statement", level="error", node=NODE, error=str(exc)[:500])
        out.update(status="invalid_statement", detail=str(exc)[:500])
        return
    pre_cfg = cfg.get("prepass")
    pc, gc, fc = cfg.get("planner") or {}, cfg.get("generator") or {}, cfg.get("autoformalizer") or {}

    # 1. pre-pass on the theorem (no LLM)
    found = prepass(thm, [], [theorem_target(thm)], runner, tracer, pre_cfg)
    out["prepass"]["theorem"] = found.get("theorem")
    if "theorem" in found:
        return _final(thm, [], [], found["theorem"], runner, tracer, out, "prepass")

    # 2. PLAN
    planned = run_planner(problem, client, runner, retriever, budget, tracer, n_max=int(pc.get("n_max", 4)),
                          retrieve=bool(pc.get("retrieve", True)), check_skeleton=bool(pc.get("check_skeleton", True)),
                          refute=pc.get("refute"), closing_auto=pc.get("closing_auto"), syntax=syntax)
    out["planner"] = planned
    if str(planned.get("detail") or "").startswith("provider_error"):  # later calls would fail too
        out.update(status="provider_error", stage="plan", detail=planned["detail"])
        return
    if planned["status"] == "budget_stop" or planned["plan"] is None:
        out.update(status=planned["status"] if planned["status"] != "ok" else "error", stage="plan",
                   detail=planned.get("detail"))
        return
    plan = Plan.model_validate(planned["plan"])
    if not plan.skeleton_compiles:  # the trivial plan failed its own check: nothing to build on
        out.update(status="failed", stage="plan", detail=f"plan not checked: {plan.reason}")
        return
    if plan.source == "direct":
        return _final(thm, [], [], plan.closing, runner, tracer, out, "plan")

    # 3. pre-pass on the subgoals (no LLM)
    n = len(plan.subgoals)
    targets = [t for t in subgoal_targets(thm, plan.subgoals, list(range(1, n + 1)))
               if (t.extra, t.goal) != ((), thm.goal)]  # the theorem itself was already tried in step 1
    found = prepass(thm, plan.subgoals, targets, runner, tracer, pre_cfg)
    blocks: dict[int, str] = {int(k[2:]): v for k, v in found.items()}
    out["prepass"]["subgoals"] = {f"sg{i}": t for i, t in sorted(blocks.items())}
    todo = [i for i in range(1, n + 1) if i not in blocks]

    # 4. GENERATE (only what automation did not close)
    if todo:
        gen = run_generator(problem, plan, client, retriever, budget, tracer, samples=int(gc.get("samples", 3)),
                            retrieve=bool(gc.get("retrieve", True)), indices=set(todo))
        out["generator"] = gen
        sketches = {s["index"]: s for s in gen["subgoals"]}
        for i in todo:
            s = sketches.get(i)
            if s is None or s.get("representative") is None:
                status = (s or {}).get("status") if (s or {}).get("status") in STOPS else gen["status"]
                out.update(status=status if status in STOPS else "failed", stage=f"generate:sg{i}",
                           detail=(s or {}).get("status") or gen.get("detail"))
                return

        # 5. FORMALIZE + VERIFY, one attempt per subgoal
        for i in todo:
            res = run_autoformalizer(problem, plan, i, sketches[i]["representative"], client, runner, retriever,
                                     budget, tracer, retrieve=bool(fc.get("retrieve", True)), syntax=syntax)
            out["formalizer"].append(res)
            if res["status"] != "ok":
                stage = f"verify:sg{i}" if str(res["reason"]).startswith("lean:") else f"formalize:sg{i}"
                out.update(status=res["status"] if res["status"] in STOPS else "failed", stage=stage,
                           detail=f"{res['reason']}: {res['detail'] or ''}"[:1000])
                return
            blocks[i] = res["tactics"]

    # 6. ASSEMBLE + accepted
    _final(thm, plan.subgoals, [blocks[i] for i in range(1, n + 1)], plan.closing, runner, tracer, out, "assemble")
