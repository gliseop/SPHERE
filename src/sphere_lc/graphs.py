"""LangGraph-графы для SPHERE-LC.

Движок может работать и без LangGraph (обычный asyncio-цикл),
но при наличии зависимостей и включённом флаге `runtime.use_langgraph`
мы используем граф, чтобы:
- сделать шаги исполнения явными (node boundaries),
- упростить будущие чекпоинты/повторы,
- иметь единый контракт состояния.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pathlib import Path
from typing import TypedDict

from .actions import Action
from .events import Event
from .state import WorldState


class WorldGraphState(TypedDict, total=False):
    """Состояние world-graph на один тик."""

    world: WorldState
    events_history: list[Event]
    proposed: dict[str, list[Action]]
    gather_errors: list[Event]
    tick_events: list[Event]


def build_world_graph(
    *,
    gather_actions_node: Callable[[WorldGraphState], Awaitable[dict]],
    apply_actions_node: Callable[[WorldGraphState], Awaitable[dict]],
    debug: bool = False,
    checkpoint_path: str | Path | None = None,
):
    """Собрать LangGraph для одного тика.

    Returns:
        Compiled graph (langgraph object).

    Raises:
        ImportError: если langgraph не установлен.
    """
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "LangGraph не установлен. Установите зависимости: pip install -e \".[lc]\""
        ) from exc

    graph = StateGraph(WorldGraphState)
    graph.add_node("gather_actions", gather_actions_node)
    graph.add_node("apply_actions", apply_actions_node)
    graph.set_entry_point("gather_actions")
    graph.add_edge("gather_actions", "apply_actions")
    graph.add_edge("apply_actions", END)
    checkpointer = None
    if checkpoint_path is not None:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore

            p = Path(checkpoint_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if hasattr(SqliteSaver, "from_conn_string"):
                checkpointer = SqliteSaver.from_conn_string(str(p))
            else:
                checkpointer = SqliteSaver(str(p))
        except Exception:
            checkpointer = None

    try:
        return graph.compile(checkpointer=checkpointer, debug=debug)
    except TypeError:
        # Старые версии LangGraph могут не поддерживать checkpointer в compile().
        return graph.compile(debug=debug)
