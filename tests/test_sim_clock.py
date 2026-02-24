"""Тесты SimClock — глобальные часы симуляции."""

from datetime import datetime, timedelta, timezone

import pytest

from magistry_sim.sim_clock import SimClock

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
