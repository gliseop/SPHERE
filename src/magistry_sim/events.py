"""Журнал событий симуляции."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Событие симуляции."""

    model_config = {"extra": "forbid"}

    round: int | None = None
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
        self._stream_file: IO[str] | None = None
        self._lock = threading.Lock()

    def set_stream_path(self, path: Path) -> None:
        """Открыть файл для потоковой дозаписи событий.

        Открывает файл в режиме append и держит дескриптор открытым
        на протяжении всего прогона. Вызвать close_stream() по завершении.

        Args:
            path: Путь к файлу для дозаписи.
        """
        with self._lock:
            if self._stream_file is not None:
                self._stream_file.close()
            self._stream_file = open(path, "a", encoding="utf-8")  # noqa: WPS515

    def close_stream(self) -> None:
        """Закрыть открытый поток записи событий."""
        with self._lock:
            if self._stream_file is not None:
                self._stream_file.close()
                self._stream_file = None

    def log(
        self,
        *,
        round: int | None = None,
        event_type: str,
        agent_id: str = "",
        payload: dict | None = None,
        timestamp: str | None = None,
    ) -> Event:
        """Записать событие.

        Args:
            round: Номер раунда (для обратной совместимости).
            event_type: Тип события.
            agent_id: Идентификатор агента.
            payload: Дополнительные данные.
            timestamp: Временная метка ISO 8601 (опционально).

        Returns:
            Записанное событие.
        """
        event = Event(
            round=round,
            event_type=event_type,
            agent_id=agent_id,
            payload=payload or {},
            **({"timestamp": timestamp} if timestamp else {}),
        )
        with self._lock:
            self._events.append(event)
            if self._stream_file is not None:
                self._stream_file.write(
                    event.model_dump_json(exclude_none=True) + "\n"
                )
                self._stream_file.flush()
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
        with self._lock:
            result = list(self._events)
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
        with self._lock:
            return list(self._events)

    def save_jsonl(self, path: Path) -> None:
        """Сохранить журнал в JSONL.

        Args:
            path: Путь к файлу.
        """
        with self._lock:
            events = list(self._events)
        with open(path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(
                    event.model_dump_json(exclude_none=True) + "\n"
                )

    def load_jsonl(self, path: Path) -> None:
        """Загрузить журнал из JSONL.

        Args:
            path: Путь к файлу.
        """
        with self._lock:
            self._events.clear()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    with self._lock:
                        self._events.append(Event.model_validate_json(line))
