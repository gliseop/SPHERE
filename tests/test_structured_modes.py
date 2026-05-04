"""Тесты для трёх режимов structured-вывода в OpenAICompatibleProvider.

Покрывают:
    1. ``_structured_via_json_object`` — обычный JSON-ответ без markdown.
    2. ``_structured_via_json_object`` — cleanup markdown-обёртки.
    3. ``_structured_via_json_object`` — ошибка парсинга при невалидном JSON.
    4. ``_extract_usage`` — поднятие ``reasoning_tokens`` из
       ``completion_tokens_details``.
    5. Отсутствие ``temperature`` в create_kwargs для reasoner-модели.
    6. Корректный диспатч в ``generate_structured`` по ``structured_mode``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from sphere_lc.llm._utils import LLMCallError
from sphere_lc.llm.providers import OpenAICompatibleProvider, _extract_usage


@dataclass
class _StubMessage:
    content: str
    tool_calls: list[Any] | None = None


@dataclass
class _StubChoice:
    message: _StubMessage


@dataclass
class _StubPromptDetails:
    cached_tokens: int


@dataclass
class _StubCompletionDetails:
    reasoning_tokens: int


@dataclass
class _StubUsage:
    prompt_tokens: int
    completion_tokens: int
    prompt_tokens_details: Any | None = None
    completion_tokens_details: Any | None = None


@dataclass
class _StubResponse:
    choices: list[_StubChoice]
    usage: _StubUsage


class _StubChatCompletions:
    def __init__(self, response_factory):
        self._response_factory = response_factory
        self.recorded_calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.recorded_calls.append(kwargs)
        return self._response_factory()


class _StubChat:
    def __init__(self, completions: _StubChatCompletions) -> None:
        self.completions = completions


class _StubClient:
    def __init__(self, response_factory) -> None:
        self._completions = _StubChatCompletions(response_factory)
        self.chat = _StubChat(self._completions)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._completions.recorded_calls


def _make_provider(
    client: _StubClient,
    *,
    model: str = "stub-model",
    structured_mode: str = "tool_call",
) -> OpenAICompatibleProvider:
    """Собрать провайдер с подменённым OpenAI-клиентом.

    Args:
        client: Заглушка клиента, имитирующая chat.completions.create.
        model: Имя модели (для проверки reasoner-детектора).
        structured_mode: Режим structured-вывода.

    Returns:
        Готовый ``OpenAICompatibleProvider``.
    """

    class _StubOpenAICls:
        def __init__(self, **_kwargs):
            pass

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._request_timeout_s = 5.0
    provider._call_deadline_s = 5.0
    provider._openai_cls = _StubOpenAICls
    provider._client_kwargs = {}
    provider._client_local = threading.local()
    provider._client_local.client = client
    provider._base_url = None
    provider._model = model
    provider._cache = None
    provider._use_tool_calls = True
    provider._structured_mode = structured_mode
    provider._max_retries = 0
    provider._parse_max_retries = 0
    provider._retry_base_delay_s = 0.05
    provider._retry_max_delay_s = 0.1
    provider._retry_on_parse = False
    provider._parse_retry_temp_jitter = 0.0
    provider._log_max_chars = 0
    provider._debug_logger = None
    provider._extra_body = None
    provider._cache_busting_prefix = None
    return provider


def test_structured_via_json_object_parses_plain_json() -> None:
    """JSON-ответ без markdown парсится без ошибок."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content='{"answer": "ok"}'))],
            usage=_StubUsage(prompt_tokens=120, completion_tokens=20),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, structured_mode="json_object")

    resp = provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}
    )

    assert resp.data == {"answer": "ok"}
    assert resp.usage["prompt_tokens"] == 120
    assert resp.usage["completion_tokens"] == 20

    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert call_kwargs["response_format"] == {"type": "json_object"}
    # tool_choice/tools не передаются при json_object режиме.
    assert "tool_choice" not in call_kwargs
    assert "tools" not in call_kwargs
    # Схема включена в system-промпт после JSON OUTPUT FORMAT блока.
    messages = call_kwargs["messages"]
    system_block = next(m for m in messages if m["role"] == "system")
    assert "=== JSON OUTPUT FORMAT ===" in system_block["content"]


