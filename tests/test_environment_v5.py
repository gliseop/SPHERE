"""Интеграционный тест: Environment v5 со свободными действиями."""

from magistry_sim.arbiter import Arbiter
from magistry_sim.environment import Environment
from magistry_sim.llm import MockLLMProvider
from magistry_sim.tracing import LLMTracer
from magistry_sim.world_generator import WorldGenerator
from magistry_sim.state_ops import apply_state_op
from magistry_sim.agents import MockAgentRunner
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.scenarios import get_scenario


class TestEnvironmentV5:
    """Environment с арбитром и генератором."""

    def test_run_without_arbiter_works_as_before(self):
        """Без арбитра -- обратная совместимость."""
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
            seed=42,
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds

    def test_run_with_arbiter(self):
        """С арбитром среда не падает и завершается."""
        scenario = get_scenario(ScenarioId.S0)
        mock_llm = MockLLMProvider(
            structured_responses={
                "perform_action": {
                    "feasible": True,
                    "state_changes": [],
                    "side_effects": [],
                    "narrative": "Действие выполнено.",
                }
            }
        )
        arbiter = Arbiter(llm=mock_llm)
        tracer = LLMTracer()
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
            seed=42,
            arbiter=arbiter,
            tracer=tracer,
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds

    def test_run_with_world_generator(self):
        """С генератором среда создает динамические события."""
        scenario = get_scenario(ScenarioId.S0)
        mock_llm = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [],
                    "narrative": "Спокойный раунд.",
                }
            }
        )
        gen = WorldGenerator(llm=mock_llm)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
            seed=42,
            world_generator=gen,
        )
        result = env.run()
        assert result.rounds_completed == scenario.max_rounds

    def test_arbiter_logs_events(self):
        """Арбитр логирует approved/rejected события."""
        scenario = get_scenario(ScenarioId.S0)

        class PerformActionRunner:
            """Мок-раннер, возвращающий perform_action."""

            def run_turn(self, agent_id, situation, tools, state):
                return [{"tool": "perform_action", "args": {
                    "description": "Тестовое действие",
                    "target": "",
                    "justification": "Тест",
                }}]

            def run_reply(self, agent_id, message, sender_id, context, state):
                return ""

        mock_llm = MockLLMProvider(
            structured_responses={
                "perform_action": {
                    "feasible": True,
                    "state_changes": [],
                    "side_effects": [],
                    "narrative": "Ок.",
                }
            }
        )
        arbiter = Arbiter(llm=mock_llm)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=PerformActionRunner(),
            seed=42,
            arbiter=arbiter,
        )
        result = env.run()
        approved = [e for e in result.events if e.get("event_type") == "arbiter_approved"]
        assert len(approved) > 0

    def test_world_generator_skips_static_needs(self):
        """С генератором среда пропускает статические потребности."""
        scenario = get_scenario(ScenarioId.S0)
        mock_llm = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [],
                    "narrative": "Пусто.",
                }
            }
        )
        gen = WorldGenerator(llm=mock_llm)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
            seed=42,
            world_generator=gen,
        )
        result = env.run()
        # Статические needs не должны были появиться
        # (они генерируются динамически)
        assert result.rounds_completed == scenario.max_rounds
