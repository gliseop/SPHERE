"""Тесты для LLM Engine."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from magistry_sim.llm import (
    LLMCache,
    LLMResponse,
    MockLLMProvider,
    create_provider,
)


class TestMockLLMProvider:
    """Тесты mock-провайдера."""

    def test_deterministic_responses(self) -> None:
        """Mock с одинаковым seed даёт одинаковые ответы."""
        provider1 = MockLLMProvider(seed=42)
        provider2 = MockLLMProvider(seed=42)

        async def run() -> tuple[LLMResponse, LLMResponse]:
            r1 = await provider1.generate("system", "user")
            r2 = await provider2.generate("system", "user")
            return r1, r2

        r1, r2 = asyncio.run(run())

        assert r1.text == r2.text
        assert r1.response_time_ms == r2.response_time_ms

    def test_different_seeds_different_responses(self) -> None:
        """Разные seed дают разные ответы."""
        provider1 = MockLLMProvider(seed=42)
        provider2 = MockLLMProvider(seed=123)

        async def run() -> tuple[LLMResponse, LLMResponse]:
            r1 = await provider1.generate("system", "user")
            r2 = await provider2.generate("system", "user")
            return r1, r2

        r1, r2 = asyncio.run(run())

        # Разное время ответа из-за разного seed
        assert r1.response_time_ms != r2.response_time_ms

    def test_call_count(self) -> None:
        """Счётчик вызовов работает."""
        provider = MockLLMProvider(seed=42)

        async def run() -> None:
            await provider.generate("s1", "u1")
            await provider.generate("s2", "u2")
            await provider.generate("s3", "u3")

        asyncio.run(run())

        assert provider.call_count == 3
        assert len(provider.call_history) == 3

    def test_reset(self) -> None:
        """Сброс состояния работает."""
        provider = MockLLMProvider(seed=42)

        async def run() -> None:
            await provider.generate("s", "u")

        asyncio.run(run())
        assert provider.call_count == 1

        provider.reset()
        assert provider.call_count == 0
        assert len(provider.call_history) == 0

    def test_prompt_type_detection(self) -> None:
        """Определение типа промпта по ключевым словам."""
        provider = MockLLMProvider(seed=42)

        async def run() -> list[str]:
            results = []

            # Negotiation
            r = await provider.generate("", "Давайте договоримся о проценте")
            results.append("negotiation" if "обсудим условия" in r.text.lower() else "other")

            # Alibi
            r = await provider.generate("", "Обоснуй выбор победителя")
            results.append("alibi" if "обоснован" in r.text.lower() else "other")

            return results

        results = asyncio.run(run())
        assert results[0] == "negotiation"
        assert results[1] == "alibi"


class TestLLMCache:
    """Тесты кэширования."""

    def test_cache_miss_and_hit(self) -> None:
        """Кэш сохраняет и возвращает ответы."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = LLMCache(Path(tmpdir) / "test.db")

            # Miss
            result = cache.get("sys", "usr", "gpt-4", 0.7)
            assert result is None

            # Set
            response = LLMResponse(
                text="test response",
                response_time_ms=100.0,
                prompt_tokens=10,
                completion_tokens=5,
                cached=False,
                model="gpt-4",
            )
            cache.set("sys", "usr", "gpt-4", 0.7, response)

            # Hit
            cached = cache.get("sys", "usr", "gpt-4", 0.7)
            assert cached is not None
            assert cached.text == "test response"
            assert cached.cached is True  # Флаг cached устанавливается при чтении

    def test_different_params_different_cache(self) -> None:
        """Разные параметры = разные ключи кэша."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = LLMCache(Path(tmpdir) / "test.db")

            response = LLMResponse(
                text="test",
                response_time_ms=100.0,
                prompt_tokens=10,
                completion_tokens=5,
                cached=False,
                model="gpt-4",
            )
            cache.set("sys", "usr", "gpt-4", 0.7, response)

            # Другая температура = miss
            assert cache.get("sys", "usr", "gpt-4", 0.5) is None

            # Другая модель = miss
            assert cache.get("sys", "usr", "gpt-3.5", 0.7) is None

            # Тот же запрос = hit
            assert cache.get("sys", "usr", "gpt-4", 0.7) is not None


class TestCreateProvider:
    """Тесты фабрики провайдеров."""

    def test_create_mock_provider(self) -> None:
        """Создание mock-провайдера."""
        provider = create_provider(mock=True, mock_seed=123)

        assert isinstance(provider, MockLLMProvider)
        assert provider.seed == 123

    def test_mock_provider_is_llm_provider(self) -> None:
        """Mock-провайдер соответствует протоколу LLMProvider."""
        from magistry_sim.llm import LLMProvider

        provider = create_provider(mock=True)
        assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_mock_async_generate() -> None:
    """Асинхронная генерация работает."""
    provider = create_provider(mock=True)
    response = await provider.generate("system prompt", "user message")

    assert isinstance(response, LLMResponse)
    assert response.text
    assert response.response_time_ms > 0
    assert response.model == "mock"
