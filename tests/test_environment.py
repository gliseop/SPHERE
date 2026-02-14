"""Тесты среды исполнения."""

from magistry_sim.agents import MockAgentRunner
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.environment import Environment, SimulationResult
from magistry_sim.scenarios import get_scenario


class TestEnvironment:
    def test_init(self):
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(scenario=scenario)
        assert env.state.round == 0
        assert "off_1" in env.state.agents

    def test_run_s0_g0(self):
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
        )
        result = env.run()
        assert isinstance(result, SimulationResult)
        assert result.rounds_completed == scenario.max_rounds
        assert result.scenario_id == "S0"
        assert len(result.cases) > 0

    def test_run_s1_g0(self):
        scenario = get_scenario(ScenarioId.S1)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds
        assert len(result.cases) > 0

    def test_run_s0_g3(self):
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G3,
            runner=MockAgentRunner(),
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds
        assert "auditor" in result.agents

    def test_run_s1_g3(self):
        scenario = get_scenario(ScenarioId.S1)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G3,
            runner=MockAgentRunner(),
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds

    def test_run_s2_g0(self):
        scenario = get_scenario(ScenarioId.S2)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds

    def test_reputation_grows(self):
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
        )
        result = env.run()
        for rep in result.final_reputation.values():
            assert rep["score"] >= 10.0

    def test_deterministic_with_seed(self):
        scenario = get_scenario(ScenarioId.S0)
        env1 = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
            seed=123,
        )
        result1 = env1.run()

        env2 = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
            seed=123,
        )
        result2 = env2.run()

        assert len(result1.cases) == len(result2.cases)
        assert len(result1.events) == len(result2.events)
