"""Глобальные часы симуляции с непрерывным временем."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


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


@dataclass
class WorkSchedule:
    """Рабочее расписание для симуляции.

    Определяет рабочие часы, рабочие дни недели и праздники.
    Используется для переноса пробуждений агентов на рабочее время.

    Args:
        work_start_hour: Час начала рабочего дня (0-23).
        work_end_hour: Час окончания рабочего дня (0-23).
        work_days: Рабочие дни недели (0=Пн, 6=Вс).
        holidays: Список праздничных/нерабочих дат.
    """

    work_start_hour: int = 9
    work_end_hour: int = 18
    work_days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    holidays: list[date] = field(default_factory=list)

    def is_work_time(self, dt: datetime) -> bool:
        """Проверить, попадает ли время в рабочие часы.

        Args:
            dt: Проверяемое время.

        Returns:
            True если время рабочее.
        """
        if dt.weekday() not in self.work_days:
            return False
        if dt.date() in self.holidays:
            return False
        return self.work_start_hour <= dt.hour < self.work_end_hour

    def next_work_time(self, dt: datetime) -> datetime:
        """Вычислить ближайшее рабочее время.

        Если dt уже в рабочих часах, возвращает dt без изменений.
        Иначе переносит на начало следующего рабочего дня.

        Args:
            dt: Исходное время.

        Returns:
            Ближайшее рабочее время (>= dt).
        """
        if self.is_work_time(dt):
            return dt

        # Если ещё до начала рабочего дня — переносим на начало этого дня
        candidate = dt.replace(
            hour=self.work_start_hour, minute=0, second=0, microsecond=0
        )
        if candidate > dt and self._is_work_day(candidate):
            return candidate

        # Ищем следующий рабочий день (до 14 дней вперёд для безопасности)
        for days_ahead in range(1, 15):
            candidate = (dt + timedelta(days=days_ahead)).replace(
                hour=self.work_start_hour, minute=0, second=0, microsecond=0
            )
            if self._is_work_day(candidate):
                return candidate

        # Крайний случай: просто следующий день (не должно случаться)
        return (dt + timedelta(days=1)).replace(
            hour=self.work_start_hour, minute=0, second=0, microsecond=0
        )

    def end_of_work_day(self, dt: datetime) -> datetime:
        """Вычислить время окончания рабочего дня для указанной даты.

        Args:
            dt: Дата/время.

        Returns:
            Время окончания рабочего дня.
        """
        return dt.replace(
            hour=self.work_end_hour, minute=0, second=0, microsecond=0
        )

    def _is_work_day(self, dt: datetime) -> bool:
        """Проверить, является ли дата рабочим днём (без учёта часов)."""
        if dt.weekday() not in self.work_days:
            return False
        return dt.date() not in self.holidays
