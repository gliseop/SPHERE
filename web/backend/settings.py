"""Константы, пути и конфигурация из переменных окружения."""

from __future__ import annotations

import os as _os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Корневые директории
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).parent
_PROJECT_ROOT = _BACKEND_DIR.parent.parent

RESULTS_DIR = (_PROJECT_ROOT / "results").resolve()
SCENARIOS_DIR = (_PROJECT_ROOT / "scenarios").resolve()
SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
AGENT_TYPES_DIR = (_PROJECT_ROOT / "data" / "agent_types").resolve()
AGENT_TYPES_DIR.mkdir(parents=True, exist_ok=True)
PERSONALITIES_DIR = (_PROJECT_ROOT / "data" / "personalities").resolve()
PERSONALITIES_DIR.mkdir(parents=True, exist_ok=True)
INTERVIEWS_DIR = (_PROJECT_ROOT / "data" / "interviews").resolve()
INTERVIEWS_DIR.mkdir(parents=True, exist_ok=True)
GOVERNANCE_MODES_DIR = (_PROJECT_ROOT / "data" / "governance_modes").resolve()
GOVERNANCE_MODES_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_DIR = (RESULTS_DIR / "artifacts").resolve()
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIST = _BACKEND_DIR.parent / "frontend" / "dist"

# ---------------------------------------------------------------------------
# Регулярные выражения
# ---------------------------------------------------------------------------
RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
SCENARIO_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
S_NUM_RE = re.compile(r"^S(\d+)$")
G_NUM_RE = re.compile(r"^G(\d+)$")

# ---------------------------------------------------------------------------
# Числовые ограничения
# ---------------------------------------------------------------------------
MAX_SEED = 2_147_483_647
MAX_ROUNDS = 1_000

# ---------------------------------------------------------------------------
# HTTP / middleware
# ---------------------------------------------------------------------------
try:
    MAX_BODY_BYTES = max(
        1,
        int(_os.environ.get("SPHERE_MAX_BODY_BYTES", str(2 * 1024 * 1024))),
    )
except ValueError:
    MAX_BODY_BYTES = 2 * 1024 * 1024

ALLOWED_ORIGIN = _os.environ.get("ALLOWED_ORIGIN", "http://localhost:5173")

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
try:
    WS_MAX_STR_CHARS = max(200, int(_os.environ.get("SPHERE_WS_MAX_STR_CHARS", "2500")))
except ValueError:
    WS_MAX_STR_CHARS = 2500

try:
    WS_PING_INTERVAL_S = max(2.0, float(_os.environ.get("SPHERE_WS_PING_INTERVAL_S", "15")))
except ValueError:
    WS_PING_INTERVAL_S = 15.0

try:
    LIVE_HISTORY_EVENTS = max(0, int(_os.environ.get("SPHERE_LIVE_HISTORY_EVENTS", "60")))
except ValueError:
    LIVE_HISTORY_EVENTS = 60

try:
    LIVE_GRAPH_THROTTLE_S = max(0.05, float(_os.environ.get("SPHERE_LIVE_GRAPH_THROTTLE_S", "0.25")))
except ValueError:
    LIVE_GRAPH_THROTTLE_S = 0.25

try:
    WS_EVENT_BATCH_SIZE = max(1, int(_os.environ.get("SPHERE_WS_EVENT_BATCH_SIZE", "50")))
except ValueError:
    WS_EVENT_BATCH_SIZE = 50

try:
    WS_EVENT_BATCH_INTERVAL_S = max(0.02, float(_os.environ.get("SPHERE_WS_EVENT_BATCH_INTERVAL_S", "0.15")))
except ValueError:
    WS_EVENT_BATCH_INTERVAL_S = 0.15

_drop_raw = (_os.environ.get("SPHERE_WS_DROP_EVENT_TYPES", "idle") or "").strip()
WS_DROP_EVENT_TYPES: set[str] = (
    {t.strip() for t in _drop_raw.split(",") if t.strip()}
    if _drop_raw
    else set()
)

# ---------------------------------------------------------------------------
# Встроенные режимы управления
# ---------------------------------------------------------------------------
BUILTIN_GOVERNANCE_IDS: set[str] = {"G0", "G1", "G2", "G3"}
