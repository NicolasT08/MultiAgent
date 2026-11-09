"""Formal-MAS demo (Gradio): Solve, Recorded examples and Study results, in Spanish and English.

    python demo/app.py                    # http://127.0.0.1:7860
    DEMO_MODE=replay python demo/app.py   # recorded examples + results only (the public Space)

Presentation only. Solving goes through demo/live.py (the frozen C5 configuration via
scripts/run_eval.py), recorded runs through demo/replay.py; demo/steps.py turns trace events
into plain-language steps and demo/catalog.py builds the problem cards and the results tab.
Every visible text comes from demo/i18n.py. The "Solve" tab exists only with .env, Lean and
DEMO_MODE != replay (live.live_available).
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gradio as gr  # noqa: E402

import catalog  # noqa: E402
from i18n import DEFAULT_LANG, has, t  # noqa: E402
from live import DEMO_RUN_USD, LOAD_STEPS, LiveEngine, live_available  # noqa: E402
from replay import REPLAY_DIR, frames, load_replays, pair_frames, pairs  # noqa: E402
from steps import build_steps, result_html, steps_html  # noqa: E402

SPEEDS = {"1": 1.0, "5": 5.0, "20": 20.0, "0": 0.0}
LANG_CHOICES = [("ES", "es"), ("EN", "en")]
PAID, FREE = "experiment", "dev"

CSS = """
:root { --fm-ok: #15803d; --fm-fail: #b91c1c; --fm-warn: #b45309; --fm-run: #4338ca; --fm-muted: #64748b; }
.dark { --fm-ok: #4ade80; --fm-fail: #f87171; --fm-warn: #fbbf24; --fm-run: #a5b4fc; --fm-muted: #94a3b8; }
.gradio-container { max-width: 920px !important; margin: 0 auto !important; padding: 0 16px !important; }
.gradio-container, .gradio-container .prose { font-size: 18px; line-height: 1.6; }
#fm-head h1 { font-size: 2.4rem; margin: 0.4em 0 0.1em; letter-spacing: -0.01em; }
#fm-head p { font-size: 1.15rem; color: var(--fm-muted); margin: 0; }
#fm-lang { max-width: 150px; margin-left: auto; align-self: flex-start; margin-top: 1.2em; }
#fm-lang .wrap { flex-direction: row !important; flex-wrap: nowrap !important; gap: 0 !important; }
#fm-lang label { margin: 0 !important; border-radius: 0 !important; padding: 6px 14px !important; }
#fm-lang label:first-child { border-radius: 8px 0 0 8px !important; }
#fm-lang label:last-child { border-radius: 0 8px 8px 0 !important; }
#fm-lang input[type=radio] { display: none; }
.fm-status { margin: 0.6em 0 0.2em; }
.fm-status-text { font-weight: 600; }
.fm-status-sub { color: var(--fm-muted); font-size: 0.95em; }
.fm-bar { height: 8px; border-radius: 4px; background: var(--border-color-primary); overflow: hidden; margin-top: 6px; }
.fm-bar-fill { height: 100%; background: var(--fm-run); transition: width 0.6s ease; }
.fm-ready { color: var(--fm-ok); font-weight: 700; }
.fm-failed { color: var(--fm-fail); font-weight: 600; }
.fm-section h2, .fm-section h3 { margin-top: 1.4em !important; }
.fm-filters { gap: 8px !important; flex-wrap: wrap; }
.fm-filters button { min-width: 110px; font-size: 1rem !important; }
.fm-hint { color: var(--fm-muted); }
.fm-cards { max-height: 520px; overflow-y: auto; padding-right: 6px; gap: 12px !important; }
.fm-card { border: 1px solid var(--border-color-primary) !important; border-radius: 12px !important;
           padding: 14px 18px !important; background: var(--block-background-fill) !important; }
.fm-card.fm-selected { border: 2px solid var(--fm-run) !important; box-shadow: 0 0 0 3px rgba(67,56,202,0.12); }
.fm-card button { align-self: flex-start; }
.fm-badge { display: inline-block; font-size: 0.8em; font-weight: 700; padding: 1px 10px; border-radius: 999px;
            color: #fff; margin-right: 6px; }
.fm-badge-easy { background: #15803d; } .fm-badge-medium { background: #b45309; }
.fm-badge-hard { background: #b91c1c; } .fm-badge-custom { background: #4338ca; }
.fm-meta { font-weight: 600; }
.fm-eta { color: var(--fm-muted); margin-left: 10px; font-size: 0.9em; }
details > summary { cursor: pointer; color: var(--fm-muted); font-size: 0.92em; }
#fm-solve { font-size: 1.35rem !important; padding: 14px 0 !important; margin-top: 18px; }
.fm-note { color: var(--fm-muted); font-size: 0.95em; text-align: center; }
.fm-steps { display: flex; flex-direction: column; gap: 4px; }
.fm-step { display: flex; gap: 14px; align-items: flex-start; padding: 10px 4px 2px;
           border-top: 1px solid var(--border-color-primary); }
.fm-icon { font-size: 1.4em; width: 1.6em; text-align: center; line-height: 1.2; font-weight: 700; }
.fm-name { font-weight: 700; }
.fm-text { color: var(--body-text-color); }
.fm-pending .fm-icon, .fm-skip .fm-icon, .fm-pending .fm-name, .fm-skip .fm-name, .fm-skip .fm-text,
.fm-pending .fm-text { color: var(--fm-muted); }
.fm-done .fm-icon { color: var(--fm-ok); } .fm-failed .fm-icon { color: var(--fm-fail); }
.fm-redo .fm-icon { color: var(--fm-warn); }
.fm-running .fm-icon { color: var(--fm-run); display: inline-block; animation: fm-spin 1.4s linear infinite; }
@keyframes fm-spin { to { transform: rotate(360deg); } }
.fm-tech { margin: 0 0 6px 3.2em; }
.fm-result { border: 1px solid var(--border-color-primary); border-radius: 14px; padding: 20px 24px; }
.fm-result.fm-ok { border: 2px solid var(--fm-ok); } .fm-result.fm-fail { border: 2px solid var(--fm-fail); }
.fm-result.fm-warn { border: 2px solid var(--fm-warn); } .fm-result.fm-idle { color: var(--fm-muted); }
.fm-result-title { font-size: 1.6rem; font-weight: 800; }
.fm-ok .fm-result-title { color: var(--fm-ok); } .fm-fail .fm-result-title { color: var(--fm-fail); }
.fm-warn .fm-result-title { color: var(--fm-warn); }
.fm-result-note { color: var(--fm-muted); margin-top: 4px; }
.fm-metrics { display: flex; gap: 36px; margin-top: 16px; flex-wrap: wrap; }
.fm-metric-label { color: var(--fm-muted); font-size: 0.85em; }
.fm-metric-value { font-size: 1.5rem; font-weight: 700; }
.fm-cache { margin-top: 12px; font-size: 0.92em; color: var(--fm-warn); }
.fm-side-title h3 { margin: 0.2em 0 !important; }
"""

__all__ = ["build_app", "main"]


# -- small views ------------------------------------------------------------------------

def _head_md(lang: str) -> str:
    return f"# {t(lang, 'app.title')}\n\n{t(lang, 'app.tagline')}"


def status_html(engine: LiveEngine | None, lang: str) -> str:
    """'Preparing the verifier… (step X of 3)' with a progress bar, then 'Ready'."""
    if engine is None:
        return f'<div class="fm-status fm-status-sub">{html.escape(t(lang, "status.replay_only"))}</div>'
    if engine.state == "ready":
        return f'<div class="fm-status fm-ready">✓ {html.escape(t(lang, "status.ready"))}</div>'
    if engine.state == "failed":
        return f'<div class="fm-status fm-failed">{html.escape(t(lang, "status.failed"))}</div>'
    state = engine.state if engine.state in LOAD_STEPS else LOAD_STEPS[0]
    step = LOAD_STEPS.index(state) + 1
    width = round((step - 0.5) / len(LOAD_STEPS) * 100)
    return (f'<div class="fm-status"><div class="fm-status-text">{html.escape(t(lang, "status.preparing", step=step))}</div>'
            f'<div class="fm-status-sub">{html.escape(t(lang, f"status.step.{state}"))}</div>'
            f'<div class="fm-bar"><div class="fm-bar-fill" style="width:{width}%"></div></div></div>')


def _view(view: dict | None, lang: str, idle: str = "steps.idle") -> tuple[str, str]:
    """(steps, result) Markdown of a live or replayed run state {events, result, error, running}."""
    if not view or (not view.get("events") and not view.get("running") and not view.get("error")):
        return t(lang, idle), result_html(None, [], lang)
    events, result = view.get("events") or [], view.get("result")
    return (steps_html(build_steps(events, result, lang), lang),
            result_html(result, events, lang, error=view.get("error"), running=bool(view.get("running"))))


def _rep_text(lang: str, key: str, rep: dict, field: str) -> str:
    k = f"rep.{key}.{field}"
    return t(lang, k) if has(lang, k) else str(rep.get(field) or key)


def _side_title(lang: str, config: str | None) -> str:
    k = f"ex.side.{config}"
    return t(lang, k) if has(lang, k) else t(lang, "ex.side.other", config=config)


def _model_of(rep: dict) -> str:
    return ", ".join(sorted({str(m) for m in (rep.get("models") or {}).values()})) or "?"


def _example_card(lang: str, key: str, rep: dict) -> str:
    p = dict(rep.get("problem") or {})
    note = _rep_text(lang, key, rep, "note")
    recorded = t(lang, "ex.recorded", model=_model_of(rep))
    return f"### {_rep_text(lang, key, rep, 'title')}\n\n{note}\n\n<small>{recorded}</small>\n\n" + catalog.card_markdown(p, lang)


# -- app --------------------------------------------------------------------------------

def build_app(engine: LiveEngine | None = None, replays: dict | None = None,
              problems: list[dict] | None = None, results: dict | None = None) -> gr.Blocks:
    """`engine=None`: recorded examples and results only (no Solve tab)."""
    replays = load_replays() if replays is None else replays
    results = catalog.load_results(REPLAY_DIR) if results is None else results
    if engine is not None and problems is None:
        problems = catalog.load_problems(engine.root)
    problems = problems or []
    statics: list[tuple] = []  # (component, fn(lang) -> update): every text that changes with the language

    def static(component, fn):
        statics.append((component, fn))
        return component

    with gr.Blocks(title="Formal-MAS") as app:
        lang = gr.State(DEFAULT_LANG)
        with gr.Row(equal_height=True):
            head = static(gr.Markdown(_head_md(DEFAULT_LANG), elem_id="fm-head"), lambda L: _head_md(L))
            lang_pick = gr.Radio(LANG_CHOICES, value=DEFAULT_LANG, show_label=False, container=False,
                                 elem_id="fm-lang", scale=0, min_width=140)
        status = gr.HTML(status_html(engine, DEFAULT_LANG), elem_id="fm-status")
        timer = gr.Timer(1.0, active=engine is not None)

        with gr.Tabs():
            solve_parts = _build_solve_tab(engine, problems, lang, static) if engine is not None else None
            ex_parts = _build_examples_tab(replays, lang, static)
            with gr.Tab(t(DEFAULT_LANG, "tab.results"), elem_id="tab-results") as tab_results:
                static(tab_results, lambda L: gr.Tab(label=t(L, "tab.results")))
                static(gr.Markdown(catalog.results_markdown(results, DEFAULT_LANG), elem_classes="fm-section"),
                       lambda L: catalog.results_markdown(results, L))

        # language: every static text, then the views that hold a run
        comps = [c for c, _ in statics]
        lang_pick.change(lambda L: [L, status_html(engine, L)] + [fn(L) for _, fn in statics],
                         inputs=lang_pick, outputs=[lang, status] + comps)
        if solve_parts is not None:
            solve_parts["on_lang"](lang_pick)
        ex_parts["on_lang"](lang_pick)

        if engine is not None:
            def tick(L, selected):
                ready = engine.ready
                stop = engine.state in ("ready", "failed")
                return (status_html(engine, L), gr.Button(interactive=ready and selected is not None),
                        gr.Timer(active=not stop))
            timer.tick(tick, inputs=[lang, solve_parts["selected"]], outputs=[status, solve_parts["button"], timer])
            app.load(engine.start)
    return app


def _build_solve_tab(engine: LiveEngine, problems: list[dict], lang, static) -> dict:
    by_id = {p["id"]: p for p in problems}
    first_filter = "new" if any(p.get("new") for p in problems) else "easy"
    paid_name, paid_models = engine.profile_models(PAID)
    free_name, free_models = engine.profile_models(FREE)
    costs = catalog.typical_costs(engine.root)

    def profile_choices(L):
        return [(t(L, "adv.paid", model=f"{paid_name} ({paid_models})"), PAID),
                (t(L, "adv.free", model=f"{free_name} ({free_models})"), FREE)]

    def note(L, profile, paid_runs):
        name, models = (paid_name, paid_models) if profile == PAID else (free_name, free_models)
        line = t(L, "solve.model", model=f"{name} ({models})")
        if profile == PAID and not paid_runs:
            cost = catalog.cost_line(L, name, costs, DEMO_RUN_USD)
            line += f"<br>{cost}" if cost else ""
        elif profile != PAID:
            line += f" · {t(L, 'cost.free')}"
        return f'<div class="fm-note">{line}</div>'

    with gr.Tab(t(DEFAULT_LANG, "tab.solve"), elem_id="tab-live") as tab:
        static(tab, lambda L: gr.Tab(label=t(L, "tab.solve")))
        flt = gr.State(first_filter)
        selected = gr.State(None)
        version = gr.State(0)  # bumped after a run: "new" problems are recomputed
        view = gr.State(None)
        paid_runs = gr.State(0)

        static(gr.Markdown(f"## {t(DEFAULT_LANG, 'pick.title')}", elem_classes="fm-section"),
               lambda L: f"## {t(L, 'pick.title')}")
        with gr.Row(elem_classes="fm-filters"):
            fbtns = {f: gr.Button(t(DEFAULT_LANG, f"filter.{f}"), size="md",
                                  variant="primary" if f == first_filter else "secondary") for f in catalog.FILTERS}
        hint = gr.Markdown(t(DEFAULT_LANG, f"filter.hint.{first_filter}"), elem_classes="fm-hint")

        @gr.render(inputs=[lang, flt, selected, version])
        def cards(L, f, sel, _v):
            nonlocal problems, by_id
            shown = catalog.filter_problems(problems, f)
            with gr.Column(elem_classes="fm-cards"):
                if not shown:
                    gr.Markdown(t(L, "filter.empty"))
                for p in shown:
                    chosen = bool(sel) and sel.get("id") == p["id"]
                    with gr.Column(elem_classes=["fm-card"] + (["fm-selected"] if chosen else []), key=f"card-{p['id']}"):
                        # preserved_by_key=None: a re-render (language, selection) must replace the text
                        gr.Markdown(catalog.card_markdown(p, L), latex_delimiters=catalog.LATEX, key=f"md-{p['id']}",
                                    preserved_by_key=None)
                        b = gr.Button(t(L, "card.chosen" if chosen else "card.choose"), size="sm",
                                      variant="primary" if chosen else "secondary", key=f"btn-{p['id']}",
                                      preserved_by_key=None)
                        b.click(lambda p=p: (p, gr.Button(interactive=engine.ready)), outputs=[selected, go])

        picked = gr.Markdown("", elem_classes="fm-hint")
        go = gr.Button(t(DEFAULT_LANG, "solve.button"), variant="primary", size="lg", elem_id="fm-solve",
                       interactive=False)
        solve_note = gr.HTML(note(DEFAULT_LANG, PAID, 0))

        with gr.Accordion(t(DEFAULT_LANG, "adv.title"), open=False) as adv:
            static(adv, lambda L: gr.Accordion(label=t(L, "adv.title")))
            profile = gr.Radio(profile_choices(DEFAULT_LANG), value=PAID, label=t(DEFAULT_LANG, "adv.model"))
            static(profile, lambda L: gr.Radio(choices=profile_choices(L), label=t(L, "adv.model")))
            static(gr.Markdown(f"**{t(DEFAULT_LANG, 'adv.custom')}**\n\n{t(DEFAULT_LANG, 'adv.custom_note')}"),
                   lambda L: f"**{t(L, 'adv.custom')}**\n\n{t(L, 'adv.custom_note')}")
            custom = gr.Textbox(lines=5, show_label=False, placeholder=t(DEFAULT_LANG, "adv.custom_placeholder"))
            static(custom, lambda L: gr.Textbox(placeholder=t(L, "adv.custom_placeholder")))
            use_custom = static(gr.Button(t(DEFAULT_LANG, "adv.use_custom"), size="sm"),
                                lambda L: gr.Button(t(L, "adv.use_custom")))
            custom_msg = gr.Markdown("")

        static(gr.Markdown(f"## {t(DEFAULT_LANG, 'steps.title')}", elem_classes="fm-section"),
               lambda L: f"## {t(L, 'steps.title')}")
        steps_md = gr.Markdown(t(DEFAULT_LANG, "steps.idle"))
        static(gr.Markdown(f"## {t(DEFAULT_LANG, 'result.title')}", elem_classes="fm-section"),
               lambda L: f"## {t(L, 'result.title')}")
        result_md = gr.Markdown(result_html(None, [], DEFAULT_LANG))

    # filters
    def pick_filter(f, L):
        return [f, t(L, f"filter.hint.{f}")] + [gr.Button(variant="primary" if g == f else "secondary")
                                                 for g in catalog.FILTERS]
    for f, b in fbtns.items():
        b.click(lambda L, f=f: pick_filter(f, L), inputs=lang, outputs=[flt, hint] + list(fbtns.values()))

    def picked_text(sel, L):
        return t(L, "picked.custom") if sel and sel.get("split") == "custom" else ""
    selected.change(picked_text, inputs=[selected, lang], outputs=picked)
    profile.change(note, inputs=[lang, profile, paid_runs], outputs=solve_note)

    def take_custom(text, L):
        if not engine.ready:
            return gr.update(), t(L, "adv.custom_not_ready"), gr.update()
        prob, err = engine.custom_problem(text)
        if prob is None:
            return gr.update(), f"{t(L, 'adv.custom_bad')}\n\n{err}", gr.update()
        return prob, t(L, "adv.custom_ok"), gr.Button(interactive=True)
    use_custom.click(take_custom, inputs=[custom, lang], outputs=[selected, custom_msg, go])

    def run(sel, prof, L, paid, v):
        if sel is None:
            yield t(L, "solve.choose_first"), result_html(None, [], L), gr.Button(), None, paid, v, gr.update()
            return
        busy = gr.Button(t(L, "solve.running"), interactive=False)
        state = {"events": [], "result": None, "error": None, "running": True}
        yield (*_view(state, L), busy, state, paid, v, gr.update())
        for f in engine.stream(sel, prof):
            if f["refused"]:
                msg = t(L, {"busy": "solve.busy", "not_ready": "solve.not_ready"}.get(f["reason"], "solve.not_ready"))
                yield msg, result_html(None, [], L), gr.Button(t(L, "solve.button"), interactive=engine.ready), None, paid, v, gr.update()
                return
            state = {"events": f["events"], "result": f["row"] if f["done"] else None,
                     "error": f["error"] if f["done"] and f["row"] is None else None, "running": not f["done"]}
            yield (*_view(state, L), busy, state, paid, v, gr.update())
        nonlocal problems, by_id
        problems = catalog.load_problems(engine.root)  # a solved "new" problem is no longer new
        by_id = {p["id"]: p for p in problems}
        paid = paid + (prof == PAID)
        yield (*_view(state, L), gr.Button(t(L, "solve.button"), interactive=True), state, paid, v + 1,
               note(L, prof, paid))

    go.click(run, inputs=[selected, profile, lang, paid_runs, version],
             outputs=[steps_md, result_md, go, view, paid_runs, version, solve_note], concurrency_limit=1)

    def on_lang(lang_pick):
        lang_pick.change(lambda L, v, prof, paid, f, sel: (*_view(v, L), gr.Button(t(L, "solve.button")),
                                                           note(L, prof, paid), t(L, f"filter.hint.{f}"),
                                                           picked_text(sel, L),
                                                           *[gr.Button(t(L, f"filter.{g}")) for g in catalog.FILTERS]),
                         inputs=[lang_pick, view, profile, paid_runs, flt, selected],
                         outputs=[steps_md, result_md, go, solve_note, hint, picked] + list(fbtns.values()))

    return {"selected": selected, "button": go, "on_lang": on_lang}


def _build_examples_tab(replays: dict, lang, static) -> dict:
    keys = list(replays)
    pair_list = pairs(replays)

    def speed_choices(L):
        return [(t(L, f"speed.{k}"), k) for k in ("1", "5", "20", "0")]

    def example_choices(L):
        return [(_rep_text(L, k, replays[k], "title"), k) for k in keys]

    with gr.Tab(t(DEFAULT_LANG, "tab.examples"), elem_id="tab-replay") as tab:
        static(tab, lambda L: gr.Tab(label=t(L, "tab.examples")))
        if not keys:
            static(gr.Markdown(t(DEFAULT_LANG, "ex.none")), lambda L: t(L, "ex.none"))
            return {"on_lang": lambda lang_pick: None}
        static(gr.Markdown(t(DEFAULT_LANG, "ex.intro"), elem_classes="fm-hint"), lambda L: t(L, "ex.intro"))
        pick = gr.Radio(example_choices(DEFAULT_LANG), value=keys[0], label=t(DEFAULT_LANG, "ex.pick"))
        static(pick, lambda L: gr.Radio(choices=example_choices(L), label=t(L, "ex.pick")))
        card = gr.Markdown(_example_card(DEFAULT_LANG, keys[0], replays[keys[0]]), latex_delimiters=catalog.LATEX)
        with gr.Row():
            speed = gr.Radio(speed_choices(DEFAULT_LANG), value="5", label=t(DEFAULT_LANG, "ex.speed"), scale=3)
            static(speed, lambda L: gr.Radio(choices=speed_choices(L), label=t(L, "ex.speed")))
            play = static(gr.Button(t(DEFAULT_LANG, "ex.play"), variant="primary", size="lg", scale=1),
                          lambda L: gr.Button(t(L, "ex.play")))
        ex_view = gr.State(None)
        static(gr.Markdown(f"## {t(DEFAULT_LANG, 'steps.title')}", elem_classes="fm-section"),
               lambda L: f"## {t(L, 'steps.title')}")
        ex_steps = gr.Markdown(t(DEFAULT_LANG, "steps.idle_replay"))
        static(gr.Markdown(f"## {t(DEFAULT_LANG, 'result.title')}", elem_classes="fm-section"),
               lambda L: f"## {t(L, 'result.title')}")
        ex_result = gr.Markdown(result_html(None, [], DEFAULT_LANG))

        pick.change(lambda k, L: (_example_card(L, k, replays[k]), *_view(None, L, "steps.idle_replay"), None),
                    inputs=[pick, lang], outputs=[card, ex_steps, ex_result, ex_view])

        def run(k, sp, L):
            for shown, result in frames(replays[k], SPEEDS.get(sp, 5.0)):
                state = {"events": shown, "result": result, "error": None, "running": result is None}
                yield (*_view(state, L, "steps.idle_replay"), state)
        play.click(run, inputs=[pick, speed, lang], outputs=[ex_steps, ex_result, ex_view])

        pair_parts = None
        if pair_list:
            pair_parts = _build_pair_view(replays, pair_list, lang, static, speed_choices)

    def on_lang(lang_pick):
        lang_pick.change(lambda L, k, v: (_example_card(L, k, replays[k]) if k in replays else "",
                                            *_view(v, L, "steps.idle_replay")),
                         inputs=[lang_pick, pick, ex_view], outputs=[card, ex_steps, ex_result])
        if pair_parts:
            pair_parts(lang_pick)

    return {"on_lang": on_lang}


def _build_pair_view(replays, pair_list, lang, static, speed_choices):
    a0, b0 = pair_list[0]
    values = [f"{a}|{b}" for a, b in pair_list]

    def titles(L, value):
        a, b = value.split("|", 1)
        return (f"### {_side_title(L, replays[a].get('config'))}", f"### {_side_title(L, replays[b].get('config'))}")

    static(gr.Markdown(f"## {t(DEFAULT_LANG, 'ex.pair_title')}\n\n{t(DEFAULT_LANG, 'ex.pair_intro')}",
                       elem_classes="fm-section"),
           lambda L: f"## {t(L, 'ex.pair_title')}\n\n{t(L, 'ex.pair_intro')}")

    def pair_choices(L):
        return [(f"{catalog.competition((replays[a].get('problem') or {}).get('id', ''), L)}: "
                 f"{_side_title(L, replays[a].get('config'))} / {_side_title(L, replays[b].get('config'))}", f"{a}|{b}")
                for a, b in pair_list]

    pick = gr.Radio(pair_choices(DEFAULT_LANG), value=values[0], show_label=False, visible=len(values) > 1)
    static(pick, lambda L: gr.Radio(choices=pair_choices(L)))
    with gr.Row():
        speed = gr.Radio(speed_choices(DEFAULT_LANG), value="5", label=t(DEFAULT_LANG, "ex.speed"), scale=3)
        static(speed, lambda L: gr.Radio(choices=speed_choices(L), label=t(L, "ex.speed")))
        play = static(gr.Button(t(DEFAULT_LANG, "ex.play_both"), variant="primary", size="lg", scale=1),
                      lambda L: gr.Button(t(L, "ex.play_both")))
    pv = gr.State(None)
    ta, tb = titles(DEFAULT_LANG, values[0])
    with gr.Row(elem_id="pair-view", equal_height=False):
        with gr.Column():
            title_a = gr.Markdown(ta, elem_classes="fm-side-title")
            steps_a = gr.Markdown(t(DEFAULT_LANG, "steps.idle_pair"))
            result_a = gr.Markdown(result_html(None, [], DEFAULT_LANG))
        with gr.Column():
            title_b = gr.Markdown(tb, elem_classes="fm-side-title")
            steps_b = gr.Markdown(t(DEFAULT_LANG, "steps.idle_pair"))
            result_b = gr.Markdown(result_html(None, [], DEFAULT_LANG))

    def views(L, v):
        va, vb = (v or (None, None))
        return (*_view(va, L, "steps.idle_pair"), *_view(vb, L, "steps.idle_pair"))

    def run(value, sp, L):
        a, b = value.split("|", 1)
        for (ea, ra), (eb, rb) in pair_frames(replays[a], replays[b], SPEEDS.get(sp, 5.0)):
            v = ({"events": ea, "result": ra, "error": None, "running": ra is None},
                 {"events": eb, "result": rb, "error": None, "running": rb is None})
            yield (*views(L, v), v)
    play.click(run, inputs=[pick, speed, lang], outputs=[steps_a, result_a, steps_b, result_b, pv])
    pick.change(lambda value, L: (*titles(L, value), *views(L, None), None), inputs=[pick, lang],
                outputs=[title_a, title_b, steps_a, result_a, steps_b, result_b, pv])

    def on_lang(lang_pick):
        lang_pick.change(lambda L, value, v: (*titles(L, value), *views(L, v)), inputs=[lang_pick, pick, pv],
                         outputs=[title_a, title_b, steps_a, result_a, steps_b, result_b])
    return on_lang


def main() -> None:
    shown, reason = live_available()
    engine = LiveEngine() if shown else None
    if engine is not None:
        engine.start()  # load Lean while the browser opens
    else:
        print(f"Live mode hidden ({reason}); recorded examples and results only.", flush=True)
    app = build_app(engine)
    app.queue(default_concurrency_limit=2)
    host = os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
    if engine is not None and host not in ("127.0.0.1", "localhost"):
        print(f"WARNING: Live mode served on {host}: anyone who can reach it can start runs "
              "(paid ones are limited only by the spend caps).", flush=True)
    theme = gr.themes.Soft(primary_hue="indigo", neutral_hue="slate", text_size=gr.themes.sizes.text_lg,
                           font=[gr.themes.Font(f) for f in ("Segoe UI", "system-ui", "sans-serif")])
    app.launch(server_name=host, server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")), theme=theme, css=CSS,
               footer_links=[])


if __name__ == "__main__":
    main()
