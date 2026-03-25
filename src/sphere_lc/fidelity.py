"""Post-hoc fidelity metrics for simulation realism."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .utils import looks_like_machine_name, looks_like_role_label, normalize_agent_display_name


_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DOTTED_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_BUREAUCRATIC_TYPES = {"follow_up", "reminder", "control", "verification"}
_LEAK_ACTION_HINTS = (
    "отправил",
    "отправила",
    "передал",
    "передала",
    "сообщил",
    "сообщила",
    "подписал",
    "подписала",
    "загрузил",
    "загрузила",
    "предоставил",
    "предоставила",
    "подтвердил",
    "подтвердила",
    "напомнил",
    "напомнила",
    "одобрил",
    "одобрила",
    "завершил",
    "завершила",
    "закрыл",
    "закрыла",
)
_STRONG_STATE_LEAK_HINTS = (
    "дело закрыто",
    "завершив работу",
    "завершил работу",
    "итоговый отчёт подписан",
    "подписал итоговый отчёт",
    "подписала итоговый отчёт",
)


class FidelitySummary(BaseModel):
    """Итоговые метрики правдоподобия и структурной дисциплины."""

    model_config = ConfigDict(extra="forbid")

    temporal_violations_total: int = 0
    identity_machine_name_total: int = 0
    identity_role_alias_total: int = 0
    phantom_rejection_total: int = 0
    bureaucratic_loop_total: int = 0
    world_event_total: int = 0
    narrating_leakage_total: int = 0
    perform_approved_total: int = 0
    reputation_event_total: int = 0
    by_metric: dict[str, int] = Field(default_factory=dict)


def evaluate_fidelity(
    *,
    events_path: Path,
    start_date: date | None,
    tick_duration_days: int,
    temporal_past_slack_days: int,
    temporal_future_horizon_days: int,
) -> FidelitySummary:
    """Посчитать sidecar-метрики правдоподобия по events.jsonl."""
    items = _iter_jsonl(events_path)
    metrics = {
        "temporal_violations_total": 0,
        "identity_machine_name_total": 0,
        "identity_role_alias_total": 0,
        "phantom_rejection_total": 0,
        "bureaucratic_loop_total": 0,
        "world_event_total": 0,
        "narrating_leakage_total": 0,
        "perform_approved_total": 0,
        "reputation_event_total": 0,
    }

    agent_terms: set[str] = set()
    work_terms: set[str] = set()
    for item in items:
        event_type = str(item.get("event_type") or "")
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if event_type == "entity_created" and str(payload.get("kind") or "") == "agent":
            meta = payload.get("meta") or {}
            if isinstance(meta, dict):
                raw_name = normalize_agent_display_name(str(meta.get("name") or ""))
                if raw_name:
                    agent_terms.add(raw_name.casefold())
                    for part in re.split(r"[\s.()\"«»,-]+", raw_name.casefold()):
                        if len(part) >= 4:
                            agent_terms.add(part)
        if event_type == "work_item_created":
            work_id = str(payload.get("work_id") or "").strip()
            if work_id:
                work_terms.add(work_id.casefold())
                if ":" in work_id:
                    work_terms.add(work_id.split(":", 1)[1].casefold())

    for item in items:
        event_type = str(item.get("event_type") or "")
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type == "world_event":
            metrics["world_event_total"] += 1
            if _world_event_has_narrating_leakage(
                description=str(payload.get("description") or ""),
                agent_terms=agent_terms,
                work_terms=work_terms,
            ):
                metrics["narrating_leakage_total"] += 1

        if event_type == "arbiter_approved" and _approved_action_is_perform(payload):
            metrics["perform_approved_total"] += 1

        if event_type == "reputation_modified":
            metrics["reputation_event_total"] += 1

        if event_type == "arbiter_rejected":
            reason = str(payload.get("reason") or "")
            if reason.startswith("unknown ") or reason.startswith("unknown_"):
                metrics["phantom_rejection_total"] += 1
            if reason.startswith("duplicate_open_work_item:"):
                metrics["bureaucratic_loop_total"] += 1

        if event_type == "work_item_created":
            work_type = str(payload.get("work_type") or "").strip().casefold()
            if work_type in _BUREAUCRATIC_TYPES:
                metrics["bureaucratic_loop_total"] += 1

        if event_type == "entity_created" and str(payload.get("kind") or "") == "agent":
            meta = payload.get("meta") or {}
            if isinstance(meta, dict):
                raw_name = str(meta.get("name") or "")
                display_name = normalize_agent_display_name(raw_name)
                if looks_like_machine_name(raw_name):
                    metrics["identity_machine_name_total"] += 1
                if display_name and looks_like_role_label(display_name):
                    metrics["identity_role_alias_total"] += 1

        if start_date is not None and _event_has_temporal_violation(
            item=item,
            start_date=start_date,
            tick_duration_days=tick_duration_days,
            past_slack_days=temporal_past_slack_days,
            future_horizon_days=temporal_future_horizon_days,
        ):
            metrics["temporal_violations_total"] += 1

    return FidelitySummary(**metrics, by_metric=dict(metrics))


def save_fidelity(summary: FidelitySummary, path: Path) -> None:
    """Сохранить fidelity summary в JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _event_has_temporal_violation(
    *,
    item: dict[str, Any],
    start_date: date,
    tick_duration_days: int,
    past_slack_days: int,
    future_horizon_days: int,
) -> bool:
    tick = int(item.get("tick", 0))
    current_date = start_date + timedelta(days=tick * int(tick_duration_days))
    low = current_date - timedelta(days=int(past_slack_days))
    high = current_date + timedelta(days=int(future_horizon_days))

    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        return False

    texts = [
        str(payload.get("text") or ""),
        str(payload.get("description") or ""),
        str(payload.get("title") or ""),
        str(payload.get("reason") or ""),
        str(payload.get("new_title") or ""),
    ]
    for text in texts:
        for value in _extract_dates(text):
            if value < low or value > high:
                return True
    return False


def _extract_dates(text: str) -> list[date]:
    out: list[date] = []
    for match in _ISO_DATE_RE.findall(text or ""):
        try:
            out.append(date.fromisoformat(match))
        except ValueError:
            continue
    for match in _DOTTED_DATE_RE.findall(text or ""):
        try:
            day, month, year = match.split(".")
            out.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return out


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def _world_event_has_narrating_leakage(
    *,
    description: str,
    agent_terms: set[str],
    work_terms: set[str],
) -> bool:
    normalized = " ".join((description or "").casefold().split())
    if not normalized:
        return False
    has_action = any(token in normalized for token in _LEAK_ACTION_HINTS)
    mentions_agent = any(term and term in normalized for term in agent_terms)
    mentions_work = any(term and term in normalized for term in work_terms)
    closes_state = any(token in normalized for token in _STRONG_STATE_LEAK_HINTS)
    return bool((has_action and mentions_agent) or (closes_state and mentions_work))


def _approved_action_is_perform(payload: dict[str, Any]) -> bool:
    action_repr = str(payload.get("action") or "")
    if "ActionType.PERFORM" in action_repr:
        return True
    return "'type': 'perform'" in action_repr or '"type": "perform"' in action_repr
