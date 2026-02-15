"""Тесты среды исполнения."""

from unittest.mock import MagicMock

from magistry_sim.agents import MockAgentRunner
from magistry_sim.cases import Case, Vote
from magistry_sim.cognitive_runner import CognitiveAgentRunner
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

    def test_tribunal_verdict_uses_exact_vote_values(self):
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G3,
            runner=MockAgentRunner(),
        )
        case = Case(
            id="T-001",
            case_type="investigation",
            title="Трибунал",
            description="Test",
            owner_id="auditor",
            stage="tribunal",
        )
        case.votes.extend([
            Vote(
                voter_id="juror_0",
                case_id="T-001",
                verdict="невиновен",
                reasoning="Нет доказательств",
                round=0,
            ),
            Vote(
                voter_id="juror_1",
                case_id="T-001",
                verdict="невиновен",
                reasoning="Сомнения",
                round=0,
            ),
            Vote(
                voter_id="juror_2",
                case_id="T-001",
                verdict="виновен",
                reasoning="Есть основания",
                round=0,
            ),
        ])
        env.state.cases["T-001"] = case
        env.state.round = 0

        env._apply_round_end_effects()

        assert case.stage == "verdict"
        assert case.decision == "невиновен"

    def test_tribunal_quorum_respects_configured_jury_size(self):
        scenario = get_scenario(ScenarioId.S0)
        scenario = scenario.model_copy(
            update={
                "governance": scenario.governance.model_copy(
                    update={"jury_size": 2}
                )
            }
        )
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G3,
            runner=MockAgentRunner(),
        )
        case = Case(
            id="T-002",
            case_type="investigation",
            title="Трибунал",
            description="Test",
            owner_id="auditor",
            stage="tribunal",
        )
        case.votes.extend([
            Vote(
                voter_id="juror_0",
                case_id="T-002",
                verdict="виновен",
                reasoning="Причина 1",
                round=0,
            ),
            Vote(
                voter_id="juror_1",
                case_id="T-002",
                verdict="невиновен",
                reasoning="Причина 2",
                round=0,
            ),
        ])
        env.state.cases["T-002"] = case
        env.state.round = 0

        env._apply_round_end_effects()

        assert case.stage == "verdict"


class TestObservationPhase:
    def test_agents_observe_prior_public_actions(self):
        """Агенты, ходящие позже, видят публичные действия предыдущих."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="[]")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(
            llm_provider=mock_llm, embedder=mock_embedder
        )

        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)

        # Симулируем публичное действие в раунде 0
        env.state.event_log.log(
            round=0,
            event_type="message_sent",
            agent_id="biz_2",
            payload={"to_id": "off_1", "private": False},
        )

        # off_1 должен наблюдать это перед своим ходом
        env._deliver_observations("off_1", prior_events_this_round=[
            {"agent_id": "biz_2", "event_type": "message_sent",
             "payload": {"to_id": "off_1", "private": False}}
        ])

        stream = runner.get_or_create_memory("off_1")
        assert len(stream) >= 1

    def test_no_observations_with_mock_runner(self):
        """С MockAgentRunner наблюдения не доставляются."""
        config = get_scenario(ScenarioId.S0)
        runner = MockAgentRunner()
        env = Environment(scenario=config, runner=runner)

        # Метод не должен падать с MockAgentRunner
        env._deliver_observations("off_1", prior_events_this_round=[
            {"agent_id": "biz_1", "event_type": "message_sent",
             "payload": {"to_id": "off_1", "private": False}}
        ])

    def test_private_events_not_observed(self):
        """Приватные события не доставляются как наблюдения."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="[]")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(
            llm_provider=mock_llm, embedder=mock_embedder
        )

        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)

        # Приватное событие — не должно наблюдаться off_2
        env._deliver_observations("off_2", prior_events_this_round=[
            {"agent_id": "biz_1", "event_type": "message_sent",
             "payload": {"to_id": "off_1", "private": True}}
        ])

        stream = runner.get_or_create_memory("off_2")
        assert len(stream) == 0