def test_structured_via_json_object_strips_markdown_wrapper() -> None:
    """Ответ в markdown-обёртке ```json ... ``` корректно очищается."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content='```json\n{"x":1}\n```'))],
            usage=_StubUsage(prompt_tokens=50, completion_tokens=8),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, structured_mode="json_object")

    resp = provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}
    )

    assert resp.data == {"x": 1}


def test_structured_via_json_object_raises_on_invalid_json() -> None:
    """Полностью невалидный JSON без шансов на восстановление падает в LLMCallError."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content="not a json at all"))],
            usage=_StubUsage(prompt_tokens=30, completion_tokens=5),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, structured_mode="json_object")

    with pytest.raises(LLMCallError) as exc_info:
        provider.generate_structured(
            system="sys", user="usr", schema={"type": "object"}
        )

    # Сообщение должно содержать упоминание о парсинге, чтобы при разборе
    # инцидентов было понятно, в какой ветке упало.
    assert "json_object" in str(exc_info.value).lower() or "parsed" in str(exc_info.value).lower() or "structured_via_json_object" in str(exc_info.value)


def test_extract_usage_picks_reasoning_tokens() -> None:
    """``completion_tokens_details.reasoning_tokens`` поднимается в usage."""
    usage = _StubUsage(
        prompt_tokens=1_000,
        completion_tokens=500,
        completion_tokens_details=_StubCompletionDetails(reasoning_tokens=50),
    )
    parsed = _extract_usage(usage)
    assert parsed["reasoning_tokens"] == 50
    assert parsed["prompt_tokens"] == 1_000
    assert parsed["completion_tokens"] == 500


def test_extract_usage_handles_dict_completion_details() -> None:
    """Dict-форма ``completion_tokens_details`` тоже поддерживается."""
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 60,
        "completion_tokens_details": {"reasoning_tokens": 12},
    }
    parsed = _extract_usage(usage)
    assert parsed["reasoning_tokens"] == 12


def test_extract_usage_without_reasoning_details() -> None:
    """Без ``completion_tokens_details`` поле отсутствует в usage."""
    usage = _StubUsage(prompt_tokens=10, completion_tokens=5)
    parsed = _extract_usage(usage)
    assert "reasoning_tokens" not in parsed


def test_reasoner_model_omits_temperature_in_create_kwargs() -> None:
    """На reasoner-модели параметр ``temperature`` не должен передаваться."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content='{"ok": true}'))],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=5),
        )

    client = _StubClient(factory)
    provider = _make_provider(
        client, model="deepseek-reasoner", structured_mode="json_object"
    )

    provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}, temperature=0.7
    )

    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert "temperature" not in call_kwargs


def test_non_reasoner_model_keeps_temperature() -> None:
    """На обычной модели температура передаётся в create_kwargs."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content='{"ok": true}'))],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=5),
        )

    client = _StubClient(factory)
    provider = _make_provider(
        client, model="deepseek-chat", structured_mode="json_object"
    )

    provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}, temperature=0.42
    )

    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert pytest.approx(call_kwargs["temperature"], rel=1e-6) == 0.42


def test_dispatch_routes_json_object_mode() -> None:
    """``structured_mode='json_object'`` идёт прямо в json_object-ветвь."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content='{"a": 1}'))],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=2),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, structured_mode="json_object")

    provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}
    )

    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert "tools" not in call_kwargs


def test_dispatch_routes_tool_call_mode_by_default() -> None:
    """``structured_mode='tool_call'`` использует function calling."""

    def factory() -> _StubResponse:
        # tool_call возвращает аргументы в tool_calls.
        return _StubResponse(
            choices=[
                _StubChoice(
                    message=_StubMessage(
                        content="",
                        tool_calls=[_make_tool_call('{"a": 1}')],
                    )
                )
            ],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=2),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, structured_mode="tool_call")

    provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}
    )

    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert "tools" in call_kwargs
    assert call_kwargs["tool_choice"]["function"]["name"] == "structured_response"


def test_dispatch_routes_json_schema_mode() -> None:
    """``structured_mode='json_schema'`` использует response_format=json_schema."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content='{"a": 1}'))],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=2),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, structured_mode="json_schema")

    provider.generate_structured(
        system="sys", user="usr", schema={"type": "object", "properties": {}}
    )

    assert len(client.calls) == 1
    call_kwargs = client.calls[0]
    assert call_kwargs["response_format"]["type"] == "json_schema"
    assert "tools" not in call_kwargs


def _make_tool_call(arguments_json: str) -> Any:
    """Собрать заглушку tool_call объекта с заданными аргументами."""

    @dataclass
    class _StubFunction:
        arguments: str

    @dataclass
    class _StubToolCall:
        function: _StubFunction

    return _StubToolCall(function=_StubFunction(arguments=arguments_json))
