"""Offline tests of the Gradio demo (demo/) and of scripts/export_replays.py: no Lean, no LLM."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

gr = pytest.importorskip("gradio")

import app  # noqa: E402
import catalog  # noqa: E402
import i18n  # noqa: E402
import steps  # noqa: E402
import live  # noqa: E402
import replay  # noqa: E402
import timeline  # noqa: E402


def _export_module():
    spec = importlib.util.spec_from_file_location("export_replays", ROOT / "scripts" / "export_replays.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EVENTS = [
    {"t": 0.0, "event": "problem_start", "config": "C5"},
    {"t": 0.3, "event": "prepass", "targets": ["theorem"], "found": {}, "seconds": 0.3},
    {"t": 6.0, "event": "llm_call", "role": "planner", "model_returned": "m", "latency_s": 5.0, "cost_usd": 0.001},
    {"t": 6.1, "event": "plan_proposed", "subgoals": ["f 600 = f 500 / (6 / 5)"], "closing": "rw [h1]\nnorm_num",
     "rationale": "use h₀"},
    {"t": 6.2, "event": "retrieve", "subgoal": 1, "lemmas": [f"L{i}" for i in range(16)]},
    {"t": 8.0, "event": "llm_call", "role": "generator", "latency_s": 2.0, "cost_usd": 0.0005,
     "strategy": "direct_computation", "sketch": "1. apply h₀"},
    {"t": 8.1, "event": "sketch_vote", "subgoal": 1, "votes": {"direct_computation": 2, "x": 1},
     "strategy": "direct_computation", "samples": 3},
    {"t": 9.0, "event": "formalize_failed", "subgoal": 1, "reason": "sanitize:parse",
     "detail": "<input>:3:12: expected end of input", "tactics": "have h := h₀ 500\n norm_num"},
    {"t": 9.5, "event": "critique", "subgoal": 1, "error_class": "syntax", "action": "retry", "route": "FORMALIZE",
     "attempts": 1, "replans": 0, "calls": 5},
    {"t": 12.0, "event": "formalize_verified", "subgoal": 1, "tactics": "exact h"},
    {"t": 12.1, "event": "lean_verify", "status": "finished", "sorry": False,
     "axioms": ["propext", "Classical.choice", "Quot.sound"]},
    {"t": 12.1, "event": "assemble", "accepted": True, "reason": "accepted", "errors": []},
    {"t": 12.1, "event": "pipeline_end", "status": "solved", "solved": True, "stage": "assemble"},
    {"t": 12.2, "event": "mystery_event", "level": "warning", "reason": "odd"},
    {"t": 12.2, "event": "problem_end", "status": "solved", "llm": {"llm_calls": 7, "b_max": 40, "cost_usd": 0.0031},
     "wall_s": 22.7},
]
REPLAY = {"schema": 1, "title": "Medium: one retry", "note": "n", "config": "C5", "run_id": "r", "profile": "experiment",
          "models": {"planner": "m"},
          "problem": {"id": "amc12_2001_p9", "split": "test", "difficulty": "medium", "area": "algebra",
                      "informal": "Find f(600).", "statement": "theorem amc12_2001_p9 : True := by\n"},
          "events": EVENTS,
          "result": {"status": "solved", "solved": True, "proof": "theorem amc12_2001_p9 : True := trivial",
                     "llm": {"llm_calls": 7, "b_max": 40, "cost_usd": 0.0031}, "wall_s": 22.7}}


# -- timeline ----------------------------------------------------------------------------

def test_render_shows_every_stage():
    md = timeline.render(EVENTS, REPLAY["result"])
    for text in ("Pre-pass", "Planner", "f 600 = f 500 / (6 / 5)", "Retriever", "+8 more", "direct_computation",
                 "1. apply h₀", "Lean attempt", "expected end of input", "κ = `syntax`", "π = **retry**",
                 "Lean accepts", "accepted ✓", "none ✓", "standard only ✓", "`mystery_event`",
                 "7/40 LLM calls", "Final proof accepted"):
        assert text in md, text


def test_render_flags_nonstandard_axioms_and_skips_unknown_info_events():
    md = timeline.render([{"event": "lean_verify", "sorry": True, "axioms": ["sorryAx"]},
                          {"event": "assemble", "accepted": True}, {"event": "quiet_event"}])
    assert "NOT standard ✗" in md and "yes ✗" in md and "quiet_event" not in md


def test_live_sketch_is_parsed_from_raw_content():
    e = {"event": "llm_call", "role": "generator", "content": json.dumps({"strategy": "induction", "sketch": "base"})}
    assert timeline.sketch_of(e) == {"strategy": "induction", "sketch": "base"}
    assert timeline.sketch_of({"content": "not json"}) is None


def test_summary_reports_calls_cost_and_minutes():
    s = timeline.summary(REPLAY["result"], EVENTS)
    assert "Solved" in s and "7 of b_max = 40" in s and "$0.0031" in s and "0.4 min" in s
    assert timeline.summary(None, []) == "_running…_"


# -- replay ------------------------------------------------------------------------------

def test_play_yields_each_event_then_the_result():
    sleeps = []
    frames = list(replay.play(REPLAY, speed=1.0, sleep=sleeps.append))
    assert len(frames) == len(EVENTS) + 1
    assert "Final proof accepted" in frames[-1][0] and "Solved" in frames[-1][1]
    assert sleeps and max(sleeps) <= replay.MAX_GAP_S  # the 5.7 s gap is shortened


def test_play_instant_never_sleeps():
    sleeps = []
    list(replay.play(REPLAY, speed=0.0, sleep=sleeps.append))
    assert sleeps == []


def test_load_replays_skips_other_files(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(REPLAY), encoding="utf-8")
    (tmp_path / "b.json").write_text("{bad", encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps({"schema": 99, "events": []}), encoding="utf-8")
    assert list(replay.load_replays(tmp_path)) == ["a"]


def test_problem_card_withholds_putnam_text():
    card = replay.problem_card({"id": "putnam_x", "informal": None, "informal_withheld": True, "statement": "theorem t"})
    assert "MAA" in card and "theorem t" in card


# -- app: modes --------------------------------------------------------------------------

def _elem_ids(blocks) -> set[str]:
    return {getattr(b, "elem_id", None) for b in blocks.blocks.values()}


def test_live_hidden_with_demo_mode_replay(tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    shown, reason = live.live_available(tmp_path, {"DEMO_MODE": "replay"})
    assert not shown and "DEMO_MODE" in reason


def test_live_hidden_without_env_file(tmp_path):
    shown, reason = live.live_available(tmp_path, {})
    assert not shown and ".env" in reason


def test_live_hidden_without_lean(tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    shown, reason = live.live_available(tmp_path, {})
    assert not shown and "Lean" in reason


def test_replay_only_app_has_no_live_tab():
    blocks = app.build_app(engine=None, replays={"1_x": REPLAY})
    ids = _elem_ids(blocks)
    assert "tab-replay" in ids and "tab-results" in ids and "tab-live" not in ids


def test_app_without_replays_still_builds():
    ids = _elem_ids(app.build_app(engine=None, replays={}))
    assert "tab-replay" in ids and "tab-live" not in ids


def test_app_with_engine_has_live_tab(tmp_path):
    (tmp_path / "data").mkdir()
    row = {"id": "p1", "split": "dev", "difficulty": "easy", "area": "algebra", "informal": "i", "statement": "theorem p1"}
    (tmp_path / "data" / "eval.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    engine = live.LiveEngine(root=tmp_path)  # never started: no Lean
    assert "tab-live" in _elem_ids(app.build_app(engine=engine, replays={}))


def test_solve_refuses_before_lean_is_ready(tmp_path):
    engine = live.LiveEngine(root=tmp_path)
    out = list(engine.solve({"id": "p"}, "dev"))
    assert len(out) == 1 and "not ready" in out[0][1]


def test_read_new_waits_for_complete_lines(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_bytes(b'{"event": "a"}\n{"event": "b"')
    events, offset = live._read_new(path, 0)
    assert [e["event"] for e in events] == ["a"]
    with path.open("ab") as fh:
        fh.write(b"}\n")
    events, _ = live._read_new(path, offset)
    assert [e["event"] for e in events] == ["b"]


def test_shared_runner_survives_close():
    class R:
        closed = False

        def close(self):
            self.closed = True

        def verify(self):
            return "ok"

    r = R()
    shared = live._Shared(r)
    shared.close()
    assert not r.closed and shared.verify() == "ok"


def test_card_withholds_maa_text():
    card = catalog.card_markdown({"id": "putnam_1966_b4", "license": "Apache-2.0; informal text with permission "
                                  "from the MAA", "informal": "secret", "difficulty": "hard", "area": "combinatorics",
                                  "statement": "theorem putnam_1966_b4 : True := by"}, "es")
    assert "secret" not in card and "MAA" in card and "Putnam 1966 · B4" in card


# -- export ------------------------------------------------------------------------------

def test_clean_event_keeps_only_whitelisted_fields():
    ex = _export_module()
    t0 = datetime(2026, 11, 3, 17, 51, 19, tzinfo=timezone.utc)
    e = {"ts": "2026-11-03T17:51:26.171Z", "event": "llm_call", "problem_id": "p", "role": "generator",
         "cost_usd": 0.001, "latency_s": 6.1, "cache_key": "abc", "system_fingerprint": "fp",
         "content": json.dumps({"strategy": "induction", "sketch": "s" * 5000}), "prompt": "secret"}
    out = ex.clean_event(e, t0)
    assert out["t"] == pytest.approx(7.171)
    assert out["sketch"].endswith("(truncated)") and out["strategy"] == "induction"
    for key in ("content", "cache_key", "system_fingerprint", "prompt", "problem_id", "ts"):
        assert key not in out
    assert ex.clean_event({"ts": e["ts"], "event": "run_start", "argv": ["x"]}, t0) is None


def test_clean_problem_end_keeps_only_totals():
    ex = _export_module()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = ex.clean_event({"ts": "2026-01-01T00:00:01.000Z", "event": "problem_end", "status": "solved",
                          "llm": {"llm_calls": 3, "b_max": 40, "cost_usd": 0.1, "tokens": {"prompt": 9}}}, t0)
    assert out["llm"] == {"llm_calls": 3, "b_max": 40, "cost_usd": 0.1}


def test_export_problem_withholds_putnam_informal_and_c2_solution():
    ex = _export_module()
    p = ex._problem({"id": "putnam_1", "license": "Apache-2.0 (Lean statement); informal text available with "
                     "permission from the MAA", "informal": "secret", "statement": "theorem putnam_1"})
    assert p["informal"] is None and p["informal_withheld"]
    res = ex._result({"config": "C2", "status": "claimed_solved", "solution": "restates secret", "llm": {}}, True)
    assert "solution" not in res


def test_results_tables_copies_tables_1_and_2():
    ex = _export_module()
    text = ("# T\n### Runs used\nx\n### Table 1. SR\n| a |\n### Table 1b. other\ny\n"
            "### Table 2. H1–H3\n| H1 |\n### Table 2b. exploratory\nz\n## Notes\n")
    out = ex.results_tables(text)
    assert "| a |" in out and "| H1 |" in out and "Table 1b" not in out and "exploratory" not in out


# -- side by side --------------------------------------------------------------------------

def test_pairs_groups_same_problem_by_config():
    c4 = {**REPLAY, "config": "C4"}
    other = {**REPLAY, "problem": {"id": "other"}}
    assert replay.pairs({"z_c5": REPLAY, "a_c4": c4, "o": other}) == [("a_c4", "z_c5")]


def test_play_pair_shows_each_result_when_its_events_end():
    short = {**REPLAY, "config": "C4", "events": EVENTS[:2],
             "result": {"status": "failed", "llm": {"llm_calls": 1, "b_max": 40, "cost_usd": 0.0}, "wall_s": 1}}
    frames = list(replay.play_pair(short, REPLAY, speed=0.0))
    assert len(frames) == len(EVENTS) + 2
    assert "Not solved" in frames[2][1] and frames[2][3] == "_replaying…_"  # C4 done, C5 still going
    assert "Solved" in frames[-1][3] and "Final proof accepted" in frames[-1][2]


def test_app_shows_pair_view_only_with_a_pair():
    assert "pair-view" not in _elem_ids(app.build_app(engine=None, replays={"x": REPLAY}))
    ids = _elem_ids(app.build_app(engine=None, replays={"x": REPLAY, "y": {**REPLAY, "config": "C4"}}))
    assert "pair-view" in ids


def test_committed_replays_are_clean():
    """demo/replays/*.json: schema 1, whitelisted events only, no raw model output or local paths."""
    ex = _export_module()
    found = replay.load_replays()
    for key, rep in found.items():
        text = json.dumps(rep, ensure_ascii=False)
        for bad in ('"content"', '"cache_key"', '"system_fingerprint"', "NickG", "Documentos", '"argv"'):
            assert bad not in text, (key, bad)
        assert not re.search(r"\b[A-Za-z]:(\\\\|/)|/home/|/Users/", text), key  # no drive or home paths
        for e in rep["events"]:
            assert e["event"] in ex.KEEP and set(e) <= {"t", *ex.COMMON, *ex.KEEP[e["event"]], "strategy", "sketch"}
        if "MAA" in str(rep["problem"].get("license")):
            assert rep["problem"]["informal"] is None and "solution" not in rep["result"]


# -- review fixes ----------------------------------------------------------------------------

def test_empty_axiom_list_is_standard_and_missing_is_not_reported():
    ok = timeline.render([{"event": "lean_verify", "sorry": False, "axioms": []}, {"event": "assemble", "accepted": True}])
    assert "standard only ✓" in ok
    missing = timeline.render([{"event": "lean_verify", "sorry": False}, {"event": "assemble", "accepted": True}])
    assert "not reported" in missing


class _FakeRunEval:
    """Stands in for scripts/run_eval.py: writes a trace and a result row like a C5 run."""

    def __init__(self, runs, gate=None):
        self.runs, self.gate, self.calls = runs, gate, []

    def main(self, argv, runner_factory, retriever_factory, on_run_dir):
        import os
        self.calls.append({"argv": argv, "profile": os.environ.get("MODEL_PROFILE"), "runner": runner_factory()})
        run_dir = self.runs / f"run{len(self.calls)}"
        run_dir.mkdir(parents=True)
        on_run_dir(run_dir)
        if self.gate is not None:
            self.gate.wait(5)
        with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as fh:
            for e in EVENTS:
                fh.write(json.dumps(e) + "\n")
        (run_dir / "results.jsonl").write_text(json.dumps({"problem_id": "p", **REPLAY["result"]}) + "\n",
                                               encoding="utf-8")
        return 0


def _ready_engine(tmp_path, fake):
    engine = live.LiveEngine(root=tmp_path)
    engine.state, engine.runner, engine.retriever = "ready", object(), object()
    engine._run_eval = lambda: fake
    return engine


def test_solve_streams_runs_c5_with_cap_and_restores_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "POLL_S", 0.01)
    monkeypatch.delenv("MODEL_PROFILE", raising=False)
    fake = _FakeRunEval(tmp_path / "runs")
    engine = _ready_engine(tmp_path, fake)
    frames = list(engine.solve({"id": "my?thm"}, "dev"))
    call = fake.calls[0]
    assert call["profile"] == "dev" and "MODEL_PROFILE" not in __import__("os").environ
    assert call["argv"][:2] == ["--config", "C5"] and call["argv"][call["argv"].index("--run-usd") + 1] == "0.20"
    assert isinstance(call["runner"], live._Shared)
    assert "Final proof accepted" in frames[-1][0] and "Solved" in frames[-1][1]
    assert json.loads((tmp_path / "runs" / "run1" / "demo_call.json").read_text(encoding="utf-8"))["argv"] == call["argv"]
    assert [p.name.split("_", 1)[1] for p in (tmp_path / "runs" / "demo_inputs").iterdir()] == ["my_thm.jsonl"]
    assert engine._run_lock.acquire(blocking=False)  # released after the run


def test_lock_outlives_a_closed_generator(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(live, "POLL_S", 0.01)
    gate = threading.Event()
    engine = _ready_engine(tmp_path, _FakeRunEval(tmp_path / "runs", gate))
    gen = engine.solve({"id": "p"}, "dev")
    next(gen)
    gen.close()  # the browser went away; the run thread is still waiting
    assert "Another problem" in list(engine.solve({"id": "q"}, "dev"))[0][1]
    gate.set()
    for _ in range(500):
        if engine._run_lock.acquire(blocking=False):
            break
        __import__("time").sleep(0.01)
    else:
        raise AssertionError("lock never released")


def test_custom_statement_is_checked_exactly_as_stored(tmp_path):
    from formal_mas.lean.runner import LeanResult
    seen = []

    class Runner:
        def verify(self, code, theorem=None):
            seen.append(code)
            return LeanResult(status="finished", backend="repl")

    engine = live.LiveEngine(root=tmp_path)
    engine.state, engine.runner = "ready", Runner()
    prob, err = engine.custom_problem("theorem t (x : ℕ) : x + 0 = x := by sorry")
    assert err == "" and prob["id"] == "t" and prob["statement"].startswith("import Mathlib")
    assert seen[0].startswith("import Mathlib") and seen[0].count("import Mathlib") == 1
    seen.clear()
    prob, _ = engine.custom_problem("import Mathlib\n-- note\ntheorem u : True := by sorry")
    assert prob["statement"].count("import Mathlib") == 1 and seen[0].count("import Mathlib") == 1
    assert engine.custom_problem("not lean")[0] is None


# -- translations ------------------------------------------------------------------------------

def test_every_key_is_translated_with_the_same_fields():
    import string
    es, en = i18n.TEXTS["es"], i18n.TEXTS["en"]
    assert set(es) == set(en), sorted(set(es) ^ set(en))
    for key in es:
        fields = [{f for _, f, _, _ in string.Formatter().parse(text) if f} for text in (es[key], en[key])]
        assert fields[0] == fields[1], key
        assert es[key].strip() and en[key].strip(), key


def test_every_literal_key_used_in_the_demo_exists():
    src = "\n".join((ROOT / "demo" / f).read_text(encoding="utf-8") for f in ("app.py", "steps.py", "catalog.py"))
    found = re.findall(r"""\bt\(\s*\w+\s*,\s*["']([a-z0-9_.]+)["']""", src)
    used = {k for k in found if not k.endswith(".")}  # "cmp." + id: covered by the dynamic-keys test
    assert used and not (used - set(i18n.TEXTS["es"])), sorted(used - set(i18n.TEXTS["es"]))


def test_dynamic_keys_exist_for_every_value():
    sys.path.insert(0, str(ROOT / "src"))
    from formal_mas.agents.contracts import STRATEGIES
    from formal_mas.orchestrator.policy import ERROR_CLASSES
    keys = set(i18n.TEXTS["es"])
    needed = ({f"strategy.{s}" for s in STRATEGIES} | {f"why.{c}" for c in ERROR_CLASSES}
              | {f"route.{r}" for r in ("FORMALIZE", "RETRIEVE", "GENERATE")}
              | {f"action.{a}" for a in ("retry", "replan")}
              | {f"step.{k}" for k in steps.FULL_STEPS + steps.BASELINE_B_STEPS}
              | {f"filter.{f}" for f in catalog.FILTERS} | {f"filter.hint.{f}" for f in catalog.FILTERS}
              | {f"difficulty.{d}" for d in (*catalog.DIFFICULTIES, "custom")}
              | {f"eta.{d}" for d in (*catalog.DIFFICULTIES, "custom")}
              | {f"area.{a}" for a in ("algebra", "number_theory", "combinatorics")}
              | {f"status.step.{s}" for s in live.LOAD_STEPS} | {f"speed.{k}" for k in app.SPEEDS}
              | {f"cfg.{c}" for c in ("C1", "C3", "C4", "C5")} | {f"cmp.H{i}" for i in (1, 2, 3)}
              | {f"cmp.H{i}.short" for i in (1, 2, 3)} | {"ex.side.C4", "ex.side.C5"})
    for key in replay.load_replays():
        needed |= {f"rep.{key}.title", f"rep.{key}.note"}
    assert not (needed - keys), sorted(needed - keys)


def test_steps_render_in_both_languages_without_raw_keys():
    for lang in i18n.LANGS:
        for key, rep in replay.load_replays().items():
            for cut in (1, len(rep["events"]) // 2, len(rep["events"])):
                res = rep["result"] if cut == len(rep["events"]) else None
                text = steps.steps_html(steps.build_steps(rep["events"][:cut], res, lang), lang)
                text += steps.result_html(res, rep["events"][:cut], lang, running=res is None)
                visible = re.sub(r"<details.*?</details>", "", text, flags=re.S)
                assert not re.search(r"\b(s|step|why|route|strategy|result|action)\.[a-z_]+\b", visible), (key, lang)


# -- plain-language steps ------------------------------------------------------------------------

def test_steps_of_a_solved_run():
    st = {s.key: s for s in steps.build_steps(EVENTS, REPLAY["result"], "es")}
    assert st["plan"].status == "done" and "un solo paso" in st["plan"].sentence
    assert st["search"].sentence.startswith("Encontró 16")
    assert "cálculo directo" in st["reason"].sentence and "2 de 3" in st["reason"].sentence
    assert st["verify"].status == "done" and st["verify"].sentence == "Lean aceptó la prueba completa."
    assert st["correct"].status == "done" and "reintentar" in st["correct"].sentence


def test_steps_while_running_mark_retry():
    cut = [e for e in EVENTS if e["event"] != "formalize_verified"][:9]
    st = {s.key: s for s in steps.build_steps(cut, None, "en")}
    assert st["verify"].status == "redo" and "not valid Lean code" in st["verify"].sentence
    assert st["correct"].status == "redo" and "rewrite the Lean code" in st["correct"].sentence


def test_details_hold_the_technical_view():
    html_ = steps.steps_html(steps.build_steps(EVENTS, REPLAY["result"], "es"), "es")
    assert "Ver detalles técnicos" in html_ and "expected end of input" in html_ and "κ = `syntax`" in html_


def test_baseline_b_steps():
    evs = [{"event": "problem_start", "config": "C2"}, {"event": "llm_call", "role": "baseline_b"},
           {"event": "self_assessment", "verdict": "correct", "confidence": 0.99}, {"event": "claim_check_start"},
           {"event": "llm_call", "role": "autoformalizer"},
           {"event": "formalize_failed", "reason": "error", "tactics": "x"},
           {"event": "claim_check_attempt", "attempt": 1, "reason": "lean:error", "error_class": "tactic_failure"},
           {"event": "claim_check_end", "status": "not_verified", "attempts": 1}]
    res = {"status": "claimed_solved", "check": {"status": "not_verified"}, "llm": {"llm_calls": 2}, "wall_s": 5}
    assert [s.key for s in steps.build_steps(evs, res, "es")] == ["solve_nl", "self_check", "translate", "verify"]
    card = steps.result_html(res, evs, "es")
    assert "Lean no lo confirmó" in card and "No confirmado no quiere decir incorrecto" in card


def test_result_card_reports_cache_reuse_and_failure():
    res = {"status": "failed", "llm": {"llm_calls": 5, "cached_calls": 5, "cost_usd": 0.0}, "wall_s": 2.3}
    card = steps.result_html(res, [], "es")
    assert "No se encontró una prueba dentro del presupuesto" in card
    assert "5 de 5 consultas se reutilizaron de una corrida anterior" in card and "2 s" in card
    ok = steps.result_html({**REPLAY["result"], "llm": {"llm_calls": 7, "cached_calls": 0, "cost_usd": 0.0031}}, [], "en")
    assert "Proof verified by Lean" in ok and "reused" not in ok and "See the proof in Lean" in ok


def test_result_card_stopped_run():
    card = steps.result_html(None, [], "en", error="RuntimeError('x')")
    assert "stopped" in card and "RuntimeError" in card


# -- catalog ----------------------------------------------------------------------------------

def test_competition_labels():
    assert catalog.competition("amc12a_2008_p25", "es") == "AMC 12A 2008 · Problema 25"
    assert catalog.competition("imo_1964_p1_2", "en") == "IMO 1964 · Problem 1"
    assert catalog.competition("aime_1991_p1", "en") == "AIME 1991 · Problem 1"
    assert catalog.competition("aimeII_2001_p3", "es") == "AIME II 2001 · Problema 3"
    assert catalog.competition("aimeI_2000_p7", "en") == "AIME I 2000 · Problem 7"
    assert catalog.competition("mathd_algebra_441", "es") == "Problema de práctica (MATH)"
    assert catalog.competition("induction_divisibility_3div2tooddnp1", "en") == "Practice exercise (miniF2F)"


def test_cards_hide_internal_names():
    p = {"id": "mathd_algebra_441", "split": "dev", "difficulty": "easy", "area": "algebra",
         "informal": "Show that $x$ is [[prime]].", "statement": "theorem mathd_algebra_441 : True := by"}
    card = catalog.card_markdown(p, "es")
    visible = card.split("<details")[0]
    assert "mathd_algebra_441" not in visible and "dev" not in visible and "split" not in visible
    assert "Fácil" in visible and "Álgebra" in visible and "unos segundos" in visible and "[[" not in visible
    assert "Ver enunciado en Lean" in card and "theorem mathd_algebra_441" in card


def test_new_filter_uses_demo_problems_never_run(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "eval.jsonl").write_text(json.dumps({"id": "s1", "difficulty": "easy"}) + "\n", encoding="utf-8")
    (tmp_path / "data" / "demo_problems.jsonl").write_text(
        "".join(json.dumps({"id": i, "difficulty": "hard", "split": "demo"}) + "\n" for i in ("d1", "d2")),
        encoding="utf-8")
    (tmp_path / "runs" / "r").mkdir(parents=True)
    (tmp_path / "runs" / "r" / "results.jsonl").write_text(json.dumps({"problem_id": "d2"}) + "\n", encoding="utf-8")
    probs = catalog.load_problems(tmp_path)
    assert [p["id"] for p in catalog.filter_problems(probs, "new")] == ["d1"]
    assert {p["id"] for p in catalog.filter_problems(probs, "hard")} == {"d1", "d2"}
    assert [p["id"] for p in catalog.filter_problems(probs, "easy")] == ["s1"]


def test_committed_demo_problems_are_new_and_outside_the_study():
    path = ROOT / "data" / "demo_problems.jsonl"
    demo = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    study = {json.loads(line)["id"] for line in (ROOT / "data" / "eval.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()}
    assert len(demo) == 6 and not ({p["id"] for p in demo} & study)
    assert {p["difficulty"] for p in demo} == {"easy", "medium", "hard"} and all(p["split"] == "demo" for p in demo)
    assert all(p["source_split"] == "valid" and p["source"].startswith("miniF2F") for p in demo)
    assert all(sum(p["difficulty"] == d for p in demo) == 2 for d in ("easy", "medium", "hard"))
    others = set()
    for name in ("dev_preview.jsonl", "smoke.jsonl"):
        f = ROOT / "data" / name
        if f.is_file():
            others |= {json.loads(line)["id"] for line in f.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert not ({p["id"] for p in demo} & others)


def test_project_spend_leaves_demo_runs_out():
    spec = importlib.util.spec_from_file_location("make_tables", ROOT / "docs" / "results" / "make_tables.py")
    mt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mt)
    assert mt.is_demo_run("20261109T190945Z_C5_experiment_demo") and mt.is_demo_run("20261109T190945Z_C5_dev_demo")
    assert not mt.is_demo_run("20261103T235556Z_C4_experiment") and not mt.is_demo_run("x_C5_dev_precheck")


def test_results_markdown_reads_numbers_in_plain_words():
    data = json.loads((replay.REPLAY_DIR / "results.json").read_text(encoding="utf-8"))
    es = catalog.results_markdown(data, "es")
    assert "24 de 30" in es and "Sistema completo sin Critic" in es and "Aporte del Critic" in es
    assert "la del Critic" in es and "C5" not in es and "H3" not in es
    en = catalog.results_markdown(data, "en")
    assert "24 of 30" in en and "What the Critic adds" in en
    assert catalog.results_markdown(None, "es") == i18n.t("es", "res.missing")


def test_cost_line():
    line = catalog.cost_line("es", "DeepSeek", {"easy": 0.0003, "medium": 0.018, "hard": 0.10}, 0.20)
    assert "menos de 1 centavo" in line and "≈ 2 centavos" in line and "≈ 10 centavos" in line and "0.20 USD" in line
    assert catalog.cost_line("es", "x", None, 0.2) == ""


# -- header status -------------------------------------------------------------------------------

def test_status_header_steps_and_ready(tmp_path):
    engine = live.LiveEngine(root=tmp_path)
    for i, state in enumerate(live.LOAD_STEPS, 1):
        engine.state = state
        assert f"paso {i} de 3" in app.status_html(engine, "es") and "fm-bar" in app.status_html(engine, "es")
    engine.state = "ready"
    assert "Listo" in app.status_html(engine, "es") and "Ready" in app.status_html(engine, "en")
    assert "recorded runs" in app.status_html(None, "en")


def test_steps_before_any_event_are_all_pending():
    for lang in i18n.LANGS:
        built = steps.build_steps([], None, lang)
        assert [s.status for s in built] == ["pending"] * len(built)


def test_every_frame_of_the_committed_replays_renders():
    """Single replays and side-by-side pairs (one side may still be empty), in both languages."""
    reps = replay.load_replays()
    for lang in i18n.LANGS:
        for rep in reps.values():
            for shown, result in replay.frames(rep, 0.0):
                app._view({"events": shown, "result": result, "error": None, "running": result is None}, lang)
        for a, b in replay.pairs(reps):
            for (ea, ra), (eb, rb) in replay.pair_frames(reps[a], reps[b], 0.0):
                for ev, res in ((ea, ra), (eb, rb)):
                    app._view({"events": ev, "result": res, "error": None, "running": res is None}, lang)


def test_bare_latex_answer_is_put_in_math_mode():
    assert catalog.clean_informal(r"Show that it is \frac{25}{8}.") == r"Show that it is $\frac{25}{8}$."
    assert catalog.clean_informal("Show that it is 23.") == "Show that it is 23."
    assert catalog.clean_informal(r"Show that it is $\sqrt 2$.") == r"Show that it is $\sqrt 2$."


def test_results_tables_are_contiguous_markdown_tables():
    data = json.loads((replay.REPLAY_DIR / "results.json").read_text(encoding="utf-8"))
    md = catalog.results_markdown(data, "es")
    blocks = md.split("\n\n")
    tables = [b for b in blocks if b.startswith("|")]
    assert len(tables) == 2 and all("\n|---|" in b for b in tables)
    assert all(len(b.splitlines()) >= 4 for b in tables)  # header, rule and rows in one block
