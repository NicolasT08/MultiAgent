"""Run traces (RNF-5) and the per-problem LLM budget (CLAUDE.md §1.5).

Tracer      one JSON object per line in runs/<run>/trace.jsonl: {"ts", "event", …context, …}.
            `context(problem_id=…)` adds fields to every event inside the block.
            `attach_logging()` also copies the JSON events that other modules log
            (`lean_verify`, `retriever_warning`, …) into the trace.
Budget      at most `b_max` LLM calls per problem. `reserve()` is called before every
            logical call (cache hits and JSON repairs included) and raises BudgetExhausted
            when the limit is reached, so the limit can never be exceeded.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Tracer:
    """Append-only JSON Lines writer. Without a path, events are kept in `self.events`."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.events: list[dict] = []
        self._ctx: dict = {}
        self._lock = threading.Lock()
        self._fh = None
        self._handler: logging.Handler | None = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")

    def event(self, event: str, **fields) -> dict:
        rec = {"ts": iso(utc_now()), "event": event, **self._ctx, **fields}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            if self._fh:
                self._fh.write(line + "\n")
                self._fh.flush()
            else:
                self.events.append(rec)
        return rec

    def current(self, name: str):
        """Value of a context field (e.g. problem_id) or None."""
        return self._ctx.get(name)

    @contextlib.contextmanager
    def context(self, **fields):
        old = self._ctx
        self._ctx = {**old, **fields}
        try:
            yield self
        finally:
            self._ctx = old

    def attach_logging(self, logger_name: str = "formal_mas") -> None:
        """Copy JSON-message log records of `logger_name` (and children) into the trace."""
        if self._handler is None:
            self._handler = _TraceHandler(self)
            logger = logging.getLogger(logger_name)
            logger.addHandler(self._handler)
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)

    def close(self) -> None:
        if self._handler is not None:
            logging.getLogger("formal_mas").removeHandler(self._handler)
            self._handler = None
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> Tracer:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class _TraceHandler(logging.Handler):
    def __init__(self, tracer: Tracer):
        super().__init__(level=logging.INFO)
        self.tracer = tracer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        if isinstance(msg, dict) and "event" in msg:
            event = msg.pop("event")
            self.tracer.event(event, level=record.levelname.lower(), **msg)


class BudgetExhausted(RuntimeError):
    """The problem already used its b_max LLM calls."""


@dataclass
class Budget:
    """LLM calls and usage of one problem."""

    b_max: int
    used: int = 0
    cached_calls: int = 0
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0  # actually spent now (cache hits cost 0)
    cost_usd_nominal: float = 0.0  # what the calls cost when they were made (cache hits included)
    finish_reasons: dict = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        return max(self.b_max - self.used, 0)

    def reserve(self) -> int:
        """Take one call from the budget (before calling). Raises BudgetExhausted."""
        if self.used >= self.b_max:
            raise BudgetExhausted(f"b_max = {self.b_max} LLM calls used")
        self.used += 1
        return self.used

    def record(self, usage, cost_usd: float, cost_usd_nominal: float, finish_reason: str | None,
               cached: bool) -> None:
        self.cached_calls += cached
        self.prompt_tokens += usage.prompt_tokens
        self.cache_hit_tokens += usage.cache_hit_tokens
        self.cache_miss_tokens += usage.cache_miss_tokens
        self.completion_tokens += usage.completion_tokens
        self.reasoning_tokens += usage.reasoning_tokens
        self.cost_usd += cost_usd
        self.cost_usd_nominal += cost_usd_nominal
        key = finish_reason or "none"
        self.finish_reasons[key] = self.finish_reasons.get(key, 0) + 1

    def summary(self) -> dict:
        return {"llm_calls": self.used, "b_max": self.b_max, "cached_calls": self.cached_calls,
                "tokens": {"prompt": self.prompt_tokens, "cache_hit": self.cache_hit_tokens,
                           "cache_miss": self.cache_miss_tokens, "completion": self.completion_tokens,
                           "reasoning": self.reasoning_tokens},
                "cost_usd": round(self.cost_usd, 6), "cost_usd_nominal": round(self.cost_usd_nominal, 6),
                "finish_reasons": dict(self.finish_reasons),
                "length_finishes": self.finish_reasons.get("length", 0)}
