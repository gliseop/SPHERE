"""LLM-обвязка для MAGISTRY-LC.

Использует ``OpenAICompatibleProvider`` из пакета ``magistry_lc.llm``:
- поддержка OpenAI-compatible API (включая OpenRouter);
- ``provider_order`` (например, Groq по умолчанию);
- кеш/логирование/ретраи.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from ..config import LLMConfig
from .protocols import LLMProvider, LLMResponse, StructuredLLMResponse
from .providers import OpenAICompatibleProvider
from ..tracing import TraceLog, TraceSpan


def create_llm_provider(cfg: LLMConfig) -> LLMProvider:
    """Создать LLM провайдер по конфигу."""
    api_key = os.getenv(cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"{cfg.api_key_env} is not set")
    return OpenAICompatibleProvider(
        model=cfg.model,
        api_key=api_key,
        base_url=cfg.base_url,
        provider_order=list(cfg.provider_order) if cfg.provider_order else None,
        use_tool_calls=cfg.use_tool_calls,
    )


@dataclass(slots=True)
class LLMCaller:
    """Обёртка, которая пишет trace spans вокруг вызовов LLM."""

    provider: LLMProvider
    trace: TraceLog

    async def generate(
        self,
        *,
        role: str,
        name: str,
        tick: int,
        system: str,
        user: str,
        temperature: float,
    ) -> LLMResponse:
        """Вызвать LLM и залогировать trace."""
        started = time.monotonic()
        span = TraceSpan(role=role, name=name, tick=tick, system=system, user=user)
        try:
            import asyncio

            resp: LLMResponse = await asyncio.to_thread(
                self.provider.generate, system, user, temperature
            )
            span.response = resp.text
            span.model = resp.model
            span.usage = resp.usage or {}
            return resp
        except Exception as exc:
            span.error = {"type": exc.__class__.__name__, "message": str(exc)}
            raise
        finally:
            span.duration_ms = (time.monotonic() - started) * 1000.0
            self.trace.append(span)

    async def generate_structured(
        self,
        *,
        role: str,
        name: str,
        tick: int,
        system: str,
        user: str,
        schema: dict[str, Any],
        temperature: float,
    ) -> StructuredLLMResponse:
        """Вызвать LLM structured output и залогировать trace."""
        started = time.monotonic()
        span = TraceSpan(role=role, name=name, tick=tick, system=system, user=user)
        try:
            import asyncio

            resp: StructuredLLMResponse = await asyncio.to_thread(
                self.provider.generate_structured, system, user, schema, temperature
            )
            span.response = str(resp.data)
            span.model = resp.model
            span.usage = resp.usage or {}
            return resp
        except Exception as exc:
            span.error = {"type": exc.__class__.__name__, "message": str(exc)}
            raise
        finally:
            span.duration_ms = (time.monotonic() - started) * 1000.0
            self.trace.append(span)
