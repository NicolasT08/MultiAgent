"""Plain-language view of a run: trace events → a list of steps, and the result card.

`build_steps(events, result, lang)` maps every trace event to one of the steps (Plan, Search,
Reason, Translate to Lean, Verify, Correct; for Baseline B: Reason in natural language,
Self-check, Translate to Lean, Verify) and gives each a status and one human sentence. The
technical details of a step are the step's own events rendered by timeline.render (Lean code,
errors, κ/π, lemmas), shown collapsed. Nothing here decides anything: the verdict shown is the
result row's `status` (the Lean kernel's acceptance), exactly as recorded.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from i18n import has, t
from timeline import render

STATUS_ICONS = {"pending": "○", "running": "◌", "done": "✓", "failed": "✗", "redo": "↻", "skip": "–"}
FULL_STEPS = ("plan", "search", "reason", "translate", "verify", "correct")
BASELINE_B_STEPS = ("solve_nl", "self_check", "translate", "verify")
STOPPED = {"provider_error", "budget_stop", "error", "lean_unavailable", "invalid_statement", "interrupted"}

__all__ = ["Step", "build_steps", "result_html", "steps_html", "STATUS_ICONS"]


@dataclass
class Step:
    key: str
    status: str = "pending"
    sentence: str = ""
    events: list[dict] = field(default_factory=list)


def _step_of(e: dict, baseline_b: bool) -> str | None:
    name = e.get("event", "")
    if name == "llm_call":
        return {"planner": "plan", "generator": "reason", "autoformalizer": "translate", "critic": "correct",
                "baseline_b": "solve_nl"}.get(e.get("role"))
    if name == "prepass":
        return "plan"  # tactics alone on the theorem, and on each subgoal right after the plan
    if name.startswith("plan_") or name == "invalid_statement":
        return "plan"
    if name == "retrieve":
        return "search"
    if name in ("sketch_vote", "sketch_failed"):
        return "reason"
    if name in ("formalize_failed", "formalize_verified", "subgoal_solved", "assemble",
                "claim_check_attempt", "claim_check_end"):
        return "verify"
    if name == "critique":
        return "correct"
    if name == "self_assessment":
        return "self_check"
    return None


def _why(lang: str, error_class: str | None) -> str:
    key = f"why.{error_class}"
    return t(lang, key) if error_class and has(lang, key) else t(lang, "why.unknown")


def _strategy(lang: str, s: str | None) -> str:
    return t(lang, f"strategy.{s}") if s and has(lang, f"strategy.{s}") else str(s or "?")


def build_steps(events: list[dict], result: dict | None, lang: str) -> list[Step]:
    """Steps with status and sentence; `result` is None while the run is going."""
    done = result is not None
    config = next((e.get("config") for e in events if e.get("event") == "problem_start"), None)
    baseline_b = config == "C2" or any(e.get("event") == "self_assessment" or e.get("role") == "baseline_b"
                                       for e in events)
    keys = BASELINE_B_STEPS if baseline_b else FULL_STEPS
    steps = {k: Step(k) for k in keys}
    current = None
    for e in events:
        if e.get("event") == "lean_verify":  # a Lean check belongs to whatever step asked for it
            steps[current or keys[0]].events.append(e)
            continue
        k = _step_of(e, baseline_b)
        if k in steps:
            steps[k].events.append(e)
            current = k
    success = bool(result and (result.get("status") == "solved"))

    def ev(k: str, name: str) -> list[dict]:
        return [e for e in steps[k].events if e.get("event") == name] if k in steps else []

    def running(k: str) -> bool:
        return not done and current == k

    # Plan --------------------------------------------------------------------------------
    if "plan" in steps:
        s = steps["plan"]
        auto = [e for e in ev("plan", "prepass") if (e.get("found") or {}).get("theorem")]
        plans = [e for e in s.events if e.get("event") in ("plan_proposed", "plan_fallback")]
        if auto:
            s.status, s.sentence = "done", t(lang, "s.plan.auto")
            for k in keys:
                if k != "plan" and not steps[k].events:
                    steps[k].status, steps[k].sentence = ("skip", t(lang, "s.not_needed")) if done else ("pending", "")
        elif not plans:  # no plan yet: thinking if the problem started, else still waiting (pending)
            if events:
                s.status, s.sentence = (("running", t(lang, "s.plan.running")) if not done
                                        else ("failed", t(lang, "why.planning")))
        else:
            last = plans[-1]
            proposed = [e for e in plans if e.get("event") == "plan_proposed"]
            n = len((proposed[-1] if proposed else {}).get("subgoals") or [])
            if last.get("event") == "plan_fallback":
                s.sentence = t(lang, "s.plan.fallback")
            elif len(proposed) > 1:
                s.sentence = t(lang, "s.plan.replanned", n=n)
            else:
                s.sentence = t(lang, "s.plan.done", n=n) if n > 1 else t(lang, "s.plan.done_one")
            s.status = "running" if running("plan") else ("redo" if len(plans) > 1 and not done else "done")

    # Search -------------------------------------------------------------------------------
    if "search" in steps and steps["search"].events:
        s = steps["search"]
        r = ev("search", "retrieve")
        n = len(r[-1].get("lemmas") or [])
        s.sentence = t(lang, "s.search.done", n=n) if len(r) == 1 else t(lang, "s.search.done_many", k=len(r), n=n)
        s.status = "running" if running("search") else "done"

    # Reason (Generator) -------------------------------------------------------------------
    if "reason" in steps and steps["reason"].events:
        s = steps["reason"]
        votes = ev("reason", "sketch_vote")
        if not votes:
            s.status, s.sentence = ("running", t(lang, "s.reason.running")) if not done else ("failed", t(lang, "why.unknown"))
        else:
            v = votes[-1]
            fields = {"samples": v.get("samples"), "strategy": _strategy(lang, v.get("strategy")),
                      "votes": (v.get("votes") or {}).get(v.get("strategy"), "?")}
            several = len({e.get("subgoal") for e in votes}) > 1
            s.sentence = (t(lang, "s.reason.done_part", i=v.get("subgoal"), **fields) if several
                          else t(lang, "s.reason.done", **fields))
            s.status = "running" if running("reason") else "done"

    # Natural-language solution and self-check (Baseline B) ------------------------------
    if "solve_nl" in steps and steps["solve_nl"].events:
        s = steps["solve_nl"]
        assessed = bool(steps["self_check"].events)
        s.status = "done" if assessed or done else "running"
        s.sentence = t(lang, "s.solve_nl.done") if s.status == "done" else t(lang, "s.solve_nl.running")
    if "self_check" in steps and steps["self_check"].events:
        s = steps["self_check"]
        a = steps["self_check"].events[-1]
        if a.get("verdict") == "correct":
            conf = a.get("confidence")
            s.status, s.sentence = "done", t(lang, "s.self_check.done", conf=round(float(conf) * 100) if conf is not None else "?")
        else:
            s.status, s.sentence = "redo", t(lang, "s.self_check.doubt")

    # Translate to Lean (Autoformalizer) ----------------------------------------------------
    if steps["translate"].events:
        s = steps["translate"]
        tried = [e for e in steps["verify"].events
                 if e.get("event") in (("claim_check_attempt",) if baseline_b else ("formalize_failed", "formalize_verified"))]
        k = len(tried) or len([e for e in s.events if e.get("event") == "llm_call"])
        s.status = "running" if running("translate") else "done"
        s.sentence = t(lang, "s.translate.running") if s.status == "running" else t(lang, "s.translate.done", k=k)

    # Verify (Lean) ------------------------------------------------------------------------
    s = steps["verify"]
    attempts = [e for e in s.events if e.get("event") in ("formalize_failed", "formalize_verified")]
    assemble = ev("verify", "assemble")
    check_end = ev("verify", "claim_check_end")
    critiques = steps["correct"].events if "correct" in steps else []
    later_critique = None
    if attempts:
        idx = events.index(attempts[-1]) if attempts[-1] in events else -1
        later_critique = next((e for e in events[idx + 1:] if e.get("event") in ("critique", "claim_check_attempt")), None)
    if check_end:
        ok = check_end[-1].get("status") == "verified"
        s.status = "done" if ok else "failed"
        s.sentence = t(lang, "s.check.confirmed") if ok else t(lang, "s.check.rejected", k=check_end[-1].get("attempts"))
    elif assemble:
        ok = bool(assemble[-1].get("accepted"))
        s.status = "done" if ok else "failed"
        s.sentence = t(lang, "s.verify.accepted") if ok else t(lang, "s.verify.assemble_rejected")
    elif attempts:
        last = attempts[-1]
        if last.get("event") == "formalize_verified":
            s.sentence = t(lang, "s.verify.part_ok", i=last.get("subgoal"))
            s.status = "running" if not done else "done"
        else:
            failed = [e for e in attempts if e.get("event") == "formalize_failed"]
            cls = (later_critique or {}).get("error_class") or _reason_class(last.get("reason"))
            k = (len([e for e in s.events if e.get("event") == "claim_check_attempt"]) or len(attempts)) if baseline_b                 else len(attempts)
            s.sentence = t(lang, "s.verify.rejected", k=max(k, 1), why=_why(lang, cls))
            if done:
                s.status = "failed"
            elif later_critique and later_critique.get("action") in ("retry", "replan"):
                s.status = "redo"
            elif baseline_b and failed:
                s.status = "redo"
            else:
                s.status = "running"
    elif s.events:
        s.status, s.sentence = ("running", t(lang, "s.verify.running")) if not done else ("done", "")

    # Correct (Critic) ---------------------------------------------------------------------
    if "correct" in steps:
        s = steps["correct"]
        crit = [e for e in critiques if e.get("event") == "critique"]
        if crit:
            c = crit[-1]
            why = _why(lang, c.get("error_class"))
            action = c.get("action")
            if action == "retry":
                route = t(lang, f"route.{c.get('route')}") if c.get("route") in ("FORMALIZE", "RETRIEVE", "GENERATE") \
                    else t(lang, "route.GENERATE")
                s.sentence = t(lang, "s.correct.retry", why=why, route=route)
            elif action == "replan":
                s.sentence = t(lang, "s.correct.replan", why=why)
            elif config == "C4":
                s.sentence = t(lang, "s.correct.abandon_no_critic", why=why)
            else:
                s.sentence = t(lang, "s.correct.abandon", why=t(lang, "why.out_of_attempts"))
            if action == "abandon":
                s.status = "failed"
            elif done and success:
                s.status = "done"
                s.sentence = t(lang, "s.correct.fixed", k=len(crit), action=t(lang, f"action.{action}"), why=why)
            elif done:
                s.status = "failed"
            else:
                s.status = "redo"
        elif done:
            s.status, s.sentence = ("done", t(lang, "s.correct.none")) if success else ("skip", t(lang, "s.not_needed"))

    for st in steps.values():  # never-reached steps of a finished run
        if done and st.status == "pending":
            st.status, st.sentence = "skip", t(lang, "s.not_needed")
        if not st.sentence and st.status == "pending":
            st.sentence = t(lang, "s.pending")
    return [steps[k] for k in keys]


def _reason_class(reason: str | None) -> str | None:
    if not reason:
        return None
    if reason.startswith("sanitize"):
        return "syntax"
    if "timeout" in reason:
        return "timeout"
    return None


def steps_html(steps: list[Step], lang: str) -> str:
    """Markdown with HTML blocks: one row per step, each with its collapsed technical details."""
    out = ['<div class="fm-steps">']
    for st in steps:
        icon = STATUS_ICONS.get(st.status, "○")
        out.append(f'<div class="fm-step fm-{st.status}"><span class="fm-icon">{icon}</span>'
                   f'<div class="fm-body"><div class="fm-name">{html.escape(t(lang, "step." + st.key))}</div>'
                   f'<div class="fm-text">{html.escape(st.sentence)}</div></div></div>')
        if st.events:
            tech = render(st.events).strip()
            if tech:
                out.append(f'<details class="fm-tech"><summary>{html.escape(t(lang, "steps.details"))}</summary>\n\n'
                           f"{tech}\n\n</details>")
    out.append("</div>")
    return "\n\n".join(out)


def _duration(lang: str, seconds: float) -> str:
    if seconds < 60:
        return t(lang, "result.seconds", s=max(1, round(seconds)))
    return t(lang, "result.minutes", m=f"{seconds / 60:.1f}")


def result_html(result: dict | None, events: list[dict], lang: str, error: str | None = None,
                running: bool = False) -> str:
    """The big result card; `result` is the run's result row (None while running or on error)."""
    if result is None and not error:
        return f'<div class="fm-result fm-idle">{html.escape(t(lang, "result.running" if running else "result.idle"))}</div>'
    r = result or {}
    status = r.get("status") or "error"
    check = (r.get("check") or {}).get("status")
    if status == "solved":
        kind, title, note = "ok", t(lang, "result.success"), t(lang, "result.success_note")
    elif status == "claimed_solved":
        if check == "verified":
            kind, title, note = "ok", t(lang, "result.claimed_ok"), ""
        else:
            kind, title, note = "warn", t(lang, "result.claimed"), t(lang, "result.claimed_note")
    elif status in STOPPED or error:
        kind, title = "warn", t(lang, "result.stopped")
        note = t(lang, "result.stopped_note", reason=error or status)
    else:
        kind, title, note = "fail", t(lang, "result.fail"), t(lang, "result.fail_note")
    llm = r.get("llm") or {}
    calls = llm.get("llm_calls")
    metrics = [(t(lang, "result.time"), _duration(lang, float(r.get("wall_s") or 0.0))),
               (t(lang, "result.queries"), str(calls if calls is not None else "—")),
               (t(lang, "result.cost"), f"{float(llm.get('cost_usd') or 0.0):.4f} USD")]
    parts = [f'<div class="fm-result fm-{kind}"><div class="fm-result-title">{html.escape(title)}</div>']
    if note:
        parts.append(f'<div class="fm-result-note">{html.escape(note)}</div>')
    parts.append('<div class="fm-metrics">' + "".join(
        f'<div class="fm-metric"><div class="fm-metric-label">{html.escape(k)}</div>'
        f'<div class="fm-metric-value">{html.escape(v)}</div></div>' for k, v in metrics) + "</div>")
    cached = int(llm.get("cached_calls") or 0)
    if cached and calls:
        parts.append(f'<div class="fm-cache">{html.escape(t(lang, "result.cache", n=cached, m=calls))}</div>')
    parts.append("</div>")
    body = "\n\n".join(parts)
    if r.get("proof"):
        body += (f'\n\n<details class="fm-proof"><summary>{html.escape(t(lang, "result.see_proof"))}</summary>\n\n'
                 f"```lean\n{r['proof'].strip()}\n```\n\n</details>")
    if r.get("solution"):
        answer = f"\n\n**{html.escape(t(lang, 'result.answer', answer=r['final_answer']))}**" if r.get("final_answer") else ""
        body += (f'\n\n<details class="fm-proof"><summary>{html.escape(t(lang, "result.see_solution"))}</summary>\n\n'
                 f"{r['solution'].strip()}{answer}\n\n</details>")
    return body
