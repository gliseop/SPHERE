"""Тесты Scheduler — приоритетная очередь пробуждений агентов."""

from datetime import datetime, timedelta, timezone

from magistry_sim.scheduler import Scheduler

MSK = timezone(timedelta(hours=3))
BASE = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)


def test_schedule_and_pop():
    scheduler = Scheduler()
    scheduler.schedule("off_1", BASE + timedelta(hours=1))
    scheduler.schedule("biz_1", BASE + timedelta(minutes=30))
    agent, wake = scheduler.next()
    assert agent == "biz_1"
    assert wake == BASE + timedelta(minutes=30)


def test_empty_scheduler():
    scheduler = Scheduler()
    assert scheduler.is_empty


def test_schedule_multiple():
    scheduler = Scheduler()
    scheduler.schedule("a", BASE + timedelta(hours=3))
    scheduler.schedule("b", BASE + timedelta(hours=1))
    scheduler.schedule("c", BASE + timedelta(hours=2))
    order: list[str] = []
    while not scheduler.is_empty:
        agent, _ = scheduler.next()
        order.append(agent)
    assert order == ["b", "c", "a"]


def test_peek_time():
    s = Scheduler()
    assert s.peek_time() is None
    s.schedule("a", BASE + timedelta(hours=1))
    assert s.peek_time() == BASE + timedelta(hours=1)
    s.next()  # remove it
    assert s.peek_time() is None


def test_fifo_same_time():
    """Agents scheduled at the same time should come out in insertion order."""
    s = Scheduler()
    same_time = BASE + timedelta(hours=1)
    s.schedule("first", same_time)
    s.schedule("second", same_time)
    s.schedule("third", same_time)
    order = []
    while not s.is_empty:
        agent, _ = s.next()
        order.append(agent)
    assert order == ["first", "second", "third"]
