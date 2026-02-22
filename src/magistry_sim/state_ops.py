"""Реестр операций над WorldState.

Каждая операция -- pydantic-модель со строгой валидацией.
Арбитр генерирует операции в формате JSON, движок парсит
и применяет их к состоянию мира.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Базовый класс и результат
# ---------------------------------------------------------------------------


class StateOp(BaseModel):
    """Базовая операция над состоянием мира.

    Все конкретные операции наследуются от этого класса
    и задают значение ``op`` по умолчанию.
    """

    model_config = {"extra": "ignore"}
    op: str


@dataclass
class OpResult:
    """Результат применения операции.

    Attributes:
        success: Успешность применения.
        message: Пояснительное сообщение.
    """

    success: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Конкретные операции
# ---------------------------------------------------------------------------


class CreateCaseOp(StateOp):
    """Создать новое дело.

    Attributes:
        params: Параметры дела (case_type, title, description, owner_id).
    """

    op: str = "create_case"
    params: dict[str, Any] = Field(default_factory=dict)


class CloseCaseOp(StateOp):
    """Закрыть или отменить дело.

    Attributes:
        case_id: Идентификатор дела.
        decision: Решение по делу.
    """

    op: str = "close_case"
    case_id: str
    decision: str = ""


class ModifyCaseOp(StateOp):
    """Изменить параметры дела.

    Attributes:
        case_id: Идентификатор дела.
        changes: Словарь изменений (поле: новое значение).
    """

    op: str = "modify_case"
    case_id: str
    changes: dict[str, Any] = Field(default_factory=dict)


class TransferFundsOp(StateOp):
    """Передать средства.

    Attributes:
        from_id: Отправитель.
        to_id: Получатель.
        amount: Сумма перевода.
    """

    op: str = "transfer_funds"
    from_id: str
    to_id: str
    amount: float


class AddEvidenceOp(StateOp):
    """Добавить улику или документ.

    Attributes:
        evidence_type: Тип улики.
        description: Описание.
        visible_to: Список агентов, которым видна улика.
    """

    op: str = "add_evidence"
    evidence_type: str = ""
    description: str = ""
    visible_to: list[str] = Field(default_factory=list)


class RemoveEvidenceOp(StateOp):
    """Удалить или подделать документ.

    Attributes:
        evidence_id: Идентификатор улики.
    """

    op: str = "remove_evidence"
    evidence_id: str = ""


class ModifyReputationOp(StateOp):
    """Изменить репутацию агента.

    Attributes:
        agent_id: Идентификатор агента.
        delta: Изменение репутации (может быть отрицательным).
    """

    op: str = "modify_reputation"
    agent_id: str
    delta: float


class SendMessageOp(StateOp):
    """Отправить сообщение.

    Attributes:
        from_id: Отправитель.
        to_id: Получатель.
        content: Содержание сообщения.
        private: Является ли сообщение приватным.
    """

    op: str = "send_message"
    from_id: str
    to_id: str
    content: str
    private: bool = True


class UpdateGraphOp(StateOp):
    """Изменить силу связи в социальном графе.

    Attributes:
        agent_a: Первый агент.
        agent_b: Второй агент.
        delta: Величина изменения силы связи.
    """

    op: str = "update_graph"
    agent_a: str
    agent_b: str
    delta: float = 0.1


class FreezeCaseOp(StateOp):
    """Заморозить дело.

    Attributes:
        case_id: Идентификатор дела.
    """

    op: str = "freeze_case"
    case_id: str


class FileComplaintOp(StateOp):
    """Подать жалобу.

    Attributes:
        case_id: Идентификатор дела.
        assessment: Текст оценки ситуации.
    """

    op: str = "file_complaint"
    case_id: str
    assessment: str = ""


class InitiateTribunalOp(StateOp):
    """Инициировать трибунал.

    Attributes:
        case_id: Идентификатор исходного дела.
        accused_id: Идентификатор обвиняемого агента.
    """

    op: str = "initiate_tribunal"
    case_id: str
    accused_id: str


class CastVoteOp(StateOp):
    """Проголосовать на трибунале.

    Attributes:
        case_id: Идентификатор дела трибунала.
        voter_id: Идентификатор голосующего.
        verdict: Вердикт.
        reasoning: Обоснование вердикта.
    """

    op: str = "cast_vote"
    case_id: str
    voter_id: str
    verdict: str
    reasoning: str = ""


class MoveAgentOp(StateOp):
    """Переместить агента в локацию.

    Attributes:
        agent_id: Идентификатор агента.
        location_id: Идентификатор целевой локации.
    """

    op: str = "move_agent"
    agent_id: str
    location_id: str


class CreateNeedOp(StateOp):
    """Создать новую потребность организации.

    Attributes:
        case_type: Тип дела для потребности.
        description: Описание потребности.
        target_agent_id: Агент, ответственный за удовлетворение.
        urgency: Степень срочности.
    """

    op: str = "create_need"
    case_type: str
    description: str
    target_agent_id: str
    urgency: str = "средняя"


class SubmitProposalOp(StateOp):
    """Подать предложение по делу.

    Attributes:
        case_id: Идентификатор дела.
        author_id: Автор предложения.
        content: Содержание предложения.
    """

    op: str = "submit_proposal"
    case_id: str
    author_id: str
    content: str


# ---------------------------------------------------------------------------
# Реестр и парсинг
# ---------------------------------------------------------------------------

_OP_REGISTRY: dict[str, type[StateOp]] = {
    "create_case": CreateCaseOp,
    "close_case": CloseCaseOp,
    "modify_case": ModifyCaseOp,
    "transfer_funds": TransferFundsOp,
    "add_evidence": AddEvidenceOp,
    "remove_evidence": RemoveEvidenceOp,
    "modify_reputation": ModifyReputationOp,
    "send_message": SendMessageOp,
    "update_graph": UpdateGraphOp,
    "freeze_case": FreezeCaseOp,
    "file_complaint": FileComplaintOp,
    "initiate_tribunal": InitiateTribunalOp,
    "cast_vote": CastVoteOp,
    "move_agent": MoveAgentOp,
    "create_need": CreateNeedOp,
    "submit_proposal": SubmitProposalOp,
}


def _normalize_raw_op(item: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать сырую операцию перед валидацией.

    LLM-модели часто возвращают поля в нестандартном формате:
    - Пробелы в именах ключей
    - create_case: параметры на верхнем уровне вместо вложенного params
    - add_evidence: visible_to как строка вместо списка
    - create_need: отсутствующий target_agent_id

    Args:
        item: Сырой словарь операции от арбитра.

    Returns:
        Нормализованный словарь.
    """
    # Убрать пробелы в ключах
    item = {k.strip(): v for k, v in item.items()}
    op_name = item.get("op", "")

    # create_case: собрать top-level поля в params
    if op_name == "create_case" and "params" not in item:
        params_keys = {"case_type", "title", "description", "owner_id"}
        params = {}
        for k in list(item.keys()):
            if k in params_keys:
                params[k] = item.pop(k)
        if params:
            item["params"] = params

    # add_evidence: visible_to строка → список
    if op_name == "add_evidence" and isinstance(
        item.get("visible_to"), str
    ):
        val = item["visible_to"]
        item["visible_to"] = [val] if val else []

    # create_need: default target_agent_id
    if op_name == "create_need" and "target_agent_id" not in item:
        item["target_agent_id"] = item.get("agent_id", "unknown")

    return item


