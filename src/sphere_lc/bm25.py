"""BM25 utilities.

Проект использует BM25 для лексического компонента retrieval.
Библиотека ``rank_bm25`` является опциональной (extras), поэтому здесь
есть лёгкая встроенная реализация, совместимая по интерфейсу ``get_scores``.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol


class BM25Like(Protocol):
    """Minimal BM25 interface used across the codebase."""

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        """Return a score per document in the corpus."""


class FallbackBM25:
    """Small BM25-Okapi implementation (no external dependencies)."""

    def __init__(
        self,
        corpus: list[list[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._corpus = corpus
        self._k1 = float(k1)
        self._b = float(b)

        self._doc_lens = [len(doc) for doc in corpus]
        self._avgdl = (
            (sum(self._doc_lens) / len(self._doc_lens))
            if self._doc_lens
            else 0.0
        )

        self._tfs: list[Counter[str]] = [
            Counter(doc) for doc in corpus
        ]

        df: Counter[str] = Counter()
        for doc in corpus:
            df.update(set(doc))
        self._df = df
        self._N = len(corpus)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        if df <= 0 or self._N <= 0:
            return 0.0
        return math.log(1.0 + (self._N - df + 0.5) / (df + 0.5))

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        if not self._corpus:
            return []
        if not query_tokens:
            return [0.0] * len(self._corpus)

        scores = [0.0] * len(self._corpus)
        for i, tf in enumerate(self._tfs):
            dl = self._doc_lens[i]
            norm = 1.0
            if self._avgdl > 0:
                norm = 1.0 - self._b + self._b * (dl / self._avgdl)
            denom_base = self._k1 * norm

            score = 0.0
            for term in query_tokens:
                freq = tf.get(term, 0)
                if freq <= 0:
                    continue
                idf = self._idf(term)
                score += idf * (freq * (self._k1 + 1.0)) / (freq + denom_base)
            scores[i] = float(score)
        return scores


try:  # pragma: no cover
    from rank_bm25 import BM25L as _RankBM25L  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    _RankBM25L = None  # type: ignore


def build_bm25(corpus: list[list[str]]) -> BM25Like:
    """Create a BM25 index.

    Uses ``rank_bm25.BM25L`` when available, otherwise falls back to a small
    local BM25 implementation.
    """
    if _RankBM25L is not None:
        return _RankBM25L(corpus)
    return FallbackBM25(corpus)

