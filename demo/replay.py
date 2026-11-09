"""Replay mode: recorded timelines of demo/replays/*.json (made by scripts/export_replays.py).

No Lean, no API key and no project imports. A replay is
    {"schema": 1, "title", "note", "config", "run_id", "profile", "models",
     "problem": {id, split, area, difficulty, source, license, informal|null, statement},
     "events": [{"t": seconds since problem_start, "event": …, …}],
     "result": {status, solved, proof, llm, wall_s, …}}
`play(replay, speed)` yields the timeline after every event, sleeping the recorded gap divided
by `speed` (each gap capped at MAX_GAP_S, so a two-minute LLM call does not stall the room).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

from timeline import render, summary

REPLAY_DIR = Path(__file__).resolve().parent / "replays"
MAX_GAP_S = 2.5
SCHEMA = 1

__all__ = ["REPLAY_DIR", "load_replays", "play", "problem_card"]


def load_replays(folder: Path = REPLAY_DIR) -> dict[str, dict]:
    """key (file stem) → replay, in file-name order; files with another schema are skipped."""
    out = {}
    for path in sorted(Path(folder).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema") == SCHEMA and isinstance(data.get("events"), list):
            out[path.stem] = data
    return out


def problem_card(problem: dict, statement: str | None = None, note: str | None = None) -> str:
    """Markdown header of a problem: metadata, informal text (when it may be shown) and statement."""
    meta = " · ".join(str(x) for x in (problem.get("split"), problem.get("difficulty"), problem.get("area")) if x)
    lines = [f"**`{problem.get('id')}`** — {meta}" if meta else f"**`{problem.get('id')}`**"]
    if problem.get("source"):
        lines.append(f"<sub>Source: {problem['source']} · license: {problem.get('license', '?')}</sub>")
    if note:
        lines.append(f"\n{note}")
    if problem.get("informal"):
        lines.append("\n**Problem (informal):** " + problem["informal"])
    elif problem.get("informal_withheld"):
        lines.append("\n_Informal text not shown (PutnamBench: available with permission from the MAA)._")
    text = statement if statement is not None else problem.get("statement") or problem.get("formal_statement") or ""
    lines.append("\n**Formal statement (written by curators, never by an LLM):**\n```lean\n" + text.strip() + "\n```")
    return "\n".join(lines)


def _wait(t: float, last_t: float, speed: float, sleep) -> None:
    if speed and speed != float("inf") and speed > 0:
        gap = max(t - last_t, 0.0) / speed
        if gap > 0:
            sleep(min(gap, MAX_GAP_S))


def frames(replay: dict, speed: float = 5.0, sleep=time.sleep) -> Iterator[tuple[list[dict], dict | None]]:
    """Yield (events shown so far, None) after every event, then (all events, result);
    speed ≤ 0 or inf = instant."""
    events = replay.get("events") or []
    last_t = 0.0
    for i, e in enumerate(events):
        t = float(e.get("t") or 0.0)
        _wait(t, last_t, speed, sleep)
        last_t = t
        yield events[:i + 1], None
    yield list(events), replay.get("result") or {}


def play(replay: dict, speed: float = 5.0, sleep=time.sleep) -> Iterator[tuple[str, str]]:
    """Yield (timeline markdown, summary markdown) after every event; speed ≤ 0 or inf = instant."""
    for shown, result in frames(replay, speed, sleep):
        if result is None:
            yield render(shown), "_replaying…_"
        else:
            yield render(shown, result), summary(result, shown)


def pairs(replays: dict[str, dict]) -> list[tuple[str, str]]:
    """(key a, key b) of replays of the same problem under two configurations, b's config last
    in name order (e.g. C4 left, C5 right)."""
    by_problem: dict[str, list[str]] = {}
    for key, rep in replays.items():
        by_problem.setdefault((rep.get("problem") or {}).get("id"), []).append(key)
    out = []
    for keys in by_problem.values():
        keys = sorted(keys, key=lambda k: str(replays[k].get("config")))
        out += [(keys[i], keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys))]
    return out


def pair_frames(a: dict, b: dict, speed: float = 5.0, sleep=time.sleep):
    """Both replays on one clock (seconds since each problem_start): yields
    ((events a, result a | None), (events b, result b | None)); a side gets its result once its
    events end."""
    reps = (a, b)
    evs = [a.get("events") or [], b.get("events") or []]
    merged = sorted([(float(e.get("t") or 0.0), k, i) for k in (0, 1) for i, e in enumerate(evs[k])])
    counts = [0, 0]
    last_t = 0.0

    def side(k: int):
        shown = evs[k][:counts[k]]
        return shown, ((reps[k].get("result") or {}) if counts[k] == len(evs[k]) else None)

    for t, k, _ in merged:
        _wait(t, last_t, speed, sleep)
        last_t = t
        counts[k] += 1
        yield side(0), side(1)
    if not merged:
        yield side(0), side(1)


def play_pair(a: dict, b: dict, speed: float = 5.0, sleep=time.sleep) -> Iterator[tuple[str, str, str, str]]:
    """(timeline a, summary a, timeline b, summary b) Markdown, on one clock (see pair_frames)."""
    def md(shown, result):
        if result is None:
            return render(shown), "_replaying…_"
        return render(shown, result), summary(result, shown)

    for fa, fb in pair_frames(a, b, speed, sleep):
        yield (*md(*fa), *md(*fb))
