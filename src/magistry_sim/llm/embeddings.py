"""Провайдеры эмбеддингов: протокол, mock, локальный, OpenAI."""

from __future__ import annotations

import hashlib
import math
import re
import threading
import zlib
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Протокол для провайдеров эмбеддингов."""

    def embed(self, text: str) -> list[float]:
        """Получить эмбеддинг текста.

        Args:
            text: Входной текст.

        Returns:
            Вектор эмбеддинга.
        """
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов.
        """
        ...


class MockEmbeddingProvider:
    """Детерминированный провайдер эмбеддингов для тестов.

    Генерирует воспроизводимые векторы на основе хеша текста.

    Attributes:
        dimensions: Размерность выходного вектора.
    """

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        """Получить детерминированный эмбеддинг текста.

        Args:
            text: Входной текст.

        Returns:
            Вектор фиксированной размерности.
        """
        h = hashlib.sha256(text.encode()).digest()
        raw = [b / 255.0 for b in h]
        while len(raw) < self.dimensions:
            raw.extend(raw)
        return raw[: self.dimensions]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов.
        """
        return [self.embed(t) for t in texts]


class LocalEmbeddingProvider:
    """Локальный провайдер эмбеддингов на базе sentence-transformers.

    Использует мультиязычную модель paraphrase-multilingual-MiniLM-L12-v2
    (384 измерения). Модель загружается лениво при первом вызове embed().

    Attributes:
        _model_name: Имя модели HuggingFace.
        _model: Экземпляр SentenceTransformer (None до первого вызова).
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ) -> None:
        self.dimensions = 384
        self._model_name = model_name
        self._model = None
        self._use_fallback = False

    def _ensure_model(self) -> None:
        """Загрузить модель при первом обращении."""
        if self._model is not None or self._use_fallback:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError:
            self._use_fallback = True
            return
        self._model = SentenceTransformer(self._model_name)

    def _fallback_embed(self, text: str) -> list[float]:
        """Быстрый детерминированный эмбеддинг без внешних зависимостей.

        Использует хешированную смесь word-tokens и char-ngram признаков,
        нормализуя вектор до единичной длины (cosine-ready).
        """
        dims = self.dimensions
        vec = [0.0] * dims
        t = (text or "").lower()

        # Word-level features (больше вес, чем у n-gram)
        for token in re.findall(r"[0-9a-zа-яё]+", t):
            idx = zlib.crc32(token.encode("utf-8")) % dims
            vec[idx] += 2.0

        # Character n-grams (устойчивы к морфологии/опечаткам)
        cleaned = re.sub(r"\s+", " ", t).strip()
        padded = f" {cleaned} "
        for n in (3, 4, 5):
            if len(padded) < n:
                continue
            for i in range(len(padded) - n + 1):
                gram = padded[i : i + n]
                idx = zlib.crc32(gram.encode("utf-8")) % dims
                vec[idx] += 1.0

        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]

    def embed(self, text: str) -> list[float]:
        """Получить эмбеддинг текста.

        Args:
            text: Входной текст.

        Returns:
            Вектор эмбеддинга (384 измерения).
        """
        self._ensure_model()
        if self._use_fallback or self._model is None:
            return self._fallback_embed(text)
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов.
        """
        self._ensure_model()
        if self._use_fallback or self._model is None:
            return [self._fallback_embed(t) for t in texts]
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


class OpenAIEmbeddingProvider:
    """Провайдер эмбеддингов через OpenAI-совместимый API.

    Attributes:
        _client: Клиент OpenAI API.
        _model: Имя модели эмбеддингов.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        self._model = model
        self._openai_cls = OpenAI
        self._client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
        }
        self._client_local = threading.local()

    def _get_client(self) -> Any:
        client = getattr(self._client_local, "client", None)
        if client is None:
            client = self._openai_cls(**self._client_kwargs)
            self._client_local.client = client
        return client

    def embed(self, text: str) -> list[float]:
        """Получить эмбеддинг текста через API.

        Args:
            text: Входной текст.

        Returns:
            Вектор эмбеддинга.
        """
        resp = self._get_client().embeddings.create(input=[text], model=self._model)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов через API.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов, упорядоченных по индексу.
        """
        resp = self._get_client().embeddings.create(input=texts, model=self._model)
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]


def create_embedding_provider(
    mock: bool = True,
    provider: str = "local",
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> EmbeddingProvider:
    """Фабрика провайдеров эмбеддингов.

    Args:
        mock: Использовать mock-провайдер.
        provider: Тип провайдера: "local" (sentence-transformers)
            или "openai" (OpenAI-совместимый API, включая OpenRouter).
        model_name: Имя модели (зависит от провайдера).
        api_key: API-ключ (для openai-провайдера).
        base_url: Базовый URL API (для OpenRouter и аналогов).

    Returns:
        Экземпляр провайдера эмбеддингов.
    """
    if mock:
        return MockEmbeddingProvider(dimensions=384)

    if provider == "openai":
        import os

        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        resolved_url = base_url or os.getenv("OPENAI_BASE_URL")
        resolved_model = model_name or os.getenv(
            "EMBEDDING_MODEL", "text-embedding-3-small"
        )
        return OpenAIEmbeddingProvider(
            model=resolved_model,
            api_key=resolved_key,
            base_url=resolved_url,
        )

    return LocalEmbeddingProvider(
        model_name=model_name
        or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
