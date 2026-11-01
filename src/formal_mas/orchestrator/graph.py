"""The state graph of the system (LangGraph), shared by the five configurations C1-C5.

    out = run_graph(problem, client, runner, retriever, budget, tracer, pipeline_cfg, syntax=checker)

States (Fig. 1 of the report, plus the deterministic PREPASS, SUBGOALS and ASSEMBLE):

    START ─ flow = baseline_a | baseline_b ─► BASELINE ─► END            (C1, C2)
    START ─ flow = full ─► PREPASS ─► PLAN ─► SUBGOALS ─► RETRIEVE ─► GENERATE ─► FORMALIZE ─► VERIFY
    VERIFY ─ more subgoals ─► RETRIEVE;  VERIFY ─ none left ─► ASSEMBLE ─► DONE
    PLAN, GENERATE, FORMALIZE, VERIFY, ASSEMBLE ─ a failure ─► CRITIQUE
    CRITIQUE ─ π ─► FORMALIZE | RETRIEVE | GENERATE (retry), PLAN (replan), FAIL (abandon)

There is one compiled graph (`GRAPH`); a configuration is a flag set in config/ablations/ (CLAUDE.md
§1.7): `flow`, `critic.k_max`, `critic.r_max`, `critic.llm_feedback`, `retrieval.enabled`, `budget.b_max`. Only `accepted` (Eq. 2) on the
assembled file gives `solved` (CLAUDE.md §1.1); the control flow is κ and π (orchestrator/policy.py).

Termination (Proposition 1): each CRITIQUE increments a_i of the subgoal or r, or ends, and the
forward path visits each subgoal once per plan. A plan-level failure (`refuted:sg<i>`, assembly not
accepted) has a_i = 0: π replans while r < R_max; otherwise a refuted plan goes on with the trivial
plan the Planner returned, and a failed assembly ends in FAIL, since it has no subgoal to retry.

Never raises: {status, solved, stage, proof, …}; status ∈ solved | failed | budget_exhausted |
budget_stop | provider_error | invalid_statement | error, or the baselines' own result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from formal_mas.agents.autoformalizer import run_formalize, run_verify
from formal_mas.agents.contracts import Critique, LemmaHit, Plan
from formal_mas.agents.critic import llm_advice, replan_feedback, report, retry_feedback
from formal_mas.agents.generator import run_generator
from formal_mas.agents.planner import run_planner
from formal_mas.agents.prepass import prepass, subgoal_targets, theorem_target
from formal_mas.baselines.baseline_a import run_baseline_a
from formal_mas.baselines.baseline_b import run_baseline_b
from formal_mas.lean.runner import accepted
from formal_mas.lean.statement import Theorem, assemble, parse_theorem, subgoal_statement
from formal_mas.orchestrator.policy import Limits, Trace, classify, decide, escalate, missing_identifier
from formal_mas.tracing import Budget, Tracer

NODE = "graph"
STOPS = ("budget_exhausted", "budget_stop", "provider_error")
_ROUTES = {"FORMALIZE": "formalize", "RETRIEVE": "retrieve", "GENERATE": "generate"}
__all__ = ["GRAPH", "build_graph", "limits_from", "run_graph"]


@dataclass
class Runtime:
    """What the nodes use but no edge depends on: one per problem (the graph itself is shared)."""

    problem: dict
    client: Any
    runner: Any
    retriever: Any
    budget: Budget
    tracer: Tracer
    cfg: dict
    syntax: Any
    limits: Limits
    rag: bool
    llm_feedback: bool
    out: dict
    thm: Theorem | None = None
    critiques: list[dict] = field(default_factory=list)


class State(TypedDict, total=False):
    rt: Runtime
    next: str
    plan: dict | None
    replans: int
    plan_feedback: str
    todo: list[int]
    blocks: dict[int, str]
    attempts: dict[int, int]
    strategies: dict[int, list[str]]
    hits: dict[int, list[LemmaHit]]
    sketch: dict | None
    candidate: dict | None
    feedback: str
    expand: str | None  # the missing identifier for the next RETRIEVE
    failure: dict | None
    assembly: dict | None


def limits_from(cfg: dict) -> Limits:
    critic = cfg["critic"]
    return Limits(k_max=int(critic["k_max"]), r_max=int(critic["r_max"]), b_max=int(cfg["budget"]["b_max"]))


def _fail(rt: Runtime, status: str, stage: str | None, detail: Any = None) -> dict:
    rt.out.update(status=status, solved=False, stage=stage, proof=None,
                  detail=str(detail)[:1000] if detail is not None else None)
    return {"next": "fail"}


def _failure(rt: Runtime, *, reason: str, stage: str, index: int | None = None, detail: Any = None,
             messages: list | None = None, statement_end=None, tactics: str | None = None) -> dict:
    """Hand a failure to CRITIQUE."""
    return {"next": "critique", "failure": {
        "reason": reason, "stage": stage, "index": index, "detail": str(detail)[:1000] if detail else None,
        "messages": messages or [], "statement_end": statement_end, "tactics": tactics}}


def _sample(state: State, i: int, rt: Runtime) -> int:
    """A cache entry per attempt, across replans: a plan identical to the last one must not replay it."""
    return state.get("replans", 0) * rt.limits.k_max + (state.get("attempts") or {}).get(i, 0)


def _route_plan_or_assembly(rt: Runtime, plan: Plan, blocks: dict[int, str]) -> dict:
    n = len(plan.subgoals)
    return {"next": "assemble", "assembly": {"subgoals": plan.subgoals, "blocks": [blocks[i] for i in range(1, n + 1)],
                                             "closing": plan.closing, "stage": "assemble"}}


# -- nodes ---------------------------------------------------------------------------------------


def node_baseline(state: State) -> dict:
    rt = state["rt"]
    flow = rt.cfg["flow"]
    attempts = int(((rt.cfg.get("baselines") or {}).get(flow) or {}).get("attempts", 1))
    if flow == "baseline_a":
        res = run_baseline_a(rt.problem, rt.client, rt.runner, rt.budget, rt.tracer, attempts=attempts,
                             syntax=rt.syntax)
    else:
        res = run_baseline_b(rt.problem, rt.client, rt.budget, rt.tracer, attempts=attempts)
    rt.out.clear()
    rt.out.update(res)
    return {}


def node_prepass(state: State) -> dict:
    rt = state["rt"]
    try:
        rt.thm = parse_theorem(rt.problem["statement"])
    except (ValueError, KeyError) as exc:
        rt.tracer.event("invalid_statement", level="error", node=NODE, error=str(exc)[:500])
        return _fail(rt, "invalid_statement", None, exc)
    found = prepass(rt.thm, [], [theorem_target(rt.thm)], rt.runner, rt.tracer, rt.cfg.get("prepass"))
    rt.out["prepass"]["theorem"] = found.get("theorem")
    if "theorem" in found:
        return {"next": "assemble", "assembly": {"subgoals": [], "blocks": [], "closing": found["theorem"],
                                                 "stage": "prepass"}}
    return {"next": "plan"}


def node_plan(state: State) -> dict:
    rt = state["rt"]
    pc, r = rt.cfg.get("planner") or {}, state.get("replans", 0)
    planned = run_planner(rt.problem, rt.client, rt.runner, rt.retriever, rt.budget, rt.tracer,
                          n_max=int(pc.get("n_max", 3)), retrieve=rt.rag and bool(pc.get("retrieve", True)),
                          check_skeleton=bool(pc.get("check_skeleton", True)), refute=pc.get("refute"),
                          closing_auto=pc.get("closing_auto"), syntax=rt.syntax,
                          feedback=state.get("plan_feedback", ""), sample=r)
    rt.out["planner"] = planned
    rt.out["plans"].append(planned)
    detail = str(planned.get("detail") or "")
    if detail.startswith("provider_error"):  # later calls would fail too
        return _fail(rt, "provider_error", "plan", detail)
    if detail.startswith("budget_exhausted"):
        return _fail(rt, "budget_exhausted", "plan", detail)
    if planned["status"] == "budget_stop" or planned["plan"] is None:
        return _fail(rt, planned["status"] if planned["status"] != "ok" else "error", "plan", detail)
    plan = Plan.model_validate(planned["plan"])
    if not plan.skeleton_compiles:  # the trivial plan failed its own check: nothing to build on
        return _fail(rt, "failed", "plan", f"plan not checked: {plan.reason}")
    update: dict = {"plan": planned["plan"], "plan_feedback": ""}
    if plan.source == "direct":
        return {**update, "next": "assemble", "assembly": {"subgoals": [], "blocks": [], "closing": plan.closing,
                                                           "stage": "plan"}}
    if str(plan.reason or "").startswith("refuted:"):
        return {**update, **_failure(rt, reason=plan.reason, stage="plan", detail=planned["proposed"])}
    return {**update, "next": "subgoals"}


def node_subgoals(state: State) -> dict:
    """The pre-pass on the plan's subgoals (no LLM); fresh counters for this plan."""
    rt = state["rt"]
    plan = Plan.model_validate(state["plan"])
    n = len(plan.subgoals)
    targets = [t for t in subgoal_targets(rt.thm, plan.subgoals, list(range(1, n + 1)))
               if (t.extra, t.goal) != ((), rt.thm.goal)]  # the theorem itself was tried in PREPASS
    found = prepass(rt.thm, plan.subgoals, targets, rt.runner, rt.tracer, rt.cfg.get("prepass"))
    blocks = {int(k[2:]): v for k, v in found.items()}
    rt.out["prepass"]["subgoals"] = {f"sg{i}": t for i, t in sorted(blocks.items())}
    for i, tactic in sorted(blocks.items()):
        rt.tracer.event("subgoal_solved", node=NODE, subgoal=i, solved_by="prepass", tactic=tactic, attempts=0)
    todo = [i for i in range(1, n + 1) if i not in blocks]
    update = {"blocks": blocks, "todo": todo, "attempts": {}, "strategies": {}, "hits": {}, "feedback": "", "expand": None, "sketch": None}
    return {**update, **(_route_plan_or_assembly(rt, plan, blocks) if not todo else {"next": "retrieve"})}


