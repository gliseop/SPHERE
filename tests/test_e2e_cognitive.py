"""Сквозные тесты когнитивного агента с mock-провайдерами."""

import pytest

from magistry_sim.cognitive_runner import CognitiveAgentRunner
from magistry_sim.config import GovernanceConfig
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.environment import Environment
from magistry_sim.llm import MockEmbeddingProvider, MockLLMProvider
from magistry_sim.metrics import compute_metrics
from magistry_sim.scenarios import add_governance_agents, get_scenario


def _make_runner() -> CognitiveAgentRunner:
    """Создать CognitiveAgentRunner с mock-провайдерами."""
    llm = MockLLMProvider()
    embedder = MockEmbeddingProvider(dimensions=16)
    return CognitiveAgentRunner(
        llm_provider=llm, embedder=embedder
    )


class TestE2ECognitiveS0:
    """Сквозные тесты на сценарии S0 (чистая сделка)."""

    def test_s0_g0_completes(self):
        """Симуляция S0/G0 завершается без ошибок."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0
        assert result.scenario_id == "S0"

    def test_s0_g0_completes_without_agent_events(self):
        """S0/G0 с mock-LLM завершается корректно (mock не порождает действий)."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        # Mock-LLM не генерирует валидных действий, поэтому событий может не быть.
        # Главное — симуляция завершилась корректно.
        assert result.rounds_completed == config.max_rounds

    def test_s0_g0_metrics_computable(self):
        """Метрики вычисляются без ошибок на результатах S0/G0."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        metrics = compute_metrics(result)
        assert metrics.rounds > 0


class TestE2ECognitiveS1:
    """Сквозные тесты на сценарии S1 (прямой сговор)."""

    def test_s1_g0_completes(self):
        """S1/G0 завершается без ошибок."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S1)
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0

    def test_s1_g2_completes(self):
        """S1/G2 (аудитор с влиянием на репутацию) завершается."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S1)
        config = add_governance_agents(
            config, GovernanceMode.G2
        )
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0
        assert result.governance in ("G2", GovernanceMode.G2.value)

    def test_s1_g3_completes(self):
        """S1/G3 (полный трибунал с присяжными) завершается."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S1)
        config = add_governance_agents(
            config, GovernanceMode.G3
        )
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0


class TestE2ECognitiveS2:
    """Сквозные тесты на сценарии S2 (кумовство)."""

    def test_s2_g0_completes(self):
        """S2/G0 завершается."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S2)
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0


class TestCognitiveMemoryAccumulation:
    """Тесты накопления воспоминаний когнитивными агентами."""

    def test_agents_accumulate_memories(self):
        """Агенты накапливают воспоминания в ходе симуляции."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        env.run()

        total_memories = sum(
            len(stream) for stream in runner._memories.values()
        )
        assert total_memories > 0, (
            "Когнитивные агенты должны накопить хотя бы одно воспоминание"
        )

    def test_memory_streams_created_per_agent(self):
        """Для каждого агента создаётся отдельный поток памяти."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        env.run()

        agent_ids = set(config.agents[i].id for i in range(len(config.agents)))
        memory_agent_ids = set(runner._memories.keys())
        # Все агенты из сценария должны иметь поток памяти
        assert agent_ids.issubset(memory_agent_ids), (
            f"Потоки памяти отсутствуют для агентов: "
            f"{agent_ids - memory_agent_ids}"
        )

    def test_plans_created_for_agents(self):
        """Для агентов создаются планы в ходе симуляции."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S1)
        env = Environment(scenario=config, runner=runner)
        env.run()

        assert len(runner._plans) > 0, (
            "Должен быть создан хотя бы один план"
        )

    def test_observations_include_various_kinds(self):
        """Поток памяти содержит записи разных типов (observation, plan)."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        env.run()

        all_kinds = set()
        for stream in runner._memories.values():
            for record in stream.records:
                all_kinds.add(record.kind)

        assert "plan" in all_kinds, "Должны быть записи типа plan"

    def test_multiple_rounds_increase_memories(self):
        """Больше раундов — больше воспоминаний."""
        runner_short = _make_runner()
        config_short = get_scenario(ScenarioId.S0)
        config_short = config_short.model_copy(update={"max_rounds": 2})
        env_short = Environment(scenario=config_short, runner=runner_short)
        env_short.run()

        runner_long = _make_runner()
        config_long = get_scenario(ScenarioId.S0)
        config_long = config_long.model_copy(update={"max_rounds": 6})
        env_long = Environment(scenario=config_long, runner=runner_long)
        env_long.run()

        memories_short = sum(
            len(s) for s in runner_short._memories.values()
        )
        memories_long = sum(
            len(s) for s in runner_long._memories.values()
        )
        assert memories_long >= memories_short, (
            "Больше раундов должно давать не меньше воспоминаний"
        )


class TestCognitiveGovernanceInteraction:
    """Тесты взаимодействия когнитивного агента с governance."""

    def test_auditor_has_memory_stream(self):
        """Аудитор (добавленный governance) получает поток памяти."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S1)
        config = add_governance_agents(config, GovernanceMode.G2)
        env = Environment(scenario=config, runner=runner)
        env.run()

        assert "auditor" in runner._memories, (
            "Аудитор должен иметь поток памяти"
        )
        assert len(runner._memories["auditor"]) > 0

    def test_jurors_have_memory_streams(self):
        """Присяжные (добавленные G3) получают потоки памяти."""
        runner = _make_runner()
        config = get_scenario(ScenarioId.S1)
        config = add_governance_agents(config, GovernanceMode.G3)
        env = Environment(scenario=config, runner=runner)
        env.run()

        juror_streams = [
            aid for aid in runner._memories
            if aid.startswith("juror_")
        ]
        assert len(juror_streams) > 0, (
            "Присяжные должны иметь потоки памяти"
        )
