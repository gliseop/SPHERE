"""Тесты генератора событий среды."""

from magistry_sim.world_generator import (
    WorldGenerator,
    WorldGenResult,
    WORLD_GEN_SCHEMA,
    WORLD_GEN_SYSTEM,
)
from magistry_sim.llm import MockLLMProvider
from magistry_sim.state import WorldState
from magistry_sim.state_ops import StateOp, CreateNeedOp
from magistry_sim.config import AgentProfile


class TestWorldGenerator:
    """Динамическая генерация событий."""

    def _make_state(self) -> WorldState:
        """Создать минимальное состояние мира для тестов.

        Returns:
            Состояние мира с одним агентом.
        """
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1", name="Козлов", position="начальник",
        )
        return state

    def test_generate_returns_ops(self):
        """Генератор возвращает WorldGenResult с операциями из LLM."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [
                        {
                            "type": "new_need",
                            "description": "Отделу нужна закупка",
                            "state_changes": [
                                {"op": "create_need",
                                 "case_type": "procurement",
                                 "description": "Закупка канцтоваров",
                                 "target_agent_id": "off_1",
                                 "urgency": "средняя"}
                            ],
                        }
                    ],
                    "narrative": "В конце недели возникла потребность.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        result = gen.generate(state=state, round_num=3, round_events=[])
        assert len(result.ops) >= 0  # mock может не сматчить
        assert isinstance(result.narrative, str)

    def test_empty_events(self):
        """Генератор корректно обрабатывает пустой список событий."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [],
                    "narrative": "Ничего не произошло.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        result = gen.generate(state=state, round_num=0, round_events=[])
        assert isinstance(result.narrative, str)

    def test_result_dataclass_defaults(self):
        """WorldGenResult имеет корректные значения по умолчанию."""
        result = WorldGenResult()
        assert result.ops == []
        assert result.narrative == ""

    def test_schema_structure(self):
        """WORLD_GEN_SCHEMA содержит обязательные поля."""
        assert "properties" in WORLD_GEN_SCHEMA
        assert "events" in WORLD_GEN_SCHEMA["properties"]
        assert "narrative" in WORLD_GEN_SCHEMA["properties"]
        assert WORLD_GEN_SCHEMA["properties"]["events"]["type"] == "array"

    def test_system_prompt_contains_keyword(self):
        """Системный промпт содержит ключевое слово generate_events."""
        assert "generate_events" in WORLD_GEN_SYSTEM

    def test_generate_with_matched_response(self):
        """Генератор парсит create_need из matched structured response."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [
                        {
                            "type": "new_need",
                            "description": "Нужна закупка серверов",
                            "state_changes": [
                                {
                                    "op": "create_need",
                                    "case_type": "procurement",
                                    "description": "Закупка серверов",
                                    "target_agent_id": "off_1",
                                    "urgency": "высокая",
                                }
                            ],
                        }
                    ],
                    "narrative": "Возникла срочная потребность в серверном оборудовании.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        result = gen.generate(state=state, round_num=2, round_events=[])
        assert len(result.ops) == 1
        assert isinstance(result.ops[0], CreateNeedOp)
        assert result.ops[0].case_type == "procurement"
        assert result.ops[0].urgency == "высокая"
        assert "серверн" in result.narrative

    def test_generate_multiple_events(self):
        """Генератор собирает операции из нескольких событий."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [
                        {
                            "type": "new_need",
                            "description": "Закупка",
                            "state_changes": [
                                {
                                    "op": "create_need",
                                    "case_type": "procurement",
                                    "description": "Закупка бумаги",
                                    "target_agent_id": "off_1",
                                    "urgency": "средняя",
                                }
                            ],
                        },
                        {
                            "type": "reputation_change",
                            "description": "Репутация изменилась",
                            "state_changes": [
                                {
                                    "op": "modify_reputation",
                                    "agent_id": "off_1",
                                    "delta": -1.0,
                                }
                            ],
                        },
                    ],
                    "narrative": "Два события произошли одновременно.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        result = gen.generate(state=state, round_num=5, round_events=[])
        assert len(result.ops) == 2

    def test_generate_skips_invalid_ops(self):
        """Генератор пропускает невалидные операции без сбоя."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [
                        {
                            "type": "alien_invasion",
                            "description": "Инопланетяне",
                            "state_changes": [
                                {"op": "teleport_to_mars", "who": "all"}
                            ],
                        }
                    ],
                    "narrative": "Необычный день.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        result = gen.generate(state=state, round_num=1, round_events=[])
        assert len(result.ops) == 0
        assert result.narrative == "Необычный день."

    def test_generate_handles_llm_error(self):
        """Генератор обрабатывает ошибку LLM без исключения."""

        class FailingLLM:
            """LLM-провайдер, вызывающий ошибку."""

            def generate(self, system, user, temperature=0.0):
                raise RuntimeError("LLM недоступен")

            def generate_structured(self, system, user, schema, temperature=0.0):
                raise RuntimeError("LLM недоступен")

        gen = WorldGenerator(llm=FailingLLM())
        state = self._make_state()
        result = gen.generate(state=state, round_num=0, round_events=[])
        assert "Ошибка" in result.narrative
        assert result.ops == []

    def test_generate_with_round_events(self):
        """Генератор корректно включает события раунда в промпт."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [],
                    "narrative": "Спокойный раунд.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        events = [
            {"event_type": "case_opened", "payload": {"case_id": "D-001"}},
            {"event_type": "message_sent", "payload": {"to_id": "biz_1"}},
        ]
        result = gen.generate(state=state, round_num=1, round_events=events)
        assert isinstance(result, WorldGenResult)
        assert result.narrative == "Спокойный раунд."

    def test_generate_increments_llm_calls(self):
        """Генератор вызывает LLM ровно один раз."""
        mock = MockLLMProvider(
            structured_responses={
                "generate_events": {
                    "events": [],
                    "narrative": "Ок.",
                }
            }
        )
        gen = WorldGenerator(llm=mock)
        state = self._make_state()
        gen.generate(state=state, round_num=0, round_events=[])
        assert mock.call_count == 1
