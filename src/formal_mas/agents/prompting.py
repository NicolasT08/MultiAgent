"""Load prompts from src/formal_mas/prompts/<name>.md (prompts never live in code).

Format: text before the first section is a comment for humans and is dropped; sections
start with a line `SYSTEM:` or `USER:`; placeholders are `{{name}}`. Every placeholder of
the template must be given (KeyError otherwise); values are inserted verbatim.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_SECTION_RE = re.compile(r"^(SYSTEM|USER):[ \t]*$", re.M)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@lru_cache(maxsize=None)
def load(name: str, prompts_dir: Path = PROMPTS_DIR) -> tuple[tuple[str, str], ...]:
    """((role, template), …) of prompts/<name>.md."""
    text = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    marks = list(_SECTION_RE.finditer(text))
    if not marks:
        raise ValueError(f"prompt {name!r} has no SYSTEM:/USER: section")
    sections = []
    for m, nxt in zip(marks, [*marks[1:], None]):
        body = text[m.end():nxt.start() if nxt else len(text)].strip("\n")
        sections.append((m.group(1).lower(), body.strip()))
    return tuple(sections)


def placeholders(name: str, prompts_dir: Path = PROMPTS_DIR) -> set[str]:
    return {p for _, t in load(name, prompts_dir) for p in _PLACEHOLDER_RE.findall(t)}


def render(name: str, prompts_dir: Path = PROMPTS_DIR, **values) -> list[dict]:
    """Chat messages of prompts/<name>.md with the placeholders filled."""
    missing = placeholders(name, prompts_dir) - set(values)
    if missing:
        raise KeyError(f"prompt {name!r}: missing values for {sorted(missing)}")

    def fill(t: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), t)

    return [{"role": role, "content": fill(t)} for role, t in load(name, prompts_dir)]
