"""LLM-провайдеры: mock и OpenAI-совместимый."""

from __future__ import annotations

import json
import os
import random
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from .protocols import LLMProvider, LLMResponse, StructuredLLMResponse
from .cache import LLMCache
from ._utils import (
    LLMCallError,
    _env_float,
    _env_int,
    _extract_json,
    _jsonable,
    _resolve_llm_log_path,
    _sanitize_create_kwargs,
    _should_log_errors,
    _should_log_success,
    _strip_think_tags,
    _truncate_text,
)
from ._debug_logger import _LLMDebugLogger


def _supports_provider_routing(base_url: str | None) -> bool:
    """Проверить, что backend понимает OpenRouter provider routing."""
    return "openrouter" in (base_url or "").lower()


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


def _normalize_provider_order(values: list[str] | None) -> list[str] | None:
    """Нормализовать список provider routing backend'ов."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized or None


def _provider_order_from_env() -> list[str] | None:
    """Прочитать provider routing order из переменных окружения."""
    raw = (
        os.getenv("OPENROUTER_PROVIDER_ORDER")
        or os.getenv("OPENAI_PROVIDER_ORDER")
        or ""
    ).strip()
    if not raw:
        return None
    return _normalize_provider_order(raw.split(","))


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
        use_tool_calls: bool = True,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "Для OpenAI-провайдера установите зависимости проекта: "
                "pip install -e ."
            ) from exc

        kwargs: dict = {"timeout": 120.0}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client_local = threading.local()
        self._openai_cls = OpenAI
        self._client_kwargs = dict(kwargs)
        self._base_url = base_url
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
        if provider_order and _supports_provider_routing(base_url):
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

    def _effective_temperature(self, temperature: float) -> float:
        """Вернуть температуру с минимальным workaround только для MiniMax."""
        if float(temperature) != 0.0:
            return float(temperature)
        base_url = (self._base_url or "").lower()
        model = (self._model or "").lower()
        if "minimax" in base_url or model.startswith("minimax"):
            return 0.01
        return 0.0

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

        safe_temperature = self._effective_temperature(temperature)

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
        safe_temperature = self._effective_temperature(temperature)

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
        safe_temperature = self._effective_temperature(temperature)

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
    use_tool_calls: bool = True,
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

    _load_dotenv_if_available()
    resolved_model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    resolved_url = base_url or os.getenv("OPENAI_BASE_URL")
    resolved_provider_order = _normalize_provider_order(provider_order) or _provider_order_from_env()

    resolved_use_tool_calls = use_tool_calls
    if not resolved_use_tool_calls:
        raw = (os.getenv("MAGISTRY_LLM_USE_TOOL_CALLS") or "").strip().lower()
        resolved_use_tool_calls = raw in ("1", "true", "yes", "on")

    return OpenAICompatibleProvider(
        model=resolved_model,
        api_key=resolved_key,
        base_url=resolved_url,
        cache_path=cache_path,
        provider_order=resolved_provider_order,
        use_tool_calls=resolved_use_tool_calls,
    )