def node_retrieve(state: State) -> dict:
    """R_k(g), Eq. 5: no LLM. After a missing lemma the query carries the identifier and k grows."""
    rt = state["rt"]
    i, plan = state["todo"][0], Plan.model_validate(state["plan"])
    hits = dict(state.get("hits") or {})
    expand = state.get("expand")
    if rt.rag and rt.retriever is not None:
        _, decl = subgoal_statement(rt.thm, plan.subgoals, i)
        rc = rt.cfg.get("retrieval") or {}
        query, k = (f"{decl} {expand}", int(rc.get("k_retry", 32))) if expand else (decl, None)
        hits[i] = rt.retriever.retrieve(query, k=k)
    else:
        hits[i] = []
    rt.tracer.event("retrieve", node=NODE, subgoal=i, expanded=expand, lemmas=[h.name for h in hits[i]])
    return {"hits": hits, "expand": None, "next": "generate"}


def node_generate(state: State) -> dict:
    rt = state["rt"]
    i, gc = state["todo"][0], rt.cfg.get("generator") or {}
    samples = int(gc.get("samples", 3))
    gen = run_generator(rt.problem, state["plan"], rt.client, rt.retriever, rt.budget, rt.tracer, samples=samples,
                        retrieve=False, indices={i}, hits=(state.get("hits") or {}).get(i, []),
                        feedback=state.get("feedback", ""), sample_offset=_sample(state, i, rt) * samples)
    rt.out["generator"].append(gen)
    sg = gen["subgoals"][0] if gen["subgoals"] else {}
    if sg.get("representative") is not None:
        tried = dict(state.get("strategies") or {})
        tried[i] = [*tried.get(i, []), sg["representative"]["strategy"]]
        return {"sketch": sg["representative"], "strategies": tried, "next": "formalize"}
    status = sg.get("status") if sg.get("status") in STOPS else gen["status"]
    if status in STOPS:
        return _fail(rt, status, f"generate:sg{i}", gen.get("detail") or sg.get("failures"))
    return _failure(rt, reason="no_sketch", stage=f"generate:sg{i}", index=i, detail=sg.get("failures") or gen.get("detail"))


