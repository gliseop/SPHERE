"""Regression tests for AsyncEnvironment tool-arg parsing."""

import asyncio
from datetime import datetime, timedelta, timezone

from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.config import AgentProfile, ScenarioConfig
from magistry_sim.llm import MockLLMProvider

MSK = timezone(timedelta(hours=3))


class _NoReplyRunner:
    """Runner that makes threaded talk end immediately."""

    def run_turn(self, agent_id, situation, tools, state):  # noqa: ANN001
        del agent_id, situation, tools, state
        return []

    def run_reply(
        self, agent_id, message, sender_id, context, state  # noqa: ANN001
    ):
        del agent_id, message, sender_id, context, state
        return ""


def test_dispatch_talk_to_parses_private_false_string():
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 16, 10, 0, tzinfo=MSK),
        seed=42,
        agents=[
            AgentProfile(id="off_1", name="Игорь", position="Начальник"),
            AgentProfile(id="biz_1", name="Сергей", position="Подрядчик"),
        ],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_NoReplyRunner(),
        llm=MockLLMProvider(),
    )
    asyncio.run(
        env._dispatch_action(
            "off_1",
            "talk_to",
            {
                "agent_id": "biz_1",
                "message": "ping",
                "channel": "telegram",
                "private": "false",
            },
        )
    )

    events = env.state.event_log.all_events
    msg = next(e for e in events if e.event_type == "message")
    assert msg.payload.get("private") is False

