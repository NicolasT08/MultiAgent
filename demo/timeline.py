"""Render trace events (runs/<run>/trace.jsonl, RNF-5) as a Markdown timeline.

Shared by the Live mode (events read from the trace while the run is going) and the Replay mode
(events exported by scripts/export_replays.py). Pure Python with no project imports, so the
public Space can use it without Lean, the LLM client or the retriever.

`render(events, result=None)` returns the whole timeline; it is called again after each new
event (a problem has at most a few hundred events). Unknown events are skipped unless they
carry `level: warning|error`, which are shown as one generic line.
"""

from __future__ import annotations

import json

STANDARD_AXIOMS = ("propext", "Classical.choice", "Quot.sound")
MAX_LEMMAS = 8
MAX_ERRORS = 3
MAX_CODE_CHARS = 2500

__all__ = ["STANDARD_AXIOMS", "render", "summary", "sketch_of"]


def _code(text: str | None, lang: str = "lean") -> str:
    text = (text or "").strip("\n")
    if not text:
        return "_(empty)_\n"
    if len(text) > MAX_CODE_CHARS:
        text = text[:MAX_CODE_CHARS] + "\n… (truncated)"
    return f"```{lang}\n{text}\n```\n"


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in (text or "").strip().splitlines()) + "\n"


def _names(names: list | None, limit: int = MAX_LEMMAS) -> str:
    names = list(names or [])
    shown = ", ".join(f"`{n}`" for n in names[:limit])
    return shown + (f" … (+{len(names) - limit} more)" if len(names) > limit else "")


def _errors(errors) -> str:
    if not errors:
        return ""
    if isinstance(errors, str):
        errors = [errors]
    shown = [str(e) for e in errors[:MAX_ERRORS]]
    more = f"\n… (+{len(errors) - MAX_ERRORS} more errors)" if len(errors) > MAX_ERRORS else ""
    return _code("\n\n".join(shown) + more, "text")


def sketch_of(event: dict) -> dict | None:
    """Strategy and sketch of a Generator call: exported replays carry them as fields, a live
    trace has the model's raw JSON in `content`."""
    if event.get("sketch") is not None:
        return {"strategy": event.get("strategy"), "sketch": event.get("sketch")}
    content = event.get("content")
    if not content:
        return None
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and data.get("sketch"):
        return {"strategy": data.get("strategy"), "sketch": str(data["sketch"])}
    return None


def _axioms_line(verify: dict | None) -> str:
    if not verify:
        return ""
    if verify.get("axioms") is None:
        return (f"- `sorry`: **{'yes ✗' if verify.get('sorry') else 'none ✓'}**\n"
                "- `#print axioms`: _not reported_\n")
    axioms = verify["axioms"]
    standard = all(a in STANDARD_AXIOMS for a in axioms)  # [] is accepted too (runner.accepted)
    sorry = verify.get("sorry")
    return (f"- `sorry`: **{'yes ✗' if sorry else 'none ✓'}**\n"
            f"- `#print axioms`: {', '.join(f'`{a}`' for a in axioms) or '_none reported_'} "
            f"→ **{'standard only ✓' if standard else 'NOT standard ✗'}**\n")