def node_formalize(state: State) -> dict:
    rt = state["rt"]
    i = state["todo"][0]
    res = run_formalize(rt.problem, state["plan"], i, state.get("sketch"), rt.client, rt.retriever, rt.budget, rt.tracer,
                        retrieve=False, syntax=rt.syntax, hits=(state.get("hits") or {}).get(i, []),
                        feedback=state.get("feedback", ""), sample=_sample(state, i, rt))
    if res["status"] == "candidate":
        return {"candidate": res, "next": "verify"}
    rt.out["formalizer"].append(_public(res))
    if res["status"] in STOPS:
        return _fail(rt, res["status"], f"formalize:sg{i}", f"{res['reason']}: {res['detail'] or ''}")
    return _failure(rt, reason=str(res["reason"]), stage=f"formalize:sg{i}", index=i, detail=res["detail"],
                    tactics=res["tactics"])


def _public(res: dict) -> dict:
    """A formalizer record for results.jsonl (the file itself stays in the trace's tactics)."""
    return {k: v for k, v in res.items() if k not in ("code", "statement_end")}


def node_verify(state: State) -> dict:
    rt = state["rt"]
    i = state["todo"][0]
    res = run_verify(state["candidate"], rt.runner, rt.tracer)
    rt.out["formalizer"].append(_public(res))
    if res["status"] != "ok":
        return _failure(rt, reason=str(res["reason"]), stage=f"verify:sg{i}", index=i, detail=res["detail"],
                        messages=res["messages"], statement_end=res["statement_end"], tactics=res["tactics"])
    attempts = (state.get("attempts") or {}).get(i, 0)
    rt.tracer.event("subgoal_solved", node=NODE, subgoal=i, solved_by="llm", tactics=res["tactics"],
                    attempts=attempts + 1)
    blocks, todo = {**state["blocks"], i: res["tactics"]}, state["todo"][1:]
    update = {"blocks": blocks, "todo": todo, "feedback": "", "expand": None, "candidate": None, "sketch": None}
    if todo:
        return {**update, "next": "retrieve"}
    return {**update, **_route_plan_or_assembly(rt, Plan.model_validate(state["plan"]), blocks)}


