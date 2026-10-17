"""Okapi BM25 over Lean declaration texts (lexical baseline, no embeddings).

Tokenisation splits qualified names on `.`/`_` and camelCase (`Nat.Prime.two_le` →
nat, prime, two, le) and keeps every mathematical symbol (`ℕ`, `≤`, `∣`, …) as a token.
The index is a sparse term-document matrix, so scoring a query is one sparse product.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Sequence

import numpy as np
from scipy import sparse

_WORD_RE = re.compile(r"[^\W\d_]+|\d+|[^\w\s(){}\[\],:∥]")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[^A-Za-z]+")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in _WORD_RE.findall(text):
        if tok.isascii() and tok.isalpha():
            out.extend(p.lower() for p in _CAMEL_RE.findall(tok))
        else:
            out.append(tok.lower())
    return out


class BM25:
    def __init__(self, texts: Sequence[str], k1: float = 1.2, b: float = 0.75):
        self.vocab: dict[str, int] = {}
        rows, cols, vals = [], [], []
        lengths = np.empty(len(texts), dtype=np.float32)
        for i, text in enumerate(texts):
            counts = Counter(tokenize(text))
            lengths[i] = sum(counts.values())
            for tok, c in counts.items():
                rows.append(i)
                cols.append(self.vocab.setdefault(tok, len(self.vocab)))
                vals.append(c)
        tf = sparse.csr_matrix((np.asarray(vals, np.float32), (rows, cols)), shape=(len(texts), len(self.vocab)))
        df = np.bincount(tf.indices, minlength=len(self.vocab))
        n = len(texts)
        self.idf = np.log1p((n - df + 0.5) / (df + 0.5)).astype(np.float32)
        norm = k1 * (1 - b + b * lengths / max(lengths.mean(), 1e-9))
        # Saturated tf, row by row: tf * (k1 + 1) / (tf + norm_i).
        row_norm = np.repeat(norm, np.diff(tf.indptr))
        tf.data = tf.data * (k1 + 1) / (tf.data + row_norm)
        self.matrix = tf.tocsc()

    def scores(self, query: str) -> np.ndarray:
        ids = [self.vocab[t] for t in set(tokenize(query)) if t in self.vocab]
        if not ids:
            return np.zeros(self.matrix.shape[0], dtype=np.float32)
        return np.asarray(self.matrix[:, ids] @ self.idf[ids]).ravel()

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        s = self.scores(query)
        k = min(k, len(s))
        idx = np.argpartition(-s, k - 1)[:k]
        idx = idx[np.argsort(-s[idx], kind="stable")]
        return [(int(i), float(s[i])) for i in idx]
