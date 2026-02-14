"""Тесты сценариев."""

from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.scenarios import (
    SCENARIOS,
    add_governance_agents,
    get_scenario,
)


class TestScenarios:
    def test_s0_exists(self):
        cfg = get_scenario(ScenarioId.S0)
        assert cfg.title == "Чистая сделка"
        assert len(cfg.agents) >= 2

    def test_s1_exists(self):
        cfg = get_scenario(ScenarioId.S1)
        assert cfg.title == "Прямой сговор"

    def test_s2_exists(self):
        cfg = get_scenario(ScenarioId.S2)
        assert cfg.title == "Кумовство при найме"

    def test_all_scenarios_valid(self):
        for sid in [ScenarioId.S0, ScenarioId.S1, ScenarioId.S2]:
            cfg = get_scenario(sid)
            assert cfg.id == sid
            assert len(cfg.agents) > 0
            assert len(cfg.needs) > 0


class TestGovernanceAgents:
    def test_g0_no_extra_agents(self):
        cfg = get_scenario(ScenarioId.S0)
        updated = add_governance_agents(cfg, GovernanceMode.G0)
        assert len(updated.agents) == len(cfg.agents)

    def test_g1_adds_auditor(self):
        cfg = get_scenario(ScenarioId.S0)
        updated = add_governance_agents(cfg, GovernanceMode.G1)
        ids = [a.id for a in updated.agents]
        assert "auditor" in ids

    def test_g3_adds_auditor_and_jurors(self):
        cfg = get_scenario(ScenarioId.S0)
        updated = add_governance_agents(cfg, GovernanceMode.G3)
        ids = [a.id for a in updated.agents]
        assert "auditor" in ids
        assert "juror_0" in ids
        assert "juror_1" in ids
        assert "juror_2" in ids

    def test_g3_governance_mode_set(self):
        cfg = get_scenario(ScenarioId.S0)
        updated = add_governance_agents(cfg, GovernanceMode.G3)
        assert updated.governance.mode == GovernanceMode.G3
