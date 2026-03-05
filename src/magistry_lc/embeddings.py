"""Асинхронные хелперы для эмбеддингов (batch + cache).

`EmbeddingProvider` синхронный и может делать HTTP-запросы.
В MAGISTRY-LC все вызовы эмбеддингов выполняются через `asyncio.to_thread`,
а также по возможности батчатся и кешируются по тексту.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import TypeVar

from .llm import EmbeddingProvider


T = TypeVar("T")

logger = logging.getLogger(__name__)
_EMBED_WARN_LIMIT = 3
_embed_warn_count = 0


def _warn_embeddings_once(message: str, *args: object) -> None:
    global _embed_warn_count
    if _embed_warn_count >= _EMBED_WARN_LIMIT:
        return
    _embed_warn_count += 1
    logger.warning(message, *args)
    if _embed_warn_count == _EMBED_WARN_LIMIT:
        logger.warning("Further embeddings warnings suppressed.")


def _chunks(items: list[T], size: int) -> Iterable[list[T]]:
    if size <= 0:
        yield items
        return
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def embed_texts(
    embedder: EmbeddingProvider, texts: list[str], *, batch_size: int
) -> list[list[float]]:
    """Получить эмбеддинги для списка текстов (батчами, не блокируя event loop)."""
    if not texts:
        return []

    out: list[list[float]] = []
    for batch in _chunks(texts, batch_size):
        try:
            vecs = await asyncio.to_thread(embedder.embed_batch, list(batch))
        except Exception as exc:
            total_chars = sum(len(t) for t in batch)
            _warn_embeddings_once(
                "Embeddings batch failed (%s): texts=%d chars=%d",
                exc.__class__.__name__,
                len(batch),
                total_chars,
            )
            vecs = []

        if len(vecs) != len(batch):
            _warn_embeddings_once(
                "Embeddings provider returned mismatched batch size: expected=%d got=%d",
                len(batch),
                len(vecs),
            )
            out.extend([[] for _ in batch])
            continue
        out.extend([list(v) for v in vecs])
    return out


async def embed_texts_cached(
    embedder: EmbeddingProvider,
    texts: list[str],
    *,
    cache: dict[str, list[float]],
    batch_size: int,
) -> list[list[float]]:
    """Как `embed_texts`, но с кешированием по точному тексту."""
    if not texts:
        return []

    missing: list[str] = []
    seen_missing: set[str] = set()
    for t in texts:
        if t in cache or t in seen_missing:
            continue
        seen_missing.add(t)
        missing.append(t)

    if missing:
        vecs = await embed_texts(embedder, missing, batch_size=batch_size)
        for t, v in zip(missing, vecs, strict=False):
            cache[t] = list(v)

    return [cache.get(t, []) for t in texts]
