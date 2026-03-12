"""Отладочный логгер для LLM-вызовов."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class _LLMDebugLogger:
    def __init__(self, path: Path, *, max_chars: int) -> None:
        self._path = path
        self._max_chars = max_chars
        self._lock = threading.Lock()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:  # noqa: WPS515
                f.write(line + "\n")
