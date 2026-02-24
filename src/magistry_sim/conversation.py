"""Управление тредами разговоров между агентами."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChannelType(str, Enum):
    """Канал связи между агентами."""

    TELEGRAM = "telegram"
    PHONE = "phone"
    FACE_TO_FACE = "face_to_face"
    EMAIL = "email"
    OFFICIAL_DOC = "official_doc"


@dataclass
class ThreadMessage:
    """Одна реплика в треде."""

    sender: str
    content: str
    timestamp: str = ""


@dataclass
class Thread:
    """Тред разговора между двумя агентами."""

    thread_id: str
    initiator: str
    recipient: str
    channel: ChannelType
    max_messages: int = 15
    messages: list[ThreadMessage] = field(default_factory=list)
    _closed: bool = field(default=False, repr=False)

    @property
    def is_closed(self) -> bool:
        """Тред завершён."""
        return self._closed or len(self.messages) >= self.max_messages

    def close(self) -> None:
        """Принудительно завершить тред."""
        self._closed = True


class ConversationManager:
    """Менеджер тредов разговоров."""

    def __init__(self, max_messages: int = 15) -> None:
        self._threads: dict[str, Thread] = {}
        self._counter = 0
        self._max_messages = max_messages

    def create_thread(
        self,
        initiator: str,
        recipient: str,
        channel: ChannelType,
    ) -> Thread:
        """Создать новый тред."""
        self._counter += 1
        thread_id = f"T-{self._counter:04d}"
        thread = Thread(
            thread_id=thread_id,
            initiator=initiator,
            recipient=recipient,
            channel=channel,
            max_messages=self._max_messages,
        )
        self._threads[thread_id] = thread
        return thread

    def add_message(
        self,
        thread_id: str,
        sender: str,
        content: str,
        timestamp: str = "",
    ) -> ThreadMessage:
        """Добавить реплику в тред."""
        thread = self._threads.get(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id} not found")
        if thread.is_closed:
            raise ValueError(f"Thread {thread_id} is closed")
        msg = ThreadMessage(
            sender=sender,
            content=content,
            timestamp=timestamp,
        )
        thread.messages.append(msg)
        return msg

    def close_thread(self, thread_id: str) -> None:
        """Закрыть тред."""
        thread = self._threads.get(thread_id)
        if thread is not None:
            thread.close()

    def get_thread(self, thread_id: str) -> Thread | None:
        """Получить тред по ID."""
        return self._threads.get(thread_id)

    def get_thread_context(self, thread_id: str) -> str:
        """Получить текстовый контекст треда для LLM-промпта."""
        thread = self._threads.get(thread_id)
        if thread is None:
            return ""
        return "\n".join(
            f"{msg.sender}: {msg.content}"
            for msg in thread.messages
        )
