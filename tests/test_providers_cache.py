"""Тесты для prompt-cache observability и cache-busting.

Проверяют:
    1. Что ``OpenAICompatibleProvider`` корректно достаёт ``cached_tokens``
       из ``resp.usage.prompt_tokens_details`` (как у pydantic-объекта,
       так и у dict);
    2. Что при включённом ``cache_busting_prefix`` провайдер собирает
       messages с уникальным cache-busting префиксом, и каждый вызов
       получает разный токен (что гарантирует промах prompt-cache).
    3. Что значения ``cached_tokens`` правильно агрегируются в
       ``perf_summary.json`` через ``WorldEngine._build_perf_summary``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sphere_lc.config import LLMConfig, ScenarioConfig
from sphere_lc.engine import WorldEngine, default_artifacts
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
class _StubUsage:
    prompt_tokens: int
    completion_tokens: int
    prompt_tokens_details: Any | None = None


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


def _make_provider(client: _StubClient, *, cache_busting_prefix: str | None = None) -> OpenAICompatibleProvider:
    """Собрать провайдер с подменённым OpenAI-клиентом.

    Args:
        client: Заглушка клиента, имитирующая chat.completions.create.
        cache_busting_prefix: Если задан, провайдер добавит cache-busting
            маркер в каждое сообщение.

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
    import threading
    provider._client_local = threading.local()
    provider._client_local.client = client
    provider._base_url = None
    provider._model = "stub-model"
    provider._cache = None
    provider._use_tool_calls = True
    provider._max_retries = 0
    provider._parse_max_retries = 0
    provider._retry_base_delay_s = 0.05
    provider._retry_max_delay_s = 0.1
    provider._retry_on_parse = False
    provider._parse_retry_temp_jitter = 0.0
    provider._log_max_chars = 0
    provider._debug_logger = None
    provider._extra_body = None
    provider._cache_busting_prefix = (
        cache_busting_prefix.strip() if (cache_busting_prefix or "").strip() else None
    )
    return provider


def test_extract_usage_with_pydantic_like_details() -> None:
    """`prompt_tokens_details` как pydantic-объект должен парситься."""
    usage = _StubUsage(
        prompt_tokens=1000,
        completion_tokens=200,
        prompt_tokens_details=_StubPromptDetails(cached_tokens=750),
    )
    parsed = _extract_usage(usage)
    assert parsed == {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "cached_tokens": 750,
    }


def test_extract_usage_with_dict_details() -> None:
    """`prompt_tokens_details` как dict тоже должен парситься (некоторые SDK)."""
    usage = _StubUsage(
        prompt_tokens=500,
        completion_tokens=120,
        prompt_tokens_details={"cached_tokens": 320},
    )
    parsed = _extract_usage(usage)
    assert parsed["cached_tokens"] == 320


def test_extract_usage_dict_input_with_dict_details() -> None:
    """Полностью dict-форма usage (включая details) тоже поддерживается."""
    usage = {
        "prompt_tokens": 999,
        "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 100},
    }
    parsed = _extract_usage(usage)
    assert parsed == {
        "prompt_tokens": 999,
        "completion_tokens": 100,
        "cached_tokens": 100,
    }


def test_extract_usage_without_details() -> None:
    """Если provider не вернул prompt_tokens_details, поле отсутствует."""
    usage = _StubUsage(
        prompt_tokens=400,
        completion_tokens=50,
        prompt_tokens_details=None,
    )
    parsed = _extract_usage(usage)
    assert "cached_tokens" not in parsed
    assert parsed == {"prompt_tokens": 400, "completion_tokens": 50}


def test_extract_usage_handles_none() -> None:
    """`None` usage возвращает пустой dict."""
    assert _extract_usage(None) == {}


