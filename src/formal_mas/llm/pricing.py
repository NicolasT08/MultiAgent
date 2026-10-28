"""Cost of one LLM call from its usage and the tariff in force at its start (models.yaml).

`pricing` of a model is either `{free: true}` or
    peak:     {input_cache_hit, input_cache_miss, output}   USD per 1M tokens
    off_peak: {…}                                            optional, same keys
    peak_windows_utc: [{days: [Mon…], start: "HH:MM", end: "HH:MM"}, …]
A call is billed by the UTC time it started (start inclusive, end exclusive). Without
`off_peak` the peak price always applies. Output tokens include reasoning tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


def _get(d: dict | None, *path: str):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def usage_from_response(resp: dict) -> Usage:
    """Normalise `usage` of an OpenAI-compatible response. DeepSeek reports
    prompt_cache_hit_tokens / prompt_cache_miss_tokens (hit + miss = prompt_tokens); other
    providers may only give prompt_tokens_details.cached_tokens, or nothing."""
    u = resp.get("usage") or {}
    prompt = int(u.get("prompt_tokens") or 0)
    hit = u.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = _get(u, "prompt_tokens_details", "cached_tokens")
    hit = int(hit or 0)
    miss = u.get("prompt_cache_miss_tokens")
    miss = int(miss) if miss is not None else max(prompt - hit, 0)
    reasoning = int(_get(u, "completion_tokens_details", "reasoning_tokens") or 0)
    return Usage(prompt_tokens=prompt, cache_hit_tokens=hit, cache_miss_tokens=miss,
                 completion_tokens=int(u.get("completion_tokens") or 0), reasoning_tokens=reasoning)


def _hhmm(s: str) -> time:
    h, m = str(s).split(":")
    return time(int(h), int(m))


def tariff(pricing: dict, when: datetime) -> str:
    """'free', 'peak' or 'off_peak' for a call that started at `when`."""
    if pricing.get("free"):
        return "free"
    if "off_peak" not in pricing:
        return "peak"
    when = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    day, t = DAYS[when.weekday()], when.time()
    for w in pricing.get("peak_windows_utc") or []:
        if day in w.get("days", DAYS) and _hhmm(w["start"]) <= t < _hhmm(w["end"]):
            return "peak"
    return "off_peak"


def call_cost(usage: Usage, pricing: dict, when: datetime) -> tuple[float, str]:
    """(USD, tariff) of one call."""
    tar = tariff(pricing, when)
    if tar == "free":
        return 0.0, tar
    p = pricing[tar]
    usd = (usage.cache_hit_tokens * p["input_cache_hit"] + usage.cache_miss_tokens * p["input_cache_miss"]
           + usage.completion_tokens * p["output"]) / 1e6
    return round(usd, 8), tar


def in_peak(models: list[dict], when: datetime) -> list[str]:
    """Keys of the given priced models whose peak window contains `when` (for warnings)."""
    return [m["key"] for m in models if tariff(m["pricing"], when) == "peak" and "off_peak" in m["pricing"]]
