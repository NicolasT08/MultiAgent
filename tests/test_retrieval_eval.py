"""Offline tests for retrieval metrics, rank fusion and BM25 (toy corpus)."""

from __future__ import annotations

import pytest

from formal_mas.retrieval.bm25 import BM25, tokenize
from formal_mas.retrieval.evaluate import evaluate, first_hit_rank, mrr, recall_at_k, rrf


def test_first_hit_rank():
    assert first_hit_rank(["a", "b", "c"], {"c", "b"}) == 2
    assert first_hit_rank(["a"], {"z"}) is None


def test_recall_and_mrr():
    ranks = [1, 3, None, 5]
    assert recall_at_k(ranks, 1) == 0.25
    assert recall_at_k(ranks, 4) == 0.5
    assert recall_at_k(ranks, 8) == 0.75
    assert mrr(ranks) == pytest.approx((1 + 1 / 3 + 1 / 5) / 4)
    assert recall_at_k([], 4) == 0.0 and mrr([]) == 0.0


def test_evaluate_keys():
    out = evaluate([["a", "b"], ["c", "d"]], [{"b"}, {"x"}], ks=[1, 2])
    assert out == {"R@1": 0.0, "R@2": 0.5, "MRR": 0.25}


def test_rrf_prefers_items_ranked_high_in_both_lists():
    fused = rrf([["a", "b", "c"], ["b", "a", "d"]], depth=4)
    assert set(fused[:2]) == {"a", "b"}
    assert fused[2:] == ["c", "d"]
    assert rrf([["a", "b"]], depth=1) == ["a"]


def test_tokenize_splits_names_and_keeps_symbols():
    toks = tokenize("Nat.Prime.two_le ∥ {p : ℕ} (hp : p.Prime) : 2 ≤ p ∥ IsCoprime")
    assert {"nat", "prime", "two", "le", "ℕ", "≤", "2", "is", "coprime"} <= set(toks)
    assert "∥" not in toks and ":" not in toks


def test_bm25_ranks_lexical_match_first():
    docs = [
        "Nat.add_comm ∥ (a b : ℕ) : a + b = b + a",
        "Nat.Prime.two_le ∥ {p : ℕ} (hp : p.Prime) : 2 ≤ p",
        "Finset.card_range ∥ (n : ℕ) : (Finset.range n).card = n",
    ]
    bm = BM25(docs)
    assert bm.top("(p : ℕ) (h : Nat.Prime p) : 2 ≤ p", 3)[0][0] == 1
    assert bm.top("card of range", 1)[0][0] == 2
    assert bm.scores("zzz unseen").max() == 0.0
