"""Planner (Phase 4): theorem → subgoals (≤ n_max) + closing tactic block (CLAUDE.md §1.3).

    out = run_planner(problem, client, runner, retriever, budget, tracer, n_max=4,
                      refute=cfg["refute"], closing_auto=cfg["closing_auto"])

1. `parse_theorem` on the curated statement (models never write it).
2. The Retriever's top-k lemmas for the theorem (pipeline.yaml → planner.retrieve).
3. One `planner` LLM call → `PlanProposal` (contracts.py), one repair call if the JSON is bad.
4. No subgoals → a *direct* plan: the closing block alone must be an accepted proof of the
   theorem (Eq. 2); otherwise the trivial plan (`direct_closing_failed`).
5. Otherwise the code checks the proposal: #subgoals ≤ n_max; every subgoal passes
   check_term and the closing block check_tactics (statement.py raises UnsafeLean); the
   hypothesis names the prompt announced are the ones the skeleton uses. Then, with Lean
   (no LLM calls):
   a. the skeleton with `sorry` for every proof and for the closing must compile (the
      subgoals are well-typed propositions);
   b. refutation (planner.refute): one file with `<name>_consistent : False` and, per
      subgoal, `<name>_ref<i> (hr : Pᵢ) : False`, from the theorem's hypotheses only, closed
      by a fixed list of automatic tactics under a heartbeat limit. If `_consistent` is
      proved the hypotheses are contradictory, every subgoal would be refuted, and the check
      is skipped. Otherwise a proved `_ref<i>` means Pᵢ is false wherever the hypotheses
      hold: the plan is rejected (`refuted:sg<i>`, event `plan_refuted`). A timeout or
      crash refutes nothing. The check can only reject a plan, never accept one;
   c. the closing step alone, as `<name>_close` with the subgoals as hypotheses, must be
      *accepted*. If it is not, the tactics of planner.closing_auto are tried, one lemma
      each in one file; the first that compiles is confirmed alone with `accepted` and
      replaces the closing (`closing_source: auto:<tactic>`, reason `closing_auto:<tactic>`);
   d. the skeleton with the final closing must compile.
Any failure → the trivial plan: the whole goal as the only subgoal, closed by `exact h1`
(checked by a, c and d only), with the reason logged as `plan_fallback`. Never raises:
the result is {status, plan, lemmas, detail, proposed}. status ∈ ok | trivial | invalid_statement |
budget_stop | error; `plan` is None only for invalid_statement (and error before parsing).
"""

from __future__ import annotations

from formal_mas.agents.contracts import Plan, PlanProposal
from formal_mas.agents.prompting import fragment, render
from formal_mas.lean.runner import accepted, compiles, compiles_between
from formal_mas.lean.statement import (Theorem, UnsafeLean, clean_tactics, closing_candidates_file, closing_lemma,
                                       parse_theorem, proof_file, refutation_file, skeleton)
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, SpendLimitReached
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

ROLE = "planner"
__all__ = ["run_planner", "trivial_plan", "lemma_block", "header_summary"]


def lemma_block(hits) -> str:
    """`- name : signature` lines for the prompts (or a note when there are none)."""
    return "\n".join(f"- {h.name} : {h.signature}" for h in hits) or fragment("no_lemmas")


def header_summary(thm: Theorem) -> str:
    """The non-import lines of the header (options and `open`), on one line."""
    lines = [ln.strip() for ln in thm.header.splitlines() if ln.strip() and not ln.strip().startswith("import")]
    return " / ".join(lines) or "import Mathlib"


def trivial_plan(thm: Theorem, reason: str) -> Plan:
    h = thm.hypothesis_names(1)
    return Plan(subgoals=[thm.goal], closing=f"exact {h[0]}", hypotheses=h, source="trivial", reason=reason)


def _errors(result) -> list[str]:
    return [f"line {m.line}: {m.text[:300]}" for m in result.errors[:3]]


