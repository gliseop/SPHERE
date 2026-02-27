"""Тесты AsyncEnvironment — асинхронный цикл симуляции."""

import asyncio
from datetime import datetime, timedelta, timezone

from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.config import AgentProfile, Capability, ScenarioConfig
from magistry_sim.llm import MockLLMProvider

MSK = timezone(timedelta(hours=3))
START = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
END = datetime(2026, 2, 16, 18, 0, tzinfo=MSK)


class _IdleRunner:
    """Раннер, который всегда бездействует."""

    def run_turn(self, agent_id, situation, tools, state):  # noqa: ANN001
        del agent_id, situation, tools, state
        return []

    def run_reply(
        self, agent_id, message, sender_id, context, state  # noqa: ANN001
    ):
        del agent_id, message, sender_id, context, state
        return ""


def test_async_env_runs_to_completion():
    """AsyncEnvironment завершается когда достигает end_time."""
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=END,
        seed=42,
        agents=[
            AgentProfile(
                id="off_1",
                name="Игорь",
                position="Начальник",
            ),
        ],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_IdleRunner(),
        llm=MockLLMProvider(),
    )
    result = asyncio.run(env.run())

    assert result is not None
    assert env.clock.now <= END
    assert len(env.state.event_log.all_events) > 0


def test_async_env_generates_events_without_agents():
    """Даже без агентов должны логироваться служебные события."""
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=START + timedelta(hours=2),
        seed=42,
        agents=[],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_IdleRunner(),
        llm=MockLLMProvider(),
    )
    asyncio.run(env.run())
    events = env.state.event_log.all_events
    assert len(events) >= 2
    assert events[0].event_type == "world_event"


def test_async_env_uses_initial_reputation_from_profile():
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=END,
        seed=42,
        agents=[
            AgentProfile(
                id="off_1",
                name="Игорь",
                position="Начальник",
                initial_reputation=7.5,
            ),
        ],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_IdleRunner(),
        llm=MockLLMProvider(),
    )
    assert env.state.reputation["off_1"].score == 7.5


def test_async_dispatch_open_case_updates_state_and_timestamp():
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=END,
        seed=42,
        agents=[
            AgentProfile(
                id="off_1",
                name="Игорь",
                position="Начальник",
                capabilities=[
                    Capability(action="open_case", case_types=[]),
                ],
            ),
        ],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_IdleRunner(),
        llm=MockLLMProvider(),
    )
    # Диспатч tool-ов в AsyncEnvironment использует текущие sim-часы
    ts_before = env.clock.iso()
    asyncio.run(
        env._dispatch_action(
            "off_1",
            "open_case",
            {
                "case_type": "procurement",
                "title": "Закупка серверов",
                "description": "Требуется серверное оборудование.",
            },
        )
    )
    assert "D-001" in env.state.cases
    ev = next(
        e
        for e in env.state.event_log.all_events
        if e.event_type == "case_opened"
    )
    assert ev.timestamp == ts_before


def test_async_reputation_tick_counts_resolutions_once():
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=START + timedelta(days=3),
        seed=42,
        agents=[
            AgentProfile(
                id="off_1",
                name="Игорь",
                position="Начальник",
            ),
        ],
    )

    env = AsyncEnvironment(
        config=config,
        runner=_IdleRunner(),
        llm=MockLLMProvider(),
    )
    base = float(env.state.reputation["off_1"].score)

    env.state.event_log.log(
        event_type="case_resolved",
        agent_id="off_1",
        payload={"case_id": "D-001", "decision": "ok"},
        timestamp=env.clock.iso(),
    )

    env.clock.advance_to(env.clock.now + timedelta(days=1))
    env._try_reputation_tick()
    assert float(env.state.reputation["off_1"].score) == base + 1.5

    env.clock.advance_to(env.clock.now + timedelta(days=1))
    env._try_reputation_tick()
    assert float(env.state.reputation["off_1"].score) == base + 2.5
