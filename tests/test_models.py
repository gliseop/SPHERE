"""Тесты моделей данных, конфигурации и конечного автомата."""

import pytest
from pydantic import ValidationError

from magistry_sim.config import (
    AgentProfile,
    Capability,
    Connection,
    GovernanceConfig,
    Need,
    ResourcePool,
    ScenarioConfig,
)
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.cases import (
    CASE_REGISTRY,
    Case,
    CaseSchema,
    Note,
    Proposal,
    apply_transition,
    check_condition,
    validate_transition,
)
from magistry_sim.resources import ResourceManager


class TestEnums:
    def test_governance_modes(self):
        assert GovernanceMode.G0.value == "G0"
        assert GovernanceMode.G3.value == "G3"

    def test_scenario_ids(self):
        assert ScenarioId.S0.value == "S0"
        assert ScenarioId.S6.value == "S6"


class TestConfig:
    def test_capability(self):
        cap = Capability(action="open_case", case_types=["procurement"])
        assert cap.action == "open_case"
        assert "procurement" in cap.case_types

    def test_capability_extra_forbid(self):
        with pytest.raises(ValidationError):
            Capability(action="open_case", unknown_field="x")

    def test_connection(self):
        conn = Connection(
            target_id="biz_1", name="Петров", relation="коллега"
        )
        assert conn.strength == 1.0

    def test_agent_profile(self):
        profile = AgentProfile(
            id="off_1",
            name="Иванов А.П.",
            position="начальник отдела",
            greed=0.5,
            fear=0.5,
            honesty=0.5,
        )
        assert profile.id == "off_1"
        assert profile.immune is False

    def test_agent_profile_extra_forbid(self):
        with pytest.raises(ValidationError):
            AgentProfile(
                id="x", name="x", position="x", extra_field="y"
            )

    def test_scenario_config(self):
        cfg = ScenarioConfig(
            id=ScenarioId.S0,
            title="Test",
            description="Test scenario",
        )
        assert cfg.max_rounds == 8
        assert cfg.seed == 42


class TestCases:
    def test_case_creation(self):
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Закупка",
            description="Описание",
            owner_id="off_1",
            stage="open",
        )
        assert case.id == "D-001"
        assert case.proposals == []

    def test_proposal_creation(self):
        p = Proposal(
            id="P-001",
            case_id="D-001",
            author_id="biz_1",
            content="Предложение",
            submitted_at=1,
        )
        assert p.author_id == "biz_1"

    def test_case_registry_procurement(self):
        schema = CASE_REGISTRY["procurement"]
        assert "open" in schema.stages
        assert schema.initial_stage == "open"
        assert schema.auto_transitions["open"] == "collecting"

    def test_case_registry_hiring(self):
        schema = CASE_REGISTRY["hiring"]
        assert "screening" in schema.stages
        assert schema.initial_stage == "open"


class TestFSM:
    def test_validate_auto_transition(self):
        assert validate_transition(
            "procurement", "open", "collecting", "auto"
        )

    def test_validate_action_transition(self):
        assert validate_transition(
            "procurement", "evaluation", "closed", "resolve_case"
        )

    def test_invalid_transition(self):
        assert not validate_transition(
            "procurement", "open", "closed", "resolve_case"
        )

    def test_unknown_case_type(self):
        assert not validate_transition(
            "unknown", "open", "closed", "resolve_case"
        )

    def test_check_condition_deadline_expired(self):
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
            deadline_round=5,
        )
        assert not check_condition("deadline_expired", case, 4)
        assert check_condition("deadline_expired", case, 5)
        assert check_condition("deadline_expired", case, 6)

    def test_check_condition_has_proposals(self):
        case = Case(
            id="D-001",
            case_type="hiring",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="open",
        )
        assert not check_condition("has_proposals", case, 0)

        case.proposals.append(
            Proposal(
                id="P-001",
                case_id="D-001",
                author_id="cand_1",
                content="Отклик",
                submitted_at=0,
            )
        )
        assert check_condition("has_proposals", case, 0)

    def test_apply_transition(self):
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="open",
        )
        apply_transition(case, "collecting")
        assert case.stage == "collecting"


class TestResources:
    def test_init_and_check_budget(self):
        rm = ResourceManager()
        rm.init_agent("off_1", budget_limit=1_000_000)
        assert rm.check_budget("off_1", 500_000)
        assert rm.check_budget("off_1", 1_000_000)
        assert not rm.check_budget("off_1", 1_000_001)

    def test_spend_budget(self):
        rm = ResourceManager()
        rm.init_agent("off_1", budget_limit=1_000)
        assert rm.spend_budget("off_1", 600)
        assert not rm.spend_budget("off_1", 500)
        assert rm.check_budget("off_1", 400)

    def test_staffing_slots(self):
        rm = ResourceManager()
        rm.init_agent("off_1", staffing_slots=2)
        assert rm.fill_slot("off_1")
        assert rm.fill_slot("off_1")
        assert not rm.fill_slot("off_1")
        assert rm.free_slot("off_1")
        assert rm.fill_slot("off_1")

    def test_contract_capacity(self):
        rm = ResourceManager()
        rm.init_agent("biz_1", contract_capacity=1)
        assert rm.use_capacity("biz_1")
        assert not rm.use_capacity("biz_1")
        assert rm.free_capacity("biz_1")
        assert rm.use_capacity("biz_1")

    def test_unknown_agent(self):
        rm = ResourceManager()
        assert not rm.check_budget("unknown", 100)
        assert not rm.spend_budget("unknown", 100)
        assert not rm.fill_slot("unknown")
        assert not rm.free_slot("unknown")
        assert not rm.use_capacity("unknown")
        assert not rm.free_capacity("unknown")
