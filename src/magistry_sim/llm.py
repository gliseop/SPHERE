"""LLM-провайдер: протокол, mock, кеш, OpenAI-совместимый."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


def _strip_think_tags(text: str) -> str:
    """Убрать блоки <think>...</think> из ответа модели.

    MiniMax-M2.5 оборачивает внутренние рассуждения в теги <think>.
    Для агентов нужен только чистый ответ.

    Args:
        text: Исходный текст ответа.

    Returns:
        Текст без блоков рассуждений.
    """
    cleaned = re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL
    )
    return cleaned.strip()


@dataclass
class LLMResponse:
    """Ответ от LLM-провайдера."""

    text: str
    model: str = "mock"
    usage: dict = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Протокол LLM-провайдера."""

    def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Сгенерировать ответ.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            temperature: Температура генерации.

        Returns:
            Ответ LLM.
        """
        ...


class LLMCache:
    """SQLite-кеш для LLM-ответов."""

    def __init__(self, db_path: str = ".llm_cache.db") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(key TEXT PRIMARY KEY, response TEXT)"
        )
        self._conn.commit()

    def _make_key(self, system: str, user: str, model: str) -> str:
        """Сформировать ключ кеша.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            model: Имя модели.

        Returns:
            SHA256-хеш.
        """
        content = f"{model}:{system}:{user}"
        return hashlib.sha256(content.encode()).hexdigest()

    def get(self, system: str, user: str, model: str) -> str | None:
        """Получить кешированный ответ.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            model: Имя модели.

        Returns:
            Кешированный текст или None.
        """
        key = self._make_key(system, user, model)
        row = self._conn.execute(
            "SELECT response FROM cache WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def put(
        self, system: str, user: str, model: str, response: str
    ) -> None:
        """Сохранить ответ в кеш.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            model: Имя модели.
            response: Текст ответа.
        """
        key = self._make_key(system, user, model)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, response) VALUES (?, ?)",
            (key, response),
        )
        self._conn.commit()

    def close(self) -> None:
        """Закрыть соединение с БД."""
        self._conn.close()


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

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def embed(self, text: str) -> list[float]:
        """Получить эмбеддинг текста через API.

        Args:
            text: Входной текст.

        Returns:
            Вектор эмбеддинга.
        """
        resp = self._client.embeddings.create(input=[text], model=self._model)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов через API.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов, упорядоченных по индексу.
        """
        resp = self._client.embeddings.create(input=texts, model=self._model)
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]


class MockLLMProvider:
    """Детерминированный mock-провайдер для тестов и отладки."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self._call_count = 0

    def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Сгенерировать детерминированный ответ.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            temperature: Не используется.

        Returns:
            Mock-ответ.
        """
        self._call_count += 1

        for key, response in self._responses.items():
            if key in user:
                return LLMResponse(text=response, model="mock")

        return LLMResponse(
            text=f"Mock-ответ #{self._call_count}",
            model="mock",
        )

    @property
    def call_count(self) -> int:
        """Количество вызовов."""
        return self._call_count


class OpenAICompatibleProvider:
    """Провайдер, совместимый с OpenAI API."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        cache_path: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "Для OpenAI-провайдера установите пакет: "
                "pip install magistry-sim[llm]"
            ) from exc

        kwargs: dict = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self._cache = LLMCache(cache_path) if cache_path else None

    def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Сгенерировать ответ через OpenAI API.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            temperature: Температура генерации.

        Returns:
            Ответ LLM.
        """
        if self._cache:
            cached = self._cache.get(system, user, self._model)
            if cached is not None:
                return LLMResponse(text=cached, model=self._model)

        # MiniMax API допускает temperature только в (0, 1].
        # Значение 0.0 заменяется на минимальное положительное.
        safe_temperature = max(temperature, 0.01)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=safe_temperature,
        )

        raw_text = response.choices[0].message.content or ""
        text = _strip_think_tags(raw_text)
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }

        if self._cache:
            self._cache.put(system, user, self._model, text)

        return LLMResponse(text=text, model=self._model, usage=usage)


def create_provider(
    mock: bool = True,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_path: str | None = None,
) -> LLMProvider:
    """Фабрика LLM-провайдеров.

    При mock=False читает переменные окружения:
    LLM_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL.

    Args:
        mock: Использовать mock-провайдер.
        model: Имя модели (по умолчанию из LLM_MODEL или gpt-4o-mini).
        api_key: API-ключ (по умолчанию из OPENAI_API_KEY).
        base_url: Базовый URL (по умолчанию из OPENAI_BASE_URL).
        cache_path: Путь к кешу.

    Returns:
        Экземпляр провайдера.
    """
    if mock:
        return MockLLMProvider()

    import os

    resolved_model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    resolved_url = base_url or os.getenv("OPENAI_BASE_URL")

    return OpenAICompatibleProvider(
        model=resolved_model,
        api_key=resolved_key,
        base_url=resolved_url,
        cache_path=cache_path,
    )
