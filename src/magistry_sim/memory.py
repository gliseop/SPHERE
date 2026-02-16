"""Поток памяти агента по модели Park et al. (2023)."""

from __future__ import annotations

import math
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from rank_bm25 import BM25L


MemoryKind = Literal["observation", "reflection", "plan"]

# Коэффициенты гибридной формулы (расширение Park et al., 2023)
RECENCY_WEIGHT = 0.5
COSINE_WEIGHT = 2.0
BM25_WEIGHT = 1.5
IMPORTANCE_WEIGHT = 2.0
RECENCY_DECAY = 0.995


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Косинусное сходство двух векторов.

    Args:
        a: Первый вектор.
        b: Второй вектор.

    Returns:
        Значение косинусного сходства от -1 до 1 или 0 при невалидных входах.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemoryRecord(BaseModel, extra="forbid"):
    """Единица памяти агента.

    Attributes:
        id: Уникальный идентификатор записи.
        created_at: Номер раунда создания.
        content: Текстовое содержание воспоминания.
        importance: Оценка важности (1.0-10.0).
        kind: Тип записи (наблюдение, рефлексия, план).
        embedding: Вектор эмбеддинга для семантического поиска.
        evidence: Идентификаторы записей-обоснований (для рефлексий).
    """

    id: str
    created_at: int
    content: str
    importance: float = Field(ge=1.0, le=10.0)
    kind: MemoryKind
    embedding: list[float] = []
    evidence: list[str] = []


class MemoryStream:
    """Персональный поток памяти агента.

    Хранит упорядоченную последовательность наблюдений, рефлексий
    и планов. Отслеживает накопленную важность для определения
    момента запуска рефлексии.

    Attributes:
        agent_id: Идентификатор агента-владельца потока.
        records: Упорядоченный список записей памяти.
        importance_since_reflection: Накопленная важность с последней рефлексии.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.records: list[MemoryRecord] = []
        self.importance_since_reflection: float = 0.0
        self._bm25_corpus: list[list[str]] = []
        self._bm25: BM25L | None = None

    def __len__(self) -> int:
        return len(self.records)

    def add(
        self,
        content: str,
        importance: float,
        kind: MemoryKind,
        round_num: int,
        embedding: list[float] | None = None,
        evidence: list[str] | None = None,
    ) -> MemoryRecord:
        """Добавляет запись в поток памяти.

        Args:
            content: Текстовое содержание воспоминания.
            importance: Оценка важности (1.0-10.0).
            kind: Тип записи.
            round_num: Номер текущего раунда.
            embedding: Вектор эмбеддинга.
            evidence: Идентификаторы записей-обоснований.

        Returns:
            Созданная запись памяти.
        """
        record = MemoryRecord(
            id=f"mem_{self.agent_id}_{uuid.uuid4().hex[:8]}",
            created_at=round_num,
            content=content,
            importance=importance,
            kind=kind,
            embedding=embedding or [],
            evidence=evidence or [],
        )
        self.records.append(record)
        tokens = content.lower().split()
        self._bm25_corpus.append(tokens)
        self._bm25 = BM25L(self._bm25_corpus)
        if kind == "observation":
            self.importance_since_reflection += importance
        return record

    def get_recent(self, n: int = 20) -> list[MemoryRecord]:
        """Возвращает последние n записей (от новых к старым).

        Args:
            n: Количество записей.

        Returns:
            Список записей в обратном хронологическом порядке.
        """
        return list(reversed(self.records[-n:]))

    def get_by_kind(self, kind: MemoryKind) -> list[MemoryRecord]:
        """Возвращает записи указанного типа.

        Args:
            kind: Тип записи для фильтрации.

        Returns:
            Список записей данного типа.
        """
        return [r for r in self.records if r.kind == kind]

    def get_by_round(self, round_num: int) -> list[MemoryRecord]:
        """Возвращает записи, созданные в указанном раунде.

        Args:
            round_num: Номер раунда.

        Returns:
            Список записей из указанного раунда.
        """
        return [r for r in self.records if r.created_at == round_num]

    def bm25_scores(self, query: str) -> list[float]:
        """Вычисляет BM25-скоры для всех записей по запросу.

        Args:
            query: Текстовый запрос.

        Returns:
            Список скоров, соответствующих порядку self.records.
        """
        if self._bm25 is None or not self._bm25_corpus:
            return []
        tokens = query.lower().split()
        return list(self._bm25.get_scores(tokens))

    def retrieve(
        self,
        query_embedding: list[float],
        current_round: int,
        top_k: int = 20,
        query_text: str | None = None,
    ) -> list[MemoryRecord]:
        """Извлекает наиболее релевантные записи гибридным поиском.

        Формула: score = alpha*recency + beta*cosine + gamma*bm25 + delta*importance.
        Если query_text не передан, BM25-компонент равен нулю.

        Args:
            query_embedding: Вектор запроса для семантического поиска.
            current_round: Номер текущего раунда (для расчёта давности).
            top_k: Максимальное количество возвращаемых записей.
            query_text: Текст запроса для BM25-поиска.

        Returns:
            Список записей, отсортированных по убыванию оценки.
        """
        candidates = [r for r in self.records if r.embedding]
        if not candidates:
            return []

        # BM25-скоры для всех записей
        bm25_raw: list[float] = []
        if query_text and self._bm25 is not None:
            all_scores = list(self._bm25.get_scores(query_text.lower().split()))
            indices_with_emb = [
                i for i, r in enumerate(self.records) if r.embedding
            ]
            bm25_raw = [all_scores[i] for i in indices_with_emb]
        else:
            bm25_raw = [0.0] * len(candidates)

        # Нормализация BM25 к [0, 1] через min-max
        bm25_max = max(bm25_raw) if bm25_raw else 0.0
        bm25_min = min(bm25_raw) if bm25_raw else 0.0
        bm25_range = bm25_max - bm25_min
        if bm25_range > 0:
            bm25_norm = [(s - bm25_min) / bm25_range for s in bm25_raw]
        else:
            bm25_norm = [0.0] * len(bm25_raw)

        scored: list[tuple[float, MemoryRecord]] = []
        for idx, rec in enumerate(candidates):
            rounds_ago = current_round - rec.created_at
            recency = RECENCY_DECAY ** rounds_ago
            cosine = _cosine_similarity(query_embedding, rec.embedding)
            cosine_norm = (cosine + 1.0) / 2.0
            importance = rec.importance / 10.0
            bm25 = bm25_norm[idx] if idx < len(bm25_norm) else 0.0

            score = (
                RECENCY_WEIGHT * recency
                + COSINE_WEIGHT * cosine_norm
                + BM25_WEIGHT * bm25
                + IMPORTANCE_WEIGHT * importance
            )
            scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rec for _, rec in scored[:top_k]]

    def reset_importance_accumulator(self) -> None:
        """Сбрасывает счётчик важности после рефлексии."""
        self.importance_since_reflection = 0.0
