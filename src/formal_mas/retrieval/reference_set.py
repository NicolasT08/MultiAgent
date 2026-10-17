"""Reference set Q for retrieval evaluation (Recall@k, MRR; Eq. 7).

Each element of Q is a Mathlib theorem: the query is its statement (signature) and the
gold set G_q is derived deterministically from its compiled proof term
(``scripts/extract_proof_consts.lean``), with no LLM involved:

- method (b) ``gold_b``: constants of the proof term that are corpus entries, minus
  ``Mathlib.Tactic.*`` and "infrastructure" lemmas used by more than ``infra_pct`` % of
  the proofs in the population;
- method (c) ``gold`` (the one used for evaluation): the subset of (b) whose last name
  component is written in the proof text, i.e. lemmas the prover had to name (lemmas
  applied implicitly by ``simp``/automation are left out). Dot notation such as
  ``h.not_ge`` still counts for ``LT.lt.not_ge``; a bare tactic such as ``ext`` does not
  count for ``Finset.ext`` (see ``TACTIC_NAMES``).

Selection favours statements over concrete types (ℕ, ℤ, ℚ, ℝ, Finset, Fin), balanced by
area, and never uses a query as another query's gold lemma, so that all queries can be
removed from the evaluation index without making any gold lemma unreachable.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from formal_mas.retrieval.corpus import SourceIndex

PROJECT_AREAS = ("algebra", "number_theory", "combinatorics")
MIN_GOLD, MAX_GOLD = 1, 10

_CONCRETE_RE = re.compile(r"[ℕℤℚℝ]|\bFinset\b|\bFin\b")
_COMMENT_RE = re.compile(r"--[^\n]*|/-.*?-/", re.S)
_TOKEN_RE = re.compile(r"[\w'!?₀-ₜ]+")
_DOTTED_RE = re.compile(r"\.([\w'!?₀-ₜ]+)")
# Lemma last components that are also tactic names: the bare tactic (`ext`, `by_cases`...)
# does not count as naming the lemma; only a dotted use (`Finset.ext`, `h.symm`) does.
TACTIC_NAMES = frozenset({
    "ext", "by_cases", "by_contra", "induction", "congr", "symm", "trans", "rfl", "absurd",
    "exfalso", "contrapose", "cases", "subst", "constructor", "left", "right",
})


@dataclass(frozen=True)
class QueryItem:
    """One element q of Q."""

    name: str
    query: str
    area: str
    module: str
    concrete: bool
    gold: list[str]
    gold_b: list[str]
    split: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def is_concrete(signature: str) -> bool:
    """Whether the statement mentions ℕ, ℤ, ℚ, ℝ, Finset or Fin."""
    return _CONCRETE_RE.search(signature) is not None


def _last(name: str) -> str:
    return name.replace("«", "").replace("»", "").rsplit(".", 1)[-1]


def proof_text(entry: dict, sources: SourceIndex) -> str | None:
    """Source text of the proof: after the first ``:=`` (or ``|`` equation) that follows the
    declaration name, with comments removed. ``None`` if the source is unavailable."""
    rng, sel = entry.get("range"), entry.get("sel")
    lines = sources.lines(entry["module"])
    if not rng or not sel or lines is None or rng[2] > len(lines):
        return None
    seg = lines[sel[2] - 1 : rng[2]]
    seg[-1] = seg[-1][: rng[3]] if len(seg) > 1 else seg[-1][sel[3] : rng[3]]
    if len(seg) > 1:
        seg[0] = seg[0][sel[3] :]
    text = _COMMENT_RE.sub(" ", "\n".join(seg))
    cuts = [i for i in (text.find(":="), text.find("\n  |")) if i >= 0]
    if not cuts:
        return None
    k = min(cuts)
    return text[k + 2 :]


def written_tokens(text: str) -> tuple[set[str], set[str]]:
    """``(all identifier components, components written after a dot)`` of a proof text."""
    text = text.replace("«", "").replace("»", "")
    return set(_TOKEN_RE.findall(text)), set(_DOTTED_RE.findall(text))


def is_written(const: str, tokens: set[str], dotted: set[str]) -> bool:
    last = _last(const)
    return last in (dotted if last in TACTIC_NAMES else tokens)


def candidate_consts(entry: dict, corpus: dict[str, dict]) -> set[str]:
    """Proof-term constants that are corpus entries outside ``Mathlib.Tactic``."""
    return {
        c for c in entry["consts"]
        if c in corpus and c != entry["name"] and not corpus[c]["module"].startswith("Mathlib.Tactic")
    }


def infrastructure(entries: Sequence[dict], corpus: dict[str, dict], infra_pct: float) -> dict[str, float]:
    """Constants used by more than ``infra_pct`` % of the proofs → their percentage."""
    df: Counter = Counter()
    for e in entries:
        df.update(candidate_consts(e, corpus))
    n = len(entries)
    if n == 0:
        return {}
    return {c: 100.0 * k / n for c, k in df.most_common() if 100.0 * k / n > infra_pct}


def gold_sets(entry: dict, corpus: dict[str, dict], infra: set[str], sources: SourceIndex) -> tuple[set[str], set[str]] | None:
    """``(gold_c, gold_b)`` for one theorem, or ``None`` if its proof text is unavailable."""
    b = candidate_consts(entry, corpus) - infra
    text = proof_text(entry, sources)
    if text is None:
        return None
    toks, dotted = written_tokens(text)
    return {c for c in b if is_written(c, toks, dotted)}, b


def build_candidates(
    entries: Iterable[dict], corpus: dict[str, dict], infra: set[str], sources: SourceIndex
) -> list[QueryItem]:
    """Hand-written theorems of the project areas with 1–10 gold lemmas (method c)."""
    out = []
    for e in entries:
        rec = corpus.get(e["name"])
        if rec is None or rec["origin"] != "manual" or rec["area"] not in PROJECT_AREAS:
            continue
        sets = gold_sets(e, corpus, infra, sources)
        if sets is None or not MIN_GOLD <= len(sets[0]) <= MAX_GOLD:
            continue
        out.append(QueryItem(
            name=e["name"], query=rec["signature"], area=rec["area"], module=rec["module"],
            concrete=is_concrete(rec["signature"]), gold=sorted(sets[0]), gold_b=sorted(sets[1]),
        ))
    return sorted(out, key=lambda q: q.name)


def _target_per_area(total: int) -> dict[str, int]:
    base, extra = divmod(total, len(PROJECT_AREAS))
    return {a: base + (i < extra) for i, a in enumerate(PROJECT_AREAS)}


def select(candidates: Sequence[QueryItem], total: int, seed: int) -> list[QueryItem]:
    """Balanced selection: per area, concrete statements first (random order), then abstract.

    A candidate is skipped if it is a gold lemma of an already selected query or one of its
    gold lemmas is already a query, so no gold lemma is removed from the evaluation index.
    """
    rng = random.Random(seed)
    targets = _target_per_area(total)
    chosen: list[QueryItem] = []
    queries: set[str] = set()
    golds: set[str] = set()
    for area in PROJECT_AREAS:
        pool = [q for q in candidates if q.area == area]
        concrete = [q for q in pool if q.concrete]
        abstract = [q for q in pool if not q.concrete]
        rng.shuffle(concrete)
        rng.shuffle(abstract)
        taken = 0
        for q in concrete + abstract:
            if taken == targets[area]:
                break
            if q.name in golds or queries & set(q.gold):
                continue
            chosen.append(q)
            queries.add(q.name)
            golds |= set(q.gold)
            taken += 1
    return chosen


def split(items: Sequence[QueryItem], seed: int, dev_frac: float = 2 / 3) -> list[QueryItem]:
    """Stratified dev/test split by area (``round(dev_frac · n_area)`` go to dev)."""
    rng = random.Random(seed)
    out: list[QueryItem] = []
    for area in PROJECT_AREAS:
        group = sorted((q for q in items if q.area == area), key=lambda q: q.name)
        rng.shuffle(group)
        n_dev = round(dev_frac * len(group))
        out += [QueryItem(**{**asdict(q), "split": "dev" if i < n_dev else "test"}) for i, q in enumerate(group)]
    return out


def summary(items: Sequence[QueryItem]) -> dict:
    """Counts by split × area × concrete/abstract and gold-set sizes."""
    cells = Counter((q.split, q.area, "concrete" if q.concrete else "abstract") for q in items)
    n = len(items)
    return {
        "n": n,
        "by_split": dict(Counter(q.split for q in items)),
        "by_area": dict(Counter(q.area for q in items)),
        "by_type": dict(Counter("concrete" if q.concrete else "abstract" for q in items)),
        "cells": {"/".join(k): v for k, v in sorted(cells.items())},
        "mean_gold": sum(len(q.gold) for q in items) / n if n else 0.0,
        "mean_gold_b": sum(len(q.gold_b) for q in items) / n if n else 0.0,
    }
