"""Инструменты действий: open_case, submit_proposal, add_note,
resolve_case, file_report, cast_vote."""

from __future__ import annotations

from ..cases import (
    CASE_REGISTRY,
    Case,
    Note,
    Proposal,
    Vote,
    apply_transition,
)
from ..state import Complaint
from . import get_caller_id, get_state


def open_case(
    case_type: str,
    title: str,
    description: str,
    params: str = "",
) -> str:
    """Открыть новое дело.

    Args:
        case_type: Тип дела (procurement, hiring, budget).
        title: Краткое название.
        description: Описание и требования.
        params: Дополнительные параметры.

    Returns:
        Идентификатор дела или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()

    schema = CASE_REGISTRY.get(case_type)
    if schema is None:
        return f"Ошибка: неизвестный тип дела «{case_type}»."

    if not state.has_capability(caller_id, "open_case", case_type):
        return (
            f"Ошибка: у вас нет полномочий открывать дела "
            f"типа «{case_type}»."
        )

    case_id = state.new_case_id()
    deadline = state.round + 3 if case_type == "procurement" else None

    case = Case(
        id=case_id,
        case_type=case_type,
        title=title,
        description=description,
        owner_id=caller_id,
        stage=schema.initial_stage,
        params=params,
        created_at=state.round,
        deadline_round=deadline,
    )

    # Авто-переходы
    auto_target = schema.auto_transitions.get(case.stage)
    if auto_target:
        apply_transition(case, auto_target)

    state.cases[case_id] = case

    state.event_log.log(
        round=state.round,
        event_type="case_opened",
        agent_id=caller_id,
        payload={
            "case_id": case_id,
            "case_type": case_type,
            "title": title,
        },
    )

    # Удалить удовлетворённую потребность
    state.active_needs = [
        n
        for n in state.active_needs
        if not (
            n.target_agent_id == caller_id
            and n.case_type == case_type
        )
    ]

    return f"Дело {case_id} открыто (тип: {case_type})."


def submit_proposal(case_id: str, content: str) -> str:
    """Подать предложение по открытому делу.

    Args:
        case_id: Идентификатор дела.
        content: Содержание предложения.

    Returns:
        Подтверждение или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()

    case = state.cases.get(case_id)
    if case is None:
        return f"Ошибка: дело {case_id} не найдено."

    schema = CASE_REGISTRY.get(case.case_type)
    if schema is None:
        return "Ошибка: неизвестный тип дела."

    if case.stage in schema.terminal_stages:
        return f"Ошибка: дело {case_id} уже закрыто."

    if not state.has_capability(
        caller_id, "submit_proposal", case.case_type
    ):
        return "Ошибка: у вас нет полномочий подавать предложения."

    if caller_id == case.owner_id:
        return "Ошибка: владелец дела не может подавать предложения."

    already = any(p.author_id == caller_id for p in case.proposals)
    if already:
        return "Ошибка: вы уже подали предложение по этому делу."

    proposal_id = state.new_proposal_id()
    proposal = Proposal(
        id=proposal_id,
        case_id=case_id,
        author_id=caller_id,
        content=content,
        submitted_at=state.round,
    )
    case.proposals.append(proposal)

    state.event_log.log(
        round=state.round,
        event_type="proposal_submitted",
        agent_id=caller_id,
        payload={
            "case_id": case_id,
            "proposal_id": proposal_id,
        },
    )

    return f"Предложение {proposal_id} подано по делу {case_id}."


def add_note(case_id: str, content: str) -> str:
    """Оставить публичную запись в деле.

    Args:
        case_id: Идентификатор дела.
        content: Текст записи.

    Returns:
        Подтверждение или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()

    case = state.cases.get(case_id)
    if case is None:
        return f"Ошибка: дело {case_id} не найдено."

    involved = (
        case.owner_id == caller_id
        or any(p.author_id == caller_id for p in case.proposals)
        or any(n.author_id == caller_id for n in case.notes)
        or any(v.voter_id == caller_id for v in case.votes)
        or state.has_capability(caller_id, "audit", "")
    )
    if not involved:
        return "Ошибка: вы не связаны с этим делом."

    note_id = state.new_note_id()
    note = Note(
        id=note_id,
        case_id=case_id,
        author_id=caller_id,
        content=content,
        created_at=state.round,
    )
    case.notes.append(note)

    state.event_log.log(
        round=state.round,
        event_type="note_added",
        agent_id=caller_id,
        payload={
            "case_id": case_id,
            "note_id": note_id,
        },
    )

    return f"Запись {note_id} добавлена в дело {case_id}."


def resolve_case(
    case_id: str, decision: str, justification: str
) -> str:
    """Принять решение по делу.

    Args:
        case_id: Идентификатор дела.
        decision: Решение.
        justification: Обоснование.

    Returns:
        Подтверждение или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()

    case = state.cases.get(case_id)
    if case is None:
        return f"Ошибка: дело {case_id} не найдено."

    if case.owner_id != caller_id:
        return "Ошибка: только владелец дела может принять решение."

    schema = CASE_REGISTRY.get(case.case_type)
    if schema is None:
        return "Ошибка: неизвестный тип дела."

    if not state.has_capability(
        caller_id, "resolve_case", case.case_type
    ):
        return "Ошибка: у вас нет полномочий принимать решения."

    act = schema.action_transitions.get(case.stage)
    if not act or act[1] != "resolve_case":
        return (
            f"Ошибка: дело {case_id} не в стадии, "
            f"допускающей решение (текущая: {case.stage})."
        )

    target_stage = act[0]
    apply_transition(case, target_stage)
    case.decision = decision
    case.justification = justification
    is_terminal = target_stage in schema.terminal_stages
    case.closed_at = state.round if is_terminal else None

    state.event_log.log(
        round=state.round,
        event_type="case_resolved",
        agent_id=caller_id,
        payload={
            "case_id": case_id,
            "decision": decision,
        },
    )

    if is_terminal:
        return f"Дело {case_id} закрыто. Решение: {decision}"
    return (
        f"Дело {case_id} переведено в стадию {target_stage}. "
        f"Решение: {decision}"
    )


