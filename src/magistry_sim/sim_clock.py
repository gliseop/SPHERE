"""Глобальные часы симуляции с непрерывным временем."""

from __future__ import annotations

from datetime import datetime


class SimClock:
    """Монотонные часы симуляции."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    @property
    def now(self) -> datetime:
        """Текущее время симуляции."""
        return self._now

    def advance_to(self, target: datetime) -> None:
        """Продвинуть часы к указанному времени."""
        if (self._now.tzinfo is None) != (target.tzinfo is None):
            raise TypeError(
                "Cannot mix tz-aware and tz-naive datetimes"
            )
        if target < self._now:
            raise ValueError(
                f"Cannot advance to past: {target} < {self._now}"
            )
        self._now = target

    def iso(self) -> str:
        """Текущее время в ISO 8601."""
        return self._now.isoformat()
