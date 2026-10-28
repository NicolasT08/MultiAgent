"""Offline tests of llm/specs.py, llm/pricing.py, llm/client.py and tracing.py (fake transport)."""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from fake_llm import MODELS_CFG, OFF_PEAK, PEAK, FakeClock, FakeTransport, http_error, pipeline_cfg, response
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, TransportError, extract_json
from formal_mas.llm.pricing import Usage, call_cost, in_peak, tariff, usage_from_response
from formal_mas.llm.specs import ConfigError, resolve_profile
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

MSGS = [{"role": "user", "content": "return json"}]


class Answer(BaseModel):
    answer: int


def make_client(tmp_path, *script, profile="experiment", cfg=None, clock=None, **pcfg):
    clock = clock or FakeClock()
    transport = FakeTransport(*script)
    tracer = Tracer()
    client = LLMClient(profile, models_cfg=cfg or MODELS_CFG, pipeline_cfg=pipeline_cfg(**pcfg), tracer=tracer,
                       transport=transport, cache_dir=tmp_path / "cache", clock=clock, sleep=clock.sleep,
                       monotonic=clock.monotonic)
    return client, transport, tracer, clock


def events(tracer, name):
    return [e for e in tracer.events if e["event"] == name]


# -- specs -------------------------------------------------------------------


def test_real_models_yaml_resolves_both_profiles():
    for profile in ("dev", "experiment"):
        roles = resolve_profile(profile=profile)
        assert {"planner", "generator", "autoformalizer", "critic", "baseline_a", "baseline_b"} <= set(roles)
        # baselines share model and parameters with the agent they are compared with
        assert roles["baseline_a"].describe() == roles["autoformalizer"].describe()
        assert roles["baseline_a"].params == roles["autoformalizer"].params
        assert roles["baseline_b"].params == roles["generator"].params
        assert roles["baseline_b"].model == roles["generator"].model
    exp = resolve_profile(profile="experiment")
    assert all(len(s.models) == 1 for s in exp.values())  # no fallbacks in experiment
    assert all(s.model.provider == "deepseek" for s in exp.values())
    assert exp["critic"].thinking is False and exp["generator"].thinking is True
    for role in ("planner", "generator", "autoformalizer"):
        assert "temperature" not in exp[role].params


def _cfg_with(path, value):
    cfg = copy.deepcopy(MODELS_CFG)
    node = cfg
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    return cfg


def test_thinking_role_cannot_set_ignored_parameter():
    cfg = _cfg_with(["profiles", "experiment", "roles", "generator", "temperature"], 0.7)
    with pytest.raises(ConfigError, match="ignored"):
        resolve_profile(cfg, "experiment")


def test_experiment_rejects_fallbacks_and_latest_aliases():
    cfg = _cfg_with(["profiles", "experiment", "roles", "generator", "fallbacks"], ["free-a"])
    with pytest.raises(ConfigError, match="fallbacks are not allowed"):
        resolve_profile(cfg, "experiment")
    cfg = _cfg_with(["models", "flash", "id"], "deepseek-flash-latest")
    with pytest.raises(ConfigError, match="alias"):
        resolve_profile(cfg, "experiment")


def test_same_as_cycle_and_unsupported_thinking_mode():
    cfg = _cfg_with(["profiles", "dev", "roles", "generator"], {"same_as": "baseline_a"})
    with pytest.raises(ConfigError, match="cycle"):
        resolve_profile(cfg, "dev")
    cfg = _cfg_with(["profiles", "dev", "roles", "generator", "thinking"], False)
    with pytest.raises(ConfigError, match="cannot set thinking disabled"):
        resolve_profile(cfg, "dev")


