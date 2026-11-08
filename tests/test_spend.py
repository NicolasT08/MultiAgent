"""Offline tests of the spend control (llm/spend.py), its use in the client, the baselines and
scripts/run_eval.py (fake LLM, temporary ledger; data/spend_ledger.jsonl is never touched)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from fake_llm import MODELS_CFG, OFF_PEAK, PEAK, FakeClock, FakeTransport, pipeline_cfg, response
from formal_mas.baselines.baseline_a import run_baseline_a
from formal_mas.baselines.baseline_b import run_baseline_b
from formal_mas.llm.client import LLMClient, SpendLimitReached
from formal_mas.llm.pricing import call_cost, usage_from_response
from formal_mas.llm.spend import SpendGuard, worst_case_cost
from formal_mas.tracing import Budget, Tracer

ROOT = Path(__file__).resolve().parents[1]
MSGS = [{"role": "user", "content": "return json"}]
PRICING = MODELS_CFG["models"]["flash"]["pricing"]
SOL = json.dumps({"solution": "Subtract 3 and divide by 2.", "final_answer": "x = 2"})
OK = json.dumps({"verdict": "correct", "confidence": 0.9, "issues": []})
PROBLEM = {"id": "p", "informal": "x = 2", "statement": "theorem p (x : ℝ) (h₀ : 2 * x + 3 = 7) : x = 2 := by sorry"}
# Cost of one fake response (100 hit + 900 miss + 200 output tokens) off-peak.
ONE_CALL = (100 * 0.003 + 900 * 0.15 + 200 * 0.60) / 1e6


def make(tmp_path, *script, profile="experiment", ledger=None, total_usd=9.0, run_usd=1.0, spend=None, clock=None):
    clock = clock or FakeClock()
    transport = FakeTransport(*script)
    tracer = Tracer()
    pcfg = pipeline_cfg(total_usd=total_usd, run_usd=run_usd, ledger=ledger)
    client = LLMClient(profile, models_cfg=MODELS_CFG, pipeline_cfg=pcfg, tracer=tracer, transport=transport,
                       cache_dir=tmp_path / "cache", clock=clock, sleep=clock.sleep, monotonic=clock.monotonic,
                       spend=spend)
    return client, transport, tracer


def ledger_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_ledger(path: Path, *costs: float) -> None:
    path.write_text("".join(json.dumps({"run_id": "old", "role": "generator", "cost_usd": c}) + "\n"
                            for c in costs), encoding="utf-8")


# -- worst case ----------------------------------------------------------------


def test_worst_case_bounds_the_real_cost():
    request = {"messages": [{"role": "user", "content": "x" * 3000}], "max_tokens": 1000}
    raw = response("ok", hit=0, miss=3000, completion=1000)  # 1 token per byte, all of max_tokens
    for when in (OFF_PEAK, PEAK):
        actual, _ = call_cost(usage_from_response(raw), PRICING, when)
        assert worst_case_cost(request, PRICING, 65536) >= actual
    # always the most expensive tariff (peak), whatever the hour
    assert worst_case_cost(request, PRICING, 65536) == pytest.approx((3000 + 4 * 16) * 0.30e-6 + 1000 * 1.20e-6,
                                                                     rel=0.01)
    assert worst_case_cost(request, {"free": True}, 65536) == 0.0
    # without max_tokens the configured default is assumed
    assert worst_case_cost({"messages": request["messages"]}, PRICING, 65536) > worst_case_cost(request, PRICING, 1)
    # multi-byte characters count by UTF-8 bytes
    wide = {"messages": [{"role": "user", "content": "ℝ" * 100}], "max_tokens": 0}
    narrow = {"messages": [{"role": "user", "content": "R" * 100}], "max_tokens": 0}
    assert worst_case_cost(wide, PRICING, 1) > worst_case_cost(narrow, PRICING, 1)


# -- ledger ----------------------------------------------------------------------


def test_paid_calls_are_appended_to_the_ledger(tmp_path):
    ledger = tmp_path / "spend_ledger.jsonl"
    guard = SpendGuard(9.0, 1.0, ledger, run_id="r1")
    client, transport, tracer = make(tmp_path, '{"ok": 1}', spend=guard)
    with tracer.context(problem_id="p1"):
        resp = client.complete("generator", MSGS, Budget(16), json_output=True)
        client.complete("generator", MSGS, Budget(16), json_output=True)  # cache hit: free, not recorded
    rows = ledger_rows(ledger)
    assert len(rows) == 1 and len(transport.calls) == 1
    r = rows[0]
    assert (r["run_id"], r["problem_id"], r["role"], r["model"], r["tariff"]) == ("r1", "p1", "generator", "flash",
                                                                               "off_peak")
    assert r["cost_usd"] == pytest.approx(resp.cost_usd) == pytest.approx(ONE_CALL)
    assert r["ts"].startswith("2026-10-28T12:00") and r["model_returned"] == "DeepSeek-V4.1-Flash-1017"
    assert guard.run_spent == pytest.approx(ONE_CALL) and guard.run_calls == 1
    # a later run (new guard) sees the project total
    info = SpendGuard(9.0, 1.0, ledger, run_id="r2").info()
    assert info["spent_before_run_usd"] == pytest.approx(ONE_CALL, abs=1e-6)
    assert info["total_remaining_usd"] == pytest.approx(9.0 - ONE_CALL, abs=1e-6) and info["run_spent_usd"] == 0


def test_free_models_are_never_checked_nor_recorded(tmp_path):
    guard = SpendGuard(None, None, None)  # no caps at all: paid calls would be refused
    client, transport, _ = make(tmp_path, '{"ok": 1}', profile="dev", spend=guard)
    client.complete("generator", MSGS, Budget(16))
    assert len(transport.calls) == 1 and guard.memory == [] and guard.stopped is None


# -- caps ------------------------------------------------------------------------


def test_project_cap_refuses_before_sending(tmp_path):
    ledger = tmp_path / "spend_ledger.jsonl"
    write_ledger(ledger, 5.0, 3.9995)  # $0.0005 left < worst case of one call (~$0.0012)
    client, transport, tracer = make(tmp_path, '{"ok": 1}', ledger=str(ledger))
    with pytest.raises(SpendLimitReached, match="project cap"):
        client.complete("generator", MSGS, Budget(16))
    assert transport.calls == [] and len(ledger_rows(ledger)) == 2
    ev = [e for e in tracer.events if e["event"] == "llm_spend_stop"]
    assert len(ev) == 1 and ev[0]["worst_case_usd"] > 0.0005
    # stopped for good: even a call that would fit is refused
    client.spend.total_usd = 100.0
    with pytest.raises(SpendLimitReached):
        client.complete("generator", MSGS, Budget(16))
    assert transport.calls == []


def test_run_cap_counts_only_this_run(tmp_path):
    ledger = tmp_path / "spend_ledger.jsonl"
    write_ledger(ledger, 4.0)  # earlier runs do not count for run_usd
    # worst case (peak) ≈ $0.00121; after one call (≈ $0.000255) the next could reach ≈ $0.00147
    client, transport, _ = make(tmp_path, '{"a": 1}', '{"b": 2}', ledger=str(ledger), run_usd=0.0013)
    client.complete("generator", MSGS, Budget(16), sample=0)
    with pytest.raises(SpendLimitReached, match="run cap"):
        client.complete("generator", MSGS, Budget(16), sample=1)
    assert len(transport.calls) == 1 and len(ledger_rows(ledger)) == 2
    assert client.spend.run_spent <= 0.0013


def test_billed_by_the_attempt_that_answered(tmp_path):
    """The call is looked up off-peak but its retry goes out at peak: peak is billed and recorded."""
    from fake_llm import http_error

    times = iter([OFF_PEAK, OFF_PEAK, PEAK])  # lookup, first attempt (503), retry

    class Clock(FakeClock):
        def __call__(self):
            return next(times, PEAK)

    client, transport, _ = make(tmp_path, http_error(503), '{"a": 1}', clock=Clock())
    resp = client.complete("generator", MSGS, Budget(16))
    assert len(transport.calls) == 2 and resp.tariff == "peak"
    assert client.spend.memory[0]["tariff"] == "peak" and client.spend.memory[0]["ts"].startswith("2026-10-28T02:00")
    assert client.spend.run_spent == pytest.approx(2 * ONE_CALL)


def test_fails_closed_without_caps_or_with_a_broken_ledger(tmp_path):
    cfg = pipeline_cfg()
    del cfg["spend"]
    client = LLMClient("experiment", models_cfg=MODELS_CFG, pipeline_cfg=cfg, tracer=Tracer(),
                       transport=FakeTransport('{"a": 1}'), cache_dir=tmp_path / "cache", clock=FakeClock())
    with pytest.raises(SpendLimitReached, match="no spend caps"):
        client.complete("generator", MSGS, Budget(16))
    ledger = tmp_path / "spend_ledger.jsonl"
    guard = SpendGuard(9.0, 1.0, ledger)
    ledger.write_text('{"cost_usd": 1}\nnot json\n', encoding="utf-8")
    client, transport, _ = make(tmp_path / "b", '{"a": 1}', spend=guard)
    with pytest.raises(SpendLimitReached, match="not a ledger record"):
        client.complete("generator", MSGS, Budget(16))
    assert transport.calls == []


def test_ledger_problems_stop_the_guard(tmp_path):
    for bad in ('{"cost_usd": "abc"}\n', '[1, 2]\n', '{"role": "x"}\n'):
        ledger = tmp_path / "l.jsonl"
        ledger.write_text(bad, encoding="utf-8")
        guard = SpendGuard(9.0, 1.0, ledger)
        assert guard.stopped and "not a ledger record" in guard.stopped
        with pytest.raises(SpendLimitReached):
            guard.allow(0.0)
    # a spend section without the `ledger` key (e.g. a typo) has no caps: paid calls refused
    guard = SpendGuard.from_config({"spend": {"total_usd": 9.0, "run_usd": 1.0, "legder": "x"}})
    assert not guard.configured
    with pytest.raises(SpendLimitReached, match="no spend caps"):
        guard.allow(0.0)


def test_failed_ledger_write_stops_after_caching(tmp_path):
    (tmp_path / "a_file").write_text("", encoding="utf-8")
    ledger = tmp_path / "a_file" / "spend_ledger.jsonl"  # its folder cannot be created
    guard = SpendGuard(9.0, 1.0, ledger)
    assert guard.stopped is None
    client, transport, tracer = make(tmp_path, '{"a": 1}', spend=guard)
    with pytest.raises(SpendLimitReached, match="spent but not recorded"):
        client.complete("generator", MSGS, Budget(16))
    assert guard.stopped and guard.run_spent == pytest.approx(ONE_CALL)
    # the answer was cached before the failed write: a rerun does not pay for it again
    client2, transport2, _ = make(tmp_path, '{"a": 1}', spend=SpendGuard(9.0, 1.0, None))
    assert client2.complete("generator", MSGS, Budget(16)).cached and transport2.calls == []


def test_cost_above_the_worst_case_stops_the_guard(tmp_path):
    # the provider bills more output than max_tokens (1000): the estimate is wrong → stop
    client, _, _ = make(tmp_path, response('{"a": 1}', completion=5000))
    with pytest.raises(SpendLimitReached, match="above its checked worst case"):
        client.complete("generator", MSGS, Budget(16))
    assert len(client.spend.memory) == 1  # still counted
    with pytest.raises(SpendLimitReached):
        client.complete("generator", MSGS, Budget(16), sample=1)


def test_real_pipeline_yaml_has_the_caps():
    from formal_mas.config import load_pipeline

    spend = load_pipeline()["spend"]
    assert spend["total_usd"] == 12.5 and spend["run_usd"] == 5.0
    assert spend["ledger"] == "data/spend_ledger.jsonl"
    assert "data/spend_ledger.jsonl" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    guard = SpendGuard.from_config({"spend": spend}, root=Path("/x"))
    assert guard.ledger == Path("/x/data/spend_ledger.jsonl")


# -- baselines -------------------------------------------------------------------


def test_baselines_report_budget_stop(tmp_path):
    client, transport, tracer = make(tmp_path, SOL, OK, run_usd=0.0001)
    res = run_baseline_b(PROBLEM, client, Budget(16), tracer)
    assert res["status"] == "budget_stop" and not res["claimed_solved"] and "run cap" in res["detail"]
    assert transport.calls == []

    class NoLean:
        def verify(self, *a, **k):  # pragma: no cover — must not be reached
            raise AssertionError("Lean called after a budget stop")

    client, transport, tracer = make(tmp_path / "a", json.dumps({"tactics": "linarith"}), run_usd=0.0001)
    res = run_baseline_a(PROBLEM, client, NoLean(), Budget(16), tracer)
    assert res["status"] == "budget_stop" and res["solved"] is False and res["attempts"] == []


# -- run_eval.py -----------------------------------------------------------------


def _run_eval_module():
    spec = importlib.util.spec_from_file_location("run_eval_under_test", ROOT / "scripts" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _problems(path: Path, n: int) -> Path:
    """n problems whose prompts all differ (no LLM cache hits between them)."""
    rows = [{**PROBLEM, "id": f"p{i}", "split": "dev", "informal": f"{PROBLEM['informal']} ({i})",
             "statement": PROBLEM["statement"].replace("theorem p ", f"theorem p{i} ")} for i in range(n)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _solve_or_assess(request: dict) -> str:
    return OK if "grader" in request["messages"][0]["content"] else SOL


def _eval(tmp_path, monkeypatch, capsys, ledger: Path, *extra: str, run_usd: float = 1.0):
    monkeypatch.delenv("MODEL_PROFILE", raising=False)  # fake default_profile: experiment (paid)
    mod = _run_eval_module()
    clock = FakeClock()
    tmp_path.mkdir(parents=True, exist_ok=True)
    data = _problems(tmp_path / "problems.jsonl", 3)
    rc = mod.main(["--config", "C2", "--data", str(data), *extra], models_cfg=MODELS_CFG,
                  pipeline_cfg={**pipeline_cfg(ledger=str(ledger), run_usd=run_usd), "baselines": {}},
                  client_kwargs={"transport": FakeTransport(_solve_or_assess), "cache_dir": tmp_path / "cache",
                                 "clock": clock, "sleep": clock.sleep, "monotonic": clock.monotonic},
                  runs_dir=tmp_path / "runs")
    out = capsys.readouterr().out.splitlines()
    run_dir = next((tmp_path / "runs").iterdir())
    cfg = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    results = [json.loads(x) for x in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    return rc, out, cfg, results


def test_run_eval_prints_spend_and_records_the_run(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "spend_ledger.jsonl"
    write_ledger(ledger, 1.5)
    rc, out, cfg, results = _eval(tmp_path, monkeypatch, capsys, ledger)
    assert rc == 0 and len(results) == 3 and cfg["status"] == "finished"
    assert out[0] == "spend: project $1.5000 of $9.00 ($7.5000 left); run cap $1.00"
    run_cost = 6 * ONE_CALL  # 3 problems × (solve + assess)
    assert out[1] == f"run cost: ${run_cost:.6f} in 6 paid calls"
    assert cfg["spend"]["run_spent_usd"] == pytest.approx(run_cost, abs=1e-6)
    assert cfg["spend"]["total_spent_usd"] == pytest.approx(1.5 + run_cost, abs=1e-6)
    rows = ledger_rows(ledger)
    assert len(rows) == 7 and {r["run_id"] for r in rows[1:]} == {cfg["run_id"]}
    assert [r["problem_id"] for r in rows[1:]] == ["p0", "p0", "p1", "p1", "p2", "p2"]


def test_run_eval_stops_cleanly_at_the_cap(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "spend_ledger.jsonl"
    # worst case ≈ $0.00139 per call with these prompts, actual ≈ $0.000255: p0 (2 calls) and
    # p1's solve fit (0.00051 + 0.00139 ≤ 0.002), p1's assess does not (0.00077 + 0.00138)
    rc, out, cfg, results = _eval(tmp_path, monkeypatch, capsys, ledger, run_usd=0.002)
    assert rc == 0 and cfg["status"] == "budget_stop"
    assert [r["status"] for r in results] == ["claimed_solved", "budget_stop"]  # p2 is never run
    assert "BUDGET STOP" in out[1] and "run cap" in cfg["spend"]["stopped"]
    rows = ledger_rows(ledger)
    assert cfg["spend"]["run_spent_usd"] <= 0.002 and len(rows) == cfg["spend"]["run_paid_calls"] >= 2
    assert {r["problem_id"] for r in rows} <= {"p0", "p1"}


def test_run_eval_run_usd_flag_only_lowers_the_cap(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "spend_ledger.jsonl"
    _, out, cfg, _ = _eval(tmp_path, monkeypatch, capsys, ledger, "--run-usd", "5.0")
    assert cfg["spend"]["run_usd_cap"] == 1.0 and out[0].endswith("run cap $1.00")
    _, out, cfg, results = _eval(tmp_path / "b", monkeypatch, capsys, tmp_path / "l2.jsonl", "--run-usd", "0.0001")
    assert cfg["spend"]["run_usd_cap"] == 0.0001 and results[0]["status"] == "budget_stop" and len(results) == 1


def test_broken_pricing_is_a_budget_stop(tmp_path):
    import copy

    cfg = copy.deepcopy(MODELS_CFG)
    cfg["models"]["flash"]["pricing"] = {"peak_windows_utc": []}  # paid, but no prices
    clock = FakeClock()
    client = LLMClient("experiment", models_cfg=cfg, pipeline_cfg=pipeline_cfg(), tracer=Tracer(),
                       transport=FakeTransport('{"a": 1}'), cache_dir=tmp_path / "cache", clock=clock)
    with pytest.raises(SpendLimitReached, match="no usable"):
        client.complete("generator", MSGS, Budget(16))
    assert client.spend.stopped and client.transport.calls == []


def test_run_eval_with_an_unreadable_ledger_refuses_and_finishes(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "spend_ledger.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    rc, out, cfg, results = _eval(tmp_path, monkeypatch, capsys, ledger)
    assert rc == 0 and cfg["status"] == "budget_stop" and [r["status"] for r in results] == ["budget_stop"]
    assert "not a ledger record" in cfg["spend"]["stopped"] and cfg["spend"]["total_spent_usd"] is None
