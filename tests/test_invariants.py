from __future__ import annotations

from random import Random

import pytest

from magistry_sim.auditor import Auditor
from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.llm import MockLLMProvider
from magistry_sim.models import GovernanceConfig
from magistry_sim.scenarios import get_scenario
from magistry_sim.tribunal import Tribunal


def _make_engine(mode: GovernanceMode = GovernanceMode.G3_FULL) -> SimulationEngine:
    """Создать движок с mock LLM для тестов."""
    return SimulationEngine(
        governance=GovernanceConfig(mode=mode),
        llm=MockLLMProvider(seed=42),
    )


@pytest.mark.asyncio
async def test_social_capital_never_negative() -> None:
    engine = _make_engine(GovernanceMode.G3_FULL)
    result = await engine.run(scenario_id=ScenarioId.S1_KICKBACK, seed=123, ticks=2)

    for agent in result.final_agents.values():
        assert agent.social_capital.work >= 0.0
        assert agent.social_capital.research >= 0.0
        assert agent.social_capital.social >= 0.0
        assert agent.social_capital.total >= 0.0


@pytest.mark.asyncio
async def test_freeze_stops_growth() -> None:
    # Пониженный порог для слепого аудитора (публичные данные дают меньший risk)
    config = GovernanceConfig(mode=GovernanceMode.G2_AUDIT_REPUTATION, risk_threshold=0.50)
    engine = SimulationEngine(governance=config, llm=MockLLMProvider(seed=42))
    result = await engine.run(scenario_id=ScenarioId.S1_KICKBACK, seed=42, ticks=1)

    # При достаточно высоком risk — есть заморозка
    assert any(a.frozen for a in result.final_agents.values())

    # При заморозке рост не происходит у части агентов (work остается 0).
    assert any(a.frozen and a.social_capital.work == 0.0 for a in result.final_agents.values())


@pytest.mark.asyncio
async def test_immunity_not_sanctioned() -> None:
    engine = _make_engine(GovernanceMode.G3_FULL)
    result = await engine.run(scenario_id=ScenarioId.S9_IMMUNITY, seed=999, ticks=1)

    immune = [a for a in result.final_agents.values() if a.immune]
    assert immune, "Scenario must include immune actor"

    for a in immune:
        assert a.eligible_for_lpr is True
        assert a.eligible_for_contracts is True


@pytest.mark.asyncio
async def test_sanctioned_contractor_not_reintroduced_when_pool_small() -> None:
    """Дисквалифицированные подрядчики не возвращаются в пул.

    Пониженные пороги (risk=0.40, critical=0.55) для слепого аудитора,
    чтобы tribunal срабатывал на публичных данных.
    """
    config = GovernanceConfig(mode=GovernanceMode.G3_FULL, risk_threshold=0.40, critical_threshold=0.55)
    llm = MockLLMProvider(seed=42)
    engine = SimulationEngine(governance=config, llm=llm)

    base = get_scenario(ScenarioId.S1_KICKBACK)
    scenario = base.model_copy(update={"num_contractors": 2, "num_officials": 6})

    # Перебираем seeds для нахождения того, где сговор → трибунал → guilty
    found_seed = None
    for test_seed in range(42, 500):
        rng = Random(test_seed)
        world = engine._init_world(scenario=scenario, rng=rng)
        auditor_test = Auditor(config=config, llm=MockLLMProvider(seed=test_seed))
        tribunal_test = Tribunal(config)
        first = await engine._run_tick(world=world, scenario=scenario, tick=0, rng=rng, auditor=auditor_test, tribunal=tribunal_test)
        if first.tribunal_triggered and first.tribunal_guilty:
            found_seed = test_seed
            break

    assert found_seed is not None, "Нет seed с tribunal_triggered=True, tribunal_guilty=True"

    # Повторяем с найденным seed
    rng = Random(found_seed)
    world = engine._init_world(scenario=scenario, rng=rng)
    auditor = Auditor(config=config, llm=MockLLMProvider(seed=found_seed))
    tribunal = Tribunal(config)

    first = await engine._run_tick(world=world, scenario=scenario, tick=0, rng=rng, auditor=auditor, tribunal=tribunal)
    assert first.tribunal_triggered is True
    assert first.tribunal_guilty is True

    banned_id = first.actual_winner_id
    assert world.agents[banned_id].eligible_for_contracts is False

    second = await engine._run_tick(world=world, scenario=scenario, tick=1, rng=rng, auditor=auditor, tribunal=tribunal)
    assert second.actual_winner_id != banned_id
    assert world.agents[banned_id].eligible_for_contracts is False
