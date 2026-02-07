"""
Память агентов по архитектуре Codex.

Основные концепции:
- Транскрипт: упорядоченный список элементов — каноническая «память» агента
- Компакция: LLM-саммари когда история слишком большая
- Нормализация: перед запросом к модели история приводится в корректную форму
- Сохранение/восстановление: JSON-lines файл (rollout)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider


# ─────────────────────────────────────────────────────────────────────────────
# Memory Item Types (аналог ResponseItem в Codex)
# ─────────────────────────────────────────────────────────────────────────────


class MemoryItemType(str, Enum):
    """Типы элементов памяти."""

    # Действия агента
    TENDER_PARTICIPATION = "tender_participation"  # Участие в тендере
    TENDER_WIN = "tender_win"  # Победа в тендере
    TENDER_LOSS = "tender_loss"  # Проигрыш в тендере

    # Коммуникация (shadow layer)
    MESSAGE_SENT = "message_sent"  # Отправил сообщение
    MESSAGE_RECEIVED = "message_received"  # Получил сообщение
    NEGOTIATION_START = "negotiation_start"  # Начало переговоров
    NEGOTIATION_END = "negotiation_end"  # Завершение переговоров
    DEAL_REACHED = "deal_reached"  # Сделка достигнута
    DEAL_REJECTED = "deal_rejected"  # Сделка отклонена

    # Governance события
    AUDIT_FLAG = "audit_flag"  # Помечен аудитором
    AUDIT_CLEAR = "audit_clear"  # Аудит пройден
    TRIBUNAL_CALLED = "tribunal_called"  # Вызван на трибунал
    TRIBUNAL_VERDICT = "tribunal_verdict"  # Вердикт трибунала
    FROZEN = "frozen"  # Заморожен
    UNFROZEN = "unfrozen"  # Разморожен

    # Репутация
    REPUTATION_CHANGE = "reputation_change"  # Изменение репутации

    # Наблюдения (что агент видел)
    OBSERVED_TENDER = "observed_tender"  # Наблюдал тендер
    OBSERVED_TRIBUNAL = "observed_tribunal"  # Наблюдал трибунал

    # Системные
    COMPACTION = "compaction"  # Компакция (саммари истории)


class MemoryItem(BaseModel):
    """
    Элемент памяти агента (аналог ResponseItem в Codex).

    Каждый элемент содержит:
    - tick: когда произошло
    - item_type: тип события
    - payload: структурированные данные события
    - text: человекочитаемое описание (для LLM-контекста)
    """

    tick: int
    item_type: MemoryItemType
    payload: dict[str, Any] = Field(default_factory=dict)
    text: str  # Человекочитаемое описание для LLM

    def to_context_string(self) -> str:
        """Преобразовать в строку для контекста LLM."""
        return f"[Tick {self.tick}] {self.text}"


class CompactionItem(BaseModel):
    """Элемент компакции — сжатое саммари истории."""

    tick: int
    summary: str  # LLM-саммари предыдущей истории
    items_compacted: int  # Сколько элементов было сжато
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────────────────
# Agent Memory (аналог транскрипта в Codex)
# ─────────────────────────────────────────────────────────────────────────────


class AgentMemory:
    """
    Память агента по архитектуре Codex.

    Основные операции:
    - add(): добавить элемент в память
    - get_context(): получить контекст для LLM (с учётом компакции)
    - compact(): сжать историю через LLM
    - save()/load(): сохранение/восстановление в JSON-lines
    """

    # Пороги для автоматической компакции
    COMPACTION_THRESHOLD = 50  # Количество элементов до компакции
    CONTEXT_WINDOW_ITEMS = 20  # Последние N элементов всегда в контексте

    def __init__(
        self,
        agent_id: str,
        *,
        compaction_threshold: int | None = None,
        context_window: int | None = None,
    ):
        self.agent_id = agent_id
        self.items: list[MemoryItem] = []
        self.compactions: list[CompactionItem] = []

        self.compaction_threshold = compaction_threshold or self.COMPACTION_THRESHOLD
        self.context_window = context_window or self.CONTEXT_WINDOW_ITEMS

        # Счётчики для аналитики
        self._total_items_added = 0
        self._total_compactions = 0

    def add(self, item: MemoryItem) -> None:
        """Добавить элемент в память."""
        self.items.append(item)
        self._total_items_added += 1

    def add_event(
        self,
        tick: int,
        item_type: MemoryItemType,
        text: str,
        **payload: Any,
    ) -> MemoryItem:
        """Удобный метод для добавления события."""
        item = MemoryItem(
            tick=tick,
            item_type=item_type,
            text=text,
            payload=payload,
        )
        self.add(item)
        return item

    def needs_compaction(self) -> bool:
        """Проверить, нужна ли компакция."""
        return len(self.items) >= self.compaction_threshold

    async def compact(self, llm: LLMProvider) -> CompactionItem:
        """
        Сжать историю через LLM.

        Аналог компакции в Codex:
        1. Берём все элементы кроме последних context_window
        2. Формируем LLM-саммари
        3. Заменяем старые элементы на CompactionItem
        """
        if len(self.items) <= self.context_window:
            raise ValueError("Not enough items to compact")

        # Элементы для сжатия (всё кроме последних context_window)
        items_to_compact = self.items[: -self.context_window]
        items_to_keep = self.items[-self.context_window :]

        # Формируем текст для сжатия
        history_text = "\n".join(item.to_context_string() for item in items_to_compact)

        # Учитываем предыдущие компакции
        previous_summary = ""
        if self.compactions:
            previous_summary = f"Предыдущее саммари:\n{self.compactions[-1].summary}\n\n"

        # LLM-саммари
        system_prompt = """Ты архивариус, сжимающий историю действий агента.