def node_assemble(state: State) -> dict:
    """ASSEMBLE: only this acceptance gives `solved`."""
    rt = state["rt"]
    a = state["assembly"]
    code = assemble(rt.thm, a["subgoals"], a["blocks"], a["closing"])
    result = rt.runner.verify(code, rt.thm.name)
    ok, why = accepted(result)
    errors = [f"line {m.line}: {m.text[:300]}" for m in result.errors[:3]]
    rt.out["final"] = {"accepted": ok, "reason": why, "errors": errors, "lean_status": result.status}
    rt.tracer.event("assemble", node=NODE, accepted=ok, reason=why, errors=errors, cached_lean=result.cached)
    if ok:
        rt.out.update(status="solved", solved=True, stage=a["stage"], proof=code, detail=None)
        return {"next": "done"}
    return _failure(rt, reason=f"assemble:{why}", stage="assemble", detail=errors[0] if errors else None,
                    messages=[[m.line, m.column, m.text[:1500]] for m in result.errors[:5]])


def node_critique(state: State) -> dict:
    """κ, then π (no LLM), then the feedback for the action π chose."""
    rt = state["rt"]
    f = state["failure"]
    i, r = f["index"], state.get("replans", 0)
    attempts = dict(state.get("attempts") or {})
    if i is not None:
        attempts[i] = attempts.get(i, 0) + 1
    a_i = attempts.get(i, 0) if i is not None else 0
    trace = Trace(f["reason"], tuple((m[0], m[1], m[2]) for m in f["messages"]),
                  tuple(f["statement_end"]) if f["statement_end"] else None)
    tried = (state.get("strategies") or {}).get(i, []) if i is not None else []
    message_class = classify(trace)
    error_class = escalate(message_class, len(set(tried)), rt.limits.k_max)
    calls = rt.budget.used  # b of Eq. 12, before any advice call
    action = decide(error_class, a_i, r, calls, rt.limits)
    ident, names = None, []
    update: dict = {"attempts": attempts}
    advice = None
    if action.kind == "retry" and i is not None:
        if error_class == "missing_lemma":
            ident = missing_identifier(trace)
            if ident and rt.rag and rt.retriever is not None:
                # Lean prints `nat.succ_le_iff.mp`: the name may end in a projection
                names = rt.retriever.suggest_names(ident) or rt.retriever.suggest_names(ident.rpartition(".")[0] or ident)
        plan = Plan.model_validate(state["plan"])
        text = report(trace, f["detail"])
        if rt.llm_feedback:
            _, decl = subgoal_statement(rt.thm, plan.subgoals, i)
            advice = llm_advice(rt.client, rt.budget, rt.tracer, lemma=decl, error_class=error_class, text=text,
                                sample=r * rt.limits.k_max + a_i)
        update["feedback"] = retry_feedback(error_class, trace, attempt=a_i, tactics=f["tactics"], detail=f["detail"],
                                            suggestions=names, advice=advice)
        update["expand"] = ident
    critique = Critique(subgoal=i, reason=f["reason"][:500], error_class=error_class, message_class=message_class,
                        action=action.kind,
                        route=action.route, attempts=a_i, replans=r, calls=calls,
                        missing_identifier=ident, suggestions=names, llm_feedback=advice is not None).model_dump()
    rt.critiques.append(critique)
    rt.tracer.event("critique", node=NODE, stage=f["stage"], **critique, feedback=update.get("feedback"))

    if action.kind == "abandon":
        status = "budget_exhausted" if rt.budget.used >= rt.limits.b_max else "failed"
        return {**update, **_fail(rt, status, f["stage"], f"{f['reason']}: {f['detail'] or ''}")}
    if action.kind == "replan":
        plan = Plan.model_validate(state["plan"]) if state.get("plan") else None
        proposed = plan.subgoals if plan is not None and plan.source == "llm" else (rt.out.get("planner") or {}).get("proposed", [])
        return {**update, "replans": r + 1, "next": "plan",
                "plan_feedback": replan_feedback(trace, subgoals=list(proposed), stage=f["stage"], attempts=a_i,
                                                 error_class=error_class, detail=f["detail"])}
    if i is not None:
        return {**update, "next": _ROUTES[action.route]}
    if f["stage"] == "plan":  # refuted, no replans left: continue with the Planner's trivial plan
        return {**update, "next": "subgoals"}
    return {**update, **_fail(rt, "failed", f["stage"], f"{f['reason']}: {f['detail'] or ''}")}