def file_report(
    case_id: str, assessment: str, recommendation: str
) -> str:
    """Подать отчёт или жалобу.

    Для аудитора — формальный отчёт. Для остальных — анонимная жалоба.

    Args:
        case_id: Идентификатор дела.
        assessment: Описание подозрений или анализ.
        recommendation: Рекомендация (observation/frozen/tribunal).

    Returns:
        Подтверждение.
    """
    state = get_state()
    caller_id = get_caller_id()

    case = state.cases.get(case_id)
    if case is None:
        return f"Ошибка: дело {case_id} не найдено."

    is_auditor = state.has_capability(caller_id, "audit", "")

    if is_auditor:
        state.event_log.log(
            round=state.round,
            event_type="report_filed",
            agent_id=caller_id,
            payload={
                "case_id": case_id,
                "assessment": assessment,
                "recommendation": recommendation,
            },
        )
        return (
            f"Отчёт по делу {case_id} зарегистрирован. "
            f"Рекомендация: {recommendation}."
        )
    else:
        complaint = Complaint(
            case_id=case_id,
            assessment=assessment,
            round=state.round,
        )
        state.complaints.append(complaint)

        state.event_log.log(
            round=state.round,
            event_type="complaint_filed",
            agent_id="anonymous",
            payload={
                "case_id": case_id,
                "assessment": assessment,
            },
        )
        return (
            f"Жалоба по делу {case_id} зарегистрирована "
            f"анонимно."
        )


def move_to(location_id: str) -> str:
    """Переместиться в указанную локацию.

    Args:
        location_id: Идентификатор целевой локации.

    Returns:
        Подтверждение или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()

    if state.locations is None:
        return "Ошибка: система локаций не инициализирована."

    location = state.locations.get_location(location_id)
    if location is None:
        return f"Ошибка: локация «{location_id}» не найдена."

    old_location = state.locations.get_agent_location(caller_id)
    state.locations.move_agent(caller_id, location_id)

    state.event_log.log(
        round=state.round,
        event_type="agent_moved",
        agent_id=caller_id,
        payload={
            "from": old_location or "",
            "to": location_id,
        },
    )

    return f"Вы переместились в «{location.name}»."


def cast_vote(case_id: str, verdict: str, reasoning: str) -> str:
    """Проголосовать по делу трибунала.

    Args:
        case_id: Идентификатор дела трибунала.
        verdict: «виновен» или «невиновен».
        reasoning: Обоснование.

    Returns:
        Подтверждение или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()

    case = state.cases.get(case_id)
    if case is None:
        return f"Ошибка: дело {case_id} не найдено."

    if not state.has_capability(caller_id, "vote", ""):
        return "Ошибка: у вас нет полномочий голосовать."

    if (
        case.case_type != "investigation"
        or case.stage != "tribunal"
    ):
        return (
            "Ошибка: голосование доступно только для дела "
            "типа investigation в стадии tribunal."
        )

    already = any(v.voter_id == caller_id for v in case.votes)
    if already:
        return "Ошибка: вы уже голосовали по этому делу."

    vote = Vote(
        voter_id=caller_id,
        case_id=case_id,
        verdict=verdict,
        reasoning=reasoning,
        round=state.round,
    )
    case.votes.append(vote)

    state.event_log.log(
        round=state.round,
        event_type="vote_cast",
        agent_id=caller_id,
        payload={
            "case_id": case_id,
            "verdict": verdict,
        },
    )

    return f"Голос по делу {case_id} засчитан: {verdict}."
