"""Генератор структурных и стохастических событий мира."""

from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel, Field


class ScheduledEvent(BaseModel):
    """Запланированное событие, привязанное к конкретному раунду.

    Attributes:
        round: Номер раунда, в котором событие должно произойти.
        event_type: Тип события.
        params: Произвольные параметры события.
    """

    model_config = {"extra": "forbid"}

    round: int
    event_type: str
    params: dict[str, Any] = Field(default_factory=dict)


class StochasticConfig(BaseModel):
    """Вероятности стохастических событий за раунд.

    Каждое поле задаёт вероятность срабатывания соответствующего
    типа случайного события в каждом раунде симуляции.

    Attributes:
        journalist_investigation: Вероятность журналистского расследования.
        citizen_complaint: Вероятность гражданской жалобы.
        external_audit: Вероятность внешнего аудита.
        economic_crisis: Вероятность экономического кризиса.
        law_change: Вероятность изменения законодательства.
    """

    model_config = {"extra": "forbid"}

    journalist_investigation: float = Field(default=0.05, ge=0.0, le=1.0)
    citizen_complaint: float = Field(default=0.1, ge=0.0, le=1.0)
    external_audit: float = Field(default=0.03, ge=0.0, le=1.0)
    economic_crisis: float = Field(default=0.02, ge=0.0, le=1.0)
    law_change: float = Field(default=0.01, ge=0.0, le=1.0)


class EventGenerator:
    """Генератор мировых событий.

    Объединяет два механизма порождения событий: запланированные
    (привязанные к конкретному раунду) и стохастические (с заданной
    вероятностью в каждом раунде).

    Args:
        scheduled: Список запланированных событий.
        stochastic: Конфигурация вероятностей стохастических событий.
        seed: Зерно генератора случайных чисел.
    """

    def __init__(
        self,
        scheduled: list[ScheduledEvent] | None = None,
        stochastic: StochasticConfig | None = None,
        seed: int = 42,
    ) -> None:
        self._scheduled = scheduled or []
        self._stochastic = stochastic or StochasticConfig()
        self._rng = random.Random(seed)

    def generate(
        self, round_num: int, world_state: Any | None = None
    ) -> list[dict[str, Any]]:
        """Генерирует события для данного раунда.

        Args:
            round_num: Номер раунда.
            world_state: Состояние мира (для контекстно-зависимых событий).

        Returns:
            Список словарей с описанием событий.
        """
        events: list[dict[str, Any]] = []

        for se in self._scheduled:
            if se.round == round_num:
                events.append({
                    "event_type": se.event_type,
                    "params": se.params,
                    "source": "scheduled",
                })

        stoch = self._stochastic
        stoch_map = {
            "journalist_investigation": stoch.journalist_investigation,
            "citizen_complaint": stoch.citizen_complaint,
            "external_audit": stoch.external_audit,
            "economic_crisis": stoch.economic_crisis,
            "law_change": stoch.law_change,
        }
        for event_type, prob in stoch_map.items():
            if self._rng.random() < prob:
                events.append({
                    "event_type": event_type,
                    "params": {},
                    "source": "stochastic",
                })

        return events
