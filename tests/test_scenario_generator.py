"""Тесты LLM-генератора сценариев."""

import pytest
from magistry_sim.scenario_generator import generate_scenario
from magistry_sim.config import ScenarioConfig
from magistry_sim.enums import GovernanceMode
from magistry_sim.llm import StructuredLLMResponse


def _make_scenario_llm_response(agent_count: int = 4) -> dict:
    """Вспомогательная функция: данные ответа LLM для генерации сценария."""
    agents = []
    positions = ["чиновник", "бизнесмен", "аудитор", "кандидат"]
    for i in range(agent_count):
        agents.append({
            "id": f"agent_{i+1}",
            "name": f"Агент {i+1}",
            "position": positions[i % len(positions)],
            "hexaco": {
                "honesty_humility": 30 + i * 10,
                "emotionality": 50,
                "extraversion": 60,
                "agreeableness": 40 + i * 5,
                "conscientiousness": 45,
                "openness": 55,
            },
            "dark_triad": {
                "narcissism": 60 - i * 10,
                "machiavellianism": 70 - i * 10,
                "psychopathy": 30,
            },
        })

    needs = [
        {
            "case_type": "procurement",
            "description": "Закупка оборудования",
            "target_agent_id": "agent_1",
            "appear_round": 0,
        },
        {
            "case_type": "contract",
            "description": "Контракт на обслуживание",
            "target_agent_id": "agent_2",
            "appear_round": 1,
        },
    ]

    return {
        "title": "Тестовый сценарий откатов",
        "description": "Симуляция откатной схемы в государственных закупках",
        "agents": agents,
        "needs": needs,
    }


class TestGenerateScenario:
    """Тесты генерации сценария через LLM."""

    def test_returns_valid_scenario_config(self):
        """generate_scenario возвращает валидный ScenarioConfig."""
        data = _make_scenario_llm_response(agent_count=4)

        class ScenarioLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data=data)

        config = generate_scenario(
            llm=ScenarioLLM(),
            corruption_type="kickback",
            agent_count=4,
            economic_pressure=0.7,
            governance=GovernanceMode.G2,
        )
        assert isinstance(config, ScenarioConfig)
        assert len(config.agents) == 4
        assert config.governance.mode == GovernanceMode.G2

    def test_agents_have_personality(self):
        """Сгенерированные агенты содержат профили HEXACO и Dark Triad."""
        data = _make_scenario_llm_response(agent_count=2)

        class ScenarioLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data=data)

        config = generate_scenario(
            llm=ScenarioLLM(),
            corruption_type="bribery",
            agent_count=2,
            economic_pressure=0.5,
            governance=GovernanceMode.G0,
        )
        for agent in config.agents:
            assert agent.personality is not None
            assert 0 <= agent.personality.hexaco.honesty_humility <= 100
            assert 0 <= agent.personality.dark_triad.narcissism <= 100

    def test_scenario_has_title_and_description(self):
        """Сценарий содержит заголовок и описание от LLM."""
        data = _make_scenario_llm_response()

        class ScenarioLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data=data)

        config = generate_scenario(
            llm=ScenarioLLM(),
            corruption_type="embezzlement",
            agent_count=4,
            economic_pressure=0.3,
            governance=GovernanceMode.G1,
        )
        assert config.title == "Тестовый сценарий откатов"
        assert "откатной" in config.description

    def test_corruption_type_in_prompt(self):
        """Тип коррупции передаётся в промпт LLM."""
        captured = []

        class CaptureLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(
                    data=_make_scenario_llm_response()
                )

        generate_scenario(
            llm=CaptureLLM(),
            corruption_type="kickback",
            agent_count=4,
            economic_pressure=0.7,
            governance=GovernanceMode.G2,
        )
        assert "kickback" in captured[0]

    def test_economic_pressure_in_prompt(self):
        """Экономическое давление передаётся в промпт LLM."""
        captured = []

        class CaptureLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(
                    data=_make_scenario_llm_response()
                )

        generate_scenario(
            llm=CaptureLLM(),
            corruption_type="bribery",
            agent_count=4,
            economic_pressure=0.9,
            governance=GovernanceMode.G0,
        )
        assert "0.9" in captured[0]

    def test_scenario_has_needs(self):
        """Сценарий содержит потребности организации."""
        data = _make_scenario_llm_response()

        class ScenarioLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data=data)

        config = generate_scenario(
            llm=ScenarioLLM(),
            corruption_type="kickback",
            agent_count=4,
            economic_pressure=0.5,
            governance=GovernanceMode.G0,
        )
        assert len(config.needs) >= 1
        assert config.needs[0].case_type == "procurement"

    def test_custom_max_rounds_and_seed(self):
        """Можно задать max_rounds и seed."""
        data = _make_scenario_llm_response(agent_count=2)

        class ScenarioLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data=data)

        config = generate_scenario(
            llm=ScenarioLLM(),
            corruption_type="kickback",
            agent_count=2,
            economic_pressure=0.5,
            governance=GovernanceMode.G0,
            max_rounds=15,
            seed=123,
        )
        assert config.max_rounds == 15
        assert config.seed == 123
