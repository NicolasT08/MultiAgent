"""The only way the system calls an LLM (CLAUDE.md §7): one OpenAI-compatible client.

    client = LLMClient(tracer=tracer)               # profile from MODEL_PROFILE (models.yaml)
    budget = Budget(b_max)                          # one per problem (tracing.py)
    resp = client.complete("generator", messages, budget, sample=i)
    obj, resps = client.complete_json("baseline_a", messages, TacticProof, budget)

Per logical call:
    budget       `budget.reserve()` first: never more than b_max calls per problem (cache
                 hits and the JSON repair call count; transport retries do not).
    cache        on disk (pipeline.yaml → llm.cache_dir). Key = provider URL, model id, the
                 full request (messages and parameters) and the sample index, so the N
                 self-consistency samples are N distinct entries. Hits cost 0 now; their
                 original cost is kept as `cost_usd_nominal`.
    retries      connection errors, timeouts, 408/409/429/5xx with exponential backoff
                 (models.yaml → retry). Then the next fallback model (only in profiles that
                 allow fallbacks); with none left, ProviderError → the problem is
                 `provider_error` (not a system failure).
    JSON         `complete_json` parses tolerantly (code fences, prose around the object,
                 trailing commas) and validates with the Pydantic contract; on failure, one
                 repair call (prompts/json_repair.md). A model whose endpoint rejects
                 `response_format` (400) is retried once without it and remembered.
    spend        before every paid request (not cache hits, not free models) the SpendGuard
                 checks its worst-case cost against pipeline.yaml → spend.total_usd and
                 spend.run_usd; if it could pass either, SpendLimitReached (problem status
                 `budget_stop`, the run ends). Every paid answer is appended to the ledger
                 data/spend_ledger.jsonl (llm/spend.py).
    versions     the first live response of each model is compared with its
                 `expected_version`; a mismatch is logged as a warning (console + trace).
    trace        one `llm_call` event per call: role, requested and returned model,
                 system_fingerprint, tokens (cache hit/miss, reasoning), cost and tariff at
                 the call's UTC start (peak/off-peak), latency, timestamp, cached, finish_reason.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from pydantic import BaseModel, ValidationError

from formal_mas.agents.prompting import render
from formal_mas.config import ROOT, load_models, load_pipeline
from formal_mas.llm.pricing import Usage, call_cost, usage_from_response
from formal_mas.llm.spend import SpendGuard, SpendLimitReached, worst_case_cost
from formal_mas.llm.specs import ModelSpec, RoleSpec, profile_name, resolve_profile
from formal_mas.tracing import Budget, BudgetExhausted, Tracer, iso, utc_now

LOG = logging.getLogger(__name__)
RETRY_DEFAULTS = {"attempts": 5, "backoff_s": 2.0, "backoff_factor": 2.0, "max_backoff_s": 60.0,
                  "request_timeout_s": 900.0}
RETRYABLE_STATUS = frozenset({408, 409, 429})

__all__ = ["LLMClient", "LLMResponse", "ProviderError", "LLMOutputError", "BudgetExhausted",
           "SpendLimitReached", "TransportError", "extract_json"]


class TransportError(Exception):
    """One failed HTTP request. `retryable` decides whether the same request is sent again."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ProviderError(RuntimeError):
    """Every model of the role failed after its retries (problem status `provider_error`)."""


class LLMOutputError(ValueError):
    """The output could not be parsed into the contract, even after the repair call."""

    def __init__(self, message: str, responses: list[LLMResponse]):
        super().__init__(message)
        self.responses = responses


class Transport(Protocol):
    def __call__(self, model: ModelSpec, request: dict, timeout: float) -> dict: ...


@dataclass
class LLMResponse:
    role: str
    content: str
    reasoning_content: str | None
    model_key: str  # models.yaml key that answered
    model_requested: str  # id sent as `model`
    model_returned: str | None  # `model` field of the response
    system_fingerprint: str | None
    finish_reason: str | None
    usage: Usage
    cost_usd: float  # spent by this call now (0 for cache hits)
    cost_usd_nominal: float  # cost when the response was produced
    tariff: str
    cached: bool
    latency_s: float
    started: str  # ISO UTC
    cache_key: str
    call_index: int  # position in the problem's budget (1-based)


