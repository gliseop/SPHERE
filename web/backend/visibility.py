"""Политика видимости событий для web API и WebSocket."""

from __future__ import annotations

from typing import Any

from magistry_lc.ids import INTERNAL_AUDIENCE, PUBLIC_AUDIENCE


def event_visible_to_role(event: dict[str, Any], *, role: str) -> bool:
    """Определить, должен ли пользователь видеть событие.

    `admin` видит полный поток. Для `viewer` доступны только общие слои:
    `aud:public`, `aud:internal` и legacy-события без поля `audience`.
    """
    if role == "admin":
        return True

    audience = _event_audience(event)
    if audience is None:
        return True
    return PUBLIC_AUDIENCE in audience or INTERNAL_AUDIENCE in audience


def sanitize_event_for_role(
    event: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any] | None:
    """Подготовить событие к выдаче пользователю с учётом его роли."""
    if not event_visible_to_role(event, role=role):
        return None
    if role == "admin":
        return event

    event_type = str(event.get("event_type") or "")
    if event_type == "llm_call":
        return None

    result = dict(event)
    payload_raw = result.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
    changed = False

    if event_type == "document_created" and "content" in payload:
        content = str(payload.get("content") or "")
        payload["content"] = "<redacted>"
        payload["content_len"] = len(content)
        changed = True

    if event_type == "message_sent" and bool(payload.get("private", True)) and "text" in payload:
        text = str(payload.get("text") or "")
        payload["text"] = "<redacted>"
        payload["text_len"] = len(text)
        changed = True

    if changed:
        result["payload"] = payload
    return result


def _event_audience(event: dict[str, Any]) -> list[str] | None:
    raw = event.get("audience")
    if not isinstance(raw, list):
        return None
    audience = [str(item) for item in raw if isinstance(item, str) and item]
    return audience or None