Создай краткое саммари (2-4 предложения), сохраняющее:
- Ключевые события (победы/проигрыши в тендерах, сделки)
- Важные взаимодействия с другими агентами
- Изменения статуса (заморозка, трибуналы)
- Общую тенденцию поведения

Не включай детали, которые не влияют на будущие решения."""

        user_prompt = f"""{previous_summary}История для сжатия:
{history_text}

Создай краткое саммари:"""

        response = await llm.generate(system_prompt, user_prompt, temperature=0.3)

        # Создаём CompactionItem
        compaction = CompactionItem(
            tick=items_to_keep[0].tick if items_to_keep else 0,
            summary=response.text.strip(),
            items_compacted=len(items_to_compact),
        )

        # Обновляем состояние
        self.compactions.append(compaction)
        self.items = items_to_keep
        self._total_compactions += 1

        return compaction

    def get_context(self, *, max_items: int | None = None) -> str:
        """
        Получить контекст для LLM.

        Формат:
        1. Последняя компакция (если есть)
        2. Последние N элементов
        """
        parts: list[str] = []

        # Добавляем последнюю компакцию
        if self.compactions:
            latest = self.compactions[-1]
            parts.append(f"[История до tick {latest.tick}] {latest.summary}")

        # Добавляем последние элементы
        items_limit = max_items or self.context_window
        recent_items = self.items[-items_limit:] if self.items else []

        for item in recent_items:
            parts.append(item.to_context_string())

        return "\n".join(parts) if parts else "Нет истории взаимодействий."

    def get_recent_items(
        self,
        n: int = 10,
        *,
        item_types: list[MemoryItemType] | None = None,
    ) -> list[MemoryItem]:
        """Получить последние N элементов, опционально фильтруя по типу."""
        items = self.items
        if item_types:
            items = [i for i in items if i.item_type in item_types]
        return items[-n:]

    def count_by_type(self, item_type: MemoryItemType) -> int:
        """Посчитать элементы определённого типа."""
        return sum(1 for i in self.items if i.item_type == item_type)

    def get_stats(self) -> dict[str, Any]:
        """Статистика памяти."""
        return {
            "agent_id": self.agent_id,
            "total_items": len(self.items),
            "total_compactions": len(self.compactions),
            "items_added_total": self._total_items_added,
            "needs_compaction": self.needs_compaction(),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Сохранение/восстановление (JSON-lines, как rollout в Codex)
    # ─────────────────────────────────────────────────────────────────────────

    def save(self, path: Path | str) -> None:
        """
        Сохранить память в JSON-lines файл (как rollout в Codex).

        Формат: одна JSON-строка на элемент.
        """
        path = Path(path)
        with path.open("w", encoding="utf-8") as f:
            # Метаданные
            meta = {
                "type": "memory_meta",
                "agent_id": self.agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_items": len(self.items),
                "total_compactions": len(self.compactions),
            }
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")

            # Компакции
            for compaction in self.compactions:
                line = {
                    "type": "compaction",
                    "payload": compaction.model_dump(mode="json"),
                }
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

            # Элементы памяти
            for item in self.items:
                line = {
                    "type": "memory_item",
                    "payload": item.model_dump(mode="json"),
                }
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path | str) -> "AgentMemory":
        """
        Восстановить память из JSON-lines файла.
        """
        path = Path(path)
        memory: AgentMemory | None = None

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                data = json.loads(line)
                line_type = data["type"]

                if line_type == "memory_meta":
                    memory = cls(agent_id=data["agent_id"])

                elif line_type == "compaction" and memory:
                    compaction = CompactionItem(**data["payload"])
                    memory.compactions.append(compaction)

                elif line_type == "memory_item" and memory:
                    item = MemoryItem(**data["payload"])
                    memory.items.append(item)

        if memory is None:
            raise ValueError(f"Invalid memory file: {path}")

        return memory


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions для создания типичных событий
# ─────────────────────────────────────────────────────────────────────────────


def create_tender_event(
    tick: int,
    tender_id: str,
    won: bool,
    *,
    bid_amount: float | None = None,
    competitors: list[str] | None = None,
) -> MemoryItem:
    """Создать событие участия в тендере."""
    item_type = MemoryItemType.TENDER_WIN if won else MemoryItemType.TENDER_LOSS
    result = "выиграл" if won else "проиграл"
    text = f"Участвовал в тендере {tender_id} и {result}"
    if bid_amount:
        text += f" (ставка: {bid_amount:,.0f})"

    return MemoryItem(
        tick=tick,
        item_type=item_type,
        text=text,
        payload={
            "tender_id": tender_id,
            "won": won,
            "bid_amount": bid_amount,
            "competitors": competitors or [],
        },
    )


def create_negotiation_event(
    tick: int,
    counterparty_id: str,
    *,
    deal_reached: bool,
    kickback_percent: float | None = None,
) -> MemoryItem:
    """Создать событие переговоров."""
    item_type = MemoryItemType.DEAL_REACHED if deal_reached else MemoryItemType.DEAL_REJECTED
    result = "достигнута договорённость" if deal_reached else "договорённость не достигнута"
    text = f"Переговоры с {counterparty_id}: {result}"
    if deal_reached and kickback_percent:
        text += f" ({kickback_percent:.0%})"

    return MemoryItem(
        tick=tick,
        item_type=item_type,
        text=text,
        payload={
            "counterparty_id": counterparty_id,
            "deal_reached": deal_reached,
            "kickback_percent": kickback_percent,
        },
    )


def create_governance_event(
    tick: int,
    event_type: MemoryItemType,
    description: str,
    **payload: Any,
) -> MemoryItem:
    """Создать событие governance (аудит, трибунал, заморозка)."""
    return MemoryItem(
        tick=tick,
        item_type=event_type,
        text=description,
        payload=payload,
    )
