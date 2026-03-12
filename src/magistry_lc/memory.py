"""Память агента (working buffer + hybrid long-term retrieval).

Цель: контролировать рост контекста и обеспечить обратную связь.
Архитектура соответствует плану greenfield:
- "рабочая память" хранит последние события дословно и суммаризирует старые;
- "долгосрочная память" — гибридный индекс embeddings + BM25 с дедупом.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .config import MemoryConfig
from .bm25 import BM25Like, build_bm25
from .llm import LLMCaller


MemoryKind = Literal[
    "persona",
    "interview",
    "summary",
    "observation",
    "result",
    "reflection",
]


_WS_RE = re.compile(r"\s+")


def _norm_text(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-zА-Яа-я0-9_]+", text.lower()) if t]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(slots=True)
class WorkingEntry:
    tick: int
    text: str


@dataclass(slots=True)
class MemoryDoc:
    doc_id: str
    created_tick: int
    last_seen_tick: int
    kind: MemoryKind
    importance: float
    text: str
    embedding: list[float] = field(default_factory=list)
    repeats: int = 1
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentMemory:
    """Память конкретного агента."""

    agent_id: str
    summary: str = ""
    working: list[WorkingEntry] = field(default_factory=list)
    docs: list[MemoryDoc] = field(default_factory=list)

    doc_counter: int = 0
    bm25_corpus: list[list[str]] = field(default_factory=list)
    bm25: BM25Like | None = None
    bm25_dirty: bool = False

    def add_working(self, *, tick: int, text: str) -> None:
        text = _norm_text(text)
        if not text:
            return
        self.working.append(WorkingEntry(tick=tick, text=text))

    def add_doc(
        self,
        *,
        tick: int,
        kind: MemoryKind,
        importance: float,
        text: str,
        cfg: MemoryConfig,
        embedding: list[float] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        text = _norm_text(text)
        if not text:
            return

        emb = list(embedding or [])

        # Дедуп: если semantic-слишком похоже на уже существующую запись — не добавляем новую.
        if emb and self.docs:
            best_sim = 0.0
            best_idx: int | None = None
            for i, d in enumerate(self.docs):
                if not d.embedding:
                    continue
                sim = _cosine_similarity(emb, d.embedding)
                if sim > best_sim:
                    best_sim = sim
                    best_idx = i
            if best_idx is not None and best_sim >= cfg.dedup_cosine_threshold:
                d = self.docs[best_idx]
                d.last_seen_tick = tick
                d.repeats += 1
                if importance > d.importance:
                    d.importance = importance
                return

        self.doc_counter += 1
        doc_id = f"mem:{self.agent_id}:{self.doc_counter}"
        doc = MemoryDoc(
            doc_id=doc_id,
            created_tick=tick,
            last_seen_tick=tick,
            kind=kind,
            importance=float(importance),
            text=text,
            embedding=emb,
            meta=dict(meta or {}),
        )
        self.docs.append(doc)
        self.bm25_corpus.append(_tokenize(text))
        self.bm25_dirty = True
        self._enforce_caps(cfg)

    def _enforce_caps(self, cfg: MemoryConfig) -> None:
        while len(self.docs) > cfg.long_term_max_docs:
            # Удаляем наименее важную и наиболее старую запись.
            idx = min(
                range(len(self.docs)),
                key=lambda i: (self.docs[i].importance, self.docs[i].last_seen_tick),
            )
            self.docs.pop(idx)
            self.bm25_corpus.pop(idx)
            self.bm25_dirty = True

    def _rebuild_bm25_if_dirty(self) -> None:
        if self.bm25_dirty and self.bm25_corpus:
            self.bm25 = build_bm25(self.bm25_corpus)
            self.bm25_dirty = False

    async def maybe_summarize_working(
        self,
        *,
        llm: LLMCaller,
        language: str,
        cfg: MemoryConfig,
        tick: int,
        temperature: float,
    ) -> None:
        """Суммаризировать старую часть working buffer в `summary`."""
        if len(self.working) <= cfg.working_max_entries:
            return

        batch_size = min(cfg.working_summarize_batch, len(self.working))
        if batch_size <= 0:
            return
        batch = self.working[:batch_size]

        prev = self.summary.strip()
        lines = "\n".join(f"- (t{e.tick}) {e.text}" for e in batch)
        user = (
            "Обнови сводку рабочей памяти агента.\n"
            "Требования:\n"
            "- Пиши кратко: 8–15 пунктов.\n"
            "- Только факты/решения/обязательства, без художественности.\n"
            "- Не добавляй новых сущностей/ID.\n\n"
            f"Язык: {language!r}\n\n"
            f"Текущая сводка:\n{prev or '(пусто)'}\n\n"
            f"Новые записи для сжатия:\n{lines}\n"
        )
        resp = await llm.generate(
            role="memory",
            name=self.agent_id,
            tick=tick,
            system="Ты — модуль суммаризации памяти агента.",
            user=user,
            temperature=temperature,
        )
        new_summary = _norm_text(resp.text)
        self.working = self.working[batch_size:]
        if new_summary:
            self.summary = new_summary

    def retrieve(
        self,
        *,
        query_text: str,
        query_embedding: list[float] | None,
        tick: int,
        cfg: MemoryConfig,
        allowed_kinds: set[MemoryKind] | None = None,
        top_k: int | None = None,
    ) -> list[MemoryDoc]:
        """Достать top-k документов по гибридному скорингу."""
        docs = [doc for doc in self.docs if allowed_kinds is None or doc.kind in allowed_kinds]
        if not docs:
            return []

        query_text = _norm_text(query_text)
        q_emb = list(query_embedding or [])

        self._rebuild_bm25_if_dirty()
        bm25_raw = []
        if self.bm25 is not None and query_text:
            full_scores = list(self.bm25.get_scores(_tokenize(query_text)))
            if allowed_kinds is None:
                bm25_raw = full_scores
            else:
                bm25_raw = [
                    score
                    for score, doc in zip(full_scores, self.docs, strict=False)
                    if doc.kind in allowed_kinds
                ]
        else:
            bm25_raw = [0.0] * len(docs)

        # Нормализация BM25 в [0, 1] через min-max.
        bm25_max = max(bm25_raw) if bm25_raw else 0.0
        bm25_min = min(bm25_raw) if bm25_raw else 0.0
        bm25_range = bm25_max - bm25_min
        if bm25_range > 0:
            bm25_norm = [(s - bm25_min) / bm25_range for s in bm25_raw]
        else:
            bm25_norm = [0.0] * len(self.docs)

        w = cfg.weights
        scored: list[tuple[float, MemoryDoc]] = []
        for i, doc in enumerate(docs):
            # Recency: экспоненциальный decay по "последнему появлению".
            age = max(0, tick - doc.last_seen_tick)
            recency = cfg.recency_decay ** age

            cosine = 0.0
            if q_emb and doc.embedding:
                cosine = _cosine_similarity(q_emb, doc.embedding)
            cosine_norm = (cosine + 1.0) / 2.0

            importance_norm = min(1.0, max(0.0, doc.importance) / 10.0)
            bm25 = bm25_norm[i] if i < len(bm25_norm) else 0.0

            score = (
                w.recency * recency
                + w.vector * cosine_norm
                + w.bm25 * bm25
                + w.importance * importance_norm
            )
            scored.append((float(score), doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        limit = cfg.retrieval_top_k if top_k is None else max(0, int(top_k))
        return [d for _, d in scored[:limit]]
