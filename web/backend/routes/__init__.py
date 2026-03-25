"""Маршруты API SPHERE — агрегирующий пакет."""

from __future__ import annotations

from .agent_types import router as agent_types_router
from .ai import router as ai_router
from .auth import router as auth_router
from .governance import router as governance_router
from .personalities import router as personalities_router
from .run_control import router as run_control_router
from .runs import router as runs_router
from .scenarios import router as scenarios_router
from .templates import router as templates_router

__all__ = [
    "agent_types_router",
    "ai_router",
    "auth_router",
    "governance_router",
    "personalities_router",
    "run_control_router",
    "runs_router",
    "scenarios_router",
    "templates_router",
]
