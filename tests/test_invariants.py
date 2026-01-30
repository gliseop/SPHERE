from __future__ import annotations

from random import Random

from magistry_sim.auditor import Auditor
from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.models import GovernanceConfig
from magistry_sim.scenarios import get_scenario
from magistry_sim.tribunal import Tribunal


def test_social_capital_never_negative() -> None:
    engine = SimulationEngine(governance=GovernanceConfig(mode=GovernanceMode.G3_FULL))
    result = engine.run(scenario_id=ScenarioId.S1_KICKBACK, seed=123, ticks=2)

    for agent in result.final_agents.values():
        assert agent.social_capital.work >= 0.0
        assert agent.social_capital.research >= 0.0
        assert agent.social_capital.social >= 0.0
        assert agent.social_capital.total >= 0.0


def test_freeze_stops_growth() -> None:
    engine = SimulationEngine(governance=GovernanceConfig(mode=GovernanceMode.G2_AUDIT_REPUTATION))
    result = engine.run(scenario_id=ScenarioId.S1_KICKBACK, seed=321, ticks=1)

    # В S1 почти всегда есть заморозка — проверяем наличие хотя бы одного frozen.
    assert any(a.frozen for a in result.final_agents.values())

    # При заморозке рост не происходит у части агентов (work остается 0).
    assert any(a.frozen and a.social_capital.work == 0.0 for a in result.final_agents.values())


def test_immunity_not_sanctioned() -> None:
    engine = SimulationEngine(governance=GovernanceConfig(mode=GovernanceMode.G3_FULL))
    result = engine.run(scenario_id=ScenarioId.S9_IMMUNITY, seed=999, ticks=1)

    immune = [a for a in result.final_agents.values() if a.immune]
    assert immune, "Scenario must include immune actor"

    for a in immune:
        assert a.eligible_for_lpr is True
        assert a.eligible_for_contracts is True


def test_sanctioned_contractor_not_reintroduced_when_pool_small() -> None:
    config = GovernanceConfig(mode=GovernanceMode.G3_FULL)
    engine = SimulationEngine(governance=config)

    base = get_scenario(ScenarioId.S1_KICKBACK)
    scenario = base.model_copy(update={"num_contractors": 2, "num_officials": 6})

    rng = Random(42)
    world = engine._init_world(scenario=scenario, rng=rng)
    auditor = Auditor(config)
    tribunal = Tribunal(config)

    first = engine._run_tick(world=world, scenario=scenario, tick=0, rng=rng, auditor=auditor, tribunal=tribunal)
    assert first.tribunal_triggered is True
    assert first.tribunal_guilty is True

    banned_id = first.actual_winner_id
    assert world.agents[banned_id].eligible_for_contracts is False

    second = engine._run_tick(world=world, scenario=scenario, tick=1, rng=rng, auditor=auditor, tribunal=tribunal)
    assert second.actual_winner_id != banned_id
    assert world.agents[banned_id].eligible_for_contracts is False