def _compile_skeleton(thm: Theorem, plan: Plan, runner, tracer: Tracer, stage: str,
                      closing: str | None) -> str | None:
    """None if the skeleton (closing block, or `sorry` when None) compiles, else the reason."""
    result = runner.verify(skeleton(thm, plan.subgoals, closing), thm.name)
    ok, errors = compiles(result), _errors(result)
    tracer.event("plan_skeleton", node=ROLE, source=plan.source, stage=stage, compiles=ok,
                 lean_status=result.status, errors=errors, cached_lean=result.cached)
    return None if ok else f"skeleton_{result.status}" + (": " + errors[0] if errors else "")


def _closing_accepted(thm: Theorem, plan: Plan, closing: str, runner, tracer: Tracer) -> str | None:
    """None if `<name>_close` (the closing step with the subgoals as hypotheses) is
    accepted, else the reason — so a closing step that only works through a hidden `sorry`
    (e.g. `sorryAx`) is rejected."""
    name, code = closing_lemma(thm, plan.subgoals, closing)
    result = runner.verify(code, name)
    ok, why = accepted(result)
    errors = _errors(result)
    tracer.event("plan_closing", node=ROLE, source=plan.source, closing=closing, accepted=ok, reason=why,
                 errors=errors, cached_lean=result.cached)
    return None if ok else f"closing_{why}" + (": " + errors[0] if errors else "")


def _check_skeleton(thm: Theorem, plan: Plan, runner, tracer: Tracer) -> tuple[bool, str]:
    """The trivial plan's check: (ok, reason). The skeleton must compile and the closing
    lemma must be accepted."""
    why = _compile_skeleton(thm, plan, runner, tracer, "full", plan.closing) or \
        _closing_accepted(thm, plan, plan.closing, runner, tracer)
    return (False, why) if why else (True, "accepted")


def _refuted(thm: Theorem, plan: Plan, runner, tracer: Tracer, cfg: dict) -> int | None:
    """Index (1-based) of the first subgoal whose negation Lean proves from the theorem's
    hypotheses, or None. Skipped when the hypotheses alone prove False."""
    code, ranges = refutation_file(thm, plan.subgoals, list(cfg["tactics"]), int(cfg["heartbeats"]))
    result = runner.verify(code, None, timeout=float(cfg["timeout_s"]))
    consistent_name = f"{thm.name}_consistent"
    inconsistent = compiles_between(result, *ranges[consistent_name])
    refuted = [i for i in range(1, len(plan.subgoals) + 1)
               if compiles_between(result, *ranges[f"{thm.name}_ref{i}"])]
    tracer.event("plan_refute_check", node=ROLE, lean_status=result.status, seconds=result.elapsed_s,
                 hypotheses_inconsistent=inconsistent, refuted=[] if inconsistent else refuted,
                 cached_lean=result.cached)
    if result.status != "finished":
        return None  # a timeout or crash refutes nothing
    if inconsistent:
        tracer.event("plan_hypotheses_inconsistent", level="warning", node=ROLE,
                     detail="the theorem's hypotheses prove False; refutation skipped")
        return None
    if not refuted:
        return None
    i = refuted[0]
    tracer.event("plan_refuted", level="warning", node=ROLE, subgoal=i, proposition=plan.subgoals[i - 1],
                 refuted=refuted, subgoals=plan.subgoals)
    return i


def _closing_auto(thm: Theorem, plan: Plan, runner, tracer: Tracer, cfg: dict) -> str | None:
    """The first tactic of planner.closing_auto that closes the goal from the subgoals
    (compiles in the candidates file, then accepted alone), or None."""
    tactics = [t.strip() for t in cfg["tactics"]]
    code, ranges = closing_candidates_file(thm, plan.subgoals, tactics, int(cfg["heartbeats"]))
    result = runner.verify(code, None, timeout=float(cfg["timeout_s"]))
    ok = [t for j, t in enumerate(tactics, start=1)
          if compiles_between(result, *ranges[f"{thm.name}_close_auto{j}"])]
    tracer.event("plan_closing_auto_check", node=ROLE, lean_status=result.status, seconds=result.elapsed_s,
                 compiles=ok, cached_lean=result.cached)
    for tactic in ok:
        if _closing_accepted(thm, plan, tactic, runner, tracer) is None:
            return tactic
    return None


