"""Провайдеры эмбеддингов: протокол, mock, OpenAI."""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._utils import _env_float, _env_int

logger = logging.getLogger(__name__)


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
        self._timeout_s = max(1.0, _env_float("SPHERE_EMBEDDING_TIMEOUT_S", 30.0))
        self._max_retries = max(0, _env_int("SPHERE_EMBEDDING_MAX_RETRIES", 3))
        self._retry_base_delay_s = max(
            0.1, _env_float("SPHERE_EMBEDDING_RETRY_BASE_DELAY_S", 1.0)
        )
        self._retry_max_delay_s = max(
            self._retry_base_delay_s,
            _env_float("SPHERE_EMBEDDING_RETRY_MAX_DELAY_S", 30.0),
        )
        self._client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": self._timeout_s,
            "max_retries": 0,
        }
        self._client_local = threading.local()

    def _get_client(self) -> Any:
        client = getattr(self._client_local, "client", None)
        if client is None:
            client = self._openai_cls(**self._client_kwargs)
            self._client_local.client = client
        return client

    def _call_with_retry(self, label: str, fn: Any) -> Any:
        """Вызов embedding-API с таймаутом и экспоненциальным retry.

        Args:
            label: Метка вызова для диагностики в логах.
            fn: Функция, принимающая OpenAI-клиент и возвращающая ответ API.

        Returns:
            Результат успешного вызова ``fn``.

        Raises:
            Exception: Если исчерпано ``max_retries + 1`` попыток — проброс последнего.
        """
        last_exc: Exception | None = None
        total_attempts = self._max_retries + 1
        for attempt in range(total_attempts):
            try:
                return fn(self._get_client())
            except Exception as exc:
                last_exc = exc
                if attempt >= self._max_retries:
                    logger.warning(
                        "embedding %s failed (attempt %d/%d): %s",
                        label,
                        attempt + 1,
                        total_attempts,
                        exc,
                    )
                    raise
                delay = min(
                    self._retry_max_delay_s,
                    self._retry_base_delay_s * (2 ** attempt),
                )
                delay = delay * (0.8 + random.random() * 0.4)
                logger.warning(
                    "embedding %s failed (attempt %d/%d): %s; retry in %.2fs",
                    label,
                    attempt + 1,
                    total_attempts,
                    exc,
                    delay,
                )
                self._client_local.client = None
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def embed(self, text: str) -> list[float]:
        """Получить эмбеддинг текста через API.

        Args:
            text: Входной текст.

        Returns:
            Вектор эмбеддинга.
        """
        resp = self._call_with_retry(
            "embed",
            lambda c: c.embeddings.create(input=[text], model=self._model),
        )
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов через API.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов, упорядоченных по индексу.
        """
        resp = self._call_with_retry(
            "embed_batch",
            lambda c: c.embeddings.create(input=texts, model=self._model),
        )
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]


def _load_dotenv_if_available() -> None:
    """Подгрузить `.env`, если библиотека доступна."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(dotenv_path=cwd_env)
        return
    load_dotenv()


def create_embedding_provider(
    mock: bool = True,
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> EmbeddingProvider:
    """Фабрика провайдеров эмбеддингов.

    Args:
        mock: Использовать mock-провайдер.
        model_name: Имя модели эмбеддингов.
        api_key: API-ключ OpenAI-совместимого провайдера.
        base_url: Базовый URL API (для OpenRouter и аналогов).

    Returns:
        Экземпляр провайдера эмбеддингов.
    """
    if mock:
        return MockEmbeddingProvider(dimensions=384)

    import os

    _load_dotenv_if_available()
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
