"""Тесты обратной совместимости Event с необязательным round."""

import json

from magistry_sim.events import Event, EventLog


def test_event_without_round():
    event = Event(
        event_type="message",
        agent_id="off_1",
        payload={"content": "hello"},
        timestamp="2026-02-16T09:00:00+03:00",
    )
    assert event.round is None
    dumped = json.loads(event.model_dump_json())
    assert "round" not in dumped or dumped["round"] is None


def test_event_with_round_backward_compat():
    event = Event(
        round=5,
        event_type="case_opened",
        agent_id="off_1",
        payload={},
        timestamp="2026-02-16T09:00:00+03:00",
    )
    assert event.round == 5


def test_eventlog_log_without_round():
    log = EventLog()
    event = log.log(
        event_type="message",
        agent_id="off_1",
        payload={"thread_id": "T-001"},
        timestamp="2026-02-16T09:00:00+03:00",
    )
    assert event.event_type == "message"
    assert event.round is None


def test_eventlog_log_with_round():
    log = EventLog()
    event = log.log(
        round=3,
        event_type="case_opened",
        agent_id="off_1",
        payload={},
    )
    assert event.round == 3