def node_done(state: State) -> dict:
    return {}


def node_fail(state: State) -> dict:
    return {}


# -- graph ---------------------------------------------------------------------------------------


def _safe(name: str, fn):
    """CLAUDE.md §7: a node never raises; the error goes to the trace and the state stays valid."""
    def wrapped(state: State) -> dict:
        try:
            return fn(state)
        except Exception as exc:  # noqa: BLE001
            rt = state["rt"]
            rt.tracer.event("node_error", level="error", node=name, error=repr(exc)[:1000])
            return _fail(rt, "error", rt.out.get("stage") or name, repr(exc))
    return wrapped


def _start(state: State) -> str:
    return "prepass" if state["rt"].cfg["flow"] == "full" else "baseline"


def _follow(state: State) -> str:
    return state["next"]


def build_graph():
    g = StateGraph(State)
    nodes = {"baseline": node_baseline, "prepass": node_prepass, "plan": node_plan, "subgoals": node_subgoals,
             "retrieve": node_retrieve, "generate": node_generate, "formalize": node_formalize, "verify": node_verify,
             "assemble": node_assemble, "critique": node_critique, "done": node_done, "fail": node_fail}
    for name, fn in nodes.items():
        g.add_node(name, _safe(name, fn))
    g.add_conditional_edges(START, _start, ["baseline", "prepass"])
    edges = {"prepass": ["assemble", "plan", "fail"],
             "plan": ["subgoals", "assemble", "critique", "fail"],
             "subgoals": ["retrieve", "assemble", "fail"],
             "retrieve": ["generate", "fail"],
             "generate": ["formalize", "critique", "fail"],
             "formalize": ["verify", "critique", "fail"],
             "verify": ["retrieve", "assemble", "critique", "fail"],
             "assemble": ["done", "critique", "fail"],
             "critique": ["formalize", "retrieve", "generate", "plan", "subgoals", "fail"]}
    for source, targets in edges.items():
        g.add_conditional_edges(source, _follow, targets)
    g.add_edge("baseline", END)
    g.add_edge("done", END)
    g.add_edge("fail", END)
    return g.compile()


