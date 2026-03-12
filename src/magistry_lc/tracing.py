"""JSONL трассировка LLM-вызовов (trace.jsonl).

Трасса отделена от EventLog, чтобы:
- world-gen не видел промпты/ответы;
- события не раздувались служебными данными.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TraceSpan(BaseModel):
    """Один span LLM-вызова."""

    model_config = ConfigDict(extra="forbid")

    role: str  # agent|arbiter|worldgen|oracle|composer|summarizer
    name: str
    tick: int
    system: str = ""
    user: str = ""
    response: str = ""
    model: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error: dict[str, Any] | None = None


@dataclass(slots=True)
class TraceLog:
    """JSONL лог LLM spans."""

    path: Path
    max_chars: int = 0

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _truncate(self, text: str) -> str:
        if self.max_chars and len(text) > self.max_chars:
            return text[: self.max_chars] + "…(truncated)"
        return text

    def append(self, span: TraceSpan) -> None:
        """Добавить span в лог."""
        record = span.model_dump(mode="json")
        if self.max_chars:
            record["system"] = self._truncate(record.get("system") or "")
            record["user"] = self._truncate(record.get("user") or "")
            record["response"] = self._truncate(record.get("response") or "")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

