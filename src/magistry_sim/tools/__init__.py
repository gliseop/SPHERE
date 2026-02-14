"""Инструменты агентов. Доступ к состоянию через contextvars."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agents import AgentRunner
    from ..state import WorldState


current_state: ContextVar["WorldState"] = ContextVar("current_state")
current_agent_id: ContextVar[str] = ContextVar("current_agent_id")
current_runner: ContextVar["AgentRunner"] = ContextVar("current_runner")


def get_state() -> "WorldState":
    """Получить текущее состояние мира.

    Returns:
        Состояние мира из контекста.

    Raises:
        LookupError: Если контекст не установлен.
    """
    return current_state.get()


def get_caller_id() -> str:
    """Получить идентификатор текущего агента.

    Returns:
        Идентификатор агента.

    Raises:
        LookupError: Если контекст не установлен.
    """
    return current_agent_id.get()


def get_runner() -> "AgentRunner":
    """Получить текущий AgentRunner.

    Returns:
        Runner из контекста.

    Raises:
        LookupError: Если контекст не установлен.
    """
    return current_runner.get()
