"""Тесты реестра операций StateOp."""

import pytest
from magistry_sim.state_ops import (
    StateOp,
    CreateCaseOp,
    CloseCaseOp,
    ModifyCaseOp,
    TransferFundsOp,
    AddEvidenceOp,
    RemoveEvidenceOp,
    ModifyReputationOp,
    SendMessageOp,
    UpdateGraphOp,
    FreezeCaseOp,
    FileComplaintOp,
    InitiateTribunalOp,
    CastVoteOp,
    MoveAgentOp,
    CreateNeedOp,
    SubmitProposalOp,
    parse_state_ops,
    apply_state_op,
)
from magistry_sim.state import WorldState
from magistry_sim.config import AgentProfile, Capability


class TestParseStateOps:
    """Парсинг и валидация операций."""

    def test_parse_valid_create_case(self):
        raw = [
            {"op": "create_case", "params": {
                "case_type": "procurement",
                "title": "Закупка оборудования",
                "description": "Тестовая закупка",
                "owner_id": "off_1",
            }}
        ]
        ops = parse_state_ops(raw)
        assert len(ops) == 1
        assert isinstance(ops[0], CreateCaseOp)
        assert ops[0].params["case_type"] == "procurement"

    def test_parse_skips_unknown_op(self):
        raw = [
            {"op": "teleport_to_moon", "params": {}},
            {"op": "send_message", "from_id": "off_1", "to_id": "biz_1",
             "content": "Привет", "private": True},
        ]
        ops = parse_state_ops(raw)
        assert len(ops) == 1
        assert isinstance(ops[0], SendMessageOp)

    def test_parse_skips_malformed(self):
        raw = [
            {"op": "transfer_funds"},  # нет обязательных полей
            "not a dict",
        ]
        ops = parse_state_ops(raw)
        assert len(ops) == 0

    def test_parse_all_op_types(self):
        raw = [
            {"op": "create_case", "params": {"case_type": "procurement",
             "title": "T", "description": "D", "owner_id": "off_1"}},
            {"op": "close_case", "case_id": "D-001", "decision": "отменён"},
            {"op": "modify_case", "case_id": "D-001",
             "changes": {"title": "Новое название"}},
            {"op": "transfer_funds", "from_id": "budget",
             "to_id": "biz_1", "amount": 500000},
            {"op": "add_evidence", "evidence_type": "forged_document",
             "description": "Поддельный акт", "visible_to": ["off_1"]},
            {"op": "remove_evidence", "evidence_id": "E-001"},
            {"op": "modify_reputation", "agent_id": "off_1", "delta": -2.0},
            {"op": "send_message", "from_id": "off_1", "to_id": "biz_1",
             "content": "Привет", "private": True},
            {"op": "update_graph", "agent_a": "off_1",
             "agent_b": "biz_1", "delta": 0.5},
            {"op": "freeze_case", "case_id": "D-001"},
            {"op": "file_complaint", "case_id": "D-001",
             "assessment": "Подозрительно"},
            {"op": "initiate_tribunal", "case_id": "D-001",
             "accused_id": "off_1"},
            {"op": "cast_vote", "case_id": "D-002",
             "voter_id": "juror_0", "verdict": "виновен",
             "reasoning": "Улики очевидны"},
            {"op": "move_agent", "agent_id": "off_1",
             "location_id": "restaurant"},
            {"op": "create_need", "case_type": "procurement",
             "description": "Нужны серверы",
             "target_agent_id": "off_1", "urgency": "высокая"},
            {"op": "submit_proposal", "case_id": "D-001",
             "author_id": "biz_1", "content": "Наше предложение"},
        ]
        ops = parse_state_ops(raw)
        assert len(ops) == 16

    def test_extra_fields_ignored(self):
        """Дополнительные поля игнорируются (extra='ignore')."""
        raw = [
            {"op": "send_message", "from_id": "off_1", "to_id": "biz_1",
             "content": "Привет", "private": True,
             "unknown_field": "should_be_ignored"},
        ]
        ops = parse_state_ops(raw)
        assert len(ops) == 1
        assert isinstance(ops[0], SendMessageOp)
        assert not hasattr(ops[0], "unknown_field")

    def test_parse_empty_list(self):
        """Пустой список возвращает пустой результат."""
        ops = parse_state_ops([])
        assert len(ops) == 0


