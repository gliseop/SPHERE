"""Post-hoc fidelity metrics for simulation realism."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .llm import LLMCaller
from .prompts import render_prompt
from .utils import looks_like_machine_name, looks_like_role_label, normalize_agent_display_name


_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DOTTED_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_SEMANTIC_REALISM_CATEGORIES: tuple[str, ...] = (
    "narrating_leakage",
    "documentary_overclaim",
    "followup_gap",
    "external_pressure_unresolved",
    "institutional_register_failure",
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
    semantic_realism_findings_total: int = 0
    semantic_realism_by_category: dict[str, int] = Field(default_factory=dict)
    semantic_realism_findings: list[dict[str, Any]] = Field(default_factory=list)
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

    for item in items:
        event_type = str(item.get("event_type") or "")
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type == "world_event":
            metrics["world_event_total"] += 1

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


def _semantic_realism_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": list(_SEMANTIC_REALISM_CATEGORIES),
                        },
                        "severity": {"type": "string"},
                        "summary": {"type": "string"},
                        "evidence_refs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "tick": {"type": "integer"},
                                    "event_type": {"type": "string"},
                                    "actor_id": {"type": "string"},
                                },
                                "required": ["tick", "event_type"],
                            },
                        },
                    },
                    "required": ["category", "severity", "summary"],
                },
            }
        },
        "required": ["findings"],
    }


async def augment_fidelity_with_semantic_judge(
    *,
    summary: FidelitySummary,
    llm: LLMCaller,
    events_path: Path,
    scenario_description: str,
    temperature: float = 0.0,
) -> FidelitySummary:
    """Дополнить structural fidelity LLM-based finding'ами правдоподобия."""

    events = _iter_jsonl(events_path)
    if not events:
        return summary
    signal_counters = _semantic_signal_counters(events)
    payload = {
        "scenario_description": scenario_description,
        "events": events[-120:],
        "structural_metrics": summary.model_dump(mode="json"),
        "semantic_signal_counters": signal_counters,
    }
    try:
        resp = await llm.generate_structured(
            role="fidelity",
            name="semantic_realism",
            tick=int(events[-1].get("tick", 0)),
            system=render_prompt("fidelity.semantic_judge.system"),
            user=render_prompt("fidelity.semantic_judge.user", payload_json=json.dumps(payload, ensure_ascii=False)),
            schema=_semantic_realism_schema(),
            temperature=temperature,
        )
    except Exception:
        return summary

    raw_findings = resp.data.get("findings") if isinstance(resp.data, dict) else None
    if not isinstance(raw_findings, list):
        return summary

    findings: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        severity = str(item.get("severity") or "").strip()
        finding_summary = str(item.get("summary") or "").strip()
        if not category or not finding_summary:
            continue
        evidence_refs = []
        raw_refs = item.get("evidence_refs") or []
        if isinstance(raw_refs, list):
            for ref in raw_refs:
                if not isinstance(ref, dict):
                    continue
                evidence_refs.append(
                    {
                        "tick": int(ref.get("tick", 0)),
                        "event_type": str(ref.get("event_type") or "").strip(),
                        "actor_id": str(ref.get("actor_id") or "").strip() or None,
                    }
                )
        finding = {
            "category": category,
            "severity": severity or "medium",
            "summary": finding_summary,
            "evidence_refs": evidence_refs,
        }
        findings.append(finding)
        by_category[category] = by_category.get(category, 0) + 1

    if not findings:
        return summary

    metrics = dict(summary.by_metric)
    leakage_count = sum(1 for item in findings if item["category"] == "narrating_leakage")
    metrics["narrating_leakage_total"] = leakage_count
    metrics["semantic_realism_findings_total"] = len(findings)
    return summary.model_copy(
        update={
            "narrating_leakage_total": leakage_count,
            "semantic_realism_findings_total": len(findings),
            "semantic_realism_by_category": by_category,
            "semantic_realism_findings": findings,
            "by_metric": metrics,
        }
    )


def _semantic_signal_counters(events: list[dict[str, Any]]) -> dict[str, int]:
    counters = {
        "unknown_to_id_count": 0,
        "ambiguous_to_id_count": 0,
        "dependency_missing_count": 0,
        "pending_expired_count": 0,
        "observation_only_approved_count": 0,
    }
    for item in events:
        event_type = str(item.get("event_type") or "")
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "worldgen_artifact_dependency_missing":
            counters["dependency_missing_count"] += 1
        elif event_type == "pending_interaction_expired":
            counters["pending_expired_count"] += 1
        elif event_type == "arbiter_approved" and str(payload.get("reason") or "").strip() == "observation_only":
            counters["observation_only_approved_count"] += 1
        elif event_type == "arbiter_rejected":
            reason = str(payload.get("reason") or "")
            if "unknown to_id" in reason:
                counters["unknown_to_id_count"] += 1
            if "ambiguous_to_id" in reason:
                counters["ambiguous_to_id_count"] += 1
    return counters


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


def _approved_action_is_perform(payload: dict[str, Any]) -> bool:
    action_repr = str(payload.get("action") or "")
    if "ActionType.PERFORM" in action_repr:
        return True
    return "'type': 'perform'" in action_repr or '"type": "perform"' in action_repr
