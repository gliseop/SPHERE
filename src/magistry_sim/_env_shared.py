"""Общие определения, используемые синхронной и асинхронной средами."""

from __future__ import annotations

from typing import Any

from .state import WorldState
from .tools.actions import (
    add_note,
    cast_vote,
    file_report,
    move_to,
    open_case,
    resolve_case,
    submit_proposal,
)
from .tools.communication import talk_to

TOOL_DISPATCH = {
    "open_case": open_case,
    "submit_proposal": submit_proposal,
    "add_note": add_note,
    "resolve_case": resolve_case,
    "file_report": file_report,
    "cast_vote": cast_vote,
    "talk_to": talk_to,
    "move_to": move_to,
}

_OBSERVABLE_EVENT_TYPES: set[str] = {
    "case_opened",
    "case_resolved",
    "proposal_submitted",
    "note_added",
    "report_filed",
    "message_sent",
    "message",
    "world_event",
    "document_created",
    "position_promoted",
    "tribunal_verdict",
}


def format_observation(
    event: dict[str, Any],
    state: WorldState,
    *,
    observer_id: str,
) -> str:
    """Сформировать человекочитаемый текст наблюдения.

    Использует allowlist типов событий и резолвит agent_id в имена.
    Не включает чувствительные поля (например, содержание сообщений)
    в наблюдения по умолчанию.

    Args:
        event: Словарь события с ключами event_type, agent_id, payload.
        state: Текущее состояние мира.
        observer_id: Идентификатор агента-наблюдателя.

    Returns:
        Человекочитаемый текст наблюдения или пустая строка,
        если тип события не поддерживается.
    """

    def _agent_ref(aid: str) -> str:
        if not aid:
            return "кто-то"
        profile = state.agents.get(aid)
        if profile is None or not profile.name:
            return aid
        return f"{profile.name} ({aid})"

    event_type = str(event.get("event_type", "") or "")
    actor_id = str(event.get("agent_id", "") or "")
    payload = event.get("payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}

    actor = _agent_ref(actor_id)

    if event_type == "case_opened":
        case_id = str(payload.get("case_id", "") or "")
        title = str(payload.get("title", "") or "").strip()
        if case_id and title:
            return f"{actor} открыл дело {case_id}: {title}"
        if case_id:
            return f"{actor} открыл дело {case_id}"
        return f"{actor} открыл новое дело"

    if event_type == "case_resolved":
        case_id = str(payload.get("case_id", "") or "")
        decision = str(payload.get("decision", "") or "").strip()
        if decision and len(decision) > 140:
            decision = decision[:140].rstrip() + "…"
        if case_id and decision:
            return f"{actor} закрыл дело {case_id} (решение: {decision})"
        if case_id:
            return f"{actor} закрыл дело {case_id}"
        return f"{actor} закрыл дело"

    if event_type == "proposal_submitted":
        case_id = str(payload.get("case_id", "") or "")
        if case_id:
            return f"{actor} подал предложение по делу {case_id}"
        return f"{actor} подал предложение"

    if event_type == "note_added":
        case_id = str(payload.get("case_id", "") or "")
        if case_id:
            return f"{actor} добавил запись в дело {case_id}"
        return f"{actor} добавил запись"

    if event_type == "report_filed":
        case_id = str(payload.get("case_id", "") or "")
        rec = str(payload.get("recommendation", "") or "").strip()
        suffix = f" (рекомендация: {rec})" if rec else ""
        if case_id:
            return f"{actor} подал отчёт по делу {case_id}{suffix}"
        return f"{actor} подал отчёт{suffix}"

    if event_type in {"message_sent", "message"}:
        to_id = str(payload.get("to_id", "") or "")
        is_private = bool(payload.get("private", False))
        if to_id == observer_id:
            privacy = "приватное " if is_private else ""
            return f"{actor} отправил вам {privacy}сообщение"
        if to_id:
            return f"{actor} отправил сообщение {_agent_ref(to_id)}"
        return f"{actor} отправил сообщение"

    if event_type == "world_event":
        narrative = str(payload.get("narrative", "") or "").strip()
        if narrative:
            return f"Мировое событие: {narrative}"
        return "Произошло мировое событие"

    if event_type == "document_created":
        title = str(payload.get("title", "") or "").strip()
        doc_type = str(payload.get("doc_type", "") or "").strip()
        doc_id = str(payload.get("doc_id", "") or "").strip()
        label = "документ"
        if title:
            label = f"документ «{title}»"
        elif doc_type:
            label = f"документ ({doc_type})"
        suffix = f" ({doc_id})" if doc_id else ""
        return f"{actor} создал {label}{suffix}"

    if event_type == "position_promoted":
        title = str(payload.get("title", "") or "").strip()
        if title:
            return f"{actor} повышен до «{title}»"
        return f"{actor} получил повышение"

    if event_type == "tribunal_verdict":
        case_id = str(payload.get("case_id", "") or "")
        verdict = str(payload.get("verdict", "") or "").strip()
        if case_id and verdict:
            return f"Трибунал вынес вердикт по делу {case_id}: {verdict}"
        if case_id:
            return f"Трибунал вынес вердикт по делу {case_id}"
        return "Трибунал вынес вердикт"

    return ""
