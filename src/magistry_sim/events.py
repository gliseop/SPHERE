"""Журнал событий симуляции."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Событие симуляции."""

    model_config = {"extra": "forbid"}

    round: int
    event_type: str
    agent_id: str = ""
    payload: dict = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class EventLog:
    """Журнал событий с записью в JSONL."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def log(
        self,
        round: int,
        event_type: str,
        agent_id: str = "",
        payload: dict | None = None,
    ) -> Event:
        """Записать событие.

        Args:
            round: Номер раунда.
            event_type: Тип события.
            agent_id: Идентификатор агента.
            payload: Дополнительные данные.

        Returns:
            Записанное событие.
        """
        event = Event(
            round=round,
            event_type=event_type,
            agent_id=agent_id,
            payload=payload or {},
        )
        self._events.append(event)
        return event

    def get_events(
        self,
        event_type: str | None = None,
        agent_id: str | None = None,
        round: int | None = None,
    ) -> list[Event]:
        """Получить события с фильтрацией.

        Args:
            event_type: Фильтр по типу события.
            agent_id: Фильтр по агенту.
            round: Фильтр по раунду.

        Returns:
            Список событий.
        """
        result = self._events
        if event_type is not None:
            result = [e for e in result if e.event_type == event_type]
        if agent_id is not None:
            result = [e for e in result if e.agent_id == agent_id]
        if round is not None:
            result = [e for e in result if e.round == round]
        return result

    @property
    def all_events(self) -> list[Event]:
        """Все события."""
        return list(self._events)

    def save_jsonl(self, path: Path) -> None:
        """Сохранить журнал в JSONL.

        Args:
            path: Путь к файлу.
        """
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(event.model_dump_json() + "\n")

    def load_jsonl(self, path: Path) -> None:
        """Загрузить журнал из JSONL.

        Args:
            path: Путь к файлу.
        """
        self._events.clear()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._events.append(Event.model_validate_json(line))
