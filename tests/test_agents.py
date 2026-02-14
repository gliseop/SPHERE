"""Тесты AgentRunner."""

from magistry_sim.agents import (
    AgentRunner,
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
