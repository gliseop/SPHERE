"""LLM Engine: OpenAI-совместимый провайдер с mock-режимом и кэшированием."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# Response model
# ─────────────────────────────────────────────────────────────────────────────


class LLMResponse(BaseModel):
    """Ответ LLM с реальными метриками."""

    text: str
    response_time_ms: float  # фактическое время запроса
    prompt_tokens: int
    completion_tokens: int
    cached: bool = False  # был ли ответ из кэша
    model: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Provider protocol
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """Протокол LLM-провайдера."""

    async def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...


# ─────────────────────────────────────────────────────────────────────────────
# SQLite Cache
# ─────────────────────────────────────────────────────────────────────────────


class LLMCache:
    """SQLite кэш для LLM ответов."""

    def __init__(self, db_path: Path | str = ".llm_cache.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    prompt_hash TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()

    @staticmethod
    def _hash_prompt(system: str, user: str, model: str, temperature: float) -> str:
        """Создаёт хэш для ключа кэша."""
        content = f"{model}:{temperature}:{system}:{user}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get(
        self, system: str, user: str, model: str, temperature: float
    ) -> LLMResponse | None:
        """Получить ответ из кэша."""
        prompt_hash = self._hash_prompt(system, user, model, temperature)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM cache WHERE prompt_hash = ?",
                (prompt_hash,),
            ).fetchone()
            if row:
                data = json.loads(row[0])
                data["cached"] = True
                return LLMResponse(**data)
        return None

    def set(
        self,
        system: str,
        user: str,
        model: str,
        temperature: float,
        response: LLMResponse,
    ) -> None:
        """Сохранить ответ в кэш."""
        prompt_hash = self._hash_prompt(system, user, model, temperature)
        response_json = response.model_dump_json()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache (prompt_hash, response_json, created_at)
                VALUES (?, ?, ?)
            """,
                (prompt_hash, response_json, time.time()),
            )
            conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible Provider
# ─────────────────────────────────────────────────────────────────────────────


class OpenAICompatibleProvider:
    """
    Провайдер для OpenAI-совместимых API.

    Работает с:
    - OpenAI (по умолчанию)
    - ollama (base_url="http://localhost:11434/v1")
    - together.ai (base_url="https://api.together.xyz/v1")
    - vLLM, LM Studio и др.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        cache: LLMCache | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ):
        # Lazy import — не требуем openai если используем mock
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError(
                "openai package required. Install with: pip install openai"
            ) from e

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.cache = cache
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    async def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Генерация с retry и кэшированием."""

        # Check cache first
        if self.cache:
            cached = self.cache.get(system, user, self.model, temperature)
            if cached:
                return cached

        # Retry loop with exponential backoff
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                start = time.perf_counter()
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000

                result = LLMResponse(
                    text=response.choices[0].message.content or "",
                    response_time_ms=elapsed_ms,
                    prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                    completion_tokens=response.usage.completion_tokens
                    if response.usage
                    else 0,
                    cached=False,
                    model=self.model,
                )

                # Save to cache
                if self.cache:
                    self.cache.set(system, user, self.model, temperature, result)

                return result

            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_base_delay * (2**attempt)
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"LLM request failed after {self.max_retries} attempts: {last_error}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mock Provider (for tests and CI)
# ─────────────────────────────────────────────────────────────────────────────


class MockLLMProvider:
    """
    Детерминированный mock-провайдер для тестов.

    Генерирует предсказуемые ответы на основе хэша промпта,
    что обеспечивает воспроизводимость тестов.
    """

    def __init__(self, seed: int = 42, *, response_templates: dict[str, str] | None = None):
        self.rng = Random(seed)
        self.seed = seed
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []

        # Шаблоны ответов для разных типов запросов
        self.response_templates = response_templates or {
            "negotiation": "Давайте обсудим условия. Я готов рассмотреть ваше предложение.",
            "alibi": "Выбор победителя обоснован техническими характеристиками заявки и ценовым предложением.",
            "decision": "Да, я принимаю это решение.",
            "summary": "Агент участвовал в нескольких тендерах с переменным успехом.",
            "default": "Понял. Продолжаем работу.",
        }

    def _detect_prompt_type(self, system: str, user: str) -> str:
        """Определяет тип запроса по ключевым словам."""
        combined = (system + user).lower()
        if any(w in combined for w in ["переговор", "договор", "откат", "процент", "сделка"]):
            return "negotiation"
        if any(w in combined for w in ["обоснуй", "alibi", "выбор", "победител"]):
            return "alibi"
        if any(w in combined for w in ["реши", "decision", "согласен", "принять"]):
            return "decision"
        if any(w in combined for w in ["сожми", "summary", "история", "саммари"]):
            return "summary"
        return "default"

    async def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Генерирует детерминированный mock-ответ."""
        self.call_count += 1

        # Детерминированный seed на основе промпта
        prompt_hash = hash((system, user, self.seed))
        local_rng = Random(prompt_hash)

        # Определяем тип и выбираем шаблон
        prompt_type = self._detect_prompt_type(system, user)
        base_response = self.response_templates.get(prompt_type, self.response_templates["default"])

        # Добавляем вариативность на основе хэша
        variation_suffix = f" [mock#{self.call_count}]"
        text = base_response + variation_suffix

        # Симулируем реалистичное время ответа (100-600ms)
        fake_time = 100 + local_rng.randint(0, 500)

        # Примерный подсчёт токенов
        prompt_tokens = len(system.split()) + len(user.split())
        completion_tokens = len(text.split())

        call_record = {
            "call_number": self.call_count,
            "system": system[:100],
            "user": user[:100],
            "prompt_type": prompt_type,
            "response": text,
        }
        self.call_history.append(call_record)

        return LLMResponse(
            text=text,
            response_time_ms=float(fake_time),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached=False,
            model="mock",
        )

    def reset(self) -> None:
        """Сброс состояния для нового теста."""
        self.rng = Random(self.seed)
        self.call_count = 0
        self.call_history.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Factory function
# ─────────────────────────────────────────────────────────────────────────────


def create_provider(
    *,
    mock: bool = False,
    mock_seed: int = 42,
    cache_path: Path | str | None = ".llm_cache.db",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """
    Создаёт LLM-провайдер на основе конфигурации.

    Args:
        mock: Использовать mock-провайдер (для тестов)
        mock_seed: Seed для детерминированных mock-ответов
        cache_path: Путь к SQLite кэшу (None = без кэша)
        api_key: API ключ (или из OPENAI_API_KEY)
        base_url: Base URL API (для ollama, together.ai и др.)
        model: Название модели

    Examples:
        # Реальный OpenAI
        provider = create_provider()

        # Mock для тестов
        provider = create_provider(mock=True)

        # Ollama локально
        provider = create_provider(
            base_url="http://localhost:11434/v1",
            model="llama3.2"
        )

        # Together.ai
        provider = create_provider(
            base_url="https://api.together.xyz/v1",
            api_key="...",
            model="meta-llama/Llama-3-70b-chat-hf"
        )
    """
    if mock:
        return MockLLMProvider(seed=mock_seed)

    cache = LLMCache(cache_path) if cache_path else None

    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=model or os.getenv("LLM_MODEL", "gpt-4o-mini"),
        cache=cache,
    )
