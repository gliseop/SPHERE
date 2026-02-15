"""Поток памяти агента по модели Park et al. (2023)."""

from __future__ import annotations

import math
import uuid
from typing import Literal

from pydantic import BaseModel, Field


MemoryKind = Literal["observation", "reflection", "plan"]

# Коэффициенты формулы Park et al. (2023)
RECENCY_WEIGHT = 0.5
RELEVANCE_WEIGHT = 3.0
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

    def retrieve(
        self,
        query_embedding: list[float],
        current_round: int,
        top_k: int = 20,
    ) -> list[MemoryRecord]:
        """Извлекает наиболее релевантные записи из потока памяти.

        Формула оценки: score = alpha*recency + beta*relevance + gamma*importance
        (Park et al., 2023).

        Args:
            query_embedding: Вектор запроса для семантического поиска.
            current_round: Номер текущего раунда (для расчёта давности).
            top_k: Максимальное количество возвращаемых записей.

        Returns:
            Список записей, отсортированных по убыванию оценки.
        """
        candidates = [r for r in self.records if r.embedding]
        if not candidates:
            return []

        scored: list[tuple[float, MemoryRecord]] = []
        for rec in candidates:
            rounds_ago = current_round - rec.created_at
            recency = RECENCY_DECAY ** rounds_ago
            relevance = _cosine_similarity(query_embedding, rec.embedding)
            importance = rec.importance / 10.0

            score = (
                RECENCY_WEIGHT * recency
                + RELEVANCE_WEIGHT * relevance
                + IMPORTANCE_WEIGHT * importance
            )
            scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rec for _, rec in scored[:top_k]]

    def reset_importance_accumulator(self) -> None:
        """Сбрасывает счётчик важности после рефлексии."""
        self.importance_since_reflection = 0.0
