"""Точка интеграции MAGISTRY-LC с `magistry_sim`.

MAGISTRY-LC — greenfield-ветка, но в репозитории уже есть проверенные реализации:
- BM25 и токенизация;
- протоколы LLM/embeddings и провайдеры.

Чтобы зависимость была явной и легко заменяемой, импортируйте внешние примитивы
только через этот модуль.
"""

from __future__ import annotations

from magistry_sim.bm25 import BM25Like, build_bm25
from magistry_sim.llm.embeddings import EmbeddingProvider, create_embedding_provider
from magistry_sim.llm.protocols import LLMProvider, LLMResponse, StructuredLLMResponse
from magistry_sim.llm.providers import OpenAICompatibleProvider

__all__ = [
    "BM25Like",
    "EmbeddingProvider",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatibleProvider",
    "StructuredLLMResponse",
    "build_bm25",
    "create_embedding_provider",
]

