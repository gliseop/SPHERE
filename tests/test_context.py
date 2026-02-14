"""Тесты генерации ситуационных сводок."""

from magistry_sim.cases import Case, Proposal
from magistry_sim.config import AgentProfile, Capability, Need
from magistry_sim.context import build_situation
from magistry_sim.state import ReputationRecord, WorldState


class TestBuildSituation:
    def _make_state(self):
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Иванов А.П.",
            position="начальник отдела",
            capabilities=[
                Capability(
                    action="open_case", case_types=["procurement"]
                ),
            ],
        )
        state.reputation["off_1"] = ReputationRecord(score=20.0)
        state.resources.init_agent(
            "off_1", budget_limit=10_000_000
        )
        state.graph.add_agent("off_1")
        return state

    def test_basic_situation(self):
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "Иванов А.П." in text
        assert "раунд 0" in text
        assert "начальник отдела" in text

    def test_situation_with_reputation(self):
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "20.0" in text
        assert "рост активен" in text

    def test_situation_with_resources(self):
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "Бюджетный лимит" in text

    def test_situation_with_cases(self):
        state = self._make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Закупка серверов",
            description="Описание",
            owner_id="off_1",
            stage="collecting",
        )
        case.proposals.append(
            Proposal(
                id="P-001",
                case_id="D-001",
                author_id="biz_1",
                content="Предложение на 5М",
                submitted_at=0,
            )
        )
        state.cases["D-001"] = case
        text = build_situation("off_1", state)
        assert "D-001" in text
        assert "Закупка серверов" in text

    def test_situation_with_needs(self):
        state = self._make_state()
        state.active_needs.append(
            Need(
                case_type="procurement",
                description="Нужно оборудование",
                target_agent_id="off_1",
                appear_round=0,
            )
        )
        text = build_situation("off_1", state)
        assert "Нужно оборудование" in text

    def test_unknown_agent(self):
        state = self._make_state()
        text = build_situation("unknown", state)
        assert "Ошибка" in text
