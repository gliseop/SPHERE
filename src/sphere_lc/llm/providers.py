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
from typing import Any, Callable, Mapping

from ..config import DEFAULT_LLM_MODEL
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


def _extract_usage(resp_usage: Any) -> dict[str, int]:
    """Извлечь usage с поддержкой parsing prompt cache и reasoning токенов.

    OpenRouter и совместимые провайдеры (DeepSeek, Anthropic, OpenAI и др.)
    возвращают данные о prompt-cache в подполе ``prompt_tokens_details``.
    DeepSeek-reasoner дополнительно возвращает
    ``completion_tokens_details.reasoning_tokens`` — число токенов,
    которое модель потратила на внутренние рассуждения. Это часть
    completion (отдельной платы за reasoning_tokens у DeepSeek нет, но
    знание величины полезно для планирования стоимости и для понимания,
    почему completion-time больше обычного).

    Эта функция нормализует структуру вне зависимости от того, отдал ли
    SDK pydantic-объект, dict или их смесь.

    Args:
        resp_usage: Объект ``resp.usage`` из ответа провайдера. Может быть
            pydantic-моделью OpenAI SDK или dict (некоторые SDK-версии
            и совместимые клиенты).

    Returns:
        Словарь ``{prompt_tokens, completion_tokens[, cached_tokens]
        [, reasoning_tokens]}``. Если ``resp_usage`` пуст или ``None``,
        возвращается пустой dict.
    """
    if resp_usage is None:
        return {}

    def _get(obj: Any, name: str) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(name)
        return getattr(obj, name, None)

    prompt_tokens = _get(resp_usage, "prompt_tokens")
    completion_tokens = _get(resp_usage, "completion_tokens")
    if prompt_tokens is None and completion_tokens is None:
        return {}

    usage: dict[str, int] = {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
    }

    details = _get(resp_usage, "prompt_tokens_details")
    if details is not None:
        cached = _get(details, "cached_tokens")
        if cached is not None:
            try:
                usage["cached_tokens"] = int(cached)
            except (TypeError, ValueError):
                pass

    if "cached_tokens" not in usage:
        cached_native = _get(resp_usage, "prompt_cache_hit_tokens")
        if cached_native is not None:
            try:
                usage["cached_tokens"] = int(cached_native)
            except (TypeError, ValueError):
                pass

    details_completion = _get(resp_usage, "completion_tokens_details")
    if details_completion is not None:
        reasoning = _get(details_completion, "reasoning_tokens")
        if reasoning is not None:
            try:
                usage["reasoning_tokens"] = int(reasoning)
            except (TypeError, ValueError):
                pass

    return usage


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


