"""События мира и JSONL EventLog.

В MAGISTRY-LC события — это "истина": именно они фиксируют применённые изменения.
LLM-трассировка (prompts/responses) пишется отдельно, чтобы не было утечки
в world-gen и чтобы промпты не раздували контекст симуляции.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .ids import INTERNAL_AUDIENCE, PUBLIC_AUDIENCE, is_audience_ref


class Event(BaseModel):
    """Событие мира, сериализуемое в JSONL."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    event_type: str
    actor_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    audience: list[str] = Field(default_factory=lambda: [INTERNAL_AUDIENCE])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field(return_type=int)
    @property
    def round(self) -> int:
        """Совместимый alias для legacy web/UI слоя."""
        return self.tick

    @computed_field(return_type=str)
    @property
    def agent_id(self) -> str:
        """Совместимый alias для legacy web/UI слоя."""
        if self.event_type == "reputation_snapshot":
            target = self.payload.get("target_agent_id")
            if isinstance(target, str) and target:
                return target
        return self.actor_id or ""

    def validate_audience(self) -> None:
        """Проверить корректность ссылок на аудиторию.

        Raises:
            ValueError: Если audience пустой или содержит мусор.
        """
        if not self.audience:
            raise ValueError("Event audience must be non-empty")
        for a in self.audience:
            if not isinstance(a, str) or not a:
                raise ValueError(f"Invalid audience entry: {a!r}")
            if not (is_audience_ref(a) or ":" in a):
                # Требуем либо aud:*, либо типизированный entity id.
                raise ValueError(f"Audience must be aud:* or typed id, got: {a!r}")

    @staticmethod
    def public(tick: int, event_type: str, actor_id: str | None, payload: dict[str, Any]) -> "Event":
        """Создать публичное событие."""
        return Event(
            tick=tick,
            event_type=event_type,
            actor_id=actor_id,
            payload=payload,
            audience=[PUBLIC_AUDIENCE],
        )


@dataclass(slots=True)
class EventLog:
    """JSONL лог событий."""

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> None:
        """Добавить событие в лог."""
        event.validate_audience()
        record = event.model_dump(mode="json")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def extend(self, events: Iterable[Event]) -> None:
        """Добавить пачку событий."""
        with self.path.open("a", encoding="utf-8") as f:
            for event in events:
                event.validate_audience()
                record = event.model_dump(mode="json")
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def iter_events(self) -> Iterable[Event]:
        """Прочитать события из файла.

        Возвращает всегда один тип (list[Event]) и не держит файл открытым во время итерации.
        """
        if not self.path.exists():
            return []
        events: list[Event] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if isinstance(data, dict):
                data.pop("round", None)
                data.pop("agent_id", None)
            events.append(Event.model_validate(data))
        return events