def parse_state_ops(raw_ops: list[Any]) -> list[StateOp]:
    """Распарсить список сырых операций от арбитра.

    Некорректные и неизвестные операции пропускаются
    с предупреждением в лог. Перед валидацией каждая операция
    проходит нормализацию для устранения типичных расхождений
    в формате LLM-ответов.

    Args:
        raw_ops: Список словарей от LLM-арбитра.

    Returns:
        Список валидных операций.
    """
    result: list[StateOp] = []
    for item in raw_ops:
        if not isinstance(item, dict):
            logger.warning("Пропуск не-словаря в state_changes: %s", item)
            continue
        op_name = item.get("op", "")
        if not op_name:
            continue
        op_cls = _OP_REGISTRY.get(op_name)
        if op_cls is None:
            logger.warning("Неизвестная операция: %s", op_name)
            continue
        try:
            normalized = _normalize_raw_op(item)
            op = op_cls.model_validate(normalized)
            result.append(op)
        except Exception as exc:
            logger.warning(
                "Ошибка валидации операции %s: %s", op_name, exc
            )
    return result


# ---------------------------------------------------------------------------
# Применение операций
# ---------------------------------------------------------------------------


def apply_state_op(
    op: StateOp,
    state: "WorldState",
    round_num: int,
    agent_id: str = "",
) -> OpResult:
    """Применить одну операцию к состоянию мира.

    Args:
        op: Операция.
        state: Состояние мира.
        round_num: Текущий раунд.
        agent_id: Идентификатор агента-инициатора.

    Returns:
        Результат применения.
    """
    from .cases import Case, Proposal, Vote
    from .config import Need
    from .state import Complaint, Message, ReputationRecord

    if isinstance(op, CreateCaseOp):
        owner_id = op.params.get("owner_id", agent_id)
        if not owner_id:
            return OpResult(
                False, "create_case: отсутствует owner_id"
            )
        case_type = op.params.get("case_type", "")
        case_id = state.new_case_id()
        case = Case(
            id=case_id,
            case_type=case_type,
            title=op.params.get("title", ""),
            description=op.params.get("description", ""),
            owner_id=owner_id,
            stage=op.params.get("stage", "open"),
            params=op.params.get("params", ""),
            created_at=round_num,
        )
        state.cases[case_id] = case
        state.event_log.log(
            round=round_num,
            event_type="case_opened",
            agent_id=agent_id,
            payload={"case_id": case_id, "case_type": case_type},
        )
        # Удалить удовлетворённую потребность (аналогично tools.actions.open_case)
        state.active_needs = [
            n
            for n in state.active_needs
            if not (
                n.target_agent_id == owner_id
                and n.case_type == case_type
            )
        ]
        return OpResult(True, f"Дело {case_id} создано")

    if isinstance(op, CloseCaseOp):
        case = state.cases.get(op.case_id)
        if case is None:
            return OpResult(False, f"Дело {op.case_id} не найдено")
        case.stage = "closed"
        case.decision = op.decision
        case.closed_at = round_num
        state.event_log.log(
            round=round_num,
            event_type="case_resolved",
            agent_id=agent_id,
            payload={"case_id": op.case_id, "decision": op.decision},
        )
        return OpResult(True, f"Дело {op.case_id} закрыто")

    if isinstance(op, ModifyCaseOp):
        case = state.cases.get(op.case_id)
        if case is None:
            return OpResult(False, f"Дело {op.case_id} не найдено")
        _MODIFIABLE_CASE_FIELDS = {
            "title", "description", "params", "deadline_round", "owner_id",
            "stage",
        }
        for key, value in op.changes.items():
            if key in _MODIFIABLE_CASE_FIELDS:
                setattr(case, key, value)
        state.event_log.log(
            round=round_num,
            event_type="case_modified",
            agent_id=agent_id,
            payload={"case_id": op.case_id, "changes": op.changes},
        )
        return OpResult(True, f"Дело {op.case_id} изменено")

    if isinstance(op, TransferFundsOp):
        state.event_log.log(
            round=round_num,
            event_type="funds_transferred",
            agent_id=agent_id,
            payload={
                "from": op.from_id,
                "to": op.to_id,
                "amount": op.amount,
            },
        )
        return OpResult(
            True, f"Перевод {op.amount} от {op.from_id} к {op.to_id}"
        )

    if isinstance(op, AddEvidenceOp):
        state.event_log.log(
            round=round_num,
            event_type="evidence_added",
            agent_id=agent_id,
            payload={
                "evidence_type": op.evidence_type,
                "description": op.description,
                "visible_to": op.visible_to,
            },
        )
        return OpResult(True, "Улика добавлена")

    if isinstance(op, RemoveEvidenceOp):
        state.event_log.log(
            round=round_num,
            event_type="evidence_removed",
            agent_id=agent_id,
            payload={"evidence_id": op.evidence_id},
        )
        return OpResult(True, "Улика удалена")

    if isinstance(op, ModifyReputationOp):
        rep = state.reputation.get(op.agent_id)
        if rep is None:
            return OpResult(False, f"Агент {op.agent_id} не найден")
        rep.score += op.delta
        state.event_log.log(
            round=round_num,
            event_type="reputation_modified",
            agent_id=agent_id,
            payload={"target": op.agent_id, "delta": op.delta},
        )
        return OpResult(
            True, f"Репутация {op.agent_id} изменена на {op.delta}"
        )

    if isinstance(op, SendMessageOp):
        msg = Message(
            from_id=op.from_id,
            to_id=op.to_id,
            content=op.content,
            private=op.private,
            round=round_num,
        )
        state.messages.append(msg)
        state.graph.strengthen(op.from_id, op.to_id, delta=0.2)
        state.event_log.log(
            round=round_num,
            event_type="message_sent",
            agent_id=op.from_id,
            payload={
                "to_id": op.to_id,
                "private": op.private,
            },
        )
        return OpResult(True, "Сообщение отправлено")

    if isinstance(op, UpdateGraphOp):
        state.graph.strengthen(op.agent_a, op.agent_b, delta=op.delta)
        state.event_log.log(
            round=round_num,
            event_type="graph_updated",
            agent_id=agent_id,
            payload={
                "agent_a": op.agent_a,
                "agent_b": op.agent_b,
                "delta": op.delta,
            },
        )
        return OpResult(True, "Граф обновлён")

    if isinstance(op, FreezeCaseOp):
        case = state.cases.get(op.case_id)
        if case is None:
            return OpResult(False, f"Дело {op.case_id} не найдено")
        case.stage = "frozen"
        case.closed_at = round_num
        state.event_log.log(
            round=round_num,
            event_type="case_frozen",
            agent_id=agent_id,
            payload={"case_id": op.case_id},
        )
        return OpResult(True, f"Дело {op.case_id} заморожено")

    if isinstance(op, FileComplaintOp):
        complaint = Complaint(
            case_id=op.case_id,
            assessment=op.assessment,
            round=round_num,
        )
        state.complaints.append(complaint)
        state.event_log.log(
            round=round_num,
            event_type="complaint_filed",
            agent_id="anonymous",
            payload={"case_id": op.case_id},
        )
        return OpResult(True, "Жалоба подана")

    if isinstance(op, InitiateTribunalOp):
        tribunal_id = state.new_case_id()
        tribunal = Case(
            id=tribunal_id,
            case_type="investigation",
            title=f"Трибунал по делу {op.case_id}",
            description=f"Обвиняемый: {op.accused_id}",
            owner_id=agent_id or "auditor",
            stage="tribunal",
            params=f"source_case={op.case_id},accused={op.accused_id}",
            created_at=round_num,
        )
        state.cases[tribunal_id] = tribunal
        state.event_log.log(
            round=round_num,
            event_type="tribunal_formed",
            payload={
                "tribunal_id": tribunal_id,
                "source_case": op.case_id,
                "accused": op.accused_id,
            },
        )
        return OpResult(True, f"Трибунал {tribunal_id} сформирован")

    if isinstance(op, CastVoteOp):
        case = state.cases.get(op.case_id)
        if case is None:
            return OpResult(False, f"Дело {op.case_id} не найдено")
        vote = Vote(
            voter_id=op.voter_id,
            case_id=op.case_id,
            verdict=op.verdict,
            reasoning=op.reasoning,
            round=round_num,
        )
        case.votes.append(vote)
        state.event_log.log(
            round=round_num,
            event_type="vote_cast",
            agent_id=op.voter_id,
            payload={"case_id": op.case_id, "verdict": op.verdict},
        )
        return OpResult(True, "Голос засчитан")

    if isinstance(op, MoveAgentOp):
        if state.locations is None:
            return OpResult(False, "Локации не инициализированы")
        location = state.locations.get_location(op.location_id)
        if location is None:
            return OpResult(
                False, f"Локация {op.location_id} не найдена"
            )
        state.locations.move_agent(op.agent_id, op.location_id)
        state.event_log.log(
            round=round_num,
            event_type="agent_moved",
            agent_id=op.agent_id,
            payload={"to": op.location_id},
        )
        return OpResult(True, f"Агент перемещён в {op.location_id}")

    if isinstance(op, CreateNeedOp):
        need = Need(
            case_type=op.case_type,
            description=op.description,
            target_agent_id=op.target_agent_id,
            appear_round=round_num,
            urgency=op.urgency,
        )
        state.active_needs.append(need)
        state.event_log.log(
            round=round_num,
            event_type="need_created",
            payload={
                "case_type": op.case_type,
                "target": op.target_agent_id,
            },
        )
        return OpResult(True, "Потребность создана")

    if isinstance(op, SubmitProposalOp):
        case = state.cases.get(op.case_id)
        if case is None:
            return OpResult(False, f"Дело {op.case_id} не найдено")
        proposal_id = state.new_proposal_id()
        proposal = Proposal(
            id=proposal_id,
            case_id=op.case_id,
            author_id=op.author_id,
            content=op.content,
            submitted_at=round_num,
        )
        case.proposals.append(proposal)
        state.event_log.log(
            round=round_num,
            event_type="proposal_submitted",
            agent_id=op.author_id,
            payload={"case_id": op.case_id, "proposal_id": proposal_id},
        )
        return OpResult(True, f"Предложение {proposal_id} подано")

    return OpResult(False, f"Необработанная операция: {op.op}")
