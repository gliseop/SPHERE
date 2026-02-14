"""Инструмент общения между агентами."""

from __future__ import annotations

from ..state import Message
from . import get_caller_id, get_runner, get_state


def talk_to(agent_id: str, message: str, private: bool = True) -> str:
    """Написать другому участнику и получить ответ.

    Args:
        agent_id: Идентификатор собеседника.
        message: Текст сообщения.
        private: Если True, содержание не видно аудитору.

    Returns:
        Ответ собеседника или сообщение об ошибке.
    """
    state = get_state()
    caller_id = get_caller_id()
    runner = get_runner()

    if agent_id not in state.agents:
        return f"Ошибка: агент {agent_id} не найден."

    if agent_id == caller_id:
        return "Ошибка: нельзя писать самому себе."

    from ..context import build_situation

    context = build_situation(agent_id, state)
    response = runner.run_reply(
        agent_id=agent_id,
        message=message,
        sender_id=caller_id,
        context=context,
        state=state,
    )

    msg = Message(
        from_id=caller_id,
        to_id=agent_id,
        content=message,
        response=response,
        private=private,
        round=state.round,
    )
    state.messages.append(msg)
    state.graph.strengthen(caller_id, agent_id, delta=0.2)

    state.event_log.log(
        round=state.round,
        event_type="message_sent",
        agent_id=caller_id,
        payload={
            "to_id": agent_id,
            "private": private,
        },
    )

    return response