def test_request_carries_thinking_toggle_and_json_mode(tmp_path):
    client, transport, *_ = make_client(tmp_path, '{"answer": 1}')
    client.complete_json("generator", MSGS, Answer, Budget(5))
    client.complete("critic", MSGS, Budget(5))
    (_, gen), (_, critic) = transport.calls
    assert gen["extra_body"] == {"thinking": {"type": "enabled"}} and gen["reasoning_effort"] == "low"
    assert gen["response_format"] == {"type": "json_object"} and "temperature" not in gen
    assert critic["extra_body"] == {"thinking": {"type": "disabled"}} and critic["temperature"] == 0.0
    assert "response_format" not in critic


# -- pricing -----------------------------------------------------------------


PRICING = MODELS_CFG["models"]["flash"]["pricing"]


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 10, 26, 0, 59, tzinfo=timezone.utc), "off_peak"),   # Monday
    (datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc), "peak"),
    (datetime(2026, 10, 26, 3, 59, tzinfo=timezone.utc), "peak"),
    (datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc), "off_peak"),
    (datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc), "peak"),
    (datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc), "off_peak"),
    (datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc), "off_peak"),    # Saturday
    (datetime(2026, 10, 26, 21, 0, tzinfo=timezone(timedelta(hours=-5))), "peak"),  # Bogotá 21:00 = Tue 02:00 UTC
])
def test_tariff_windows(when, expected):
    assert tariff(PRICING, when) == expected


def test_call_cost_and_free_models():
    u = Usage(prompt_tokens=3000, cache_hit_tokens=1000, cache_miss_tokens=2000, completion_tokens=500)
    assert call_cost(u, PRICING, PEAK) == (pytest.approx((1000 * 0.006 + 2000 * 0.30 + 500 * 1.20) / 1e6), "peak")
    assert call_cost(u, PRICING, OFF_PEAK) == (pytest.approx((1000 * 0.003 + 2000 * 0.15 + 500 * 0.60) / 1e6), "off_peak")
    assert call_cost(u, {"free": True}, PEAK) == (0.0, "free")
    assert call_cost(u, {"peak": PRICING["peak"]}, OFF_PEAK)[1] == "peak"  # no off_peak → always peak
    assert in_peak([{"key": "flash", "pricing": PRICING}, {"key": "f", "pricing": {"free": True}}], PEAK) == ["flash"]


def test_usage_normalisation():
    u = usage_from_response(response("x", hit=10, miss=90, completion=7, reasoning=3))
    assert (u.prompt_tokens, u.cache_hit_tokens, u.cache_miss_tokens, u.completion_tokens, u.reasoning_tokens) == \
        (100, 10, 90, 7, 3)
    u = usage_from_response({"usage": {"prompt_tokens": 50, "completion_tokens": 5,
                                       "prompt_tokens_details": {"cached_tokens": 20}}})
    assert (u.cache_hit_tokens, u.cache_miss_tokens) == (20, 30)
    assert usage_from_response({}) == Usage()


# -- budget ------------------------------------------------------------------


def test_budget_is_never_exceeded(tmp_path):
    client, transport, tracer, _ = make_client(tmp_path, lambda req: response(f"{len(transport.calls)}"))
    budget = Budget(3)
    for i in range(3):
        client.complete("generator", MSGS, budget, sample=i)
    with pytest.raises(BudgetExhausted):
        client.complete("generator", MSGS, budget, sample=99)
    assert budget.used == 3 and len(transport.calls) == 3
    assert events(tracer, "llm_budget_exhausted")


def test_cache_hits_count_for_budget(tmp_path):
    client, transport, *_ = make_client(tmp_path, "same")
    budget = Budget(2)
    client.complete("generator", MSGS, budget)
    client.complete("generator", MSGS, budget)  # cache hit
    assert len(transport.calls) == 1
    with pytest.raises(BudgetExhausted):
        client.complete("generator", MSGS, budget)


