"""Тесты адаптации build_situation под временное окружение."""

from datetime import datetime, timedelta, timezone

from magistry_sim.config import AgentProfile
from magistry_sim.context import build_situation
from magistry_sim.state import Message, WorldState

MSK = timezone(timedelta(hours=3))


def test_build_situation_uses_current_time_when_available():
    state = WorldState()
    state.agents["off_1"] = AgentProfile(
        id="off_1",
        name="Козлов И.М.",
        position="Начальник отдела",
    )
    state.reputation = {}
    state.graph.add_agent("off_1")
    state.current_time = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)

    result = build_situation("off_1", state)
    assert "2026-02-16T09:00:00+03:00" in result
    assert "Сейчас раунд" not in result


def test_build_situation_includes_recent_messages_by_timestamp():
    state = WorldState()
    state.agents["off_1"] = AgentProfile(
        id="off_1",
        name="Козлов И.М.",
        position="Начальник отдела",
    )
    state.agents["biz_1"] = AgentProfile(
        id="biz_1",
        name="Петров С.И.",
        position="Директор",
    )
    state.graph.add_agent("off_1")
    state.graph.add_agent("biz_1")
    state.current_time = datetime(2026, 2, 16, 11, 0, tzinfo=MSK)
    state.messages.append(
        Message(
            from_id="biz_1",
            to_id="off_1",
            content="Добрый день",
            timestamp="2026-02-16T10:30:00+03:00",
            round=100,  # проверка, что фильтр идет по времени, а не по round
        )
    )

    result = build_situation("off_1", state)
    assert "Входящие сообщения" in result
    assert "Добрый день" in result