def _check_llm_plan(thm: Theorem, plan: Plan, runner, tracer: Tracer, refute: dict | None,
                    closing_auto: dict | None) -> str | None:
    """Steps a–d of the module docstring. None if the plan is accepted (its closing may have
    been replaced), else the reason. Raises UnsafeLean."""
    if why := _compile_skeleton(thm, plan, runner, tracer, "subgoals", None):
        return why
    if refute and refute.get("enabled", True):
        if (i := _refuted(thm, plan, runner, tracer, refute)) is not None:
            return f"refuted:sg{i}"
    if why := _closing_accepted(thm, plan, plan.closing, runner, tracer):
        tactic = _closing_auto(thm, plan, runner, tracer, closing_auto) \
            if closing_auto and closing_auto.get("enabled", True) else None
        if tactic is None:
            return why
        tracer.event("plan_closing_auto", node=ROLE, reason=f"closing_auto:{tactic}", failed_closing=plan.closing,
                     failed_reason=why[:500])
        plan.closing, plan.closing_source, plan.reason = tactic, f"auto:{tactic}", f"closing_auto:{tactic}"
    return _compile_skeleton(thm, plan, runner, tracer, "full", plan.closing)


def run_planner(problem: dict, client: LLMClient, runner, retriever, budget: Budget, tracer: Tracer, *,
                n_max: int = 4, retrieve: bool = True, check_skeleton: bool = True,
                refute: dict | None = None, closing_auto: dict | None = None, syntax=None, feedback: str = "",
                sample: int = 0) -> dict:
    """`feedback` and `sample`: on a replan, why the last plan failed and a new cache entry.
    `refute` / `closing_auto`: pipeline.yaml → planner.refute / planner.closing_auto
    ({enabled, tactics, heartbeats, timeout_s}); None turns the step off. `syntax`: a
    lean.syntax_check.SyntaxChecker (Lean's parser + node whitelist) for the model's subgoals
    and closing block, before anything is compiled; None skips that layer (sanitize.py
    always runs)."""
    out = {"status": "error", "plan": None, "lemmas": [], "detail": None, "proposed": []}
    try:
        _run(problem, client, runner, retriever, budget, tracer, n_max, retrieve, check_skeleton,
             refute, closing_auto, syntax, feedback, sample, out)
    except Exception as exc:  # noqa: BLE001 — a node never raises to the caller
        tracer.event("node_error", level="error", node=ROLE, error=repr(exc)[:1000])
        out.update(status="error", detail=repr(exc)[:1000])
        thm = out.pop("_thm", None)
        if thm is not None and out["plan"] is None:
            try:
                out["plan"] = trivial_plan(thm, "error").model_dump()
            except ValueError:
                pass
    out.pop("_thm", None)
    return out


