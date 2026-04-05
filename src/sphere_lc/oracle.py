"""Oracle: пост-фактум анализ событий чанками."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .llm import LLMCaller
from .prompts import render_prompt


class Violation(BaseModel):
    """Обнаруженное нарушение."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    actor_id: str | None = None
    violation_type: str
    severity: int = 1
    rationale: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class FreeformTruthRecord(BaseModel):
    """Свободная truth-запись по схеме."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    subject_agent_id: str | None = None
    target_agent_id: str | None = None
    violation_type_freeform: str
    summary: str
    mechanism: str = ""
    beneficiary: str | None = None
    risk_tags: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "medium"
    confidence: float = 0.0
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


def _oracle_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tick": {"type": "integer"},
                "actor_id": {"type": ["string", "null"]},
                "violation_type": {"type": "string"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                "rationale": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["tick", "violation_type"],
        },
    }


def _freeform_truth_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tick": {"type": "integer"},
                "subject_agent_id": {"type": ["string", "null"]},
                "target_agent_id": {"type": ["string", "null"]},
                "violation_type_freeform": {"type": "string"},
                "summary": {"type": "string"},
                "mechanism": {"type": "string"},
                "beneficiary": {"type": ["string", "null"]},
                "risk_tags": {"type": "array", "items": {"type": "string"}},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "evidence_refs": {"type": "array", "items": {"type": "object"}},
                "notes": {"type": "string"},
            },
            "required": ["tick", "violation_type_freeform", "summary"],
        },
    }


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


@dataclass(slots=True)
class ViolationOracle:
    """Инкрементальный оракул по EventLog."""

    llm: LLMCaller
    window_ticks: int = 5
    temperature: float = 0.0

    async def analyze_events(self, *, events_path: Path) -> list[Violation]:
        """Проанализировать лог событий окнами."""
        by_tick: dict[int, list[dict[str, Any]]] = {}
        for ev in _iter_jsonl(events_path):
            tick = int(ev.get("tick", 0))
            et = str(ev.get("event_type") or "")
            # Фильтр: избегаем шума.
            if et in ("arbiter_approved",):
                continue
            by_tick.setdefault(tick, []).append({"event_type": et, "actor_id": ev.get("actor_id"), "payload": ev.get("payload", {})})

        if not by_tick:
            return []

        ticks_sorted = sorted(by_tick.keys())
        violations: list[Violation] = []
        schema = _oracle_schema()

        for i in range(0, len(ticks_sorted), self.window_ticks):
            window = ticks_sorted[i : i + self.window_ticks]
            window_events = []
            for t in window:
                window_events.append({"tick": t, "events": by_tick.get(t, [])})

            system = render_prompt("oracle.violations.system")
            user = render_prompt(
                "oracle.violations.user",
                payload_json=json.dumps({"window_ticks": window, "events": window_events}, ensure_ascii=False),
            )

            resp = await self.llm.generate_structured(
                role="oracle",
                name="violation_oracle",
                tick=max(window),
                system=system,
                user=user,
                schema=schema,
                temperature=self.temperature,
            )
            if not isinstance(resp.data, list):
                continue
            for item in resp.data:
                try:
                    violations.append(Violation.model_validate(item))
                except Exception:
                    continue
        # Дедуп по (tick, actor_id, violation_type)
        uniq: dict[tuple[int, str | None, str], Violation] = {}
        for v in violations:
            key = (v.tick, v.actor_id, v.violation_type)
            uniq.setdefault(key, v)
        return list(uniq.values())


@dataclass(slots=True)
class FreeformTruthRecorder:
    """LLM-based post-hoc recorder свободных truth-записей."""

    llm: LLMCaller
    window_ticks: int = 5
    temperature: float = 0.0

    async def analyze_events(
        self,
        *,
        events_path: Path,
        scenario_description: str = "",
    ) -> list[FreeformTruthRecord]:
        """Проанализировать лог событий окнами и записать нарушения в свободной форме."""
        by_tick: dict[int, list[dict[str, Any]]] = {}
        for ev in _iter_jsonl(events_path):
            tick = int(ev.get("tick", 0))
            et = str(ev.get("event_type") or "")
            if et in ("reputation_snapshot",):
                continue
            by_tick.setdefault(tick, []).append(
                {
                    "event_type": et,
                    "actor_id": ev.get("actor_id"),
                    "payload": ev.get("payload", {}),
                }
            )

        if not by_tick:
            return []

        ticks_sorted = sorted(by_tick.keys())
        records: list[FreeformTruthRecord] = []
        schema = _freeform_truth_schema()

        for i in range(0, len(ticks_sorted), self.window_ticks):
            window = ticks_sorted[i : i + self.window_ticks]
            window_events = []
            for t in window:
                window_events.append({"tick": t, "events": by_tick.get(t, [])})

            system = render_prompt("oracle.truth_recorder.system")
            user = render_prompt(
                "oracle.truth_recorder.user",
                payload_json=json.dumps(
                    {
                        "scenario_description": scenario_description,
                        "window_ticks": window,
                        "events": window_events,
                    },
                    ensure_ascii=False,
                ),
            )

            resp = await self.llm.generate_structured(
                role="oracle",
                name="freeform_truth_recorder",
                tick=max(window),
                system=system,
                user=user,
                schema=schema,
                temperature=self.temperature,
            )
            if not isinstance(resp.data, list):
                continue
            for item in resp.data:
                try:
                    records.append(FreeformTruthRecord.model_validate(item))
                except Exception:
                    continue

        uniq: dict[tuple[int, str | None, str, str], FreeformTruthRecord] = {}
        for record in records:
            key = (
                int(record.tick),
                record.subject_agent_id,
                (record.violation_type_freeform or "").strip().casefold(),
                (record.summary or "").strip().casefold(),
            )
            uniq.setdefault(key, record)
        return list(uniq.values())


def save_violations(violations: list[Violation], path: Path) -> None:
    """Сохранить нарушения в JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [v.model_dump(mode="json") for v in violations]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_freeform_truth(records: list[FreeformTruthRecord], path: Path) -> None:
    """Сохранить freeform truth в JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")

