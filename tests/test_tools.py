"""Тесты инструментов через прямой вызов с contextvars."""

import pytest

from magistry_sim.agents import MockAgentRunner
from magistry_sim.cases import Case, Proposal
from magistry_sim.config import AgentProfile, Capability
from magistry_sim.state import WorldState
from magistry_sim.tools import (
    current_agent_id,
    current_runner,
    current_state,
)
from magistry_sim.tools.actions import (
    add_note,
    cast_vote,
    file_report,
    open_case,
    resolve_case,
    submit_proposal,
)
from magistry_sim.tools.communication import talk_to


def _setup_context(state, agent_id, runner=None):
    """Установить контекст для инструментов."""
    runner = runner or MockAgentRunner()
    t1 = current_state.set(state)
    t2 = current_agent_id.set(agent_id)
    t3 = current_runner.set(runner)
    return t1, t2, t3


def _reset_context(tokens):
    """Сбросить контекст."""
    current_state.reset(tokens[0])
    current_agent_id.reset(tokens[1])
    current_runner.reset(tokens[2])


def _make_state():
    """Создать состояние с типичными агентами."""
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
    state.agents["auditor"] = AgentProfile(
        id="auditor",
        name="Аудитор",
        position="аудитор",
        capabilities=[
            Capability(action="audit", case_types=[]),
            Capability(action="file_report", case_types=[]),
        ],
    )
    state.agents["juror_0"] = AgentProfile(
        id="juror_0",
        name="Присяжный 1",
        position="присяжный",
        capabilities=[
            Capability(action="vote", case_types=[]),
        ],
    )
    state.graph.add_agent("off_1")
    state.graph.add_agent("biz_1")
    state.graph.add_agent("auditor")
    state.graph.add_agent("juror_0")
    return state


class TestOpenCase:
    def test_open_case_success(self):
        state = _make_state()
        tokens = _setup_context(state, "off_1")
        try:
            result = open_case(
                "procurement", "Закупка серверов", "Описание"
            )
            assert "D-001" in result
            assert "D-001" in state.cases
            assert state.cases["D-001"].stage == "collecting"
        finally:
            _reset_context(tokens)

    def test_open_case_no_capability(self):
        state = _make_state()
        tokens = _setup_context(state, "biz_1")
        try:
            result = open_case(
                "procurement", "Закупка", "Описание"
            )
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_open_case_unknown_type(self):
        state = _make_state()
        tokens = _setup_context(state, "off_1")
        try:
            result = open_case(
                "unknown_type", "Что-то", "Описание"
            )
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)


class TestSubmitProposal:
    def test_submit_success(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "biz_1")
        try:
            result = submit_proposal("D-001", "Моё предложение")
            assert "P-001" in result
            assert len(case.proposals) == 1
        finally:
            _reset_context(tokens)

    def test_submit_owner_rejected(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "off_1")
        try:
            result = submit_proposal("D-001", "Моё предложение")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_submit_duplicate_rejected(self):
        state = _make_state()
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
                content="First",
                submitted_at=0,
            )
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "biz_1")
        try:
            result = submit_proposal("D-001", "Second")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)


class TestAddNote:
    def test_add_note_owner(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "off_1")
        try:
            result = add_note("D-001", "Уточнение")
            assert "N-001" in result
            assert len(case.notes) == 1
        finally:
            _reset_context(tokens)

    def test_add_note_uninvolved_rejected(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "biz_1")
        try:
            result = add_note("D-001", "Комментарий")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)


