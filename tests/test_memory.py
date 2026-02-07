"""Тесты для памяти агентов (по архитектуре Codex)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from magistry_sim.llm import MockLLMProvider, create_provider
from magistry_sim.memory import (
    AgentMemory,
    CompactionItem,
    MemoryItem,
    MemoryItemType,
    create_governance_event,
    create_negotiation_event,
    create_tender_event,
)


class TestMemoryItem:
    """Тесты элементов памяти."""

    def test_create_memory_item(self) -> None:
        """Создание элемента памяти."""
        item = MemoryItem(
            tick=5,
            item_type=MemoryItemType.TENDER_WIN,
            text="Выиграл тендер T001",
            payload={"tender_id": "T001", "amount": 100000},
        )

        assert item.tick == 5
        assert item.item_type == MemoryItemType.TENDER_WIN
        assert "T001" in item.text

    def test_to_context_string(self) -> None:
        """Преобразование в строку для контекста."""
        item = MemoryItem(
            tick=10,
            item_type=MemoryItemType.FROZEN,
            text="Заморожен за подозрительную активность",
        )

        context = item.to_context_string()
        assert "[Tick 10]" in context
        assert "Заморожен" in context


class TestAgentMemory:
    """Тесты транскрипта агента."""

    def test_add_items(self) -> None:
        """Добавление элементов в память."""
        memory = AgentMemory("agent_1")

        memory.add_event(
            tick=1,
            item_type=MemoryItemType.TENDER_PARTICIPATION,
            text="Участвовал в тендере",
        )
        memory.add_event(
            tick=2,
            item_type=MemoryItemType.TENDER_WIN,
            text="Выиграл тендер",
        )

        assert len(memory.items) == 2
        assert memory._total_items_added == 2

    def test_get_context_empty(self) -> None:
        """Контекст пустой памяти."""
        memory = AgentMemory("agent_1")
        context = memory.get_context()
        assert "Нет истории" in context

    def test_get_context_with_items(self) -> None:
        """Контекст с элементами."""
        memory = AgentMemory("agent_1")

        memory.add_event(tick=1, item_type=MemoryItemType.TENDER_WIN, text="Выиграл T1")
        memory.add_event(tick=2, item_type=MemoryItemType.FROZEN, text="Заморожен")

        context = memory.get_context()
        assert "Выиграл T1" in context
        assert "Заморожен" in context
        assert "[Tick 1]" in context

    def test_needs_compaction(self) -> None:
        """Проверка необходимости компакции."""
        memory = AgentMemory("agent_1", compaction_threshold=5)

        for i in range(4):
            memory.add_event(tick=i, item_type=MemoryItemType.OBSERVED_TENDER, text=f"Event {i}")

        assert not memory.needs_compaction()

        memory.add_event(tick=5, item_type=MemoryItemType.OBSERVED_TENDER, text="Event 5")
        assert memory.needs_compaction()

    def test_get_recent_items(self) -> None:
        """Получение последних элементов."""
        memory = AgentMemory("agent_1")

        for i in range(10):
            item_type = MemoryItemType.TENDER_WIN if i % 2 == 0 else MemoryItemType.TENDER_LOSS
            memory.add_event(tick=i, item_type=item_type, text=f"Event {i}")

        # Последние 3
        recent = memory.get_recent_items(3)
        assert len(recent) == 3
        assert recent[-1].tick == 9

        # Только победы
        wins = memory.get_recent_items(10, item_types=[MemoryItemType.TENDER_WIN])
        assert len(wins) == 5
        assert all(w.item_type == MemoryItemType.TENDER_WIN for w in wins)

    def test_count_by_type(self) -> None:
        """Подсчёт по типу."""
        memory = AgentMemory("agent_1")

        memory.add_event(tick=1, item_type=MemoryItemType.TENDER_WIN, text="Win 1")
        memory.add_event(tick=2, item_type=MemoryItemType.TENDER_WIN, text="Win 2")
        memory.add_event(tick=3, item_type=MemoryItemType.TENDER_LOSS, text="Loss 1")

        assert memory.count_by_type(MemoryItemType.TENDER_WIN) == 2
        assert memory.count_by_type(MemoryItemType.TENDER_LOSS) == 1
        assert memory.count_by_type(MemoryItemType.FROZEN) == 0


class TestCompaction:
    """Тесты компакции."""

    def test_compact_reduces_items(self) -> None:
        """Компакция уменьшает количество элементов."""
        memory = AgentMemory("agent_1", compaction_threshold=10, context_window=3)

        # Добавляем 10 элементов
        for i in range(10):
            memory.add_event(tick=i, item_type=MemoryItemType.TENDER_WIN, text=f"Выиграл тендер {i}")

        assert memory.needs_compaction()

        # Компакция с mock LLM
        provider = create_provider(mock=True)

        async def do_compact() -> CompactionItem:
            return await memory.compact(provider)

        compaction = asyncio.run(do_compact())

        # Проверяем результат
        assert len(memory.items) == 3  # Осталось context_window
        assert len(memory.compactions) == 1
        assert compaction.items_compacted == 7

    def test_get_context_with_compaction(self) -> None:
        """Контекст включает саммари компакции."""
        memory = AgentMemory("agent_1", compaction_threshold=10, context_window=3)

        for i in range(10):
            memory.add_event(tick=i, item_type=MemoryItemType.TENDER_WIN, text=f"Event {i}")

        provider = create_provider(mock=True)
        asyncio.run(memory.compact(provider))

        context = memory.get_context()

        # Должен содержать саммари и последние элементы
        assert "[История до tick" in context
        assert "Event 9" in context  # Последний элемент


class TestPersistence:
    """Тесты сохранения/восстановления."""

    def test_save_and_load(self) -> None:
        """Сохранение и загрузка памяти."""
        memory = AgentMemory("agent_test")

        memory.add_event(tick=1, item_type=MemoryItemType.TENDER_WIN, text="Win 1")
        memory.add_event(tick=2, item_type=MemoryItemType.FROZEN, text="Frozen")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.jsonl"
            memory.save(path)

            # Загружаем
            loaded = AgentMemory.load(path)

            assert loaded.agent_id == "agent_test"
            assert len(loaded.items) == 2
            assert loaded.items[0].item_type == MemoryItemType.TENDER_WIN
            assert loaded.items[1].item_type == MemoryItemType.FROZEN

    def test_save_and_load_with_compaction(self) -> None:
        """Сохранение/загрузка с компакцией."""
        memory = AgentMemory("agent_test", compaction_threshold=10, context_window=3)

        for i in range(10):
            memory.add_event(tick=i, item_type=MemoryItemType.TENDER_WIN, text=f"Event {i}")

        # Компакция
        provider = create_provider(mock=True)
        asyncio.run(memory.compact(provider))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.jsonl"
            memory.save(path)

            # Загружаем
            loaded = AgentMemory.load(path)

            assert loaded.agent_id == "agent_test"
            assert len(loaded.items) == 3  # После компакции
            assert len(loaded.compactions) == 1


class TestHelperFunctions:
    """Тесты вспомогательных функций."""

    def test_create_tender_event(self) -> None:
        """Создание события тендера."""
        event = create_tender_event(
            tick=5,
            tender_id="T001",
            won=True,
            bid_amount=100000,
            competitors=["comp_1", "comp_2"],
        )

        assert event.tick == 5
        assert event.item_type == MemoryItemType.TENDER_WIN
        assert "100,000" in event.text
        assert event.payload["tender_id"] == "T001"

    def test_create_negotiation_event(self) -> None:
        """Создание события переговоров."""
        event = create_negotiation_event(
            tick=10,
            counterparty_id="off_1",
            deal_reached=True,
            kickback_percent=0.15,
        )

        assert event.item_type == MemoryItemType.DEAL_REACHED
        assert "off_1" in event.text
        assert "15%" in event.text

    def test_create_governance_event(self) -> None:
        """Создание governance события."""
        event = create_governance_event(
            tick=15,
            event_type=MemoryItemType.TRIBUNAL_VERDICT,
            description="Признан виновным по делу T001",
            verdict="guilty",
            case_id="T001",
        )

        assert event.item_type == MemoryItemType.TRIBUNAL_VERDICT
        assert event.payload["verdict"] == "guilty"


class TestStats:
    """Тесты статистики."""

    def test_get_stats(self) -> None:
        """Получение статистики памяти."""
        memory = AgentMemory("agent_1", compaction_threshold=100)

        for i in range(5):
            memory.add_event(tick=i, item_type=MemoryItemType.TENDER_WIN, text=f"Event {i}")

        stats = memory.get_stats()

        assert stats["agent_id"] == "agent_1"
        assert stats["total_items"] == 5
        assert stats["items_added_total"] == 5
        assert stats["needs_compaction"] is False
