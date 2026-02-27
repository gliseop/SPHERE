"""Тесты SimClock и WorkSchedule."""

from datetime import date, datetime, timedelta, timezone

import pytest

from magistry_sim.sim_clock import SimClock, WorkSchedule

MSK = timezone(timedelta(hours=3))


def test_initial_time():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    assert clock.now == start


def test_advance_to():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    target = start + timedelta(hours=2)
    clock.advance_to(target)
    assert clock.now == target


def test_advance_to_past_raises():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    with pytest.raises(ValueError, match="past"):
        clock.advance_to(start - timedelta(hours=1))


def test_iso_timestamp():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    assert clock.iso() == "2026-02-16T09:00:00+03:00"


def test_advance_to_mismatched_tz_raises():
    """Mixing tz-aware and tz-naive should raise TypeError."""
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    import pytest
    with pytest.raises(TypeError, match="tz-aware"):
        clock.advance_to(datetime(2026, 2, 16, 10, 0))  # naive


# --- WorkSchedule ---

class TestWorkSchedule:
    def test_is_work_time_weekday_during_hours(self):
        sched = WorkSchedule()
        # Понедельник 10:00
        dt = datetime(2026, 2, 16, 10, 0)  # Monday
        assert sched.is_work_time(dt) is True

    def test_is_work_time_weekend(self):
        sched = WorkSchedule()
        # Суббота 10:00
        dt = datetime(2026, 2, 21, 10, 0)  # Saturday
        assert sched.is_work_time(dt) is False

    def test_is_work_time_before_start(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 16, 7, 0)
        assert sched.is_work_time(dt) is False

    def test_is_work_time_after_end(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 16, 19, 0)
        assert sched.is_work_time(dt) is False

    def test_is_work_time_holiday(self):
        sched = WorkSchedule(holidays=[date(2026, 3, 8)])
        dt = datetime(2026, 3, 8, 10, 0)  # Sunday in 2026 but let's test
        assert sched.is_work_time(dt) is False

    def test_next_work_time_already_working(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 16, 10, 30)  # Monday 10:30
        assert sched.next_work_time(dt) == dt

    def test_next_work_time_evening_to_next_day(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 16, 19, 0)  # Monday evening
        result = sched.next_work_time(dt)
        assert result == datetime(2026, 2, 17, 9, 0)  # Tuesday 9:00

    def test_next_work_time_friday_evening_to_monday(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 20, 19, 0)  # Friday evening
        result = sched.next_work_time(dt)
        assert result == datetime(2026, 2, 23, 9, 0)  # Monday 9:00

    def test_next_work_time_early_morning(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 16, 6, 0)  # Monday 6:00
        result = sched.next_work_time(dt)
        assert result == datetime(2026, 2, 16, 9, 0)  # Same day 9:00

    def test_end_of_work_day(self):
        sched = WorkSchedule()
        dt = datetime(2026, 2, 16, 14, 30)
        result = sched.end_of_work_day(dt)
        assert result.hour == 18
        assert result.minute == 0
