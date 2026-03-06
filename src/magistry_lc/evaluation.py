"""Post-hoc evaluation against truth.jsonl."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvaluationSummary(BaseModel):
    """Итог сравнения runtime-сигналов и truth-layer."""

    model_config = ConfigDict(extra="forbid")

    truth_total: int = 0
    runtime_flagged_total: int = 0
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    by_violation_type: dict[str, dict[str, int]] = Field(default_factory=dict)


def evaluate_run(*, events_path: Path, truth_path: Path) -> EvaluationSummary:
    """Сравнить audit_flagged из events.jsonl с truth.jsonl."""
    truth_records = [item for item in _iter_jsonl(truth_path) if isinstance(item, dict)]
    event_records = [item for item in _iter_jsonl(events_path) if isinstance(item, dict)]

    truth_keys: set[tuple[int, str, str]] = set()
    signal_keys: set[tuple[int, str, str]] = set()
    by_violation: dict[str, dict[str, int]] = {}

    for item in truth_records:
        tick = int(item.get("tick", 0))
        subject = str(item.get("subject_agent_id") or "")
        violation_type = str(item.get("violation_type") or "")
        if not subject or not violation_type:
            continue
        key = (tick, subject, violation_type)
        truth_keys.add(key)
        stats = by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})
        stats["truth"] += 1

    for item in event_records:
        if str(item.get("event_type") or "") != "audit_flagged":
            continue
        payload = item.get("payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        tick = int(item.get("tick", 0))
        subject = str(payload.get("target_agent_id") or "")
        violation_type = str(payload.get("violation_type") or "")
        if not subject or not violation_type:
            continue
        key = (tick, subject, violation_type)
        signal_keys.add(key)
        stats = by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})
        stats["signals"] += 1

    tp = truth_keys & signal_keys
    fp = signal_keys - truth_keys
    fn = truth_keys - signal_keys

    for _, _, violation_type in tp:
        by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})["tp"] += 1
    for _, _, violation_type in fp:
        by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})["fp"] += 1
    for _, _, violation_type in fn:
        by_violation.setdefault(violation_type, {"truth": 0, "signals": 0, "tp": 0, "fp": 0, "fn": 0})["fn"] += 1

    precision = (len(tp) / len(signal_keys)) if signal_keys else 0.0
    recall = (len(tp) / len(truth_keys)) if truth_keys else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return EvaluationSummary(
        truth_total=len(truth_keys),
        runtime_flagged_total=len(signal_keys),
        true_positive=len(tp),
        false_positive=len(fp),
        false_negative=len(fn),
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        by_violation_type=by_violation,
    )


def save_evaluation(summary: EvaluationSummary, path: Path) -> None:
    """Сохранить evaluation summary в JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
