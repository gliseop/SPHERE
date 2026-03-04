"""Oracle: пост-фактум анализ событий чанками."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .llm import LLMCaller


class Violation(BaseModel):
    """Обнаруженное нарушение."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    actor_id: str | None = None
    violation_type: str
    severity: int = 1
    rationale: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)


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

            system = (
                "Ты — оракул нарушений в симуляции организационных процессов.\n"
                "На вход: события нескольких тиков.\n"
                "Найди потенциальные нарушения (например: кумовство, злоупотребление властью, конфликт интересов).\n"
                "Верни JSON-массив Violation по схеме.\n"
            )
            user = json.dumps({"window_ticks": window, "events": window_events}, ensure_ascii=False)

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


def save_violations(violations: list[Violation], path: Path) -> None:
    """Сохранить нарушения в JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [v.model_dump(mode="json") for v in violations]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