GRAPH = build_graph()


def _recursion_limit(limits: Limits, n_max: int) -> int:
    """More steps than any execution takes: each plan visits PLAN, SUBGOALS and, per attempt on each of
    its subgoals, CRITIQUE and the four states of the attempt; plus the entry and exit states."""
    return 10 + (limits.r_max + 1) * (4 + n_max * (1 + 5 * limits.k_max))


def run_graph(problem: dict, client, runner, retriever, budget: Budget, tracer: Tracer, pipeline_cfg: dict, *,
              syntax=None) -> dict:
    out = {"status": "error", "solved": False, "stage": None, "proof": None, "prepass": {}, "planner": None,
           "plans": [], "generator": [], "formalizer": [], "final": None, "detail": None}
    critiques: list[dict] = []
    state_replans = 0
    try:
        cfg = {**pipeline_cfg, "flow": pipeline_cfg.get("flow", "full")}
        limits = Limits(limits_from(cfg).k_max, limits_from(cfg).r_max, budget.b_max)  # one truth for b: the Budget
        retrieval = cfg.get("retrieval") or {}
        rt = Runtime(problem=problem, client=client, runner=runner, retriever=retriever, budget=budget, tracer=tracer,
                     cfg=cfg, syntax=syntax, limits=limits, rag=bool(retrieval.get("enabled", True)),
                     llm_feedback=bool((cfg.get("critic") or {}).get("llm_feedback", False)), out=out,
                     critiques=critiques)
        n_max = int((cfg.get("planner") or {}).get("n_max", 3))
        final = GRAPH.invoke({"rt": rt, "replans": 0, "next": ""},
                             {"recursion_limit": _recursion_limit(limits, n_max)})
        state_replans = final.get("replans", 0)
    except Exception as exc:  # noqa: BLE001 — never raises to the caller
        tracer.event("node_error", level="error", node=NODE, error=repr(exc)[:1000])
        out.update(status="error", solved=False, detail=repr(exc)[:1000])
    out["critiques"] = critiques
    out["error_classes"] = [c["error_class"] for c in critiques]
    out["replans"] = state_replans
    tracer.event("pipeline_end", node=NODE, status=out.get("status"), solved=out.get("solved"), stage=out.get("stage"),
                 error_classes=out["error_classes"], replans=state_replans)
    return out
