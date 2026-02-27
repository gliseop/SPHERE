"""Поток памяти агента по модели Park et al. (2023)."""

from __future__ import annotations

import math
import uuid
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .bm25 import BM25Like, build_bm25

if TYPE_CHECKING:
    from .llm import LLMProvider


MemoryKind = Literal["observation", "reflection", "plan", "summary"]

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

    def __init__(self, agent_id: str, *, max_records: int = 500) -> None:
        self.agent_id = agent_id
        self.records: list[MemoryRecord] = []
        self.importance_since_reflection: float = 0.0
        self.max_records = max_records
        self._bm25_corpus: list[list[str]] = []
        self._bm25: BM25Like | None = None
        self._bm25_dirty: bool = False

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
        self._bm25_dirty = True
        if kind == "observation":
            self.importance_since_reflection += importance
        self._enforce_cap()
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

    def _rebuild_bm25_if_dirty(self) -> None:
        """Перестроить BM25-индекс при наличии грязного флага."""
        if self._bm25_dirty and self._bm25_corpus:
            self._bm25 = build_bm25(self._bm25_corpus)
            self._bm25_dirty = False

    def bm25_scores(self, query: str) -> list[float]:
        """Вычисляет BM25-скоры для всех записей по запросу.

        Args:
            query: Текстовый запрос.

        Returns:
            Список скоров, соответствующих порядку self.records.
        """
        self._rebuild_bm25_if_dirty()
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
        self._rebuild_bm25_if_dirty()

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

    def _enforce_cap(self) -> None:
        """Вытеснить самые старые observation-записи при превышении лимита.

        Удаляет по одной observation-записи (самая старая), пока количество
        записей не станет <= max_records.
        """
        while len(self.records) > self.max_records:
            idx_to_remove: int | None = None
            for i, rec in enumerate(self.records):
                if rec.kind == "observation":
                    idx_to_remove = i
                    break
            if idx_to_remove is None:
                # Нет observation — удаляем самую старую запись любого типа.
                idx_to_remove = 0
            self.records.pop(idx_to_remove)
            self._bm25_corpus.pop(idx_to_remove)
            self._bm25_dirty = True

    def summarize_old(
        self,
        llm: "LLMProvider",
        *,
        threshold: int = 100,
        batch_size: int = 50,
    ) -> int:
        """Суммаризировать старые записи через LLM.

        Если количество записей превышает threshold, берёт batch_size
        самых старых observation-записей, сжимает их в 3-5 ключевых
        фактов через LLM и заменяет оригиналы summary-записями.

        Args:
            llm: Провайдер языковой модели.
            threshold: Минимальное количество записей для запуска суммаризации.
            batch_size: Количество старых записей для сжатия за один вызов.

        Returns:
            Количество удалённых записей (0 если суммаризация не нужна).
        """
        if len(self.records) < threshold:
            return 0

        # Собираем самые старые observation-записи
        obs_indices: list[int] = []
        for i, rec in enumerate(self.records):
            if rec.kind == "observation":
                obs_indices.append(i)
                if len(obs_indices) >= batch_size:
                    break

        if len(obs_indices) < 5:
            return 0

        # Формируем текст для суммаризации
        texts = [self.records[i].content for i in obs_indices]
        combined = "\n".join(f"- {t}" for t in texts)

        prompt = (
            f"Сожми следующие {len(texts)} воспоминаний в 3-5 ключевых фактов. "
            f"Каждый факт — одно предложение. Верни только список фактов, "
            f"каждый факт на отдельной строке.\n\n{combined}"
        )

        try:
            response = llm.generate(system="", user=prompt)
            summary_text = response.text.strip()
        except Exception:
            return 0

        if not summary_text:
            return 0

        # Определяем round_num для summary-записей (от самой старой)
        round_num = self.records[obs_indices[0]].created_at

        # Парсим факты (каждая непустая строка — отдельный факт)
        facts = [
            line.lstrip("- ").strip()
            for line in summary_text.split("\n")
            if line.strip()
        ]

        # Удаляем оригиналы (в обратном порядке чтобы индексы не сдвигались)
        removed = 0
        for i in reversed(obs_indices):
            self.records.pop(i)
            self._bm25_corpus.pop(i)
            removed += 1

        self._bm25_dirty = True

        # Добавляем summary-записи
        for fact in facts[:5]:
            self.add(
                content=fact,
                importance=7.0,
                kind="summary",
                round_num=round_num,
            )

        return removed
