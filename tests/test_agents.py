"""Тесты AgentRunner."""

from magistry_sim.agents import (
    AgentRunner,
    LLMAgentRunner,
    MockAgentRunner,
    build_backstory,
    _greed_text,
    _fear_text,
    _honesty_text,
    _competence_text,
)
from magistry_sim.config import (
    AgentProfile,
    Capability,
    Connection,
)
from magistry_sim.llm import MockLLMProvider
from magistry_sim.state import WorldState


class TestBackstory:
    def test_basic_backstory(self):
        profile = AgentProfile(
            id="off_1",
            name="Иванов А.П.",
            position="начальник отдела",
            capabilities=[
                Capability(
                    action="open_case", case_types=["procurement"]
                ),
            ],
            greed=0.8,
            fear=0.3,
            honesty=0.2,
            connections=[
                Connection(
                    target_id="biz_1",
                    name="Петров",
                    relation="коллега",
                ),
            ],
        )
        text = build_backstory(profile)
        assert "Иванов А.П." in text
        assert "начальник отдела" in text
        assert "Петров" in text
        assert "открывать дела" in text

    def test_immune_backstory(self):
        profile = AgentProfile(
            id="boss",
            name="Директор",
            position="директор",
            immune=True,
        )
        text = build_backstory(profile)
        assert "неподсудны" in text

    def test_greed_text(self):
        assert "заслуживаете" in _greed_text(0.8)
        assert "удобный случай" in _greed_text(0.5)
        assert "репутация" in _greed_text(0.2)

    def test_fear_text(self):
        assert "осторожны" in _fear_text(0.8)
        assert "осмотрительны" in _fear_text(0.5)
        assert "уверенно" in _fear_text(0.2)

    def test_honesty_text(self):
        assert "честности" in _honesty_text(0.8)
        assert "систем" in _honesty_text(0.5)
        assert "прагматик" in _honesty_text(0.2)

    def test_competence_text(self):
        assert "лучших" in _competence_text(0.8)
        assert "среднего" in _competence_text(0.5)
        assert "невысокое" in _competence_text(0.2)


class TestMockAgentRunner:
    def test_protocol_compliance(self):
        runner = MockAgentRunner()
        assert isinstance(runner, AgentRunner)

    def test_run_turn_empty(self):
        runner = MockAgentRunner()
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Test",
            position="Test",
        )
        actions = runner.run_turn("off_1", "situation", [], state)
        assert isinstance(actions, list)

    def test_run_reply_stranger(self):
        runner = MockAgentRunner()
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Test",
            position="Test",
            greed=0.3,
        )
        reply = runner.run_reply(
            "off_1", "Привет", "biz_1", "", state
        )
        assert "Спасибо" in reply

    def test_run_reply_connected(self):
        runner = MockAgentRunner()
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Test",
            position="Test",
            greed=0.8,
            connections=[
                Connection(
                    target_id="biz_1",
                    name="Друг",
                    relation="коллега",
                ),
            ],
        )
        reply = runner.run_reply(
            "off_1", "Привет", "biz_1", "", state
        )
        assert "сотрудничеств" in reply


class TestJsonParser:
    """Тесты парсера JSON-действий LLMAgentRunner."""

    def _make_runner(self) -> LLMAgentRunner:
        return LLMAgentRunner(
            llm_provider=MockLLMProvider(), verbose=False
        )

    def test_clean_json(self):
        runner = self._make_runner()
        text = '[{"tool": "open_case", "args": {"case_type": "procurement"}}]'
        result = runner._parse_json_actions(text)
        assert len(result) == 1
        assert result[0]["tool"] == "open_case"

    def test_markdown_fences(self):
        runner = self._make_runner()
        text = '```json\n[{"tool": "talk_to", "args": {"agent_id": "biz_1", "message": "hi"}}]\n```'
        result = runner._parse_json_actions(text)
        assert len(result) == 1
        assert result[0]["tool"] == "talk_to"

    def test_mixed_text_and_json(self):
        runner = self._make_runner()
        text = (
            "Я решил открыть дело.\n\n"
            '[{"tool": "open_case", "args": {"case_type": "procurement", '
            '"title": "Закупка", "description": "Нужно оборудование"}}]\n'
        )
        result = runner._parse_json_actions(text)
        assert len(result) == 1
        assert result[0]["tool"] == "open_case"

    def test_narrative_with_embedded_json(self):
        runner = self._make_runner()
        text = (
            "Анализирую ситуацию. Вижу потребность в закупке.\n"
            "Мои действия:\n"
            '[\n'
            '  {"tool": "talk_to", "args": {"agent_id": "biz_1", '
            '"message": "Привет", "private": true}},\n'
            '  {"tool": "open_case", "args": {"case_type": "procurement", '
            '"title": "Серверы", "description": "Нужны серверы"}}\n'
            ']\n\n'
            "Жду результата."
        )
        result = runner._parse_json_actions(text)
        assert len(result) == 2

    def test_trailing_garbage(self):
        runner = self._make_runner()
        text = (
            '[{"tool": "talk_to", "args": {"agent_id": "biz_1", '
            '"message": "Привет", "private": false}}]\n}\n```'
        )
        result = runner._parse_json_actions(text)
        assert len(result) == 1
        assert result[0]["tool"] == "talk_to"

    def test_single_object_without_array(self):
        runner = self._make_runner()
        text = (
            "Вот моё действие:\n"
            '{"tool": "add_note", "args": {"case_id": "D-001", '
            '"content": "Запись"}}'
        )
        result = runner._parse_json_actions(text)
        assert len(result) == 1
        assert result[0]["tool"] == "add_note"

    def test_empty_array(self):
        runner = self._make_runner()
        text = "[]"
        result = runner._parse_json_actions(text)
        assert result == []

    def test_invalid_items_filtered(self):
        runner = self._make_runner()
        text = '[{"tool": "talk_to", "args": {}}, "not_a_dict", 42]'
        result = runner._parse_json_actions(text)
        assert len(result) == 1

    def test_no_json_at_all(self):
        runner = self._make_runner()
        text = "Я просто хочу подождать и ничего не делать."
        result = runner._parse_json_actions(text)
        assert result == []
