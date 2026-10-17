"""Retrieval metrics (Eq. 7) and rank fusion.

Recall@k is the share of queries with at least one gold lemma in the top-k; MRR is the
mean reciprocal rank of the first gold lemma (0 if none is retrieved within the list).
"""

from __future__ import annotations

from typing import Iterable, Sequence

RRF_K = 60


def first_hit_rank(ranked: Sequence[str], gold: Iterable[str]) -> int | None:
    """1-based rank of the first gold name in `ranked`, or None."""
    gold = set(gold)
    for i, name in enumerate(ranked, start=1):
        if name in gold:
            return i
    return None


def recall_at_k(ranks: Sequence[int | None], k: int) -> float:
    return sum(r is not None and r <= k for r in ranks) / len(ranks) if ranks else 0.0


def mrr(ranks: Sequence[int | None]) -> float:
    return sum(1 / r for r in ranks if r is not None) / len(ranks) if ranks else 0.0


def evaluate(rankings: Sequence[Sequence[str]], golds: Sequence[Iterable[str]], ks: Sequence[int]) -> dict:
    """Recall@k for each k and MRR over the full ranked lists."""
    ranks = [first_hit_rank(r, g) for r, g in zip(rankings, golds, strict=True)]
    return {**{f"R@{k}": recall_at_k(ranks, k) for k in ks}, "MRR": mrr(ranks)}


def rrf(rankings: Sequence[Sequence[str]], depth: int, k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion: score(d) = sum over lists of 1 / (k + rank(d))."""
    scores: dict[str, float] = {}
    for ranked in rankings:
        for i, name in enumerate(ranked, start=1):
            scores[name] = scores.get(name, 0.0) + 1 / (k + i)
    return sorted(scores, key=lambda n: (-scores[n], n))[:depth]
