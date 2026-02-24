"""Интеграционный smoke-тест AsyncEnvironment с mock-раннером."""

import asyncio
from datetime import datetime, timedelta, timezone

from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.config import AgentProfile, ScenarioConfig
from magistry_sim.llm import MockLLMProvider

MSK = timezone(timedelta(hours=3))


class _ThreadedSmokeRunner:
    """Минимальный раннер для проверки end-to-end пути."""

    def __init__(self) -> None:
        self._did_talk = False

    def run_turn(self, agent_id, situation, tools, state):  # noqa: ANN001
        del situation, tools, state
        if agent_id == "off_1" and not self._did_talk:
            self._did_talk = True
            return [
                {
                    "tool": "talk_to",
                    "args": {
                        "agent_id": "biz_1",
                        "message": "Сергей, как идет подготовка?",
                        "private": True,
                        "channel": "telegram",
                    },
                }
            ]
        return []

    def run_reply(
        self, agent_id, message, sender_id, context, state  # noqa: ANN001
    ):
        del agent_id, message, sender_id, context, state
        return "Подготовка идет по плану."


def test_full_simulation_with_mock_runner():
    """Полный прогон: события создаются с timestamp и без обязательного round."""
    config = ScenarioConfig(
        id="S0",
        title="Smoke test",
        description="Integration test",
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 16, 10, 0, tzinfo=MSK),
        seed=42,
        agents=[
            AgentProfile(
                id="off_1",
                name="Козлов И.М.",
                position="Начальник",
            ),
            AgentProfile(
                id="biz_1",
                name="Петров С.И.",
                position="Директор",
            ),
        ],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_ThreadedSmokeRunner(),
        llm=MockLLMProvider(),
    )
    result = asyncio.run(env.run())
    assert result.events_count > 0

    events = result.state.event_log.all_events
    assert events
    for event in events:
        assert event.timestamp
        assert isinstance(event.timestamp, str)
        assert event.round is None
