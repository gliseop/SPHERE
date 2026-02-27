"""LLM-провайдер: протокол, mock, кеш, OpenAI-совместимый."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import zlib
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


def _extract_json(text: str) -> str:
    """Извлечь JSON из текста, который может содержать markdown-обёртку.

    Модели иногда оборачивают JSON в блоки ```json ... ``` или
    добавляют текст до/после. Функция пытается найти и извлечь
    первый валидный JSON-объект из текста.

    Args:
        text: Текст, потенциально содержащий JSON.

    Returns:
        Извлечённый JSON-текст.
    """
    # Убрать markdown-блоки
    md_match = re.search(
        r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL
    )
    if md_match:
        return md_match.group(1).strip()

    # Найти первый { ... } блок
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return text[start:end]


@dataclass
class LLMResponse:
    """Ответ от LLM-провайдера."""

    text: str
    model: str = "mock"
    usage: dict = field(default_factory=dict)


@dataclass
class StructuredLLMResponse:
    """Ответ LLM со structured output.

    Attributes:
        data: Словарь, соответствующий переданной JSON-схеме.
        model: Имя модели.
        usage: Статистика использования токенов.
    """

    data: dict
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

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        """Сгенерировать ответ по JSON-схеме.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            schema: JSON-схема ожидаемого ответа.
            temperature: Температура генерации.

        Returns:
            Structured-ответ LLM.
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

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        structured_responses: dict[str, dict] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._structured_responses = structured_responses or {}
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

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        """Сгенерировать structured-ответ.

        Ищет совпадение ключа из structured_responses в user-промпте.
        При отсутствии совпадения генерирует заглушку из JSON-схемы.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            schema: JSON-схема ожидаемого ответа.
            temperature: Не используется.

        Returns:
            Structured mock-ответ.
        """
        self._call_count += 1

        for key, response_data in self._structured_responses.items():
            if key in user:
                return StructuredLLMResponse(data=response_data, model="mock")

        return StructuredLLMResponse(
            data=self._generate_stub(schema), model="mock"
        )

    @staticmethod
    def _generate_stub(schema: dict) -> dict:
        """Сгенерировать заглушку на основе JSON-схемы.

        Args:
            schema: JSON-схема с описанием свойств.

        Returns:
            Словарь со значениями по умолчанию для каждого свойства.
        """
        properties = schema.get("properties", {})
        stub: dict = {}
        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type", "string")
            if prop_type == "string":
                stub[prop_name] = ""
            elif prop_type == "number":
                stub[prop_name] = 0
            elif prop_type == "integer":
                stub[prop_name] = 0
            elif prop_type == "boolean":
                stub[prop_name] = False
            elif prop_type == "array":
                stub[prop_name] = []
            elif prop_type == "object":
                stub[prop_name] = {}
            else:
                stub[prop_name] = None
        return stub

    @property
    def call_count(self) -> int:
        """Количество вызовов."""
        return self._call_count


class OpenAICompatibleProvider:
    """Провайдер, совместимый с OpenAI API.

    Attributes:
        _client: Клиент OpenAI API.
        _model: Имя модели.
        _cache: Кеш ответов (опционально).
        _extra_body: Дополнительные параметры запроса (например, provider).
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        cache_path: str | None = None,
        provider_order: list[str] | None = None,
        use_tool_calls: bool = False,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "Для OpenAI-провайдера установите пакет: "
                "pip install magistry-sim[llm]"
            ) from exc

        kwargs: dict = {"timeout": 120.0}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self._cache = LLMCache(cache_path) if cache_path else None
        self._use_tool_calls = use_tool_calls
        self._extra_body: dict | None = None
        if provider_order:
            self._extra_body = {
                "provider": {
                    "order": provider_order,
                    "allow_fallbacks": True,
                }
            }

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

        create_kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": safe_temperature,
        }
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body

        response = self._client.chat.completions.create(**create_kwargs)

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

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        """Сгенерировать structured-ответ через OpenAI API.

        Поддерживает два режима: json_schema (response_format)
        и tool_calls (function calling). Режим tool_calls часто
        работает быстрее на провайдерах с высоким throughput.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            schema: JSON-схема ожидаемого ответа.
            temperature: Температура генерации.

        Returns:
            Structured-ответ LLM.
        """
        if self._use_tool_calls:
            return self._structured_via_tool_call(
                system, user, schema, temperature
            )
        return self._structured_via_json_schema(
            system, user, schema, temperature
        )

    def _structured_via_json_schema(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float,
    ) -> StructuredLLMResponse:
        """Structured output через response_format json_schema."""
        safe_temperature = max(temperature, 0.01)

        create_kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": safe_temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body

        response = self._client.chat.completions.create(**create_kwargs)

        raw_text = response.choices[0].message.content or "{}"
        text = _strip_think_tags(raw_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            extracted = _extract_json(text)
            data = json.loads(extracted)
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }

        return StructuredLLMResponse(
            data=data, model=self._model, usage=usage
        )

    def _structured_via_tool_call(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float,
    ) -> StructuredLLMResponse:
        """Structured output через function calling (tool use)."""
        safe_temperature = max(temperature, 0.01)

        create_kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": safe_temperature,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "structured_response",
                        "description": "Return structured response",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "structured_response"},
            },
        }
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body

        response = self._client.chat.completions.create(**create_kwargs)

        msg = response.choices[0].message
        raw_args = ""
        if msg.tool_calls and msg.tool_calls[0].function.arguments:
            raw_args = msg.tool_calls[0].function.arguments
        elif msg.content:
            raw_args = msg.content

        text = _strip_think_tags(raw_args) if raw_args else "{}"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            extracted = _extract_json(text)
            data = json.loads(extracted)
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }

        return StructuredLLMResponse(
            data=data, model=self._model, usage=usage
        )


def create_provider(
    mock: bool = True,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_path: str | None = None,
    provider_order: list[str] | None = None,
    use_tool_calls: bool = False,
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
        provider_order: Приоритет провайдеров OpenRouter
            (например, ["DeepInfra", "Groq"]).
        use_tool_calls: Использовать function calling вместо
            json_schema для structured output.

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
        provider_order=provider_order,
        use_tool_calls=use_tool_calls,
    )


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