class _Renderer:
    def __init__(self):
        self.out: list[str] = []
        self.last_verify: dict | None = None
        self.calls = 0

    def add(self, text: str) -> None:
        self.out.append(text.rstrip("\n") + "\n")

    # one method per event ------------------------------------------------------------

    def problem_start(self, e):
        self.add(f"### ▶ Problem `{e.get('problem_id', '?')}` — configuration {e.get('config', '?')}")

    def prepass(self, e):
        found = e.get("found") or {}
        targets = ", ".join(e.get("targets") or [])
        if found:
            closed = "; ".join(f"`{tac}` closes **{tgt}**" for tgt, tac in found.items())
            self.add(f"**Pre-pass** (tactics alone, no LLM) on {targets}: {closed} ✓")
        else:
            self.add(f"**Pre-pass** (tactics alone, no LLM) on {targets}: no tactic closes it "
                     f"({e.get('seconds', '?')} s)")

    def llm_call(self, e):
        self.calls += 1
        cost = e.get("cost_usd") or 0.0
        cached = " · cached" if e.get("cached") else ""
        self.add(f"<sub>LLM call {self.calls}: **{e.get('role', '?')}** ({e.get('model_returned') or e.get('model_key') or '?'}) "
                 f"· {e.get('latency_s', 0):.1f} s · ${cost:.4f}{cached}</sub>")
        if e.get("role") == "generator":
            sk = sketch_of(e)
            if sk:
                self.add(f"<details><summary>Sketch (strategy <code>{sk.get('strategy')}</code>)</summary>\n\n"
                         f"{_quote(sk['sketch'][:MAX_CODE_CHARS])}\n</details>")

    def plan_proposed(self, e):
        subgoals = e.get("subgoals") or []
        self.add(f"**Planner** proposes {len(subgoals)} subgoal(s):")
        for i, sg in enumerate(subgoals, 1):
            self.add(f"{i}. `{sg}`")
        if e.get("closing"):
            self.add("Closing step (uses the subgoals as hypotheses):")
            self.add(_code(e["closing"]))
        if e.get("rationale"):
            self.add(f"_Rationale: {e['rationale']}_")

    def plan_skeleton(self, e):
        ok = e.get("compiles")
        self.add(f"Skeleton built by the code ({e.get('stage', '?')}, with `sorry`): "
                 f"{'compiles ✓' if ok else 'does not compile ✗'}")
        if not ok:
            self.add(_errors(e.get("errors")))

    def plan_refute_check(self, e):
        refuted = e.get("refuted") or []
        if e.get("hypotheses_inconsistent"):
            self.add("Refutation check: the hypotheses are contradictory, check skipped")
        elif refuted:
            self.add(f"Refutation check: **false subgoal(s) {refuted}** → plan rejected ✗")
        else:
            self.add("Refutation check: no subgoal is refutable ✓")

    def plan_closing(self, e):
        self.add(f"Closing step checked by Lean: {'accepted ✓' if e.get('accepted') else 'rejected ✗ (' + str(e.get('reason')) + ')'}")

    def plan_closing_auto(self, e):
        self.add(f"Closing step replaced by an automatic tactic: `{e.get('reason')}` ✓")

    def plan_fallback(self, e):
        self.add(f"⚠ Plan not usable (`{str(e.get('reason'))[:200]}`) → **trivial plan** (the whole goal is the only subgoal)")

    def plan_syntax_rejected(self, e):
        self.add(f"⚠ Plan rejected by the syntax whitelist: `{str(e.get('reason') or e.get('detail') or '')[:200]}`")

    def retrieve(self, e):
        lemmas = e.get("lemmas") or []
        extra = " (k raised after a missing lemma)" if e.get("expanded") else ""
        self.add(f"**Retriever** (RAG over Mathlib), subgoal {e.get('subgoal')}: {len(lemmas)} lemmas{extra}: {_names(lemmas)}")

    def sketch_vote(self, e):
        votes = ", ".join(f"{k}: {v}" for k, v in (e.get("votes") or {}).items())
        self.add(f"**Generator** subgoal {e.get('subgoal')}: {e.get('samples')} sketches, votes {{{votes}}} "
                 f"→ strategy **{e.get('strategy')}**")

    def lean_verify(self, e):
        self.last_verify = e  # shown with `assemble`; single checks are too many to list

    def formalize_failed(self, e):
        where = f"subgoal {e.get('subgoal')}"
        self.add(f"✗ **Lean attempt** on {where}: `{e.get('reason')}`")
        self.add(_code(e.get("tactics")))
        if e.get("errors"):
            self.add(_errors(e.get("errors")))
        elif e.get("detail"):
            self.add(_errors([e["detail"]]))

    def formalize_verified(self, e):
        self.add(f"✓ **Lean accepts** subgoal {e.get('subgoal')}:")
        self.add(_code(e.get("tactics")))

    def critique(self, e):
        line = (f"**Critic** (rules): κ = `{e.get('error_class')}` → π = **{e.get('action')}**"
                f" ({'route ' + str(e['route']) + '; ' if e.get('route') else ''}attempt {e.get('attempts')}, replans {e.get('replans')}, "
                f"{e.get('calls')} LLM calls used)")
        if e.get("missing_identifier"):
            line += f"; missing name `{e['missing_identifier']}`"
            if e.get("suggestions"):
                line += f", suggestions {_names(e['suggestions'], 5)}"
        self.add(line)

    def subgoal_solved(self, e):
        by = "the pre-pass" if e.get("solved_by") == "prepass" else e.get("solved_by")
        self.add(f"Subgoal {e.get('subgoal')} solved by {by} (attempt {e.get('attempts')})")

    def assemble(self, e):
        if e.get("accepted"):
            self.add("**Assembly**: the code puts the verified pieces into the original theorem. "
                     "Lean kernel verdict: **accepted ✓**")
            self.add(_axioms_line(self.last_verify))
        else:
            self.add(f"**Assembly** rejected ✗ (`{e.get('reason')}`)")
            self.add(_errors(e.get("errors")))

    def pipeline_end(self, e):
        self.add(f"**Pipeline end**: `{e.get('status')}`" + (f" (stage {e['stage']})" if e.get("stage") else ""))

    def self_assessment(self, e):
        self.add(f"**Baseline B self-assessment** (same model, no Lean): verdict **{e.get('verdict')}**, "
                 f"confidence {e.get('confidence')}")

    def claim_check_start(self, e):
        self.add("---\n#### Lean check of Baseline B's claimed proof\n"
                 "The claimed natural-language proof is handed to the Autoformalizer → Verifier loop (up to 3 attempts).")

    def claim_check_attempt(self, e):
        self.add(f"Check attempt {e.get('attempt')}: `{e.get('reason')}` (κ = `{e.get('error_class')}`)")

    def claim_check_end(self, e):
        ok = e.get("status") == "verified"
        self.add(f"**Claim check**: `{e.get('status')}` — "
                 + ("confirmed by Lean ✓" if ok else "**not confirmed by Lean** ✗ (which is not the same as incorrect)"))

    def invalid_statement(self, e):
        self.add(f"✗ The statement could not be parsed: `{e.get('error')}`")

    def problem_end(self, e):
        llm = e.get("llm") or {}
        wall = float(e.get("wall_s") or 0.0)
        self.add(f"### ■ {e.get('status')} — {llm.get('llm_calls', '?')}/{llm.get('b_max', '?')} LLM calls, "
                 f"${llm.get('cost_usd', 0):.4f}, {wall / 60:.1f} min")

    def generic(self, e):
        if e.get("level") in ("warning", "error"):
            detail = e.get("reason") or e.get("error") or e.get("detail") or ""
            self.add(f"⚠ `{e.get('event')}` {str(detail)[:200]}")


