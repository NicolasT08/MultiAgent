"""A fake OpenAI-compatible transport for offline tests (no network, no keys)."""

from __future__ import annotations

from datetime import datetime, timezone

from formal_mas.llm.client import TransportError

# A Wednesday, 12:00 UTC: off-peak for DeepSeek.
OFF_PEAK = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
PEAK = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc)


def response(content: str, *, model: str = "DeepSeek-V4.1-Flash-1017", hit: int = 100, miss: int = 900,
             completion: int = 200, reasoning: int = 50, finish: str = "stop", fingerprint: str = "fp_1") -> dict:
    return {
        "id": "x", "object": "chat.completion", "model": model, "system_fingerprint": fingerprint,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content, "reasoning_content": "thinking..."}}],
        "usage": {"prompt_tokens": hit + miss, "completion_tokens": completion, "total_tokens": hit + miss + completion,
                  "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": miss,
                  "completion_tokens_details": {"reasoning_tokens": reasoning}},
    }


class FakeTransport:
    """Answers from a script: each item is a dict (response), a str (content), an Exception
    to raise, or a callable(request) → one of those. The last item repeats forever."""

    def __init__(self, *script):
        self.script = list(script) or ['{"ok": true}']
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, model, request, timeout):
        self.calls.append((model.key, request))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(item) and not isinstance(item, type):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            item = response(item)
        return item


def http_error(status: int) -> TransportError:
    return TransportError(f"HTTP {status}", status=status, retryable=status in (408, 409, 429) or status >= 500)


class FakeClock:
    def __init__(self, now: datetime = OFF_PEAK):
        self.now = now
        self.sleeps: list[float] = []
        self.mono = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.mono += s

    def monotonic(self) -> float:
        return self.mono


MODELS_CFG = {
    "default_profile": "experiment",
    "providers": {
        "deepseek": {"base_url": "https://api.deepseek.test", "api_key_env": "DEEPSEEK_API_KEY"},
        "free": {"base_url": "https://free.test/v1/", "api_key_env": "FREE_KEY"},
    },
    "retry": {"attempts": 3, "backoff_s": 2.0, "backoff_factor": 2.0, "max_backoff_s": 60.0, "request_timeout_s": 10},
    "models": {
        "flash": {
            "provider": "deepseek", "id": "deepseek-flash", "expected_version": "DeepSeek-V4.1-Flash-1017",
            "json_mode": True,
            "thinking": {"enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
                         "disabled": {"extra_body": {"thinking": {"type": "disabled"}}},
                         "ignores": ["temperature", "presence_penalty", "frequency_penalty"]},
            "pricing": {
                "peak": {"input_cache_hit": 0.006, "input_cache_miss": 0.30, "output": 1.20},
                "off_peak": {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.60},
                "peak_windows_utc": [{"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "01:00", "end": "04:00"},
                                     {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "06:00", "end": "10:00"}],
            },
        },
        "free-a": {"provider": "free", "id": "free-a", "expected_version": None, "thinking": {"enabled": {}},
                   "pricing": {"free": True}},
        "free-b": {"provider": "free", "id": "free-b", "expected_version": None, "thinking": {"enabled": {}},
                   "pricing": {"free": True}},
    },
    "profiles": {
        "experiment": {"fallbacks_allowed": False, "pinned": True, "roles": {
            "planner": {"model": "flash", "thinking": True, "reasoning_effort": "low", "max_tokens": 1000},
            "generator": {"model": "flash", "thinking": True, "reasoning_effort": "low", "max_tokens": 1000},
            "autoformalizer": {"model": "flash", "thinking": True, "reasoning_effort": "low", "max_tokens": 1000},
            "critic": {"model": "flash", "thinking": False, "temperature": 0.0, "max_tokens": 400},
            "baseline_a": {"same_as": "autoformalizer"},
            "baseline_b": {"same_as": "generator"},
        }},
        "dev": {"fallbacks_allowed": True, "roles": {
            "generator": {"model": "free-a", "reasoning_effort": "low", "max_tokens": 1000, "fallbacks": ["free-b"]},
            "baseline_a": {"same_as": "generator"},
            "baseline_b": {"same_as": "generator"},
        }},
    },
}


def pipeline_cfg(cache: bool = True, json_repair: bool = True, total_usd: float = 9.0, run_usd: float = 1.0,
                 ledger=None) -> dict:
    """`ledger=None` keeps the spend ledger in memory: tests never touch data/spend_ledger.jsonl."""
    return {"llm": {"cache": cache, "json_repair": json_repair}, "budget": {"b_max": 16},
            "spend": {"total_usd": total_usd, "run_usd": run_usd, "ledger": ledger, "default_max_tokens": 65536}}
