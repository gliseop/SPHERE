"""Тесты обновленной talk_to с тредами."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from magistry_sim.config import AgentProfile
from magistry_sim.conversation import ChannelType, ConversationManager
from magistry_sim.sim_clock import SimClock
from magistry_sim.state import WorldState
from magistry_sim.tools.communication import talk_to_threaded

MSK = timezone(timedelta(hours=3))


def test_talk_to_creates_thread_events():
    """talk_to_threaded создает отдельные события на каждую реплику."""
    mock_runner = MagicMock()
    mock_runner.run_reply.side_effect = [
        "Привет, Игорь! Что нового?",
        "",  # Конец диалога
    ]

    state = WorldState()
    state.agents = {
        "off_1": AgentProfile(
            id="off_1",
            name="Игорь",
            position="Начальник",
        ),
        "biz_1": AgentProfile(
            id="biz_1",
            name="Сергей",
            position="Подрядчик",
        ),
    }
    state.graph.add_agent("off_1")
    state.graph.add_agent("biz_1")

    cm = ConversationManager()
    clock = SimClock(datetime(2026, 2, 16, 11, 30, tzinfo=MSK))
    events = talk_to_threaded(
        caller_id="off_1",
        agent_id="biz_1",
        message="Сергей, привет!",
        channel=ChannelType.TELEGRAM,
        private=True,
        state=state,
        runner=mock_runner,
        conversation_manager=cm,
        clock=clock,
    )

    assert len(events) >= 2
    assert events[0]["event_type"] == "message"
    assert events[0]["payload"]["thread_id"].startswith("T-")
    assert events[0]["payload"]["channel"] == "telegram"
    assert events[0]["agent_id"] == "off_1"
    assert events[1]["agent_id"] == "biz_1"
    assert len(state.messages) == len(events)
    # I-1: timestamps должны отличаться между репликами
    assert events[0]["timestamp"] != events[1]["timestamp"]


def test_talk_to_threaded_returns_empty_for_invalid_target():
    state = WorldState()
    state.agents["off_1"] = AgentProfile(
        id="off_1",
        name="Игорь",
        position="Начальник",
    )
    cm = ConversationManager()
    clock = SimClock(datetime(2026, 2, 16, 11, 30, tzinfo=MSK))
    events = talk_to_threaded(
        caller_id="off_1",
        agent_id="ghost",
        message="Привет",
        channel=ChannelType.TELEGRAM,
        private=True,
        state=state,
        runner=MagicMock(),
        conversation_manager=cm,
        clock=clock,
    )
    assert events == []
