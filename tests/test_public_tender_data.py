from __future__ import annotations

from random import Random

import pytest

from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.llm import MockLLMProvider
from magistry_sim.models import GovernanceConfig, PublicTenderData, RiskReport
from magistry_sim.scenarios import get_scenario
from magistry_sim.tribunal import Tribunal


class CapturingAuditor:
    def __init__(self) -> None:
        self.last_data: PublicTenderData | None = None

    async def assess(self, *, data: PublicTenderData) -> RiskReport:
        self.last_data = data
        return RiskReport(
            tick=data.tick,
            lpr_id=data.lpr_id,
            contractor_id=data.contractor_id,
            risk_score=0.0,
            reasons=[],
            critical=False,
        )


@pytest.mark.asyncio
async def test_public_tender_data_hides_unrelated_negotiation_messages() -> None:
    """Если победитель не тот, с кем были переговоры, аудитор не должен видеть чужие messages."""
    config = GovernanceConfig(mode=GovernanceMode.G1_AUDIT)
    engine = SimulationEngine(governance=config, llm=MockLLMProvider(seed=42))
    scenario = get_scenario(ScenarioId.S0_CLEAN)

    found = None
    for seed in range(1, 500):
        rng = Random(seed)
        world = engine._init_world(scenario=scenario, rng=rng)
        auditor = CapturingAuditor()
        tribunal = Tribunal(config)
        outcome = await engine._run_tick(world=world, scenario=scenario, tick=0, rng=rng, auditor=auditor, tribunal=tribunal)

        if not outcome.corruption and outcome.actual_winner_id != outcome.negotiation.contractor_id:
            found = (outcome, auditor.last_data)
            break

    assert found is not None, "Не найден seed, где corruption=False и победитель != подрядчика из переговоров"
    outcome, public_data = found
    assert public_data is not None
    assert public_data.contractor_id == outcome.actual_winner_id
    assert public_data.messages == []
    assert public_data.response_times_ms == []


@pytest.mark.asyncio
async def test_public_tender_data_carousel_messages_match_actual_winner() -> None:
    """В режиме карусели messages и contractor_id должны соответствовать actual_winner_id."""
    config = GovernanceConfig(mode=GovernanceMode.G1_AUDIT)
    engine = SimulationEngine(governance=config, llm=MockLLMProvider(seed=42))
    scenario = get_scenario(ScenarioId.S3_CAROUSEL)

    rng = Random(42)
    world = engine._init_world(scenario=scenario, rng=rng)
    auditor = CapturingAuditor()
    tribunal = Tribunal(config)
    outcome = await engine._run_tick(world=world, scenario=scenario, tick=0, rng=rng, auditor=auditor, tribunal=tribunal)

    assert outcome.corruption is True

    public_data = auditor.last_data
    assert public_data is not None
    assert public_data.contractor_id == outcome.actual_winner_id
    assert public_data.messages
    assert public_data.response_times_ms == [m.response_time_ms for m in public_data.messages]

    for m in public_data.messages:
        assert {m.sender_id, m.receiver_id} == {public_data.lpr_id, public_data.contractor_id}

