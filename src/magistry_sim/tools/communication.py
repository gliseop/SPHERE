"""Инструмент общения между агентами."""

from __future__ import annotations

from datetime import timedelta

from ..conversation import ChannelType, ConversationManager
from ..context import build_situation
from ..sim_clock import SimClock
from ..state import Message
from . import get_caller_id, get_runner, get_state

# Фиксированная задержка между репликами треда (секунды).
_THREAD_MSG_DELAY = timedelta(seconds=120)


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
            "content": message,
            "response": response,
        },
    )

    return response


def talk_to_threaded(
    caller_id: str,
    agent_id: str,
    message: str,
    channel: ChannelType,
    private: bool,
    state,
    runner,
    conversation_manager: ConversationManager,
    clock: SimClock,
    max_turns: int = 15,
) -> list[dict]:
    """Провести многорепликовый диалог между агентами.

    Args:
        caller_id: Инициатор разговора.
        agent_id: Собеседник.
        message: Первая реплика.
        channel: Канал связи.
        private: Приватный разговор.
        state: Состояние мира.
        runner: AgentRunner для генерации реплик.
        conversation_manager: Менеджер тредов.
        clock: Часы симуляции (продвигаются между репликами).
        max_turns: Максимальное число реплик.

    Returns:
        Список событий message.
    """
    text = message.strip()
    if agent_id not in state.agents:
        return []
    if agent_id == caller_id:
        return []
    if not text:
        return []

    thread = conversation_manager.create_thread(
        initiator=caller_id,
        recipient=agent_id,
        channel=channel,
    )
    events: list[dict] = []

    def _append_event(sender: str, recipient: str, content: str) -> None:
        ts = clock.iso()
        payload = {
            "thread_id": thread.thread_id,
            "to_id": recipient,
            "channel": channel.value,
            "content": content,
            "private": private,
        }
        event = {
            "event_type": "message",
            "agent_id": sender,
            "payload": payload,
            "timestamp": ts,
        }
        events.append(event)
        state.messages.append(
            Message(
                from_id=sender,
                to_id=recipient,
                content=content,
                private=private,
                round=state.round,
                thread_id=thread.thread_id,
                channel=channel.value,
                timestamp=ts,
            )
        )
        # Сразу персистим событие в журнал
        state.event_log.log(
            event_type="message",
            agent_id=sender,
            payload=payload,
            timestamp=ts,
        )
        # Продвигаем часы на фиксированный интервал между репликами
        clock.advance_to(clock.now + _THREAD_MSG_DELAY)

    conversation_manager.add_message(
        thread_id=thread.thread_id,
        sender=caller_id,
        content=text,
        timestamp=clock.iso(),
    )
    _append_event(caller_id, agent_id, text)

    current_speaker = agent_id
    other_speaker = caller_id
    for _ in range(max_turns - 1):
        if thread.is_closed:
            break

        thread_context = conversation_manager.get_thread_context(
            thread.thread_id
        )
        context = build_situation(current_speaker, state)
        response = runner.run_reply(
            agent_id=current_speaker,
            message=thread_context,
            sender_id=other_speaker,
            context=context,
            state=state,
        )
        response_text = response.strip() if response else ""
        if not response_text:
            break

        conversation_manager.add_message(
            thread_id=thread.thread_id,
            sender=current_speaker,
            content=response_text,
            timestamp=clock.iso(),
        )
        _append_event(current_speaker, other_speaker, response_text)
        current_speaker, other_speaker = (
            other_speaker,
            current_speaker,
        )

    conversation_manager.close_thread(thread.thread_id)
    # R-5: масштабирование дельты по числу сообщений в треде
    msg_count = len(events)
    delta = 0.1 * msg_count  # 0.1 за каждое сообщение вместо фиксированных 0.2
    state.graph.strengthen(caller_id, agent_id, delta=min(delta, 1.0))
    return events
