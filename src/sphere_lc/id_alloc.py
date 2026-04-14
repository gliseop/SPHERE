"""Детерминированное выделение новых ID."""

from __future__ import annotations

from dataclasses import dataclass

from .ids import EntityKind, make_id


@dataclass(slots=True)
class IdAllocator:
    """Детерминированный генератор ID внутри прогона."""

    counters: dict[EntityKind, int] | None = None

    def __post_init__(self) -> None:
        if self.counters is None:
            self.counters = {}

    def next_id(self, kind: EntityKind, *, tick: int) -> str:
        """Получить следующий ID заданного вида."""
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        return make_id(kind, f"{tick}_{n}")

