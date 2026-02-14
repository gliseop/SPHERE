"""Тесты состояния мира."""

from magistry_sim.cases import Case, Proposal, Note
from magistry_sim.config import AgentProfile, Capability
from magistry_sim.state import WorldState, Message, Complaint


class TestWorldState:
    def _make_state(self):
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Иванов",
            position="начальник",
            capabilities=[
                Capability(action="open_case", case_types=["procurement"]),
                Capability(
                    action="resolve_case", case_types=["procurement"]
                ),
            ],
        )
        state.agents["biz_1"] = AgentProfile(
            id="biz_1",
            name="Петров",
            position="подрядчик",
            capabilities=[
                Capability(
                    action="submit_proposal", case_types=["procurement"]
                ),
            ],
        )
        return state

    def test_new_case_id(self):
        state = WorldState()
        assert state.new_case_id() == "D-001"
        assert state.new_case_id() == "D-002"

    def test_new_proposal_id(self):
        state = WorldState()
        assert state.new_proposal_id() == "P-001"

    def test_has_capability(self):
        state = self._make_state()
        assert state.has_capability("off_1", "open_case", "procurement")
        assert not state.has_capability("off_1", "open_case", "hiring")
        assert not state.has_capability("biz_1", "open_case", "procurement")
        assert state.has_capability(
            "biz_1", "submit_proposal", "procurement"
        )

    def test_get_agent_cases(self):
        state = self._make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="open",
        )
        state.cases["D-001"] = case
        assert len(state.get_agent_cases("off_1")) == 1
        assert len(state.get_agent_cases("biz_1")) == 0

    def test_get_open_cases(self):
        state = self._make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        state.cases["D-001"] = case
        assert len(state.get_open_cases()) == 1

        case.stage = "closed"
        assert len(state.get_open_cases()) == 0

    def test_get_cases_involving(self):
        state = self._make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        case.proposals.append(
            Proposal(
                id="P-001",
                case_id="D-001",
                author_id="biz_1",
                content="Test",
                submitted_at=0,
            )
        )
        state.cases["D-001"] = case
        assert len(state.get_cases_involving("off_1")) == 1
        assert len(state.get_cases_involving("biz_1")) == 1
        assert len(state.get_cases_involving("unknown")) == 0

    def test_message(self):
        msg = Message(
            from_id="off_1",
            to_id="biz_1",
            content="Привет",
            round=0,
        )
        assert msg.private is True

    def test_complaint(self):
        comp = Complaint(
            case_id="D-001",
            assessment="Подозрительно",
            round=3,
        )
        assert comp.case_id == "D-001"