class TestApplyStateOp:
    """Применение операций к WorldState."""

    def _make_state(self) -> WorldState:
        """Создать базовое состояние мира для тестов.

        Returns:
            Состояние мира с двумя агентами.
        """
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1", name="Козлов", position="начальник",
            capabilities=[
                Capability(action="open_case",
                           case_types=["procurement"]),
            ],
        )
        state.agents["biz_1"] = AgentProfile(
            id="biz_1", name="Петров", position="директор",
        )
        state.graph.add_agent("off_1")
        state.graph.add_agent("biz_1")
        from magistry_sim.state import ReputationRecord
        state.reputation["off_1"] = ReputationRecord()
        state.reputation["biz_1"] = ReputationRecord()
        return state

    def test_apply_create_case(self):
        state = self._make_state()
        op = CreateCaseOp(params={
            "case_type": "procurement",
            "title": "Тест",
            "description": "Описание",
            "owner_id": "off_1",
        })
        result = apply_state_op(op, state, round_num=0)
        assert result.success
        assert len(state.cases) == 1

    def test_apply_create_case_arbitrary_type(self):
        """Создание дела с произвольным типом проходит успешно."""
        state = self._make_state()
        op = CreateCaseOp(params={
            "case_type": "training",
            "title": "Курс повышения квалификации",
            "description": "Обучение сотрудников",
            "owner_id": "off_1",
        })
        result = apply_state_op(op, state, round_num=0)
        assert result.success
        assert len(state.cases) == 1
        case = list(state.cases.values())[0]
        assert case.case_type == "training"
        assert case.stage == "open"

    def test_apply_close_case(self):
        state = self._make_state()
        # Сначала создаём дело
        create_op = CreateCaseOp(params={
            "case_type": "procurement",
            "title": "Тест",
            "description": "Описание",
            "owner_id": "off_1",
        })
        apply_state_op(create_op, state, round_num=0)
        case_id = list(state.cases.keys())[0]

        close_op = CloseCaseOp(case_id=case_id, decision="отменён")
        result = apply_state_op(close_op, state, round_num=1)
        assert result.success
        assert state.cases[case_id].decision == "отменён"
        assert state.cases[case_id].closed_at == 1

    def test_close_case_any_type(self):
        """Закрытие дела произвольного типа устанавливает stage='closed'."""
        state = self._make_state()
        create_op = CreateCaseOp(params={
            "case_type": "training",
            "title": "Курс",
            "description": "Обучение",
            "owner_id": "off_1",
        })
        apply_state_op(create_op, state, round_num=0)
        case_id = list(state.cases.keys())[0]

        close_op = CloseCaseOp(case_id=case_id, decision="завершён")
        result = apply_state_op(close_op, state, round_num=2)
        assert result.success
        assert state.cases[case_id].stage == "closed"
        assert state.cases[case_id].decision == "завершён"
        assert state.cases[case_id].closed_at == 2

    def test_apply_close_case_not_found(self):
        state = self._make_state()
        op = CloseCaseOp(case_id="D-999", decision="отменён")
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_apply_modify_case(self):
        state = self._make_state()
        create_op = CreateCaseOp(params={
            "case_type": "procurement",
            "title": "Старое",
            "description": "Описание",
            "owner_id": "off_1",
        })
        apply_state_op(create_op, state, round_num=0)
        case_id = list(state.cases.keys())[0]

        modify_op = ModifyCaseOp(
            case_id=case_id, changes={"title": "Новое название"}
        )
        result = apply_state_op(modify_op, state, round_num=1)
        assert result.success
        assert state.cases[case_id].title == "Новое название"

    def test_apply_send_message(self):
        state = self._make_state()
        op = SendMessageOp(
            from_id="off_1", to_id="biz_1",
            content="Привет", private=True,
        )
        result = apply_state_op(op, state, round_num=0)
        assert result.success
        assert len(state.messages) == 1
        assert state.messages[0].private is True

    def test_apply_modify_reputation(self):
        state = self._make_state()
        old_score = state.reputation["off_1"].score
        op = ModifyReputationOp(agent_id="off_1", delta=-3.0)
        result = apply_state_op(op, state, round_num=0)
        assert result.success
        assert state.reputation["off_1"].score == old_score - 3.0

    def test_apply_update_graph(self):
        state = self._make_state()
        op = UpdateGraphOp(agent_a="off_1", agent_b="biz_1", delta=2.0)
        result = apply_state_op(op, state, round_num=0)
        assert result.success

    def test_apply_unknown_agent_fails(self):
        state = self._make_state()
        op = ModifyReputationOp(agent_id="ghost", delta=1.0)
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_apply_create_need(self):
        state = self._make_state()
        op = CreateNeedOp(
            case_type="procurement",
            description="Нужны серверы",
            target_agent_id="off_1",
            urgency="высокая",
        )
        result = apply_state_op(op, state, round_num=3)
        assert result.success
        assert len(state.active_needs) == 1
        assert state.active_needs[0].appear_round == 3

    def test_apply_transfer_funds(self):
        state = self._make_state()
        op = TransferFundsOp(from_id="budget", to_id="biz_1", amount=500000)
        result = apply_state_op(op, state, round_num=0)
        assert result.success

    def test_apply_add_evidence(self):
        state = self._make_state()
        op = AddEvidenceOp(
            evidence_type="forged_document",
            description="Поддельный акт",
            visible_to=["off_1"],
        )
        result = apply_state_op(op, state, round_num=0)
        assert result.success

    def test_apply_remove_evidence(self):
        state = self._make_state()
        op = RemoveEvidenceOp(evidence_id="E-001")
        result = apply_state_op(op, state, round_num=0)
        assert result.success

    def test_apply_file_complaint(self):
        state = self._make_state()
        op = FileComplaintOp(case_id="D-001", assessment="Подозрительно")
        result = apply_state_op(op, state, round_num=2)
        assert result.success
        assert len(state.complaints) == 1
        assert state.complaints[0].round == 2

    def test_apply_initiate_tribunal(self):
        state = self._make_state()
        op = InitiateTribunalOp(case_id="D-001", accused_id="off_1")
        result = apply_state_op(op, state, round_num=1)
        assert result.success
        assert len(state.cases) == 1
        tribunal = list(state.cases.values())[0]
        assert tribunal.case_type == "investigation"
        assert tribunal.stage == "tribunal"

    def test_apply_cast_vote(self):
        state = self._make_state()
        # Сначала создаём дело для голосования
        op_tribunal = InitiateTribunalOp(case_id="D-999", accused_id="off_1")
        apply_state_op(op_tribunal, state, round_num=0)
        tribunal_id = list(state.cases.keys())[0]

        op = CastVoteOp(
            case_id=tribunal_id, voter_id="juror_0",
            verdict="виновен", reasoning="Улики очевидны",
        )
        result = apply_state_op(op, state, round_num=1)
        assert result.success
        assert len(state.cases[tribunal_id].votes) == 1

    def test_apply_cast_vote_case_not_found(self):
        state = self._make_state()
        op = CastVoteOp(
            case_id="D-999", voter_id="juror_0",
            verdict="виновен", reasoning="Улики",
        )
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_apply_move_agent_no_locations(self):
        """Перемещение без инициализации локаций завершается ошибкой."""
        state = self._make_state()
        op = MoveAgentOp(agent_id="off_1", location_id="restaurant")
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_apply_move_agent_with_locations(self):
        """Перемещение в существующую локацию работает."""
        from magistry_sim.locations import LocationManager, Location
        state = self._make_state()
        state.locations = LocationManager()
        state.locations.add_location(
            Location(id="restaurant", name="Ресторан", public=False)
        )
        op = MoveAgentOp(agent_id="off_1", location_id="restaurant")
        result = apply_state_op(op, state, round_num=0)
        assert result.success

    def test_apply_move_agent_unknown_location(self):
        """Перемещение в несуществующую локацию завершается ошибкой."""
        from magistry_sim.locations import LocationManager
        state = self._make_state()
        state.locations = LocationManager()
        op = MoveAgentOp(agent_id="off_1", location_id="mars_base")
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_apply_submit_proposal(self):
        state = self._make_state()
        # Сначала создаём дело
        create_op = CreateCaseOp(params={
            "case_type": "procurement",
            "title": "Тест",
            "description": "Описание",
            "owner_id": "off_1",
        })
        apply_state_op(create_op, state, round_num=0)
        case_id = list(state.cases.keys())[0]

        op = SubmitProposalOp(
            case_id=case_id, author_id="biz_1",
            content="Наше предложение",
        )
        result = apply_state_op(op, state, round_num=1)
        assert result.success
        assert len(state.cases[case_id].proposals) == 1

    def test_apply_submit_proposal_case_not_found(self):
        state = self._make_state()
        op = SubmitProposalOp(
            case_id="D-999", author_id="biz_1",
            content="Наше предложение",
        )
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_apply_freeze_case(self):
        state = self._make_state()
        create_op = CreateCaseOp(params={
            "case_type": "procurement",
            "title": "Тест",
            "description": "Описание",
            "owner_id": "off_1",
        })
        apply_state_op(create_op, state, round_num=0)
        case_id = list(state.cases.keys())[0]

        op = FreezeCaseOp(case_id=case_id)
        result = apply_state_op(op, state, round_num=1)
        assert result.success

    def test_apply_freeze_case_not_found(self):
        state = self._make_state()
        op = FreezeCaseOp(case_id="D-999")
        result = apply_state_op(op, state, round_num=0)
        assert not result.success

    def test_event_log_populated(self):
        """Все операции записывают события в журнал."""
        state = self._make_state()
        op = SendMessageOp(
            from_id="off_1", to_id="biz_1",
            content="Тест", private=False,
        )
        apply_state_op(op, state, round_num=5)
        events = state.event_log.get_events(event_type="message_sent")
        assert len(events) == 1
        assert events[0].round == 5