def test_json_repair_counts_for_budget(tmp_path):
    client, transport, *_ = make_client(tmp_path, "not json at all", '{"answer": 3}')
    with pytest.raises(BudgetExhausted):
        client.complete_json("generator", MSGS, Answer, Budget(1))
    assert len(transport.calls) == 1
    budget = Budget(2)
    client2, transport2, tracer2, _ = make_client(tmp_path / "b", "not json at all", '{"answer": 3}')
    obj, resps = client2.complete_json("generator", MSGS, Answer, budget)
    assert obj.answer == 3 and len(resps) == 2 and budget.used == 2
    assert [r.call_index for r in resps] == [1, 2]
    assert events(tracer2, "llm_json_repaired")
    repair_prompt = transport2.calls[1][1]["messages"][-1]["content"]
    assert "not json at all" in repair_prompt and '"answer"' in repair_prompt


# -- cache -------------------------------------------------------------------


def test_cache_key_includes_sample_messages_and_parameters(tmp_path):
    client, transport, tracer, _ = make_client(tmp_path, lambda req: response(f"r{len(transport.calls)}"))
    b = Budget(10)
    r1 = client.complete("generator", MSGS, b, sample=0)
    r2 = client.complete("generator", MSGS, b, sample=0)
    r3 = client.complete("generator", MSGS, b, sample=1)
    r4 = client.complete("generator", [{"role": "user", "content": "other json"}], b, sample=0)
    r5 = client.complete("critic", MSGS, b, sample=0)  # other parameters
    assert len(transport.calls) == 4
    assert r2.cached and r2.content == r1.content and r2.cost_usd == 0 and r2.cost_usd_nominal == r1.cost_usd > 0
    assert not r3.cached and r3.content != r1.content  # N self-consistency samples differ
    assert not r4.cached and not r5.cached
    assert [e["cached"] for e in events(tracer, "llm_call")] == [False, True, False, False, False]


def test_cache_persists_on_disk_and_can_be_off(tmp_path):
    client, transport, *_ = make_client(tmp_path, "persisted")
    client.complete("generator", MSGS, Budget(1))
    again, transport2, *_ = make_client(tmp_path, "new")
    assert again.complete("generator", MSGS, Budget(1)).content == "persisted" and not transport2.calls
    off, transport3, *_ = make_client(tmp_path, "new", cache=False)
    assert off.complete("generator", MSGS, Budget(1)).content == "new" and len(transport3.calls) == 1


# -- tolerant JSON -------------------------------------------------------------


@pytest.mark.parametrize("text", [
    '{"answer": 1}',
    '```json\n{"answer": 1}\n```',
    'Sure! Here is the result:\n{"answer": 1}\nHope it helps.',
    '{"answer": 1,}',
    'prefix {not json} then {"answer": 1, "note": "a } inside"}',
])
def test_extract_json_tolerant(text):
    assert extract_json(text)["answer"] == 1