def _provider_ignore_from_env() -> list[str] | None:
    """Прочитать список игнорируемых апстрим-провайдеров из окружения.

    OpenRouter маршрутизирует запросы на разные апстримы (DeepInfra, DeepSeek,
    Novita, Together AI и т.д.) и при срабатывании rate-limit на одном из них
    возвращает 429 без автоматического fallback. Через
    ``SPHERE_LLM_PROVIDER_IGNORE`` (или ``OPENROUTER_PROVIDER_IGNORE``) можно
    запретить конкретного апстрима — OpenRouter будет сразу выбирать другой
    при доступности.

    Returns:
        Нормализованный список идентификаторов апстримов либо ``None``.
    """
    raw = (
        os.getenv("SPHERE_LLM_PROVIDER_IGNORE")
        or os.getenv("OPENROUTER_PROVIDER_IGNORE")
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
        max_completion_tokens: int | None = None,
    ) -> LLMResponse:
        """Сгенерировать детерминированный ответ.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            temperature: Не используется.
            max_completion_tokens: Не используется в mock-провайдере.

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
        model: str = DEFAULT_LLM_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_path: str | None = None,
        provider_order: list[str] | None = None,
        use_tool_calls: bool = True,
        cache_busting_prefix: str | None = None,
        structured_mode: str = "tool_call",
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "Для OpenAI-провайдера установите зависимости проекта: "
                "pip install -e ."
            ) from exc

        self._request_timeout_s = max(1.0, _env_float("SPHERE_LLM_REQUEST_TIMEOUT_S", 30.0))
        self._call_deadline_s = max(1.0, _env_float("SPHERE_LLM_CALL_DEADLINE_S", self._request_timeout_s))
        kwargs: dict = {"timeout": self._request_timeout_s}
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
        normalized_mode = (structured_mode or "").strip().lower()
        if normalized_mode not in ("tool_call", "json_schema", "json_object"):
            normalized_mode = "tool_call"
        self._structured_mode = normalized_mode
        self._max_retries = max(0, _env_int("SPHERE_LLM_MAX_RETRIES", 2))
        # Дополнительный бюджет ретраев именно для парс-ошибок (пустой JSON):
        # SDK ретраит сетевые коды, но не реагирует на пустой ответ модели.
        # Расширенный бюджет позволяет пережить короткое окно деградации провайдера
        # без эскалации в LLMCallError.
        self._parse_max_retries = max(
            self._max_retries,
            _env_int("SPHERE_LLM_PARSE_MAX_RETRIES", 4),
        )
        self._retry_base_delay_s = max(0.05, _env_float("SPHERE_LLM_RETRY_BASE_DELAY_S", 0.75))
        self._retry_max_delay_s = max(self._retry_base_delay_s, _env_float("SPHERE_LLM_RETRY_MAX_DELAY_S", 8.0))
        self._retry_on_parse = (os.getenv("SPHERE_LLM_RETRY_ON_PARSE") or "").strip().lower() not in ("0", "false", "no", "off")
        self._parse_retry_temp_jitter = max(
            0.0, _env_float("SPHERE_LLM_PARSE_RETRY_TEMP_JITTER", 0.1)
        )
        self._log_max_chars = _env_int("SPHERE_LLM_LOG_MAX_CHARS", 0)
        log_path = _resolve_llm_log_path()
        self._debug_logger = _LLMDebugLogger(log_path, max_chars=self._log_max_chars) if log_path else None
        self._extra_body: dict | None = None
        provider_ignore = _provider_ignore_from_env()
        if _supports_provider_routing(base_url) and (provider_order or provider_ignore):
            provider_block: dict[str, Any] = {"allow_fallbacks": True}
            if provider_order:
                provider_block["order"] = provider_order
            if provider_ignore:
                provider_block["ignore"] = provider_ignore
            self._extra_body = {"provider": provider_block}
        prefix_value = (cache_busting_prefix or "").strip()
        self._cache_busting_prefix: str | None = prefix_value or None

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
        overall_started = time.monotonic()
        # Бюджет: max_retries для штатных ошибок, parse_max_retries — для пустого JSON.
        max_attempts = max(self._max_retries, self._parse_max_retries) + 1
        base_temperature = create_kwargs.get("temperature")

        for attempt in range(max_attempts):
            elapsed_s = time.monotonic() - overall_started
            remaining_budget_s = self._call_deadline_s - elapsed_s
            if remaining_budget_s <= 0:
                exc = TimeoutError(
                    f"LLM call deadline exceeded ({self._call_deadline_s:.1f}s) before attempt {attempt + 1}"
                )
                raise exc
            attempt_timeout_s = min(self._request_timeout_s, max(0.5, remaining_budget_s))
            create_kwargs_with_timeout = dict(create_kwargs)
            create_kwargs_with_timeout["timeout"] = attempt_timeout_s
            # На retry после парс-ошибки слегка варьируем температуру: помогает,
            # когда провайдер залип на пустом JSON для конкретной точки сэмплинга.
            if attempt > 0 and self._parse_retry_temp_jitter > 0 and base_temperature is not None:
                jitter_sign = 1 if attempt % 2 == 1 else -1
                jitter_amount = self._parse_retry_temp_jitter * (0.5 + random.random())
                jittered = float(base_temperature) + jitter_sign * jitter_amount
                create_kwargs_with_timeout["temperature"] = max(0.0, min(2.0, jittered))
            sanitized_kwargs = _sanitize_create_kwargs(
                create_kwargs_with_timeout,
                max_chars=self._log_max_chars,
            )
            started = time.monotonic()
            self._log(
                {
                    "ts": time.time(),
                    "call_id": call_id,
                    "attempt": attempt,
                    "kind": kind,
                    "model": self._model,
                    "phase": "request",
                    "timeout_s": round(attempt_timeout_s, 3),
                    "remaining_budget_s": round(remaining_budget_s, 3),
                    "request": sanitized_kwargs,
                },
                is_error=False,
            )

            response = None
            try:
                response = self._get_client().chat.completions.create(**create_kwargs_with_timeout)
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
                        "timeout_s": round(attempt_timeout_s, 3),
                        "response": raw_response,
                    },
                    is_error=False,
                )

                parsed, meta = parse(response)
                meta = dict(meta or {})
                meta.update(
                    {
                        "call_id": call_id,
                        "attempts": attempt + 1,
                        "retries_used": attempt,
                        "timeout_s": round(attempt_timeout_s, 3),
                        "deadline_s": round(self._call_deadline_s, 3),
                    }
                )
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
                return parsed, meta
            except Exception as exc:
                duration_ms = round((time.monotonic() - started) * 1000, 1)
                is_parse = exc.__class__.__name__ in ("JSONDecodeError", "_LLMStructuredParseError")
                is_timeout = exc.__class__.__name__ == "APITimeoutError" or isinstance(exc, TimeoutError)
                retryable = (
                    not is_timeout
                    and (self._is_retryable_error(exc) or (self._retry_on_parse and is_parse))
                )
                elapsed_after_s = time.monotonic() - overall_started
                remaining_after_s = self._call_deadline_s - elapsed_after_s
                # Парс-ошибкам выдаём расширенный бюджет (parse_max_retries),
                # обычным ретраиваемым — стандартный (max_retries).
                effective_max_retries = (
                    self._parse_max_retries if (is_parse and self._retry_on_parse) else self._max_retries
                )
                will_retry = attempt < effective_max_retries and retryable and remaining_after_s > 0.05
                sleep_s = min(self._retry_sleep_s(attempt), max(0.0, remaining_after_s - 0.05)) if will_retry else 0.0

                self._log(
                    {
                        "ts": time.time(),
                        "call_id": call_id,
                        "attempt": attempt,
                        "kind": kind,
                        "model": self._model,
                        "phase": "error",
                        "duration_ms": duration_ms,
                        "timeout_s": round(attempt_timeout_s, 3),
                        "remaining_budget_s": round(max(0.0, remaining_after_s), 3),
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
                    if is_timeout:
                        raise TimeoutError(
                            f"LLM {kind} timed out after {attempt + 1} attempt(s); deadline={self._call_deadline_s:.1f}s"
                        ) from exc
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

    def _build_messages(
        self,
        system: str,
        user: str,
    ) -> list[dict[str, str]]:
        """Собрать messages с опциональной cache-busting приставкой.

        Если задан ``cache_busting_prefix``, добавляется отдельная
        системная message в самое начало с уникальным маркером
        ``ab_test_cache_busting=<prefix>:<uuid4-token>``. Уникальный токен
        перегенерируется на каждый вызов, что гарантирует разный
        prefix у каждого запроса и принудительный промах prompt-cache.
        Базовый ``system``-блок остаётся неизменным, что не повредит
        качеству ответов модели.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.

        Returns:
            Список messages в формате OpenAI chat completions.
        """
        if self._cache_busting_prefix:
            cache_buster_token = uuid.uuid4().hex
            buster_text = (
                f"ab_test_cache_busting={self._cache_busting_prefix}:{cache_buster_token}"
            )
            return [
                {"role": "system", "content": buster_text},
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_completion_tokens: int | None = None,
    ) -> LLMResponse:
        """Сгенерировать ответ через OpenAI API.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            temperature: Температура генерации.
            max_completion_tokens: Жёсткий потолок длины ответа в токенах.
                None — без явного лимита (модель сама решает).

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
            "messages": self._build_messages(system, user),
            "temperature": safe_temperature,
        }
        if max_completion_tokens is not None and max_completion_tokens > 0:
            create_kwargs["max_tokens"] = int(max_completion_tokens)
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body

        def _parse(resp: Any) -> tuple[LLMResponse, dict[str, Any]]:
            raw_text = resp.choices[0].message.content or ""
            text = _strip_think_tags(raw_text)
            usage = _extract_usage(resp.usage)
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

        Поддерживает три режима, выбираемых через ``self._structured_mode``:
        ``tool_call`` (function calling, default), ``json_schema``
        (``response_format=json_schema``) и ``json_object``
        (``response_format=json_object`` без strict-схемы — для reasoning
        моделей DeepSeek, которые не поддерживают tool_call/json_schema).
        Режим ``tool_call`` падает с graceful fallback в ``json_schema``,
        затем в ``json_object``: первая успешная ветвь возвращается, что
        позволяет переключаться между моделями без изменений кода вызывающего.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            schema: JSON-схема ожидаемого ответа.
            temperature: Температура генерации.

        Returns:
            Structured-ответ LLM.
        """
        if self._structured_mode == "json_object":
            return self._structured_via_json_object(
                system, user, schema, temperature
            )

        if self._structured_mode == "json_schema":
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

        # Default: structured_mode == "tool_call" (или legacy use_tool_calls).
        if self._structured_mode == "tool_call" or self._use_tool_calls:
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
                try:
                    return self._structured_via_json_schema(
                        system, user, schema, temperature
                    )
                except Exception as exc2:
                    self._log(
                        {
                            "ts": time.time(),
                            "kind": "structured_fallback",
                            "model": self._model,
                            "from": "json_schema",
                            "to": "json_object",
                            "error": {"type": exc2.__class__.__name__, "message": str(exc2)},
                        },
                        is_error=True,
                    )
                    return self._structured_via_json_object(
                        system, user, schema, temperature
                    )

        # use_tool_calls=False и structured_mode != json_object: legacy путь
        # (json_schema → tool_call fallback).
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
            "messages": self._build_messages(system, user),
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
            usage = _extract_usage(resp.usage)
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
            "messages": self._build_messages(system, user),
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
            usage = _extract_usage(resp.usage)
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

    def _is_reasoner_model(self) -> bool:
        """Проверить, является ли модель reasoning-моделью DeepSeek.

        DeepSeek reasoner-модели игнорируют параметр ``temperature`` и
        возвращают HTTP 400, если он передан. К этому семейству относятся
        ``deepseek-reasoner`` и ``deepseek-v4-flash`` в thinking-режиме.

        Returns:
            ``True``, если по имени модели можно судить о reasoning-режиме.
        """
        model_name = (self._model or "").lower()
        return model_name.startswith("deepseek-reasoner") or "reasoner" in model_name

    def _structured_via_json_object(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float,
    ) -> StructuredLLMResponse:
        """Structured output через response_format json_object.

        Этот режим предназначен для reasoning-моделей DeepSeek
        (``deepseek-reasoner``, ``deepseek-v4-flash`` в thinking-режиме),
        которые не поддерживают ни ``tool_call`` с ``tool_choice``, ни
        ``response_format=json_schema``. Поддерживается только
        ``response_format=json_object`` без strict-схемы, поэтому схема
        выводится в system-промпт как описание формата, а валидация
        перекладывается на саму модель.

        Логика парсинга: если возвращённый текст не парсится как JSON,
        делается попытка очистить markdown-обёртки (`````json ... ```` `)
        и повторить ``json.loads``. Если и это падает — поднимается
        ``LLMCallError`` с понятным сообщением.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            schema: JSON-схема ожидаемого ответа (превращается в текст).
            temperature: Температура генерации. Игнорируется на
                reasoning-моделях DeepSeek (см. ``_is_reasoner_model``).

        Returns:
            Структурированный ответ.

        Raises:
            LLMCallError: если ответ модели не удалось распарсить как JSON
                ни напрямую, ни после удаления markdown-обёртки.
        """
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        format_block = (
            "\n\n=== JSON OUTPUT FORMAT ===\n"
            "Верните ответ строго в формате JSON, соответствующего следующей "
            "схеме (не повторяйте схему, верните только данные):\n"
            f"{schema_text}\n\n"
            "Ответ должен быть валидным JSON-объектом без markdown-обёртки, "
            "без префиксов и без комментариев."
        )
        enriched_system = (system or "") + format_block

        create_kwargs: dict = {
            "model": self._model,
            "messages": self._build_messages(enriched_system, user),
            "response_format": {"type": "json_object"},
        }
        # На reasoning-моделях температура вызывает HTTP 400. Передаём её
        # только для обычных моделей.
        if not self._is_reasoner_model():
            create_kwargs["temperature"] = self._effective_temperature(temperature)
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body

        def _parse(resp: Any) -> tuple[StructuredLLMResponse, dict[str, Any]]:
            raw_text = resp.choices[0].message.content or "{}"
            text = _strip_think_tags(raw_text)
            data: Any | None = None
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                cleaned = self._strip_markdown_json(text)
                try:
                    data = json.loads(cleaned)
                except json.JSONDecodeError:
                    # Последняя попытка — извлечь первый JSON-объект из текста.
                    try:
                        extracted = _extract_json(cleaned)
                        data = json.loads(extracted)
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ValueError(
                            "structured_via_json_object: не удалось распарсить "
                            f"ответ модели как JSON. raw_preview="
                            f"{_truncate_text(raw_text, 400)!r}"
                        ) from exc
            usage = _extract_usage(resp.usage)
            return (
                StructuredLLMResponse(data=data, model=self._model, usage=usage),
                {
                    "usage": usage,
                    "raw_text": _truncate_text(raw_text, self._log_max_chars),
                    "parsed": data,
                },
            )

        result, _meta = self._call_with_retries(
            kind="structured_json_object",
            create_kwargs=create_kwargs,
            parse=_parse,
        )
        return result

    @staticmethod
    def _strip_markdown_json(text: str) -> str:
        """Убрать markdown-обёртку вокруг JSON-блока.

        Reasoning-модели иногда возвращают ответ как ```json ... ```
        вопреки явной инструкции «без markdown». Этот хелпер вырезает
        обёртку безопасно: ищет первый блок ``` (с возможным указанием
        языка) и берёт содержимое до ближайшего закрывающего ```.

        Args:
            text: Текст ответа модели.

        Returns:
            Текст без markdown-обёртки. Если обёртки нет, возвращает вход
            как есть (со срезанными пробельными символами по краям).
        """
        if not text:
            return text or ""
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped
        # Убираем открывающие ``` и опциональный спецификатор языка (json, JSON и т.п.).
        without_open = stripped[3:]
        # Удаляем спецификатор языка до первого перевода строки.
        newline_idx = without_open.find("\n")
        if newline_idx != -1:
            language_marker = without_open[:newline_idx].strip().lower()
            if language_marker in ("", "json"):
                without_open = without_open[newline_idx + 1 :]
        # Снимаем закрывающую ```.
        close_idx = without_open.rfind("```")
        if close_idx != -1:
            without_open = without_open[:close_idx]
        return without_open.strip()


def create_provider(
    mock: bool = True,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_path: str | None = None,
    provider_order: list[str] | None = None,
    use_tool_calls: bool = True,
    cache_busting_prefix: str | None = None,
    structured_mode: str = "tool_call",
) -> LLMProvider:
    """Фабрика LLM-провайдеров.

    При mock=False читает переменные окружения:
    LLM_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL.

    Args:
        mock: Использовать mock-провайдер.
        model: Имя модели (по умолчанию из LLM_MODEL или ``config.DEFAULT_LLM_MODEL``).
        api_key: API-ключ (по умолчанию из OPENAI_API_KEY).
        base_url: Базовый URL (по умолчанию из OPENAI_BASE_URL).
        cache_path: Путь к кешу.
        provider_order: Приоритет провайдеров OpenRouter
            (например, ["DeepInfra", "Groq"]).
        use_tool_calls: Устаревшее поле. Использовать function calling
            вместо json_schema для structured output. При наличии явного
            ``structured_mode`` игнорируется.
        cache_busting_prefix: Если непустое, добавляет уникальный префикс
            в каждое сообщение (для A/B-теста с отключённым prompt-cache).
        structured_mode: Режим structured-вывода
            (``tool_call``/``json_schema``/``json_object``).

    Returns:
        Экземпляр провайдера.
    """
    if mock:
        return MockLLMProvider()

    _load_dotenv_if_available()
    resolved_model = model or os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    resolved_url = base_url or os.getenv("OPENAI_BASE_URL")
    resolved_provider_order = _normalize_provider_order(provider_order) or _provider_order_from_env()

    resolved_use_tool_calls = use_tool_calls
    if not resolved_use_tool_calls:
        raw = (os.getenv("SPHERE_LLM_USE_TOOL_CALLS") or "").strip().lower()
        resolved_use_tool_calls = raw in ("1", "true", "yes", "on")

    return OpenAICompatibleProvider(
        model=resolved_model,
        api_key=resolved_key,
        base_url=resolved_url,
        cache_path=cache_path,
        provider_order=resolved_provider_order,
        use_tool_calls=resolved_use_tool_calls,
        cache_busting_prefix=cache_busting_prefix,
        structured_mode=structured_mode,
    )