_SKIP = {"run_start", "run_end", "lean_runner_info"}


def render(events: list[dict], result: dict | None = None) -> str:
    r = _Renderer()
    for e in events:
        name = e.get("event", "")
        if name in _SKIP:
            continue
        handler = getattr(r, name, None) if name.isidentifier() and not name.startswith("_") else None
        if not callable(handler) or name in ("add", "generic"):
            handler = r.generic
        handler(e)
    if result:
        r.add(_result_block(result))
    return "\n".join(r.out)


def _result_block(result: dict) -> str:
    parts = []
    if result.get("solution"):
        parts.append("#### Baseline B's natural-language solution (never checked by Lean during C2)\n"
                     + _quote(result["solution"][:4000]))
        if result.get("final_answer"):
            parts.append(f"**Claimed final answer:** {result['final_answer']}")
    if result.get("proof"):
        parts.append("#### Final proof accepted by the Lean kernel\n" + _code(result["proof"]))
    return "\n".join(parts)


def summary(result: dict | None, events: list[dict]) -> str:
    """One-paragraph outcome: status, calls, cost, minutes."""
    end = next((e for e in reversed(events) if e.get("event") == "problem_end"), None)
    if end is None and not result:
        return "_running…_"
    src = result or {}
    llm = src.get("llm") or (end or {}).get("llm") or {}
    status = src.get("status") or (end or {}).get("status")
    wall = float(src.get("wall_s") or (end or {}).get("wall_s") or 0.0)
    verdict = {"solved": "**Solved** — the Lean kernel accepted the proof.",
               "claimed_solved": "**Claimed solved** by Baseline B (not verified by Lean)."}.get(
        status, f"**Not solved** (`{status}`).")
    check = (src.get("check") or {}).get("status")
    if check:
        verdict += (" Lean check of the claim: **confirmed by Lean**." if check == "verified"
                    else f" Lean check of the claim: **not confirmed by Lean** (`{check}`).")
    return (f"{verdict}\n\nLLM calls: {llm.get('llm_calls', '?')} of b_max = {llm.get('b_max', '?')} · "
            f"cost: ${llm.get('cost_usd', 0):.4f} · time: {wall / 60:.1f} min")