# ---------------------------------------------------------------------------
# Tolerant JSON


def _objects(text: str):
    """Balanced `{…}` substrings, outermost first, aware of JSON strings."""
    starts = [i for i, ch in enumerate(text) if ch == "{"][:50]
    for s in starts:
        depth, in_str, esc = 0, False, False
        for j in range(s, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[s:j + 1]
                    break


def extract_json(text: str | None) -> dict:
    """The first JSON object in a model's output. Raises ValueError."""
    if not text or not text.strip():
        raise ValueError("empty output")
    t = text.strip()
    candidates = [t, *re.findall(r"```(?:json|JSON)?\s*\n?(.*?)```", t, re.S), *_objects(t)]
    for c in candidates:
        for variant in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                val = json.loads(variant)
            except ValueError:
                continue
            if isinstance(val, dict):
                return val
    raise ValueError("no JSON object found in the output")


# ---------------------------------------------------------------------------
# Transport (OpenAI SDK)


class OpenAITransport:
    """Sends a chat completion with the `openai` SDK; its own retries are off (max_retries=0)
    so that backoff and its logging live in LLMClient. Keys are read from the environment
    (.env is loaded once; never logged)."""

    def __init__(self):
        self._clients: dict[tuple[str, str], object] = {}
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env", override=False)
        except ImportError:  # pragma: no cover
            pass

    def _client(self, model: ModelSpec):
        import openai

        key = os.environ.get(model.api_key_env)
        if not key:
            raise TransportError(f"environment variable {model.api_key_env} is not set", retryable=False)
        ck = (model.base_url, model.api_key_env)
        if ck not in self._clients:
            self._clients[ck] = openai.OpenAI(api_key=key, base_url=model.base_url, max_retries=0)
        return self._clients[ck]

    def __call__(self, model: ModelSpec, request: dict, timeout: float) -> dict:
        import openai

        client = self._client(model)
        try:
            completion = client.chat.completions.create(**request, timeout=timeout)
        except openai.APIConnectionError as exc:  # includes APITimeoutError
            raise TransportError(f"{type(exc).__name__}: {exc}", retryable=True) from exc
        except openai.APIStatusError as exc:
            status = exc.status_code
            raise TransportError(f"HTTP {status}: {exc}", status=status,
                                 retryable=status in RETRYABLE_STATUS or status >= 500) from exc
        except openai.OpenAIError as exc:  # anything else the SDK raises (e.g. an unparsable body)
            raise TransportError(f"{type(exc).__name__}: {exc}", retryable=False) from exc
        return completion.model_dump()


# ---------------------------------------------------------------------------
# Client


class LLMClient:
    def __init__(self, profile: str | None = None, models_cfg: dict | None = None, pipeline_cfg: dict | None = None,
                 tracer: Tracer | None = None, transport: Transport | None = None, cache_dir: Path | None = None,
                 clock: Callable = utc_now, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic, spend: SpendGuard | None = None):
        self.models_cfg = models_cfg if models_cfg is not None else load_models()
        pipeline_cfg = pipeline_cfg if pipeline_cfg is not None else load_pipeline()
        self.spend = spend or SpendGuard.from_config(pipeline_cfg)
        self.profile = profile or profile_name(self.models_cfg)
        self.roles: dict[str, RoleSpec] = resolve_profile(self.models_cfg, self.profile)
        self.retry = {**RETRY_DEFAULTS, **(self.models_cfg.get("retry") or {})}
        llm_cfg = pipeline_cfg.get("llm") or {}
        self.cache_enabled = bool(llm_cfg.get("cache", True))
        self.cache_dir = Path(cache_dir) if cache_dir else ROOT / llm_cfg.get("cache_dir", "data/llm_cache")
        self.json_repair = bool(llm_cfg.get("json_repair", True))
        self.tracer = tracer or Tracer()
        self.transport = transport or OpenAITransport()
        self.clock, self.sleep, self.monotonic = clock, sleep, monotonic
        self.json_disabled: set[str] = set()  # model keys whose endpoint rejected response_format
        self.observed: dict[str, dict] = {}  # model key → returned models / fingerprints
        self.version_mismatches: list[dict] = []
        self._last_request: dict[str, float] = {}  # provider → monotonic time

    # -- public -------------------------------------------------------------

    def info(self) -> dict:
        """For run_config.json: profile, exact role specs, and what the providers returned."""
        return {"profile": self.profile, "roles": {r: s.describe() for r, s in self.roles.items()},
                "observed": {k: {"returned": sorted(v["returned"]), "fingerprints": sorted(v["fingerprints"])}
                             for k, v in self.observed.items()},
                "version_mismatches": list(self.version_mismatches),
                "json_mode_disabled": sorted(self.json_disabled), "cache": self.cache_enabled,
                "retry": dict(self.retry)}

    def priced_models(self) -> list[dict]:
        seen = {}
        for spec in self.roles.values():
            for m in spec.models:
                seen[m.key] = {"key": m.key, "pricing": m.pricing}
        return list(seen.values())

    def complete(self, role: str, messages: list[dict], budget: Budget, *, sample: int = 0,
                 json_output: bool = False, purpose: str = "main") -> LLMResponse:
        """One logical call. Raises BudgetExhausted, SpendLimitReached or ProviderError."""
        spec = self.roles[role]
        try:
            call_index = budget.reserve()
        except BudgetExhausted:
            self.tracer.event("llm_budget_exhausted", role=role, b_max=budget.b_max, purpose=purpose)
            raise
        failures = []
        for model in spec.models:
            request = self._request(spec, model, messages, json_output)
            # The cache key is that of the request as configured, before the client drops a
            # rejected `response_format`, so later runs (json_disabled empty) still hit it.
            key = self._cache_key(model, self._request(spec, model, messages, json_output, configured=True), sample)
            try:
                resp = self._answer(role, model, request, key, sample, call_index, purpose)
            except TransportError as exc:
                failures.append(f"{model.key}: {exc}")
                self.tracer.event("llm_model_failed", level="warning", role=role, model=model.key,
                                  status=exc.status, error=str(exc)[:500], purpose=purpose)
                continue
            budget.record(resp.usage, resp.cost_usd, resp.cost_usd_nominal, resp.finish_reason, resp.cached)
            return resp
        self.tracer.event("llm_provider_error", level="error", role=role, failures=failures, purpose=purpose)
        raise ProviderError("; ".join(failures))

    def complete_json(self, role: str, messages: list[dict], schema: type[BaseModel], budget: Budget, *,
                      sample: int = 0) -> tuple[BaseModel, list[LLMResponse]]:
        """A call whose output must be a JSON object of `schema`; one repair call if not.
        Raises LLMOutputError, BudgetExhausted, SpendLimitReached or ProviderError."""
        resp = self.complete(role, messages, budget, sample=sample, json_output=True)
        try:
            return schema.model_validate(extract_json(resp.content)), [resp]
        except ValueError as err:  # includes pydantic.ValidationError
            first_error = _short(err)
        self.tracer.event("llm_json_invalid", role=role, error=first_error, call_index=resp.call_index)
        if not self.json_repair:
            raise LLMOutputError(first_error, [resp])
        repair = render("json_repair", schema=json.dumps(schema.model_json_schema(), ensure_ascii=False),
                        output=(resp.content or "")[:20000], error=first_error)
        resp2 = self.complete(role, repair, budget, sample=sample, json_output=True, purpose="json_repair")
        try:
            obj = schema.model_validate(extract_json(resp2.content))
        except ValueError as err:
            self.tracer.event("llm_json_invalid", role=role, error=_short(err), call_index=resp2.call_index,
                              purpose="json_repair")
            raise LLMOutputError(_short(err), [resp, resp2]) from None
        self.tracer.event("llm_json_repaired", role=role, call_index=resp2.call_index)
        return obj, [resp, resp2]

    # -- internals ----------------------------------------------------------

    def _request(self, spec: RoleSpec, model: ModelSpec, messages: list[dict], json_output: bool,
                 configured: bool = False) -> dict:
        """The request to send; `configured=True` ignores response_format rejections seen so far."""
        request = {"model": model.model_id, "messages": messages, **spec.params}
        if json_output and model.json_mode and (configured or model.key not in self.json_disabled):
            request["response_format"] = {"type": "json_object"}
        extra = dict(model.extra_body)
        if spec.thinking is not None:
            extra.update((model.thinking.get("enabled" if spec.thinking else "disabled") or {}).get("extra_body") or {})
        if extra:
            request["extra_body"] = extra
        return request

    def _cache_key(self, model: ModelSpec, request: dict, sample: int) -> str:
        blob = json.dumps({"base_url": model.base_url, "request": request, "sample": sample},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_get(self, key: str) -> dict | None:
        if not self.cache_enabled:
            return None
        path = self._cache_path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _cache_put(self, key: str, entry: dict) -> None:
        if not self.cache_enabled:
            return
        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            self.tracer.event("llm_cache_write_failed", level="warning", error=repr(exc))

    def _answer(self, role: str, model: ModelSpec, request: dict, key: str, sample: int, call_index: int,
                purpose: str) -> LLMResponse:
        started = self.clock()
        t0 = self.monotonic()
        entry = self._cache_get(key)
        cached = entry is not None
        if cached:
            raw, nominal = entry["response"], float(entry.get("cost_usd", 0.0))
            usage = usage_from_response(raw)
            tariff = entry.get("tariff", "unknown")
            cost = 0.0
        else:
            worst = self._check_spend(role, model, request, call_index, purpose)
            # response_format may have been dropped; billed by the start of the attempt that answered
            raw, request, started = self._send(model, request)
            usage = usage_from_response(raw)
            cost, tariff = call_cost(usage, model.pricing, started)
            nominal = cost
            # cache first: if the ledger write then fails, a rerun does not pay for this again
            self._cache_put(key, {"key": key, "model": model.key, "request": request, "sample": sample,
                                  "response": raw, "cost_usd": cost, "tariff": tariff, "started": iso(started)})
            if tariff != "free":
                self.spend.record(cost_usd=cost, role=role, model=model.key, model_returned=raw.get("model"),
                                  tariff_=tariff, usage=usage, started=started, purpose=purpose,
                                  problem_id=self.tracer.current("problem_id"), cache_key=key, worst_case_usd=worst)
        choice = (raw.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        resp = LLMResponse(role=role, content=message.get("content") or "",
                           reasoning_content=message.get("reasoning_content"), model_key=model.key,
                           model_requested=model.model_id, model_returned=raw.get("model"),
                           system_fingerprint=raw.get("system_fingerprint"), finish_reason=choice.get("finish_reason"),
                           usage=usage, cost_usd=cost, cost_usd_nominal=nominal, tariff=tariff, cached=cached,
                           latency_s=round(self.monotonic() - t0, 3), started=iso(started), cache_key=key,
                           call_index=call_index)
        self._observe(model, resp)  # cached responses too: an old alias version must not pass silently
        self.tracer.event(
            "llm_call", role=role, purpose=purpose, call_index=call_index, sample=sample,
            model_requested=model.model_id, model_key=model.key, provider=model.provider,
            model_returned=resp.model_returned, system_fingerprint=resp.system_fingerprint,
            prompt_tokens=usage.prompt_tokens, cache_hit_tokens=usage.cache_hit_tokens,
            cache_miss_tokens=usage.cache_miss_tokens, completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens, cost_usd=cost, cost_usd_nominal=nominal, tariff=tariff,
            latency_s=resp.latency_s, started=resp.started, cached=cached, finish_reason=resp.finish_reason,
            json_mode="response_format" in request, cache_key=key, content=resp.content,
            reasoning_chars=len(resp.reasoning_content or ""))
        if resp.finish_reason == "length":
            self.tracer.event("llm_length_finish", level="warning", role=role, call_index=call_index,
                              max_tokens=request.get("max_tokens"), completion_tokens=usage.completion_tokens)
        return resp

    def _check_spend(self, role: str, model: ModelSpec, request: dict, call_index: int,
                     purpose: str) -> float | None:
        """Refuse a paid request whose worst-case cost could pass a spend cap → that worst case."""
        if model.pricing.get("free"):
            return None
        worst = None
        try:
            worst = worst_case_cost(request, model.pricing, self.spend.default_max_tokens)
            self.spend.allow(worst)
        except SpendLimitReached as exc:
            self.spend.stopped = self.spend.stopped or str(exc)
            self.tracer.event("llm_spend_stop", level="error", role=role, model=model.key, call_index=call_index,
                              purpose=purpose, worst_case_usd=None if worst is None else round(worst, 6),
                              reason=str(exc),
                              run_spent_usd=round(self.spend.run_spent, 6))
            LOG.error("spend cap reached, paid call refused: %s", exc)
            raise
        return worst

    def _pace(self, model: ModelSpec) -> None:
        if model.min_interval_s <= 0:
            return
        last = self._last_request.get(model.provider)
        if last is not None:
            wait = model.min_interval_s - (self.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_request[model.provider] = self.monotonic()

    def _send(self, model: ModelSpec, request: dict) -> tuple[dict, dict, datetime]:
        """Transport call with backoff → (raw response, request actually sent, UTC start of
        the attempt that answered: the tariff that applies)."""
        attempts = max(int(self.retry["attempts"]), 1)
        timeout = float(self.retry["request_timeout_s"])
        attempt = 1
        while True:
            self._pace(model)
            sent_at = self.clock()
            try:
                return self.transport(model, request, timeout), request, sent_at
            except TransportError as exc:
                if exc.status == 400 and "response_format" in request:  # once per model, not an attempt
                    self.json_disabled.add(model.key)
                    self.tracer.event("llm_json_mode_disabled", level="warning", model=model.key, error=str(exc)[:500])
                    request = {k: v for k, v in request.items() if k != "response_format"}
                    continue
                if not exc.retryable or attempt >= attempts:
                    raise
                delay = min(float(self.retry["backoff_s"]) * float(self.retry["backoff_factor"]) ** (attempt - 1),
                            float(self.retry["max_backoff_s"]))
                self.tracer.event("llm_retry", level="warning", model=model.key, attempt=attempt, status=exc.status,
                                  delay_s=delay, error=str(exc)[:500])
                self.sleep(delay)
                attempt += 1

    def _observe(self, model: ModelSpec, resp: LLMResponse) -> None:
        first_live = model.key not in self.observed
        obs = self.observed.setdefault(model.key, {"returned": set(), "fingerprints": set()})
        returned = resp.model_returned
        if returned is not None and obs["returned"] and returned not in obs["returned"]:
            self.tracer.event("llm_model_changed", level="warning", model=model.key, before=sorted(obs["returned"]),
                              now=returned)
            LOG.warning("model %s now answers as %r (was %s)", model.key, returned, sorted(obs["returned"]))
        if returned is not None:
            obs["returned"].add(returned)
        if resp.system_fingerprint:
            obs["fingerprints"].add(resp.system_fingerprint)
        exp = model.expected_version
        if first_live and exp and (returned or "").lower() != exp.lower():
            entry = {"model": model.key, "expected_version": exp, "returned": returned, "cached": resp.cached}
            self.version_mismatches.append(entry)
            self.tracer.event("llm_version_mismatch", level="warning", **entry)
            LOG.warning("model %s: expected version %r but the provider returned %r — check before spending more",
                        model.key, exp, returned)


def _short(err: Exception) -> str:
    return str(err).replace("\n", " ")[:1000]
