"""Планировщик пробуждений агентов на основе приоритетной очереди."""

from __future__ import annotations

import heapq
from datetime import datetime


class Scheduler:
    """Приоритетная очередь пробуждений агентов."""

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, str]] = []
        self._counter = 0

    def schedule(self, agent_id: str, wake_time: datetime) -> None:
        """Запланировать пробуждение агента."""
        self._counter += 1
        heapq.heappush(
            self._heap,
            (wake_time, self._counter, agent_id),
        )

    def next(self) -> tuple[str, datetime]:
        """Извлечь следующего агента."""
        wake_time, _, agent_id = heapq.heappop(self._heap)
        return agent_id, wake_time

    @property
    def is_empty(self) -> bool:
        """Очередь пуста."""
        return len(self._heap) == 0

    def peek_time(self) -> datetime | None:
        """Время ближайшего пробуждения без извлечения."""
        if self._heap:
            return self._heap[0][0]
        return None
