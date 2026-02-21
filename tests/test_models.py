"""Тесты моделей данных и конфигурации."""

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
    Case,
    Note,
    Proposal,
    Vote,
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

    def test_capability_extra_ignored(self):
        cap = Capability(action="open_case", unknown_field="x")
        assert cap.action == "open_case"
        assert not hasattr(cap, "unknown_field")

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

    def test_agent_profile_extra_ignored(self):
        profile = AgentProfile(
            id="x", name="x", position="x", extra_field="y"
        )
        assert profile.id == "x"
        assert not hasattr(profile, "extra_field")

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
        """Стандартное создание дела с типом procurement."""
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

    def test_case_arbitrary_type(self):
        """Дело может иметь произвольный тип, не ограниченный реестром."""
        case = Case(
            id="D-100",
            case_type="training",
            title="Обучение персонала",
            description="Курс повышения квалификации",
            owner_id="hr_1",
            stage="planned",
        )
        assert case.case_type == "training"
        assert case.stage == "planned"

    def test_case_extra_fields_ignored(self):
        """Лишние поля при создании дела игнорируются, а не вызывают ошибку."""
        case = Case(
            id="D-200",
            case_type="audit",
            title="Проверка",
            description="Внеплановая проверка",
            owner_id="aud_1",
            stage="initiated",
            unknown_field="значение",
            priority=5,
        )
        assert case.id == "D-200"
        assert case.case_type == "audit"
        assert not hasattr(case, "unknown_field")
        assert not hasattr(case, "priority")

    def test_case_stage_changed_freely(self):
        """Стадия дела может быть изменена на произвольное значение."""
        case = Case(
            id="D-300",
            case_type="procurement",
            title="Закупка",
            description="Описание",
            owner_id="off_1",
            stage="open",
        )
        case.stage = "custom_review"
        assert case.stage == "custom_review"

        case.stage = "approved"
        assert case.stage == "approved"

    def test_proposal_creation(self):
        """Корректное создание предложения."""
        p = Proposal(
            id="P-001",
            case_id="D-001",
            author_id="biz_1",
            content="Предложение",
            submitted_at=1,
        )
        assert p.author_id == "biz_1"
        assert p.submitted_at == 1

    def test_proposal_extra_fields_ignored(self):
        """Лишние поля при создании предложения игнорируются."""
        p = Proposal(
            id="P-002",
            case_id="D-001",
            author_id="biz_2",
            content="Текст",
            submitted_at=2,
            extra="лишнее",
        )
        assert p.id == "P-002"
        assert not hasattr(p, "extra")

    def test_note_creation(self):
        """Корректное создание записи в деле."""
        n = Note(
            id="N-001",
            case_id="D-001",
            author_id="off_1",
            content="Комментарий",
            created_at=3,
        )
        assert n.author_id == "off_1"
        assert n.created_at == 3

    def test_note_extra_fields_ignored(self):
        """Лишние поля при создании записи игнорируются."""
        n = Note(
            id="N-002",
            case_id="D-001",
            author_id="off_2",
            content="Текст",
            created_at=4,
            visibility="private",
        )
        assert n.id == "N-002"
        assert not hasattr(n, "visibility")

    def test_vote_creation(self):
        """Корректное создание голоса."""
        v = Vote(
            voter_id="juror_1",
            case_id="T-001",
            verdict="виновен",
            reasoning="Обоснование",
            round=0,
        )
        assert v.verdict == "виновен"
        assert v.round == 0

    def test_vote_extra_fields_ignored(self):
        """Лишние поля при создании голоса игнорируются."""
        v = Vote(
            voter_id="juror_2",
            case_id="T-001",
            verdict="невиновен",
            reasoning="Причина",
            round=1,
            confidence=0.95,
        )
        assert v.voter_id == "juror_2"
        assert not hasattr(v, "confidence")


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


class TestAgentProfileV4:
    def test_profile_with_personality(self):
        from magistry_sim.personality import (
            AgentPersonality,
            HEXACOProfile,
            DarkTriadProfile,
            NeutralizationTechnique,
        )

        personality = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=20,
                emotionality=30,
                extraversion=80,
                agreeableness=25,
                conscientiousness=60,
                openness=70,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=90, psychopathy=70
            ),
            neutralization_techniques=[NeutralizationTechnique.EVERYONE_DOES_IT],
        )
        profile = AgentProfile(
            id="test_1",
            name="Тестов Т.Т.",
            position="чиновник",
            capabilities=[],
            personality=personality,
        )
        assert profile.personality.hexaco.honesty_humility == 20
        assert profile.personality.classify_archetype() == "initiator"

    def test_legacy_profile_still_works(self):
        """Обратная совместимость: старые профили без personality."""
        profile = AgentProfile(
            id="old_1",
            name="Старый Т.Т.",
            position="чиновник",
            capabilities=[],
            greed=0.8,
            honesty=0.2,
        )
        assert profile.greed == 0.8