def _run(problem, client, runner, retriever, budget, tracer, n_max, retrieve, check, refute, closing_auto,
         syntax, feedback, sample, out) -> None:
    try:
        thm = parse_theorem(problem["statement"])
    except (ValueError, KeyError) as exc:
        tracer.event("invalid_statement", level="error", node=ROLE, error=str(exc)[:500])
        out.update(status="invalid_statement", detail=str(exc)[:500])
        return
    out["_thm"] = thm
    hits = retriever.retrieve(thm.declaration()) if (retrieve and retriever is not None) else []
    out["lemmas"] = [h.name for h in hits]
    names = thm.hypothesis_names(n_max)

    def fallback(reason: str, status: str = "trivial") -> None:
        tracer.event("plan_fallback", level="warning", node=ROLE, reason=reason[:500])
        plan = trivial_plan(thm, reason[:500])
        if check:
            ok, why = _check_skeleton(thm, plan, runner, tracer)
            plan.skeleton_compiles = ok
            if not ok:
                tracer.event("plan_trivial_skeleton_failed", level="error", node=ROLE, reason=why)
        out.update(status=status, plan=plan.model_dump(), detail=reason[:500])

    messages = render(ROLE, informal=problem.get("informal", "") or fragment("no_informal"), declaration=thm.declaration(),
                      header_summary=header_summary(thm), lemmas=lemma_block(hits), n_max=n_max,
                      hypotheses=", ".join(names), feedback=feedback)
    try:
        proposal, _ = client.complete_json(ROLE, messages, PlanProposal, budget, sample=sample)
    except SpendLimitReached as exc:
        return fallback(f"budget_stop: {exc}", status="budget_stop")
    except BudgetExhausted:
        return fallback("budget_exhausted")
    except ProviderError as exc:
        return fallback(f"provider_error: {exc}")
    except LLMOutputError as exc:
        return fallback(f"invalid_output: {exc}")

    subgoals = [" ".join(s.split()) for s in proposal.subgoals]
    out["proposed"] = subgoals
    closing = clean_tactics(proposal.closing)
    if syntax is not None and len(subgoals) <= n_max:
        checked = syntax.check(thm.header, tactics=[closing] if closing.strip() else [], terms=subgoals)
        if bad := next((r for r in checked if not r.ok), None):
            tracer.event("plan_syntax_rejected", level="warning", node=ROLE, reason=bad.reason, detail=bad.detail)
            prefix = "direct_closing_failed: " if not subgoals else ""
            return fallback(f"{prefix}sanitize:{bad.reason}: {bad.detail}")
    if not subgoals:
        return _direct(thm, closing, proposal.rationale, runner, tracer, check, fallback, out)
    if len(subgoals) > n_max:
        return fallback(f"too_many_subgoals: {len(subgoals)} > n_max {n_max}")
    hyps = thm.hypothesis_names(len(subgoals))
    if hyps != names[:len(subgoals)]:  # the prompt announced other names than the skeleton uses
        return fallback(f"hypothesis_names: prompt {names[:len(subgoals)]} vs skeleton {hyps}")
    plan = Plan(subgoals=subgoals, closing=closing, hypotheses=hyps, rationale=proposal.rationale, source="llm")
    tracer.event("plan_proposed", node=ROLE, subgoals=subgoals, closing=closing, rationale=proposal.rationale)
    try:
        if check:
            if why := _check_llm_plan(thm, plan, runner, tracer, refute, closing_auto):
                return fallback(why)
            plan.skeleton_compiles = True
        else:
            skeleton(thm, plan.subgoals, plan.closing)  # sanitize only
    except UnsafeLean as exc:
        return fallback(f"sanitize:{exc.result.reason}: {exc}")
    out.update(status="ok", plan=plan.model_dump(), detail=None)


def _direct(thm: Theorem, closing: str, rationale: str, runner, tracer: Tracer, check: bool, fallback, out) -> None:
    """No subgoals: the closing block must be an accepted proof of the theorem itself."""
    tracer.event("plan_proposed", node=ROLE, subgoals=[], closing=closing, rationale=rationale)
    try:
        code = proof_file(thm, closing)
    except UnsafeLean as exc:
        return fallback(f"direct_closing_failed: sanitize:{exc.result.reason}: {exc}")
    plan = Plan(subgoals=[], closing=closing, hypotheses=[], rationale=rationale, source="direct")
    if check:
        result = runner.verify(code, thm.name)
        ok, why = accepted(result)
        errors = _errors(result)
        tracer.event("plan_direct", node=ROLE, accepted=ok, reason=why, errors=errors, cached_lean=result.cached)
        if not ok:
            return fallback(f"direct_closing_failed: {why}" + (": " + errors[0] if errors else ""))
        plan.skeleton_compiles = True
    out.update(status="ok", plan=plan.model_dump(), detail=None)
