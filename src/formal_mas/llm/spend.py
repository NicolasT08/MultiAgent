"""Spend control for paid LLM calls (CLAUDE.md §2: the budget is minimal).

ledger   data/spend_ledger.jsonl (pipeline.yaml → spend.ledger, outside git): one line per
         paid call answered by the provider: {ts, run_id, problem_id, role, model, purpose,
         cost_usd, tariff, tokens…}. Its sum is everything the project has spent.
caps     pipeline.yaml → spend.total_usd (whole project) and spend.run_usd (one run).
         Only the team raises them.
check    before every paid request the client calls `allow(worst_case_usd)`: the request is
         sent only if ledger total + worst case ≤ total_usd and run spent + worst case ≤
         run_usd. The worst case (`worst_case_cost`) bills every prompt byte as a cache-miss
         token and the whole `max_tokens` as output, at the model's most expensive tariff
         whatever the hour (a retried request may go out after a peak window opens), so the
         actual cost can never pass a cap. Otherwise SpendLimitReached: the problem is
         `budget_stop`, the guard stays stopped (every later paid call is refused) and the
         run ends cleanly. Free models and cache hits are never checked nor recorded.

The ledger is re-read on every check, so runs started one after the other (or a run and a
probe script) share the project cap. Two runs at the same time are not coordinated (each
could pass the project cap by one worst case): run paid configurations one at a time.
Fails closed: an unreadable ledger, a failed ledger write, a call that cost more than its
checked worst case, a missing `spend` section or a missing `spend.ledger` key stop the
guard (`ledger: null` keeps the records in memory, for tests only).
Limits: the check is done once per logical call; failed transport attempts (timeouts,
5xx) are assumed unbilled and are not in the ledger.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from formal_mas.config import ROOT
from formal_mas.llm.pricing import Usage
from formal_mas.tracing import iso

# Upper bound on the tokens a chat template adds around each message (role markers…).
MESSAGE_OVERHEAD_TOKENS = 16

__all__ = ["SpendGuard", "SpendLimitReached", "worst_case_cost"]


class SpendLimitReached(RuntimeError):
    """A paid call could pass spend.total_usd or spend.run_usd (problem status `budget_stop`)."""


def worst_case_cost(request: dict, pricing: dict, default_max_tokens: int) -> float:
    """Upper bound in USD of one request: byte-level BPE gives ≤ 1 token per UTF-8 byte, all
    input billed as cache miss, output = max_tokens (reasoning tokens included), both at the
    most expensive tariff of the model."""
    if pricing.get("free"):
        return 0.0
    try:
        tariffs = [pricing[t] for t in ("peak", "off_peak") if t in pricing]
        miss = max(t["input_cache_miss"] for t in tariffs)
        out = max(t["output"] for t in tariffs)
    except (KeyError, TypeError, ValueError) as exc:  # a paid model with broken pricing: no call
        raise SpendLimitReached(f"models.yaml pricing has no usable peak/off_peak prices: {pricing!r}") from exc
    prompt = sum(len(json.dumps(m, ensure_ascii=False).encode("utf-8")) + MESSAGE_OVERHEAD_TOKENS
                 for m in request.get("messages") or [])
    output = int(request.get("max_tokens") or default_max_tokens)
    return (prompt * miss + output * out) / 1e6


class SpendGuard:
    """Caps and ledger of one run. `ledger=None` keeps the records in memory (tests)."""

    def __init__(self, total_usd: float | None, run_usd: float | None, ledger: Path | None, run_id: str = "adhoc",
                 default_max_tokens: int = 65536):
        self.total_usd = total_usd
        self.run_usd = run_usd
        self.ledger = Path(ledger) if ledger else None
        self.run_id = run_id
        self.default_max_tokens = default_max_tokens
        self.memory: list[dict] = []  # records when there is no ledger file
        self.run_spent = 0.0
        self.run_calls = 0
        self.stopped: str | None = None  # reason of the first refusal
        self.spent_before = 0.0
        if self.configured:
            try:
                self.spent_before = self.total_spent()
            except SpendLimitReached as exc:
                self.stopped = str(exc)  # an unreadable ledger refuses every paid call

    @classmethod
    def from_config(cls, pipeline_cfg: dict, run_id: str = "adhoc", root: Path = ROOT) -> SpendGuard:
        cfg = pipeline_cfg.get("spend")
        if not cfg or "ledger" not in cfg:  # fails closed: no caps → no paid calls
            return cls(None, None, None, run_id)
        ledger = cfg["ledger"]
        return cls(float(cfg["total_usd"]), float(cfg.get("run_usd", 1.0)),
                   (root / ledger) if ledger else None, run_id,
                   int(cfg.get("default_max_tokens", 65536)))

    @property
    def configured(self) -> bool:
        return self.total_usd is not None and self.run_usd is not None

    def total_spent(self) -> float:
        """USD of every paid call in the ledger (all runs). Raises SpendLimitReached if unreadable."""
        if self.ledger is None:
            return round(sum(r["cost_usd"] for r in self.memory), 8)
        if not self.ledger.exists():
            return 0.0
        try:
            text = self.ledger.read_text(encoding="utf-8")
        except OSError as exc:
            raise SpendLimitReached(f"cannot read {self.ledger}: {exc}") from exc
        total = 0.0
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                total += float(json.loads(line)["cost_usd"])
            except (ValueError, TypeError, KeyError) as exc:
                raise SpendLimitReached(f"{self.ledger}:{n} is not a ledger record "
                                        f"({type(exc).__name__}); fix the ledger") from exc
        return round(total, 8)

    def _stop(self, reason: str) -> SpendLimitReached:
        self.stopped = reason
        return SpendLimitReached(reason)

    def allow(self, worst_case_usd: float) -> None:
        """Raise SpendLimitReached unless a call costing at most `worst_case_usd` fits both caps."""
        if self.stopped:
            raise SpendLimitReached(self.stopped)
        if not self.configured:
            raise self._stop("no spend caps configured (pipeline.yaml → spend with total_usd, run_usd, ledger)")
        try:
            total = self.total_spent()
        except SpendLimitReached as exc:
            raise self._stop(str(exc)) from exc
        if total + worst_case_usd > self.total_usd:
            raise self._stop(f"project cap: spent ${total:.4f} + worst case ${worst_case_usd:.4f} "
                             f"> spend.total_usd ${self.total_usd:.2f}")
        if self.run_spent + worst_case_usd > self.run_usd:
            raise self._stop(f"run cap: spent ${self.run_spent:.4f} + worst case ${worst_case_usd:.4f} "
                             f"> spend.run_usd ${self.run_usd:.2f}")

    def record(self, *, cost_usd: float, role: str, model: str, model_returned: str | None, tariff_: str,
               usage: Usage, started: datetime, purpose: str, problem_id: str | None, cache_key: str,
               worst_case_usd: float | None = None) -> dict:
        """Append one paid call to the ledger. The call is always counted for the run; a failed
        write or a cost above the checked worst case then stops the guard (SpendLimitReached)."""
        rec = {"ts": iso(started), "run_id": self.run_id, "problem_id": problem_id, "role": role,
               "purpose": purpose, "model": model, "model_returned": model_returned, "tariff": tariff_,
               "cost_usd": round(cost_usd, 8), "prompt_tokens": usage.prompt_tokens,
               "cache_hit_tokens": usage.cache_hit_tokens, "cache_miss_tokens": usage.cache_miss_tokens,
               "completion_tokens": usage.completion_tokens, "reasoning_tokens": usage.reasoning_tokens,
               "cache_key": cache_key}
        self.run_spent += cost_usd
        self.run_calls += 1
        if self.ledger is None:
            self.memory.append(rec)
        else:
            try:
                self.ledger.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
            except OSError as exc:
                raise self._stop(f"could not write {self.ledger}: ${cost_usd:.6f} spent but not recorded "
                                 f"(add it by hand): {exc}") from exc
        if worst_case_usd is not None and cost_usd > worst_case_usd + 1e-12:
            raise self._stop(f"a call to {model} cost ${cost_usd:.6f}, above its checked worst case "
                             f"${worst_case_usd:.6f}: fix the estimate before spending more")
        return rec

    def info(self) -> dict:
        """For run_config.json and the run summary."""
        total = None
        try:
            total = self.total_spent() if self.configured else None
        except SpendLimitReached:
            pass
        ledger = self.ledger
        if ledger is not None and ledger.is_relative_to(ROOT):
            ledger = ledger.relative_to(ROOT)
        return {"total_usd_cap": self.total_usd, "run_usd_cap": self.run_usd,
                "ledger": ledger.as_posix() if ledger is not None else None,
                "spent_before_run_usd": round(self.spent_before, 6), "run_spent_usd": round(self.run_spent, 6),
                "run_paid_calls": self.run_calls, "total_spent_usd": total,
                "total_remaining_usd": None if total is None else round(self.total_usd - total, 6),
                "stopped": self.stopped}