@pytest.mark.parametrize("text", ["", "   ", "no braces", "[1, 2]", "{broken"])
def test_extract_json_rejects(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_invalid_after_repair_and_repair_off(tmp_path):
    client, *_ = make_client(tmp_path, "nope", "still nope")
    with pytest.raises(LLMOutputError) as exc:
        client.complete_json("generator", MSGS, Answer, Budget(5))
    assert len(exc.value.responses) == 2
    client, transport, *_ = make_client(tmp_path / "off", '{"answer": "x"}', json_repair=False)
    with pytest.raises(LLMOutputError):
        client.complete_json("generator", MSGS, Answer, Budget(5))
    assert len(transport.calls) == 1


# -- retries, fallbacks, provider errors --------------------------------------


def test_retries_with_backoff(tmp_path):
    client, transport, tracer, clock = make_client(tmp_path, http_error(503), http_error(429), "ok")
    budget = Budget(1)
    assert client.complete("generator", MSGS, budget).content == "ok"
    assert len(transport.calls) == 3 and budget.used == 1  # retries are one logical call
    assert clock.sleeps == [2.0, 4.0]
    assert [e["attempt"] for e in events(tracer, "llm_retry")] == [1, 2]


def test_provider_error_without_fallback(tmp_path):
    client, transport, tracer, _ = make_client(tmp_path, http_error(402))
    with pytest.raises(ProviderError):
        client.complete("generator", MSGS, Budget(1))
    assert len(transport.calls) == 1  # 402 (no balance) is not retried
    client, transport, *_ = make_client(tmp_path / "b", http_error(500))
    with pytest.raises(ProviderError):
        client.complete("generator", MSGS, Budget(1))
    assert len(transport.calls) == 3  # retry.attempts
    client, transport, *_ = make_client(tmp_path / "c", TransportError("conn reset", retryable=True))
    with pytest.raises(ProviderError):
        client.complete("generator", MSGS, Budget(1))


def test_dev_fallback_model(tmp_path):
    def by_model(req):
        return http_error(401) if req["model"] == "free-a" else response("from b", model="free-b")
    client, transport, tracer, _ = make_client(tmp_path, by_model, profile="dev")
    resp = client.complete("generator", MSGS, Budget(1))
    assert resp.model_key == "free-b" and resp.cost_usd == 0 and resp.tariff == "free"
    assert [k for k, _ in transport.calls] == ["free-a", "free-b"]
    assert events(tracer, "llm_model_failed")[0]["model"] == "free-a"


def test_json_mode_auto_disabled_on_400(tmp_path):
    def reject_format(req):
        return http_error(400) if "response_format" in req else response('{"answer": 2}')
    client, transport, tracer, _ = make_client(tmp_path, reject_format)
    obj, _ = client.complete_json("generator", MSGS, Answer, Budget(2))
    assert obj.answer == 2 and "flash" in client.json_disabled
    client.complete_json("generator", MSGS, Answer, Budget(2), sample=1)
    assert "response_format" not in transport.calls[-1][1]
    assert events(tracer, "llm_json_mode_disabled")


# -- trace, cost, versions -----------------------------------------------------


def test_llm_call_event_fields_and_peak_cost(tmp_path):
    client, _, tracer, _ = make_client(tmp_path, response("hi", hit=1000, miss=2000, completion=500), clock=FakeClock(PEAK))
    budget = Budget(1)
    resp = client.complete("generator", MSGS, budget)
    (ev,) = events(tracer, "llm_call")
    for key in ("role", "model_requested", "model_returned", "system_fingerprint", "prompt_tokens", "cache_hit_tokens",
                "cache_miss_tokens", "completion_tokens", "reasoning_tokens", "cost_usd", "tariff", "latency_s",
                "started", "ts", "cached", "finish_reason"):
        assert key in ev, key
    assert ev["tariff"] == "peak" and ev["model_returned"] == "DeepSeek-V4.1-Flash-1017"
    assert ev["cost_usd"] == pytest.approx((1000 * 0.006 + 2000 * 0.30 + 500 * 1.20) / 1e6) == resp.cost_usd
    assert ev["started"] == "2026-10-28T02:00:00.000Z"
    s = budget.summary()
    assert s["tokens"]["cache_hit"] == 1000 and s["cost_usd"] == pytest.approx(resp.cost_usd)


def test_version_mismatch_warns_once(tmp_path, caplog):
    client, _, tracer, _ = make_client(tmp_path, lambda req: response(req["messages"][0]["content"],
                                                                       model="DeepSeek-V5-Flash"))
    with caplog.at_level(logging.WARNING, logger="formal_mas.llm.client"):
        for i in range(3):
            client.complete("generator", [{"role": "user", "content": f"q{i}"}], Budget(1))
    assert len(events(tracer, "llm_version_mismatch")) == 1
    assert "expected version" in caplog.text
    assert client.info()["version_mismatches"][0]["returned"] == "DeepSeek-V5-Flash"


def test_model_change_mid_run_and_observed_info(tmp_path):
    names = iter(["DeepSeek-V4.1-Flash-1017", "DeepSeek-V4.2-Flash"])
    client, _, tracer, _ = make_client(tmp_path, lambda req: response(req["messages"][0]["content"], model=next(names)))
    client.complete("generator", [{"role": "user", "content": "a"}], Budget(1))
    client.complete("generator", [{"role": "user", "content": "b"}], Budget(1))
    assert not events(tracer, "llm_version_mismatch")
    assert events(tracer, "llm_model_changed")
    info = client.info()
    assert info["observed"]["flash"]["returned"] == ["DeepSeek-V4.1-Flash-1017", "DeepSeek-V4.2-Flash"]
    assert info["observed"]["flash"]["fingerprints"] == ["fp_1"]


def test_length_finish_is_counted(tmp_path):
    client, _, tracer, _ = make_client(tmp_path, response("cut", finish="length"))
    budget = Budget(1)
    client.complete("generator", MSGS, budget)
    assert budget.summary()["length_finishes"] == 1 and events(tracer, "llm_length_finish")


def test_missing_api_key_is_a_provider_error(tmp_path, monkeypatch):
    from formal_mas.llm.client import OpenAITransport
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    transport = OpenAITransport.__new__(OpenAITransport)
    transport._clients = {}
    client = LLMClient("experiment", models_cfg=MODELS_CFG, pipeline_cfg=pipeline_cfg(), tracer=Tracer(),
                       transport=transport, cache_dir=tmp_path)
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        client.complete("generator", MSGS, Budget(1))


# -- tracer --------------------------------------------------------------------


def test_tracer_file_context_and_logging_bridge(tmp_path):
    path = tmp_path / "trace.jsonl"
    with Tracer(path) as tracer:
        tracer.attach_logging()
        with tracer.context(problem_id="p1"):
            tracer.event("hello", x=1)
            logging.getLogger("formal_mas.lean.runner").info(json.dumps({"event": "lean_verify", "status": "ok"}))
            logging.getLogger("formal_mas.lean.runner").info("plain text is ignored")
        tracer.event("bye")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["hello", "lean_verify", "bye"]
    assert rows[0]["problem_id"] == rows[1]["problem_id"] == "p1" and "problem_id" not in rows[2]
    assert all("ts" in r for r in rows)


def test_cache_hit_across_runs_after_json_mode_was_disabled(tmp_path):
    def reject_format(req):
        return http_error(400) if "response_format" in req else response('{"answer": 5}')
    client, *_ = make_client(tmp_path, reject_format)
    client.complete_json("generator", MSGS, Answer, Budget(1))
    later, transport, *_ = make_client(tmp_path, reject_format)  # new run: json_disabled is empty
    obj, (resp,) = later.complete_json("generator", MSGS, Answer, Budget(1))
    assert obj.answer == 5 and resp.cached and not transport.calls


def test_version_mismatch_checked_on_cached_responses(tmp_path):
    client, *_ = make_client(tmp_path, response("old", model="DeepSeek-V4-Flash"))
    client.complete("generator", MSGS, Budget(1))
    later, transport, tracer, _ = make_client(tmp_path, "unused")
    assert later.complete("generator", MSGS, Budget(1)).cached and not transport.calls
    (ev,) = events(tracer, "llm_version_mismatch")
    assert ev["returned"] == "DeepSeek-V4-Flash" and ev["cached"] is True


def test_sdk_errors_become_transport_errors(tmp_path, monkeypatch):
    import openai

    from formal_mas.llm.client import OpenAITransport

    class Boom:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    raise openai.OpenAIError("unparsable body")

    transport = OpenAITransport.__new__(OpenAITransport)
    transport._clients = {}
    monkeypatch.setattr(transport, "_client", lambda model: Boom)
    client = LLMClient("experiment", models_cfg=MODELS_CFG, pipeline_cfg=pipeline_cfg(), tracer=Tracer(),
                       transport=transport, cache_dir=tmp_path)
    with pytest.raises(ProviderError, match="unparsable body"):
        client.complete("generator", MSGS, Budget(1))