class TestResolveCase:
    def test_resolve_success(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="evaluation",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "off_1")
        try:
            result = resolve_case(
                "D-001", "Выбран Петров", "Лучшая цена"
            )
            assert "закрыто" in result
            assert case.stage == "closed"
            assert case.decision == "Выбран Петров"
        finally:
            _reset_context(tokens)

    def test_resolve_wrong_stage(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="collecting",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "off_1")
        try:
            result = resolve_case(
                "D-001", "Решение", "Обоснование"
            )
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_resolve_not_owner(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="evaluation",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "biz_1")
        try:
            result = resolve_case(
                "D-001", "Решение", "Обоснование"
            )
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_resolve_intermediate_stage_not_closed(self):
        state = _make_state()
        state.agents["off_1"].capabilities.append(
            Capability(action="resolve_case", case_types=["hiring"])
        )
        case = Case(
            id="H-001",
            case_type="hiring",
            title="Найм",
            description="Test",
            owner_id="off_1",
            stage="screening",
        )
        state.cases["H-001"] = case

        tokens = _setup_context(state, "off_1")
        try:
            result = resolve_case(
                "H-001", "Переход к интервью", "Кандидаты отобраны"
            )
            assert "переведено в стадию decision" in result
            assert case.stage == "decision"
            assert case.closed_at is None
        finally:
            _reset_context(tokens)


class TestFileReport:
    def test_auditor_report(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="closed",
            closed_at=1,
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "auditor")
        try:
            result = file_report(
                "D-001", "Подозрительная связь", "tribunal"
            )
            assert "Отчёт" in result
            assert "tribunal" in result
        finally:
            _reset_context(tokens)

    def test_anonymous_complaint(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Test",
            description="Test",
            owner_id="off_1",
            stage="closed",
            closed_at=1,
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "biz_1")
        try:
            result = file_report(
                "D-001", "Нечестное решение", ""
            )
            assert "анонимно" in result
            assert len(state.complaints) == 1
        finally:
            _reset_context(tokens)


class TestCastVote:
    def test_vote_success(self):
        state = _make_state()
        case = Case(
            id="T-001",
            case_type="investigation",
            title="Трибунал",
            description="Test",
            owner_id="auditor",
            stage="tribunal",
        )
        state.cases["T-001"] = case

        tokens = _setup_context(state, "juror_0")
        try:
            result = cast_vote(
                "T-001", "виновен", "Доказательства убедительны"
            )
            assert "засчитан" in result
            assert len(case.votes) == 1
        finally:
            _reset_context(tokens)

    def test_vote_no_capability(self):
        state = _make_state()
        case = Case(
            id="T-001",
            case_type="investigation",
            title="Трибунал",
            description="Test",
            owner_id="auditor",
            stage="tribunal",
        )
        state.cases["T-001"] = case

        tokens = _setup_context(state, "off_1")
        try:
            result = cast_vote("T-001", "виновен", "Причина")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_vote_duplicate(self):
        state = _make_state()
        case = Case(
            id="T-001",
            case_type="investigation",
            title="Трибунал",
            description="Test",
            owner_id="auditor",
            stage="tribunal",
        )
        state.cases["T-001"] = case

        tokens = _setup_context(state, "juror_0")
        try:
            cast_vote("T-001", "виновен", "Причина")
            result = cast_vote("T-001", "невиновен", "Передумал")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_vote_rejected_for_non_tribunal_case(self):
        state = _make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Закупка",
            description="Test",
            owner_id="off_1",
            stage="evaluation",
        )
        state.cases["D-001"] = case

        tokens = _setup_context(state, "juror_0")
        try:
            result = cast_vote("D-001", "виновен", "Причина")
            assert "Ошибка" in result
            assert len(case.votes) == 0
        finally:
            _reset_context(tokens)


class TestTalkTo:
    def test_talk_to_success(self):
        state = _make_state()
        runner = MockAgentRunner()
        tokens = _setup_context(state, "off_1", runner)
        try:
            response = talk_to("biz_1", "Привет")
            assert isinstance(response, str)
            assert len(response) > 0
            assert len(state.messages) == 1
        finally:
            _reset_context(tokens)

    def test_talk_to_self(self):
        state = _make_state()
        tokens = _setup_context(state, "off_1")
        try:
            result = talk_to("off_1", "Привет")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)

    def test_talk_to_nonexistent(self):
        state = _make_state()
        tokens = _setup_context(state, "off_1")
        try:
            result = talk_to("nonexistent", "Привет")
            assert "Ошибка" in result
        finally:
            _reset_context(tokens)