def test_provider_generate_picks_cached_tokens() -> None:
    """`OpenAICompatibleProvider.generate` кладёт cached_tokens в LLMResponse."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content="ok"))],
            usage=_StubUsage(
                prompt_tokens=2_000,
                completion_tokens=100,
                prompt_tokens_details=_StubPromptDetails(cached_tokens=1_500),
            ),
        )

    client = _StubClient(factory)
    provider = _make_provider(client)

    resp = provider.generate(system="sys", user="usr")

    assert resp.usage.get("cached_tokens") == 1_500
    assert resp.usage.get("prompt_tokens") == 2_000


def test_provider_generate_structured_picks_cached_tokens() -> None:
    """`generate_structured` тоже сохраняет cached_tokens."""

    def factory() -> _StubResponse:
        msg = _StubMessage(content='{"value": 1}')
        return _StubResponse(
            choices=[_StubChoice(message=msg)],
            usage=_StubUsage(
                prompt_tokens=300,
                completion_tokens=10,
                prompt_tokens_details=_StubPromptDetails(cached_tokens=200),
            ),
        )

    client = _StubClient(factory)
    provider = _make_provider(client)
    provider._use_tool_calls = False

    resp = provider.generate_structured(
        system="sys", user="usr", schema={"type": "object"}
    )
    assert resp.usage.get("cached_tokens") == 200


def test_cache_busting_adds_unique_system_prefix() -> None:
    """С `cache_busting_prefix` каждый запрос получает уникальный префикс."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content="ok"))],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=2, prompt_tokens_details=None),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, cache_busting_prefix="run-A")

    provider.generate(system="базовый системный промпт", user="вопрос-1")
    provider.generate(system="базовый системный промпт", user="вопрос-2")

    assert len(client.calls) == 2
    first_messages = client.calls[0]["messages"]
    second_messages = client.calls[1]["messages"]

    # Первое сообщение каждой пары — busting system message с уникальным маркером.
    assert first_messages[0]["role"] == "system"
    assert first_messages[0]["content"].startswith("ab_test_cache_busting=run-A:")
    assert second_messages[0]["role"] == "system"
    assert second_messages[0]["content"].startswith("ab_test_cache_busting=run-A:")

    # Маркеры должны быть разными между вызовами (uuid token меняется).
    assert first_messages[0]["content"] != second_messages[0]["content"]

    # Базовый system-блок и user сохранились.
    assert first_messages[1] == {"role": "system", "content": "базовый системный промпт"}
    assert first_messages[2] == {"role": "user", "content": "вопрос-1"}


def test_cache_busting_disabled_default() -> None:
    """Без префикса messages идут как обычно: system + user."""

    def factory() -> _StubResponse:
        return _StubResponse(
            choices=[_StubChoice(message=_StubMessage(content="ok"))],
            usage=_StubUsage(prompt_tokens=10, completion_tokens=2, prompt_tokens_details=None),
        )

    client = _StubClient(factory)
    provider = _make_provider(client, cache_busting_prefix=None)
    provider.generate(system="sys", user="usr")
    assert client.calls[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


def test_llm_config_normalizes_cache_busting_prefix() -> None:
    """Пустые/whitespace значения превращаются в None."""
    cfg_default = LLMConfig()
    assert cfg_default.cache_busting_prefix is None
    assert pytest.approx(cfg_default.input_price_per_m) == 0.14
    assert pytest.approx(cfg_default.cache_read_price_per_m) == 0.028
    assert pytest.approx(cfg_default.output_price_per_m) == 0.28

    cfg_empty = LLMConfig(cache_busting_prefix="   ")
    assert cfg_empty.cache_busting_prefix is None

    cfg_set = LLMConfig(cache_busting_prefix="run-X")
    assert cfg_set.cache_busting_prefix == "run-X"


def test_perf_summary_aggregates_cached_tokens(tmp_path: Path) -> None:
    """`_build_perf_summary` суммирует cached_tokens и считает экономию."""
    # Готовим минимальный движок и пишем рукотворный trace.
    cfg = ScenarioConfig(ticks=1)
    cfg.llm.input_price_per_m = 0.14
    cfg.llm.cache_read_price_per_m = 0.028
    cfg.llm.output_price_per_m = 0.28

    artifacts = default_artifacts(tmp_path)
    engine = WorldEngine(cfg=cfg, artifacts=artifacts)

    trace_lines = [
        {
            "role": "agent",
            "tick": 0,
            "duration_ms": 1000,
            "usage": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 50_000,
                "cached_tokens": 800_000,
            },
            "meta": {},
        },
        {
            "role": "arbiter",
            "tick": 0,
            "duration_ms": 500,
            "usage": {
                "prompt_tokens": 500_000,
                "completion_tokens": 20_000,
                "cached_tokens": 400_000,
            },
            "meta": {},
        },
    ]
    artifacts.trace_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in trace_lines) + "\n",
        encoding="utf-8",
    )

    summary = engine._build_perf_summary()

    overall = summary["overall"]
    assert overall["prompt_tokens"] == 1_500_000
    assert overall["cached_tokens"] == 1_200_000
    # 1_200_000 / 1_500_000 = 0.8
    assert overall["cache_hit_ratio"] == pytest.approx(0.8)
    # savings = 1.2M * (0.14 - 0.028) = 0.1344
    assert overall["cache_savings_usd"] == pytest.approx(0.1344, rel=1e-3)
    # total = (1.5-1.2)M*0.14 + 1.2M*0.028 + 0.07M*0.28 = 0.042 + 0.0336 + 0.0196 = 0.0952
    assert overall["total_cost_usd"] == pytest.approx(0.0952, rel=1e-3)

    by_phase = summary["by_phase"]
    assert by_phase["agent"]["cached_tokens"] == 800_000
    assert by_phase["agent"]["cache_hit_ratio"] == pytest.approx(0.8)
    assert by_phase["arbiter"]["cached_tokens"] == 400_000
