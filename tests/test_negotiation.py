"""Тесты переговоров (Stage 3)."""
from __future__ import annotations

from random import Random

import pytest

from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.llm import MockLLMProvider
from magistry_sim.memory import AgentMemory, MemoryItemType
from magistry_sim.models import Agent, GovernanceConfig, Scenario
from magistry_sim.enums import AgentRole
from magistry_sim.negotiation import NegotiationProtocol


def _make_agent(agent_id: str, role: AgentRole, *, greed: float, fear: float, honesty: float) -> Agent:
    """Создать агента с заданными trait'ами."""
    return Agent(
        id=agent_id, name=f"Test {agent_id}", role=role,
        greed=greed, fear=fear, honesty=honesty, competence=0.7,
    )


def _make_scenario() -> Scenario:
    """Создать базовый сценарий для тестов."""
    return Scenario(
        id=ScenarioId.S1_KICKBACK,
        title="Test", description="Test scenario",
        ticks=1, seed=42, tender_budget=1_000_000.0,
        relationship_strength=0.5,
    )


@pytest.mark.asyncio
async def test_honest_agents_reject_deal() -> None:
    """Честные агенты не достигают коррупционного сговора."""
    llm = MockLLMProvider(seed=42)
    protocol = NegotiationProtocol(llm)

    lpr = _make_agent("off_0", AgentRole.OFFICIAL, greed=0.1, fear=0.8, honesty=0.9)
    contractor = _make_agent("biz_0", AgentRole.CONTRACTOR, greed=0.1, fear=0.8, honesty=0.9)

    result = await protocol.negotiate(
        lpr=lpr, contractor=contractor,
        scenario=_make_scenario(),
        lpr_memory=AgentMemory("off_0"),
        contractor_memory=AgentMemory("biz_0"),
        rng=Random(42),
    )

    assert result.deal_reached is False
    assert result.kickback_percent is None
    assert len(result.messages) > 0


@pytest.mark.asyncio
async def test_greedy_agents_reach_deal() -> None:
    """Жадные агенты достигают коррупционного сговора."""
    llm = MockLLMProvider(seed=42)
    protocol = NegotiationProtocol(llm)

    lpr = _make_agent("off_0", AgentRole.OFFICIAL, greed=0.9, fear=0.1, honesty=0.1)
    contractor = _make_agent("biz_0", AgentRole.CONTRACTOR, greed=0.9, fear=0.1, honesty=0.1)

    result = await protocol.negotiate(
        lpr=lpr, contractor=contractor,
        scenario=_make_scenario(),
        lpr_memory=AgentMemory("off_0"),
        contractor_memory=AgentMemory("biz_0"),
        rng=Random(42),
    )

    assert result.deal_reached is True
    assert result.kickback_percent is not None
    assert 0 < result.kickback_percent <= 30
    assert len(result.messages) > 0


@pytest.mark.asyncio
async def test_negotiation_recorded_in_memory() -> None:
    """Сообщения переговоров записываются в AgentMemory."""
    llm = MockLLMProvider(seed=42)
    protocol = NegotiationProtocol(llm)

    lpr = _make_agent("off_0", AgentRole.OFFICIAL, greed=0.5, fear=0.5, honesty=0.5)
    contractor = _make_agent("biz_0", AgentRole.CONTRACTOR, greed=0.5, fear=0.5, honesty=0.5)

    lpr_mem = AgentMemory("off_0")
    contractor_mem = AgentMemory("biz_0")

    await protocol.negotiate(
        lpr=lpr, contractor=contractor,
        scenario=_make_scenario(),
        lpr_memory=lpr_mem,
        contractor_memory=contractor_mem,
        rng=Random(42),
    )

    # Должны быть события начала и завершения переговоров
    assert lpr_mem.count_by_type(MemoryItemType.NEGOTIATION_START) >= 1
    assert contractor_mem.count_by_type(MemoryItemType.NEGOTIATION_START) >= 1

    # Должно быть либо DEAL_REACHED, либо DEAL_REJECTED
    deal_events = (
        lpr_mem.count_by_type(MemoryItemType.DEAL_REACHED) +
        lpr_mem.count_by_type(MemoryItemType.DEAL_REJECTED)
    )
    assert deal_events >= 1


@pytest.mark.asyncio
async def test_max_rounds_respected() -> None:
    """Число раундов не превышает MAX_ROUNDS."""
    llm = MockLLMProvider(seed=42)
    max_rounds = 2
    protocol = NegotiationProtocol(llm, max_rounds=max_rounds)

    lpr = _make_agent("off_0", AgentRole.OFFICIAL, greed=0.5, fear=0.5, honesty=0.5)
    contractor = _make_agent("biz_0", AgentRole.CONTRACTOR, greed=0.5, fear=0.5, honesty=0.5)

    result = await protocol.negotiate(
        lpr=lpr, contractor=contractor,
        scenario=_make_scenario(),
        lpr_memory=AgentMemory("off_0"),
        contractor_memory=AgentMemory("biz_0"),
        rng=Random(42),
    )

    assert result.total_rounds == max_rounds
    # Каждый раунд: 1 contractor msg + 1 official msg = 2 * max_rounds
    assert len(result.messages) == max_rounds * 2


@pytest.mark.asyncio
async def test_engine_with_negotiation() -> None:
    """Engine использует NegotiationProtocol и возвращает negotiation в TickOutcome."""
    engine = SimulationEngine(
        governance=GovernanceConfig(mode=GovernanceMode.G3_FULL),
        llm=MockLLMProvider(seed=42),
    )
    result = await engine.run(scenario_id=ScenarioId.S1_KICKBACK, seed=42, ticks=1)

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.negotiation is not None
    assert len(outcome.negotiation.messages) > 0
    assert outcome.negotiation.total_rounds > 0
