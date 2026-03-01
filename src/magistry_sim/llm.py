"""LLM-провайдер: протокол, mock, кеш, OpenAI-совместимый."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
import traceback
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


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


class LLMCallError(RuntimeError):
    def __init__(self, *, call_id: str, kind: str, attempt: int, original: Exception) -> None:
        self.call_id = call_id
        self.kind = kind
        self.attempt = attempt
        self.original = original
        super().__init__(f"{kind} failed (call_id={call_id}, attempt={attempt}): {original}")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_llm_log_path() -> Path | None:
    """Вернуть путь для debug-лога LLM, если логирование включено."""
    explicit = (os.getenv("MAGISTRY_LLM_LOG_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if not (_should_log_success() or _should_log_errors()):
        return None
    return (_project_root() / "results" / "llm_debug.jsonl").resolve()


def _should_log_success() -> bool:
    return (os.getenv("MAGISTRY_LLM_LOG") or "").strip().lower() in ("1", "true", "yes", "on")


def _should_log_errors() -> bool:
    # По умолчанию пишем ошибки (не мусорит при нормальной работе, но помогает дебажить).
    raw = (os.getenv("MAGISTRY_LLM_LOG_ERRORS") or "").strip()
    if raw == "":
        return True
    return raw.lower() in ("1", "true", "yes", "on")


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _jsonable(value: Any) -> Any:
    """Преобразовать объект в JSON-совместимый вид (best-effort)."""
    try:
        if hasattr(value, "model_dump"):
            return value.model_dump()  # type: ignore[attr-defined]
        if hasattr(value, "to_dict"):
            return value.to_dict()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _sanitize_create_kwargs(create_kwargs: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    """Обрезать потенциально большие строки в запросе (messages/response_format)."""
    result: dict[str, Any] = {}
    for k, v in create_kwargs.items():
        if k == "messages" and isinstance(v, list):
            sanitized_msgs = []
            for msg in v:
                if not isinstance(msg, dict):
                    sanitized_msgs.append(_jsonable(msg))
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    msg = dict(msg)
                    msg["content"] = _truncate_text(content, max_chars)
                sanitized_msgs.append(msg)
            result[k] = sanitized_msgs
            continue
        if k in ("response_format", "tools", "tool_choice", "extra_body"):
            result[k] = _jsonable(v)
            continue
        result[k] = _jsonable(v)
    return result


class _LLMDebugLogger:
    def __init__(self, path: Path, *, max_chars: int) -> None:
        self._path = path
        self._max_chars = max_chars
        self._lock = threading.Lock()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:  # noqa: WPS515
                f.write(line + "\n")


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
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=30.0,
        )
        with self._lock:
            # WAL improves concurrent reads/writes across threads/processes.
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("PRAGMA busy_timeout=30000")
            except Exception:
                # Pragmas are best-effort; cache must never break main flow.
                pass
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
        with self._lock:
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
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, response) VALUES (?, ?)",
                (key, response),
            )
            self._conn.commit()

    def close(self) -> None:
        """Закрыть соединение с БД."""
        with self._lock:
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
        self._client_local = threading.local()
        self._openai_cls = OpenAI
        self._client_kwargs = dict(kwargs)
        self._model = model
        self._cache = LLMCache(cache_path) if cache_path else None
        self._use_tool_calls = use_tool_calls
        self._max_retries = max(0, _env_int("MAGISTRY_LLM_MAX_RETRIES", 2))
        self._retry_base_delay_s = max(0.05, _env_float("MAGISTRY_LLM_RETRY_BASE_DELAY_S", 0.75))
        self._retry_max_delay_s = max(self._retry_base_delay_s, _env_float("MAGISTRY_LLM_RETRY_MAX_DELAY_S", 8.0))
        self._retry_on_parse = (os.getenv("MAGISTRY_LLM_RETRY_ON_PARSE") or "").strip().lower() not in ("0", "false", "no", "off")
        self._log_max_chars = _env_int("MAGISTRY_LLM_LOG_MAX_CHARS", 0)
        log_path = _resolve_llm_log_path()
        self._debug_logger = _LLMDebugLogger(log_path, max_chars=self._log_max_chars) if log_path else None
        self._extra_body: dict | None = None
        if provider_order:
            self._extra_body = {
                "provider": {
                    "order": provider_order,
                    "allow_fallbacks": True,
                }
            }

    def _get_client(self) -> Any:
        client = getattr(self._client_local, "client", None)
        if client is None:
            client = self._openai_cls(**self._client_kwargs)
            self._client_local.client = client
        return client

    def _retry_sleep_s(self, attempt: int) -> float:
        base = self._retry_base_delay_s * (2 ** max(0, attempt))
        jitter = random.uniform(0.85, 1.25)
        return min(self._retry_max_delay_s, base * jitter)

    def _is_retryable_error(self, exc: Exception) -> bool:
        name = exc.__class__.__name__
        if name in (
            "RateLimitError",
            "APITimeoutError",
            "APIConnectionError",
            "InternalServerError",
            "ServiceUnavailableError",
            "APIError",
        ):
            return True
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and (status == 429 or status >= 500):
            return True
        return False

    def _log(self, record: dict[str, Any], *, is_error: bool) -> None:
        if self._debug_logger is None:
            return
        if is_error and not _should_log_errors():
            return
        if not is_error and not _should_log_success():
            return
        try:
            self._debug_logger.write(record)
        except Exception:
            # Никогда не ломаем основной поток из-за логирования.
            return

    def _call_with_retries(
        self,
        *,
        kind: str,
        create_kwargs: dict[str, Any],
        parse: Callable[[Any], tuple[Any, dict[str, Any]]],
    ) -> tuple[Any, dict[str, Any]]:
        call_id = uuid.uuid4().hex
        sanitized_kwargs = _sanitize_create_kwargs(create_kwargs, max_chars=self._log_max_chars)

        for attempt in range(self._max_retries + 1):
            started = time.monotonic()
            self._log(
                {
                    "ts": time.time(),
                    "call_id": call_id,
                    "attempt": attempt,
                    "kind": kind,
                    "model": self._model,
                    "phase": "request",
                    "request": sanitized_kwargs,
                },
                is_error=False,
            )

            response = None
            try:
                response = self._get_client().chat.completions.create(**create_kwargs)
                duration_ms = round((time.monotonic() - started) * 1000, 1)
                raw_response = _jsonable(response)
                self._log(
                    {
                        "ts": time.time(),
                        "call_id": call_id,
                        "attempt": attempt,
                        "kind": kind,
                        "model": self._model,
                        "phase": "response",
                        "duration_ms": duration_ms,
                        "response": raw_response,
                    },
                    is_error=False,
                )

                parsed, meta = parse(response)
                meta = meta or {}
                self._log(
                    {
                        "ts": time.time(),
                        "call_id": call_id,
                        "attempt": attempt,
                        "kind": kind,
                        "model": self._model,
                        "phase": "parsed",
                        "duration_ms": duration_ms,
                        **meta,
                    },
                    is_error=False,
                )
                return parsed, {"call_id": call_id, **meta}
            except Exception as exc:
                duration_ms = round((time.monotonic() - started) * 1000, 1)
                is_parse = exc.__class__.__name__ in ("JSONDecodeError", "_LLMStructuredParseError")
                retryable = self._is_retryable_error(exc) or (self._retry_on_parse and is_parse)
                will_retry = attempt < self._max_retries and retryable
                sleep_s = self._retry_sleep_s(attempt) if will_retry else 0.0

                self._log(
                    {
                        "ts": time.time(),
                        "call_id": call_id,
                        "attempt": attempt,
                        "kind": kind,
                        "model": self._model,
                        "phase": "error",
                        "duration_ms": duration_ms,
                        "error": {
                            "type": exc.__class__.__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc(limit=30),
                        },
                        "retry": {
                            "will_retry": will_retry,
                            "sleep_s": round(sleep_s, 3),
                        },
                        "request": sanitized_kwargs,
                        "response": _jsonable(response) if response is not None else None,
                    },
                    is_error=True,
                )

                if not will_retry:
                    raise LLMCallError(call_id=call_id, kind=kind, attempt=attempt, original=exc) from exc
                time.sleep(sleep_s)

        raise RuntimeError("LLM retries exhausted")

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
                self._log(
                    {
                        "ts": time.time(),
                        "kind": "generate",
                        "model": self._model,
                        "phase": "cache_hit",
                    },
                    is_error=False,
                )
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

        def _parse(resp: Any) -> tuple[LLMResponse, dict[str, Any]]:
            raw_text = resp.choices[0].message.content or ""
            text = _strip_think_tags(raw_text)
            usage = {}
            if resp.usage:
                usage = {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                }
            return (
                LLMResponse(text=text, model=self._model, usage=usage),
                {
                    "usage": usage,
                    "raw_text": _truncate_text(raw_text, self._log_max_chars),
                },
            )

        result, _meta = self._call_with_retries(
            kind="generate",
            create_kwargs=create_kwargs,
            parse=_parse,
        )

        if self._cache:
            self._cache.put(system, user, self._model, result.text)

        return result

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
            try:
                return self._structured_via_tool_call(
                    system, user, schema, temperature
                )
            except Exception as exc:
                self._log(
                    {
                        "ts": time.time(),
                        "kind": "structured_fallback",
                        "model": self._model,
                        "from": "tool_call",
                        "to": "json_schema",
                        "error": {"type": exc.__class__.__name__, "message": str(exc)},
                    },
                    is_error=True,
                )
                return self._structured_via_json_schema(
                    system, user, schema, temperature
                )

        try:
            return self._structured_via_json_schema(
                system, user, schema, temperature
            )
        except Exception as exc:
            self._log(
                {
                    "ts": time.time(),
                    "kind": "structured_fallback",
                    "model": self._model,
                    "from": "json_schema",
                    "to": "tool_call",
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                },
                is_error=True,
            )
            return self._structured_via_tool_call(
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

        def _parse(resp: Any) -> tuple[StructuredLLMResponse, dict[str, Any]]:
            raw_text = resp.choices[0].message.content or "{}"
            text = _strip_think_tags(raw_text)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                extracted = _extract_json(text)
                data = json.loads(extracted)
            usage = {}
            if resp.usage:
                usage = {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                }
            return (
                StructuredLLMResponse(data=data, model=self._model, usage=usage),
                {
                    "usage": usage,
                    "raw_text": _truncate_text(raw_text, self._log_max_chars),
                    "parsed": data,
                },
            )

        result, _meta = self._call_with_retries(
            kind="structured_json_schema",
            create_kwargs=create_kwargs,
            parse=_parse,
        )
        return result

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

        def _parse(resp: Any) -> tuple[StructuredLLMResponse, dict[str, Any]]:
            msg = resp.choices[0].message
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
            if resp.usage:
                usage = {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                }
            return (
                StructuredLLMResponse(data=data, model=self._model, usage=usage),
                {
                    "usage": usage,
                    "raw_text": _truncate_text(raw_args, self._log_max_chars),
                    "parsed": data,
                },
            )

        result, _meta = self._call_with_retries(
            kind="structured_tool_call",
            create_kwargs=create_kwargs,
            parse=_parse,
        )
        return result


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

    resolved_use_tool_calls = use_tool_calls
    if not resolved_use_tool_calls:
        raw = (os.getenv("MAGISTRY_LLM_USE_TOOL_CALLS") or "").strip().lower()
        resolved_use_tool_calls = raw in ("1", "true", "yes", "on")

    return OpenAICompatibleProvider(
        model=resolved_model,
        api_key=resolved_key,
        base_url=resolved_url,
        cache_path=cache_path,
        provider_order=provider_order,
        use_tool_calls=resolved_use_tool_calls,
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
