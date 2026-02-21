"""Трассировка LLM-вызовов в JSONL."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .llm import LLMProvider, LLMResponse, StructuredLLMResponse


class LLMTracer:
    """Сборщик трассировочных span-ов.

    Каждый LLM-вызов (агент, арбитр, генератор среды, классификатор)
    записывается как отдельный span со всеми промптами, ответом,
    моделью и статистикой токенов. Накопленные span-ы сохраняются
    в JSONL для последующего анализа.

    Attributes:
        spans: Список записанных span-ов.
    """

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    def record(
        self,
        role: str,
        agent_id: str,
        round_num: int,
        system: str,
        user: str,
        response: str,
        model: str,
        usage: dict[str, Any],
        duration_ms: float = 0.0,
    ) -> None:
        """Записать span.

        Args:
            role: Роль вызова (agent, arbiter, world_gen, classifier).
            agent_id: Идентификатор агента.
            round_num: Номер раунда.
            system: Системный промпт.
            user: Пользовательский промпт.
            response: Ответ модели.
            model: Имя модели.
            usage: Статистика токенов.
            duration_ms: Длительность вызова в миллисекундах.
        """
        self.spans.append({
            "role": role,
            "agent_id": agent_id,
            "round_num": round_num,
            "system": system,
            "user": user,
            "response": response,
            "model": model,
            "usage": usage,
            "duration_ms": duration_ms,
        })

    @property
    def total_tokens(self) -> int:
        """Общее количество токенов по всем span-ам."""
        total = 0
        for span in self.spans:
            usage = span.get("usage", {})
            total += usage.get("prompt_tokens", 0)
            total += usage.get("completion_tokens", 0)
        return total

    def save_jsonl(self, path: Path) -> None:
        """Сохранить трассы в JSONL.

        Args:
            path: Путь к файлу.
        """
        with open(path, "w", encoding="utf-8") as f:
            for span in self.spans:
                f.write(json.dumps(span, ensure_ascii=False) + "\n")


class TracingLLMProvider:
    """Обёртка над LLMProvider с записью трасс.

    Делегирует вызовы generate/generate_structured внутреннему
    провайдеру, замеряет время и записывает span через LLMTracer.
    Атрибут round_num обновляется извне на каждом раунде симуляции.

    Attributes:
        role: Роль вызова (agent, arbiter и т.д.).
        agent_id: Идентификатор агента.
        round_num: Номер текущего раунда (обновляется извне).
    """

    def __init__(
        self,
        inner: LLMProvider,
        tracer: LLMTracer,
        role: str = "agent",
        agent_id: str = "",
    ) -> None:
        """Инициализировать обёртку.

        Args:
            inner: Оборачиваемый провайдер.
            tracer: Сборщик трасс.
            role: Роль вызова.
            agent_id: Идентификатор агента (может обновляться).
        """
        self._inner = inner
        self._tracer = tracer
        self.role = role
        self.agent_id = agent_id
        self.round_num: int = 0

    def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Сгенерировать ответ с записью трассы.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            temperature: Температура генерации.

        Returns:
            Ответ LLM.
        """
        start = time.monotonic()
        resp = self._inner.generate(system, user, temperature)
        duration_ms = (time.monotonic() - start) * 1000

        self._tracer.record(
            role=self.role,
            agent_id=self.agent_id,
            round_num=self.round_num,
            system=system,
            user=user,
            response=resp.text,
            model=resp.model,
            usage=resp.usage,
            duration_ms=round(duration_ms, 1),
        )
        return resp

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        """Сгенерировать structured-ответ с записью трассы.

        Args:
            system: Системный промпт.
            user: Пользовательский промпт.
            schema: JSON-схема.
            temperature: Температура генерации.

        Returns:
            Structured-ответ LLM.
        """
        start = time.monotonic()
        resp = self._inner.generate_structured(system, user, schema, temperature)
        duration_ms = (time.monotonic() - start) * 1000

        self._tracer.record(
            role=self.role,
            agent_id=self.agent_id,
            round_num=self.round_num,
            system=system,
            user=user,
            response=json.dumps(resp.data, ensure_ascii=False),
            model=resp.model,
            usage=resp.usage,
            duration_ms=round(duration_ms, 1),
        )
        return resp
