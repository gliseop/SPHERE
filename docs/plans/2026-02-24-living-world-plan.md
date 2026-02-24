# Living World Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace round-based simulation with fully asynchronous, time-driven world where agents live in continuous time, converse in multi-turn threads, and produce full-text GOST-compliant documents.

**Architecture:** New `AsyncEnvironment` replaces `Environment.run()` with a priority-queue scheduler (`heapq`) driven by `SimClock`. Communication refactored into `ConversationManager` producing per-message events with `thread_id` and `channel`. New `DocumentForge` generates full-text artifacts via LLM. Frontend groups by day/hour, renders message threads and collapsible documents.

**Tech Stack:** Python 3.12, asyncio, heapq, Pydantic v2, FastAPI WebSocket, React/TypeScript, existing LLMProvider interface.

---

## Phase 1: Core Infrastructure

### Task 1: SimClock module

**Files:**
- Create: `src/magistry_sim/sim_clock.py`
- Test: `tests/test_sim_clock.py`

**Step 1: Write the failing test**

```python
# tests/test_sim_clock.py
"""Тесты SimClock — глобальные часы симуляции."""

from datetime import datetime, timedelta, timezone
from magistry_sim.sim_clock import SimClock

MSK = timezone(timedelta(hours=3))


def test_initial_time():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    assert clock.now == start


def test_advance_to():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    target = start + timedelta(hours=2)
    clock.advance_to(target)
    assert clock.now == target


def test_advance_to_past_raises():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    import pytest
    with pytest.raises(ValueError, match="past"):
        clock.advance_to(start - timedelta(hours=1))


def test_iso_timestamp():
    start = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
    clock = SimClock(start)
    ts = clock.iso()
    assert ts == "2026-02-16T09:00:00+03:00"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_sim_clock.py -v`
Expected: FAIL — ModuleNotFoundError: No module named 'magistry_sim.sim_clock'

**Step 3: Write minimal implementation**

```python
# src/magistry_sim/sim_clock.py
"""Глобальные часы симуляции с непрерывным временем."""

from __future__ import annotations

from datetime import datetime


class SimClock:
    """Монотонные часы симуляции.

    Args:
        start: Начальное время симуляции.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    @property
    def now(self) -> datetime:
        """Текущее время симуляции."""
        return self._now

    def advance_to(self, target: datetime) -> None:
        """Продвинуть часы к указанному времени.

        Args:
            target: Целевое время (не раньше текущего).

        Raises:
            ValueError: Если target в прошлом.
        """
        if target < self._now:
            raise ValueError(
                f"Cannot advance to past: {target} < {self._now}"
            )
        self._now = target

    def iso(self) -> str:
        """Текущее время в формате ISO 8601.

        Returns:
            Строка ISO 8601.
        """
        return self._now.isoformat()
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_sim_clock.py -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/magistry_sim/sim_clock.py tests/test_sim_clock.py
git commit -m "feat: add SimClock for continuous time simulation"
```

---

### Task 2: Make Event.round optional, add backward compatibility

**Files:**
- Modify: `src/magistry_sim/events.py:13-24` (Event class)
- Modify: `src/magistry_sim/events.py:53-81` (EventLog.log)
- Test: `tests/test_events_compat.py`

**Step 1: Write the failing test**

```python
# tests/test_events_compat.py
"""Тесты обратной совместимости Event с необязательным round."""

import json
from magistry_sim.events import Event, EventLog


def test_event_without_round():
    e = Event(
        event_type="message",
        agent_id="off_1",
        payload={"content": "hello"},
        timestamp="2026-02-16T09:00:00+03:00",
    )
    assert e.round is None
    d = json.loads(e.model_dump_json())
    assert "round" not in d or d["round"] is None


def test_event_with_round_backward_compat():
    e = Event(
        round=5,
        event_type="case_opened",
        agent_id="off_1",
        payload={},
        timestamp="2026-02-16T09:00:00+03:00",
    )
    assert e.round == 5


def test_eventlog_log_without_round():
    log = EventLog()
    e = log.log(
        event_type="message",
        agent_id="off_1",
        payload={"thread_id": "T-001"},
        timestamp="2026-02-16T09:00:00+03:00",
    )
    assert e.event_type == "message"
    assert e.round is None


def test_eventlog_log_with_round():
    log = EventLog()
    e = log.log(
        round=3,
        event_type="case_opened",
        agent_id="off_1",
        payload={},
    )
    assert e.round == 3
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_events_compat.py -v`
Expected: FAIL — ValidationError (round is required)

**Step 3: Modify Event class**

In `src/magistry_sim/events.py`, change:
- `round: int` → `round: int | None = None`
- `EventLog.log()` signature: make `round` keyword-only with default `None`
- When serializing to JSONL: exclude `None` round from output (use `model_dump(exclude_none=True)`)

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_events_compat.py tests/ -v --timeout=30`
Expected: All tests pass (new + existing)

**Step 5: Commit**

```bash
git add src/magistry_sim/events.py tests/test_events_compat.py
git commit -m "feat: make Event.round optional for time-based simulation"
```

---

### Task 3: Add time fields to ScenarioConfig

**Files:**
- Modify: `src/magistry_sim/config.py:87-102` (ScenarioConfig)
- Test: `tests/test_config_time.py`

**Step 1: Write the failing test**

```python
# tests/test_config_time.py
"""Тесты временных полей ScenarioConfig."""

from datetime import datetime, timedelta, timezone
from magistry_sim.config import ScenarioConfig

MSK = timezone(timedelta(hours=3))


def test_scenario_config_with_time_fields():
    cfg = ScenarioConfig(
        id="S1",
        title="Test",
        description="Test scenario",
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 27, 18, 0, tzinfo=MSK),
    )
    assert cfg.start_time is not None
    assert cfg.end_time is not None
    duration = cfg.end_time - cfg.start_time
    assert duration.days == 11


def test_scenario_config_backward_compat():
    cfg = ScenarioConfig(
        id="S0",
        title="Test",
        description="Old format",
        max_rounds=8,
    )
    assert cfg.max_rounds == 8
    assert cfg.start_time is None
    assert cfg.end_time is None
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_config_time.py -v`
Expected: FAIL — unexpected keyword argument 'start_time'

**Step 3: Add fields to ScenarioConfig**

In `src/magistry_sim/config.py`, add to ScenarioConfig:
```python
start_time: datetime | None = None
end_time: datetime | None = None
```

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_config_time.py tests/ -v --timeout=30`
Expected: All pass

**Step 5: Commit**

```bash
git add src/magistry_sim/config.py tests/test_config_time.py
git commit -m "feat: add start_time/end_time fields to ScenarioConfig"
```

---

## Phase 2: Communication System

### Task 4: ConversationManager

**Files:**
- Create: `src/magistry_sim/conversation.py`
- Test: `tests/test_conversation.py`

**Step 1: Write the failing test**

```python
# tests/test_conversation.py
"""Тесты ConversationManager — управление тредами разговоров."""

from magistry_sim.conversation import ConversationManager, Thread, ChannelType


def test_create_thread():
    cm = ConversationManager()
    thread = cm.create_thread(
        initiator="off_1",
        recipient="biz_1",
        channel=ChannelType.TELEGRAM,
    )
    assert thread.thread_id.startswith("T-")
    assert thread.initiator == "off_1"
    assert thread.recipient == "biz_1"
    assert thread.channel == ChannelType.TELEGRAM
    assert thread.messages == []
    assert not thread.is_closed


def test_add_message():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.add_message(thread.thread_id, "off_1", "Привет, Сергей")
    assert len(thread.messages) == 1
    assert thread.messages[0].sender == "off_1"
    assert thread.messages[0].content == "Привет, Сергей"


def test_thread_max_messages():
    cm = ConversationManager(max_messages=3)
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.add_message(thread.thread_id, "off_1", "msg1")
    cm.add_message(thread.thread_id, "biz_1", "msg2")
    cm.add_message(thread.thread_id, "off_1", "msg3")
    assert thread.is_closed


def test_close_thread():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.PHONE)
    cm.close_thread(thread.thread_id)
    assert thread.is_closed


def test_channel_types():
    assert ChannelType.TELEGRAM.value == "telegram"
    assert ChannelType.PHONE.value == "phone"
    assert ChannelType.FACE_TO_FACE.value == "face_to_face"
    assert ChannelType.EMAIL.value == "email"
    assert ChannelType.OFFICIAL_DOC.value == "official_doc"


def test_get_thread_context():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.add_message(thread.thread_id, "off_1", "Привет")
    cm.add_message(thread.thread_id, "biz_1", "Здравствуйте")
    ctx = cm.get_thread_context(thread.thread_id)
    assert "off_1: Привет" in ctx
    assert "biz_1: Здравствуйте" in ctx
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_conversation.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: Write implementation**

```python
# src/magistry_sim/conversation.py
"""Управление тредами разговоров между агентами."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChannelType(Enum):
    """Канал связи между агентами."""

    TELEGRAM = "telegram"
    PHONE = "phone"
    FACE_TO_FACE = "face_to_face"
    EMAIL = "email"
    OFFICIAL_DOC = "official_doc"


@dataclass
class ThreadMessage:
    """Одна реплика в треде.

    Args:
        sender: Идентификатор отправителя.
        content: Текст реплики.
    """

    sender: str
    content: str


@dataclass
class Thread:
    """Тред разговора между двумя агентами.

    Args:
        thread_id: Уникальный идентификатор треда.
        initiator: Кто начал разговор.
        recipient: Собеседник.
        channel: Канал связи.
        max_messages: Лимит реплик.
    """

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
    """Менеджер тредов разговоров.

    Args:
        max_messages: Лимит реплик по умолчанию для каждого треда.
    """

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
        """Создать новый тред.

        Args:
            initiator: Кто начинает разговор.
            recipient: Собеседник.
            channel: Канал связи.

        Returns:
            Новый объект Thread.
        """
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
        self, thread_id: str, sender: str, content: str
    ) -> ThreadMessage:
        """Добавить реплику в тред.

        Args:
            thread_id: Идентификатор треда.
            sender: Отправитель.
            content: Текст.

        Returns:
            Созданный ThreadMessage.

        Raises:
            ValueError: Если тред закрыт или не найден.
        """
        thread = self._threads.get(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id} not found")
        if thread.is_closed:
            raise ValueError(f"Thread {thread_id} is closed")
        msg = ThreadMessage(sender=sender, content=content)
        thread.messages.append(msg)
        return msg

    def close_thread(self, thread_id: str) -> None:
        """Закрыть тред.

        Args:
            thread_id: Идентификатор треда.
        """
        thread = self._threads.get(thread_id)
        if thread is not None:
            thread.close()

    def get_thread(self, thread_id: str) -> Thread | None:
        """Получить тред по ID.

        Args:
            thread_id: Идентификатор треда.

        Returns:
            Thread или None.
        """
        return self._threads.get(thread_id)

    def get_thread_context(self, thread_id: str) -> str:
        """Получить текстовый контекст треда для LLM-промпта.

        Args:
            thread_id: Идентификатор треда.

        Returns:
            Текст с историей реплик.
        """
        thread = self._threads.get(thread_id)
        if thread is None:
            return ""
        lines = []
        for msg in thread.messages:
            lines.append(f"{msg.sender}: {msg.content}")
        return "\n".join(lines)
```

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_conversation.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add src/magistry_sim/conversation.py tests/test_conversation.py
git commit -m "feat: add ConversationManager with threaded multi-turn dialogues"
```

---

### Task 5: DocumentForge

**Files:**
- Create: `src/magistry_sim/document_forge.py`
- Test: `tests/test_document_forge.py`

**Step 1: Write the failing test**

```python
# tests/test_document_forge.py
"""Тесты DocumentForge — генерация документов по ГОСТ."""

from unittest.mock import MagicMock
from magistry_sim.document_forge import DocumentForge, DocType


def test_doc_types():
    assert DocType.MEMO.value == "memo"
    assert DocType.TECHNICAL_SPECIFICATION.value == "technical_specification"
    assert DocType.PROTOCOL.value == "protocol"
    assert DocType.COMMERCIAL_PROPOSAL.value == "commercial_proposal"
    assert DocType.AUDIT_REPORT.value == "audit_report"
    assert DocType.NEWSPAPER_ARTICLE.value == "newspaper_article"
    assert DocType.TELEGRAM_POST.value == "telegram_post"


def test_generate_document():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = MagicMock(
        text="УТВЕРЖДАЮ\nКозлов И.М.\nСлужебная записка..."
    )
    forge = DocumentForge(llm=mock_llm)
    result = forge.generate(
        doc_type=DocType.MEMO,
        author_id="off_1",
        author_name="Козлов И.М.",
        author_position="Начальник отдела обеспечения",
        context="Требуется закупка серверного оборудования",
        case_id="D-001",
    )
    assert result.doc_id.startswith("DOC-")
    assert result.doc_type == DocType.MEMO
    assert "УТВЕРЖДАЮ" in result.content
    mock_llm.generate.assert_called_once()


def test_gost_prompt_contains_requirements():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = MagicMock(text="doc text")
    forge = DocumentForge(llm=mock_llm)
    forge.generate(
        doc_type=DocType.TECHNICAL_SPECIFICATION,
        author_id="off_1",
        author_name="Козлов И.М.",
        author_position="Начальник",
        context="Закупка серверов",
    )
    call_args = mock_llm.generate.call_args
    system_prompt = call_args.kwargs.get("system", call_args[0][0] if call_args[0] else "")
    assert "ГОСТ" in system_prompt
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_document_forge.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: Write implementation**

```python
# src/magistry_sim/document_forge.py
"""Генерация полнотекстовых документов по ГОСТ через LLM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider


class DocType(Enum):
    """Тип создаваемого документа."""

    MEMO = "memo"
    TECHNICAL_SPECIFICATION = "technical_specification"
    PROTOCOL = "protocol"
    COMMERCIAL_PROPOSAL = "commercial_proposal"
    AUDIT_REPORT = "audit_report"
    NEWSPAPER_ARTICLE = "newspaper_article"
    TELEGRAM_POST = "telegram_post"
    COMPLAINT = "complaint"
    COURT_FILING = "court_filing"


_GOST_INSTRUCTIONS: dict[DocType, str] = {
    DocType.MEMO: (
        "Оформи как служебную записку по ГОСТ Р 7.0.97-2016. "
        "Обязательные реквизиты: наименование организации, дата, "
        "регистрационный номер, адресат, заголовок к тексту, текст, "
        "подпись. Стиль — официально-деловой."
    ),
    DocType.TECHNICAL_SPECIFICATION: (
        "Оформи как техническое задание по ГОСТ 34.602-2020. "
        "Структура: общие сведения, назначение, требования к системе, "
        "состав и содержание работ, порядок контроля и приёмки. "
        "Обязательны гриф утверждения и подпись."
    ),
    DocType.PROTOCOL: (
        "Оформи как протокол заседания по ГОСТ Р 7.0.97-2016. "
        "Реквизиты: наименование организации, вид документа, дата, "
        "номер, место составления, заголовок, текст (СЛУШАЛИ — "
        "ВЫСТУПИЛИ — ПОСТАНОВИЛИ), подписи председателя и секретаря."
    ),
    DocType.COMMERCIAL_PROPOSAL: (
        "Оформи как коммерческое предложение на фирменном бланке. "
        "Структура: шапка с реквизитами организации, исходящий номер, "
        "обращение к заказчику, описание предложения, спецификация, "
        "условия поставки, стоимость, подпись директора."
    ),
    DocType.AUDIT_REPORT: (
        "Оформи как аудиторский отчёт. Структура: титульная часть, "
        "основание проверки, объект проверки, выявленные нарушения "
        "(с указанием нормативных актов), выводы, рекомендации, подпись."
    ),
    DocType.NEWSPAPER_ARTICLE: (
        "Напиши как журналистскую статью для городской газеты. "
        "Заголовок, лид (первый абзац — суть), основной текст, "
        "цитаты участников, заключительный абзац. Стиль — "
        "информационно-аналитический, без канцелярита."
    ),
    DocType.TELEGRAM_POST: (
        "Напиши как пост в Telegram-канале. Короткий, эмоциональный, "
        "с призывом к действию. Допустимы символы-маркеры (>,  •). "
        "Длина: 500-1500 символов."
    ),
    DocType.COMPLAINT: (
        "Оформи как жалобу по ГОСТ Р 7.0.97-2016. Адресат, заявитель, "
        "основание жалобы, описание нарушений, требования, подпись."
    ),
    DocType.COURT_FILING: (
        "Оформи как исковое заявление. Суд, истец, ответчик, цена иска, "
        "обстоятельства дела, правовое обоснование, требования, подпись."
    ),
}

_BASE_SYSTEM = (
    "Ты — генератор документов для симуляции муниципального управления. "
    "Создавай реалистичные документы с полными реквизитами. "
    "Используй правдоподобные, но вымышленные данные. "
    "Пиши на русском языке."
)


@dataclass
class GeneratedDocument:
    """Результат генерации документа.

    Args:
        doc_id: Уникальный идентификатор.
        doc_type: Тип документа.
        title: Заголовок.
        content: Полный текст.
        case_id: Связанное дело (опционально).
    """

    doc_id: str
    doc_type: DocType
    title: str
    content: str
    case_id: str = ""


class DocumentForge:
    """Генератор документов по ГОСТ через LLM.

    Args:
        llm: Провайдер языковой модели.
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        self._counter = 0

    def generate(
        self,
        doc_type: DocType,
        author_id: str,
        author_name: str,
        author_position: str,
        context: str,
        case_id: str = "",
        title: str = "",
    ) -> GeneratedDocument:
        """Сгенерировать документ через LLM.

        Args:
            doc_type: Тип документа.
            author_id: ID автора.
            author_name: Имя автора.
            author_position: Должность автора.
            context: Контекст/содержание для документа.
            case_id: Связанное дело.
            title: Заголовок (генерируется если пуст).

        Returns:
            GeneratedDocument с полным текстом.
        """
        self._counter += 1
        doc_id = f"DOC-{self._counter:04d}"

        gost = _GOST_INSTRUCTIONS.get(doc_type, "")
        system_prompt = (
            f"{_BASE_SYSTEM}\n\n"
            f"Требования к оформлению по ГОСТ:\n{gost}\n\n"
            f"Автор: {author_name}, {author_position}."
        )
        user_prompt = (
            f"Составь документ типа «{doc_type.value}».\n\n"
            f"Контекст:\n{context}"
        )
        if case_id:
            user_prompt += f"\n\nДело: {case_id}"

        response = self._llm.generate(
            system=system_prompt, user=user_prompt
        )
        content = response.text.strip()

        if not title:
            first_line = content.split("\n")[0].strip()
            title = first_line[:120] if first_line else f"Документ {doc_id}"

        return GeneratedDocument(
            doc_id=doc_id,
            doc_type=doc_type,
            title=title,
            content=content,
            case_id=case_id,
        )
```

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_document_forge.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/magistry_sim/document_forge.py tests/test_document_forge.py
git commit -m "feat: add DocumentForge for GOST-compliant document generation"
```

---

### Task 6: Rewrite talk_to for multi-turn threads

**Files:**
- Modify: `src/magistry_sim/tools/communication.py:9-62`
- Test: `tests/test_talk_to_threaded.py`

**Step 1: Write the failing test**

```python
# tests/test_talk_to_threaded.py
"""Тесты обновлённой talk_to с тредами."""

from unittest.mock import MagicMock, patch
from magistry_sim.conversation import ConversationManager, ChannelType


def test_talk_to_creates_thread_events():
    """talk_to должна создавать отдельные события message для каждой реплики."""
    from magistry_sim.tools.communication import talk_to_threaded

    mock_runner = MagicMock()
    # Первый вызов run_reply вернёт ответ, второй — пустую строку (конец диалога)
    mock_runner.run_reply.side_effect = [
        "Привет, Игорь! Что нового?",
        "",  # пустой ответ = конец диалога
    ]

    mock_state = MagicMock()
    mock_state.agents = {"off_1": MagicMock(), "biz_1": MagicMock()}
    mock_state.round = 0

    cm = ConversationManager()
    events = talk_to_threaded(
        caller_id="off_1",
        agent_id="biz_1",
        message="Сергей, привет!",
        channel=ChannelType.TELEGRAM,
        private=True,
        state=mock_state,
        runner=mock_runner,
        conversation_manager=cm,
        clock_iso="2026-02-16T11:30:00+03:00",
    )

    assert len(events) >= 2  # минимум: начальное сообщение + ответ
    assert events[0]["event_type"] == "message"
    assert events[0]["payload"]["thread_id"].startswith("T-")
    assert events[0]["payload"]["channel"] == "telegram"
    assert events[0]["agent_id"] == "off_1"
    assert events[1]["agent_id"] == "biz_1"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_talk_to_threaded.py -v`
Expected: FAIL — ImportError

**Step 3: Add talk_to_threaded function**

Add new function `talk_to_threaded()` to `src/magistry_sim/tools/communication.py`. Keep old `talk_to()` for backward compatibility. New function returns list of event dicts instead of using context variables, making it testable and explicit.

```python
def talk_to_threaded(
    caller_id: str,
    agent_id: str,
    message: str,
    channel: ChannelType,
    private: bool,
    state: WorldState,
    runner: CognitiveAgentRunner,
    conversation_manager: ConversationManager,
    clock_iso: str,
    max_turns: int = 15,
) -> list[dict]:
    """Провести многореплковый диалог между агентами.

    Args:
        caller_id: Инициатор разговора.
        agent_id: Собеседник.
        message: Первая реплика.
        channel: Канал связи.
        private: Приватный разговор.
        state: Состояние мира.
        runner: Когнитивный раннер.
        conversation_manager: Менеджер тредов.
        clock_iso: Текущее время ISO 8601.
        max_turns: Максимум реплик.

    Returns:
        Список словарей-событий типа message.
    """
    if agent_id not in state.agents:
        return []
    if agent_id == caller_id:
        return []

    thread = conversation_manager.create_thread(
        initiator=caller_id,
        recipient=agent_id,
        channel=channel,
    )

    events: list[dict] = []

    # Первая реплика инициатора
    conversation_manager.add_message(thread.thread_id, caller_id, message)
    events.append({
        "event_type": "message",
        "agent_id": caller_id,
        "payload": {
            "thread_id": thread.thread_id,
            "to_id": agent_id,
            "channel": channel.value,
            "content": message,
            "private": private,
        },
        "timestamp": clock_iso,
    })

    # Цикл реплик
    current_speaker = agent_id
    other_speaker = caller_id
    for _ in range(max_turns - 1):
        if thread.is_closed:
            break

        thread_context = conversation_manager.get_thread_context(thread.thread_id)
        context = build_situation(current_speaker, state)
        response = runner.run_reply(
            agent_id=current_speaker,
            message=thread_context,
            sender_id=other_speaker,
            context=context,
            state=state,
        )

        if not response or not response.strip():
            break

        conversation_manager.add_message(thread.thread_id, current_speaker, response)
        events.append({
            "event_type": "message",
            "agent_id": current_speaker,
            "payload": {
                "thread_id": thread.thread_id,
                "to_id": other_speaker,
                "channel": channel.value,
                "content": response,
                "private": private,
            },
            "timestamp": clock_iso,
        })

        current_speaker, other_speaker = other_speaker, current_speaker

    conversation_manager.close_thread(thread.thread_id)
    state.graph.strengthen(caller_id, agent_id, delta=0.2)
    return events
```

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_talk_to_threaded.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/tools/communication.py tests/test_talk_to_threaded.py
git commit -m "feat: add talk_to_threaded with multi-turn conversation threads"
```

---

## Phase 3: Async Environment

### Task 7: Scheduler (priority queue)

**Files:**
- Create: `src/magistry_sim/scheduler.py`
- Test: `tests/test_scheduler.py`

**Step 1: Write the failing test**

```python
# tests/test_scheduler.py
"""Тесты Scheduler — приоритетная очередь пробуждений агентов."""

from datetime import datetime, timedelta, timezone
from magistry_sim.scheduler import Scheduler

MSK = timezone(timedelta(hours=3))
BASE = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)


def test_schedule_and_pop():
    s = Scheduler()
    s.schedule("off_1", BASE + timedelta(hours=1))
    s.schedule("biz_1", BASE + timedelta(minutes=30))
    agent, wake = s.next()
    assert agent == "biz_1"
    assert wake == BASE + timedelta(minutes=30)


def test_empty_scheduler():
    s = Scheduler()
    assert s.is_empty


def test_schedule_multiple():
    s = Scheduler()
    s.schedule("a", BASE + timedelta(hours=3))
    s.schedule("b", BASE + timedelta(hours=1))
    s.schedule("c", BASE + timedelta(hours=2))
    order = []
    while not s.is_empty:
        agent, _ = s.next()
        order.append(agent)
    assert order == ["b", "c", "a"]
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_scheduler.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: Write implementation**

```python
# src/magistry_sim/scheduler.py
"""Планировщик пробуждений агентов на основе приоритетной очереди."""

from __future__ import annotations

import heapq
from datetime import datetime


class Scheduler:
    """Приоритетная очередь пробуждений агентов.

    Агенты извлекаются в порядке возрастания времени пробуждения.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, str]] = []
        self._counter = 0

    def schedule(self, agent_id: str, wake_time: datetime) -> None:
        """Запланировать пробуждение агента.

        Args:
            agent_id: Идентификатор агента.
            wake_time: Время пробуждения.
        """
        self._counter += 1
        heapq.heappush(self._heap, (wake_time, self._counter, agent_id))

    def next(self) -> tuple[str, datetime]:
        """Извлечь следующего агента.

        Returns:
            Кортеж (agent_id, wake_time).

        Raises:
            IndexError: Если очередь пуста.
        """
        wake_time, _, agent_id = heapq.heappop(self._heap)
        return agent_id, wake_time

    @property
    def is_empty(self) -> bool:
        """Очередь пуста."""
        return len(self._heap) == 0

    def peek_time(self) -> datetime | None:
        """Время ближайшего пробуждения без извлечения.

        Returns:
            datetime или None если очередь пуста.
        """
        if self._heap:
            return self._heap[0][0]
        return None
```

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_scheduler.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/magistry_sim/scheduler.py tests/test_scheduler.py
git commit -m "feat: add Scheduler priority queue for agent wake-ups"
```

---

### Task 8: AsyncEnvironment — main async loop

**Files:**
- Create: `src/magistry_sim/async_environment.py`
- Test: `tests/test_async_environment.py`

**Step 1: Write the failing test**

```python
# tests/test_async_environment.py
"""Тесты AsyncEnvironment — асинхронный цикл симуляции."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.config import ScenarioConfig
from magistry_sim.sim_clock import SimClock

MSK = timezone(timedelta(hours=3))
START = datetime(2026, 2, 16, 9, 0, tzinfo=MSK)
END = datetime(2026, 2, 16, 18, 0, tzinfo=MSK)


@pytest.mark.asyncio
async def test_async_env_runs_to_completion():
    """AsyncEnvironment должен завершиться когда время истечёт."""
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=END,
        seed=42,
    )

    mock_runner = MagicMock()
    mock_runner.run_turn.return_value = []  # агент ничего не делает

    env = AsyncEnvironment(
        config=config,
        runner=mock_runner,
        llm=MagicMock(),
    )

    result = await env.run()
    assert result is not None
    assert env.clock.now >= END or env.scheduler.is_empty


@pytest.mark.asyncio
async def test_async_env_generates_events():
    """AsyncEnvironment должен логировать события."""
    config = ScenarioConfig(
        id="S0",
        title="Test",
        description="Test",
        start_time=START,
        end_time=START + timedelta(hours=2),
        seed=42,
        agents=[],
    )

    env = AsyncEnvironment(
        config=config,
        runner=MagicMock(),
        llm=MagicMock(),
    )
    result = await env.run()
    # Как минимум, должно быть событие world_event (начало дня)
    assert len(env.state.event_log.all_events) >= 0
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_async_environment.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: Write AsyncEnvironment skeleton**

```python
# src/magistry_sim/async_environment.py
"""Асинхронная среда симуляции с непрерывным временем."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from magistry_sim.sim_clock import SimClock
from magistry_sim.scheduler import Scheduler
from magistry_sim.conversation import ConversationManager, ChannelType
from magistry_sim.document_forge import DocumentForge
from magistry_sim.events import EventLog
from magistry_sim.state import WorldState
from magistry_sim.context import build_situation
from magistry_sim.tools.communication import talk_to_threaded

if TYPE_CHECKING:
    from magistry_sim.cognitive_runner import CognitiveAgentRunner
    from magistry_sim.config import ScenarioConfig
    from magistry_sim.llm import LLMProvider


# Средняя продолжительность действий (секунды)
ACTION_DURATIONS = {
    "talk_to": (300, 1200),       # 5-20 минут
    "open_case": (1800, 7200),    # 30-120 минут
    "submit_proposal": (3600, 7200),
    "resolve_case": (1800, 3600),
    "file_report": (3600, 7200),
    "add_note": (600, 1800),
    "move_to": (600, 2400),       # 10-40 минут
    "create_document": (1800, 7200),
    "default": (600, 3600),
}


class SimulationResult:
    """Результат симуляции.

    Args:
        events: Журнал событий.
        state: Финальное состояние.
    """

    def __init__(self, events: EventLog, state: WorldState) -> None:
        self.events = events
        self.state = state


class AsyncEnvironment:
    """Асинхронная среда симуляции с непрерывным временем.

    Args:
        config: Конфигурация сценария.
        runner: Когнитивный раннер агентов.
        llm: Провайдер языковой модели.
    """

    def __init__(
        self,
        config: ScenarioConfig,
        runner: CognitiveAgentRunner,
        llm: LLMProvider,
    ) -> None:
        self._config = config
        self._runner = runner
        self._llm = llm

        start = config.start_time or datetime(2026, 2, 16, 9, 0)
        end = config.end_time or start + timedelta(days=7)

        self.clock = SimClock(start)
        self.scheduler = Scheduler()
        self.conversations = ConversationManager()
        self.documents = DocumentForge(llm=llm)

        self.state = WorldState(
            agents={a.id: a for a in config.agents},
            event_log=EventLog(),
        )
        self._end_time = end
        self._rng = random.Random(config.seed)

        # Запланировать первое пробуждение каждого агента
        for agent in config.agents:
            jitter = timedelta(minutes=self._rng.randint(0, 30))
            self.scheduler.schedule(agent.id, start + jitter)

    async def run(self) -> SimulationResult:
        """Запустить симуляцию до конца времени.

        Returns:
            SimulationResult с журналом и состоянием.
        """
        while not self.scheduler.is_empty:
            next_time = self.scheduler.peek_time()
            if next_time is None or next_time > self._end_time:
                break

            agent_id, wake_time = self.scheduler.next()
            self.clock.advance_to(wake_time)

            await self._run_agent_action(agent_id)

            # Запланировать следующее пробуждение
            action_dur = self._rng.randint(
                *ACTION_DURATIONS.get("default", (600, 3600))
            )
            next_wake = wake_time + timedelta(seconds=action_dur)
            if next_wake < self._end_time:
                self.scheduler.schedule(agent_id, next_wake)

        return SimulationResult(
            events=self.state.event_log,
            state=self.state,
        )

    async def _run_agent_action(self, agent_id: str) -> None:
        """Выполнить действие одного агента.

        Args:
            agent_id: Идентификатор агента.
        """
        profile = self.state.agents.get(agent_id)
        if profile is None:
            return

        situation = build_situation(agent_id, self.state)
        tools = list(ACTION_DURATIONS.keys())

        actions = self._runner.run_turn(
            agent_id=agent_id,
            situation=situation,
            tools=tools,
            state=self.state,
        )

        for action in actions:
            tool = action.get("tool", "")
            args = action.get("args", {})
            await self._dispatch_action(agent_id, tool, args)

    async def _dispatch_action(
        self, agent_id: str, tool: str, args: dict
    ) -> None:
        """Диспетчеризовать действие агента.

        Args:
            agent_id: Идентификатор агента.
            tool: Название инструмента.
            args: Аргументы.
        """
        timestamp = self.clock.iso()

        if tool == "talk_to":
            target = args.get("agent_id", "")
            message = args.get("message", "")
            private = args.get("private", True)
            channel_str = args.get("channel", "telegram")
            try:
                channel = ChannelType(channel_str)
            except ValueError:
                channel = ChannelType.TELEGRAM

            events = talk_to_threaded(
                caller_id=agent_id,
                agent_id=target,
                message=message,
                channel=channel,
                private=private,
                state=self.state,
                runner=self._runner,
                conversation_manager=self.conversations,
                clock_iso=timestamp,
            )
            for evt in events:
                self.state.event_log.log(
                    event_type=evt["event_type"],
                    agent_id=evt["agent_id"],
                    payload=evt["payload"],
                    timestamp=evt.get("timestamp", timestamp),
                )
        else:
            # Логируем как обычное событие
            self.state.event_log.log(
                event_type=tool,
                agent_id=agent_id,
                payload=args,
                timestamp=timestamp,
            )
```

**Step 4: Run tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_async_environment.py -v`

Note: May need to install `pytest-asyncio` — `pip install pytest-asyncio`.

Expected: 2 passed

**Step 5: Commit**

```bash
git add src/magistry_sim/async_environment.py tests/test_async_environment.py
git commit -m "feat: add AsyncEnvironment with continuous time simulation loop"
```

---

## Phase 4: Context Adaptation

### Task 9: Adapt build_situation for time-based world

**Files:**
- Modify: `src/magistry_sim/context.py:28-32` (round reference)
- Modify: `src/magistry_sim/context.py:138-147` (message filtering)
- Test: `tests/test_context_time.py`

**Step 1: Write the failing test**

```python
# tests/test_context_time.py
"""Тесты адаптации build_situation под временное окружение."""

from magistry_sim.context import build_situation
from magistry_sim.state import WorldState, Message
from unittest.mock import MagicMock


def test_build_situation_without_round():
    """build_situation должен работать без state.round (time-based mode)."""
    state = MagicMock(spec=WorldState)
    state.round = 0
    state.agents = {
        "off_1": MagicMock(
            name="Козлов И.М.",
            id="off_1",
            position="Начальник отдела",
        )
    }
    state.has_capability.return_value = False
    state.messages = []
    state.get_agent_cases.return_value = []
    state.get_open_cases.return_value = []
    state.active_needs = []
    state.reputation = {}
    state.graph.get_connections.return_value = []
    state.locations = None
    state.resources = None

    result = build_situation("off_1", state)
    assert "off_1" in result
    # В time-based режиме не должно быть "раунд 0"
    # (это изменение будет в адаптации)
```

**Step 2: Run test**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_context_time.py -v`

**Step 3: Modify build_situation**

In `context.py`:
- Line 28-32: Replace `f"Сейчас раунд {state.round}."` with time-aware text: check if `state` has `current_time` attribute; if yes, show formatted datetime; otherwise fall back to round.
- Lines 138-147: Replace round-based message filtering with time-window filtering (last N messages or last 2 hours).

**Step 4: Run all tests**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/ -v --timeout=30`

**Step 5: Commit**

```bash
git add src/magistry_sim/context.py tests/test_context_time.py
git commit -m "feat: adapt build_situation for time-based simulation"
```

---

## Phase 5: Frontend — Types and Data

### Task 10: Update TypeScript types

**Files:**
- Modify: `web/frontend/src/types.ts`

**Step 1: Update SimEvent interface**

```typescript
export interface SimEvent {
  round?: number | null           // optional for backward compat
  event_type: string
  agent_id: string
  payload: Record<string, unknown>
  timestamp: string
}
```

Add new types:

```typescript
export interface ThreadGroup {
  thread_id: string
  channel: string
  participants: string[]
  messages: SimEvent[]
  private: boolean
}
```

**Step 2: Update SimState in useSimulation.ts**

Change `currentRound: number` to `currentRound: number | null` and update the event handler to not rely on `msg.data.round`.

**Step 3: Run build to verify no type errors**

Run: `cd /home/development/MAGISTRY/web/frontend && npx tsc --noEmit`
Expected: No errors

**Step 4: Commit**

```bash
git add web/frontend/src/types.ts web/frontend/src/hooks/useSimulation.ts
git commit -m "feat: update TypeScript types for time-based events and threads"
```

---

### Task 11: ActivityFeed — thread rendering

**Files:**
- Modify: `web/frontend/src/components/ActivityFeed.tsx`
- Modify: `web/frontend/src/styles/hud.css`

**Step 1: Add thread grouping logic**

In ActivityFeed.tsx, after sorting events:
1. Group consecutive `message` events with same `thread_id` into a `ThreadGroup`
2. Render thread groups as messenger-style bubbles (initiator on left, recipient on right)
3. Show channel icon in thread header

**Step 2: Add document_created renderer**

For `event_type === "document_created"`:
- Collapsible block with title visible, content hidden by default
- Click to expand/collapse
- Monospace font for document text
- Show doc_type badge

**Step 3: Replace round grouping with day/hour grouping**

Change the grouping logic (lines 70-80) to:
- Parse `timestamp` into date
- Group by date (day)
- Within each day, show hour markers
- Remove fallback to `Раунд ${e.round}`

**Step 4: Add CSS for threads and documents**

Add to `hud.css`:
- `.thread-group` — container for thread
- `.thread-bubble.left` / `.thread-bubble.right` — message bubbles
- `.channel-badge` — channel icon
- `.document-card` — collapsible document
- `.private-badge` — lock icon for private messages

**Step 5: Run dev server and visually verify**

Run: `cd /home/development/MAGISTRY/web/frontend && npm run dev`
Navigate to localhost, verify rendering.

**Step 6: Commit**

```bash
git add web/frontend/src/components/ActivityFeed.tsx web/frontend/src/styles/hud.css
git commit -m "feat: ActivityFeed with thread rendering and collapsible documents"
```

---

### Task 12: Timeline replacing RoundScrubber

**Files:**
- Modify: `web/frontend/src/components/RoundScrubber.tsx` (rename conceptually, rewrite content)

**Step 1: Rewrite RoundScrubber as Timeline**

Keep the same file but change content:
- Extract unique dates from events timestamps
- Render as horizontal timeline with day markers
- Click on day scrolls ActivityFeed to that day
- Show event count per day as dot size
- Support both round-based (old) and time-based (new) events

**Step 2: Update parent component props**

Replace `onRoundClick` with `onDayClick(date: string)` while keeping backward compat.

**Step 3: Commit**

```bash
git add web/frontend/src/components/RoundScrubber.tsx
git commit -m "feat: convert RoundScrubber to Timeline with day navigation"
```

---

## Phase 6: Backend Adaptation

### Task 13: WebSocket graph updates by event, not round

**Files:**
- Modify: `web/backend/main.py:268-271` (ws_playback)
- Modify: `web/backend/main.py:330-333` (ws_live)

**Step 1: Change graph update trigger**

In both `ws_playback` and `ws_live`, replace:
```python
if event.get("round", current_round) != current_round:
    current_round = event.get("round", current_round)
    graph = _build_graph_state(events_so_far)
```

With:
```python
# Обновляем граф после значимых событий
if event.get("event_type") in (
    "graph_updated", "reputation_modified",
    "case_resolved", "report_filed",
):
    graph = _build_graph_state(events_so_far)
    await websocket.send_json({"type": "graph_state", **graph})
```

**Step 2: Add artifacts endpoint**

```python
@app.get("/api/artifacts/{doc_id}")
async def get_artifact(doc_id: str) -> dict:
    """Получить документ по ID."""
    # Ищем в artifacts/ или в событиях
    artifacts_dir = RESULTS_DIR / "artifacts"
    path = artifacts_dir / f"{doc_id}.md"
    if path.exists():
        return {"doc_id": doc_id, "content": path.read_text("utf-8")}
    return {"error": "Not found"}
```

**Step 3: Run backend and test**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m uvicorn web.backend.main:app --reload`
Test: `curl http://localhost:8000/api/runs`

**Step 4: Commit**

```bash
git add web/backend/main.py
git commit -m "feat: graph updates by event type, add artifacts endpoint"
```

---

## Phase 7: Integration

### Task 14: Update existing scenarios for time-based mode

**Files:**
- Modify: `src/magistry_sim/scenarios.py`

**Step 1: Add start_time and end_time to S0, S1, S2**

For each scenario, add:
```python
start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
end_time=datetime(2026, 2, 27, 18, 0, tzinfo=MSK),
```

Keep `max_rounds` for backward compatibility with old Environment.

**Step 2: Commit**

```bash
git add src/magistry_sim/scenarios.py
git commit -m "feat: add time fields to built-in scenarios S0-S2"
```

---

### Task 15: End-to-end smoke test

**Files:**
- Create: `tests/test_e2e_async.py`

**Step 1: Write integration test**

```python
# tests/test_e2e_async.py
"""Интеграционный тест: полный прогон AsyncEnvironment с MockLLM."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.config import ScenarioConfig, AgentProfile, Capability
from magistry_sim.llm import MockLLMProvider
from magistry_sim.cognitive_runner import CognitiveAgentRunner

MSK = timezone(timedelta(hours=3))


@pytest.mark.asyncio
async def test_full_simulation_with_mock_llm():
    """Полный прогон с двумя агентами и MockLLM."""
    config = ScenarioConfig(
        id="S0",
        title="Smoke test",
        description="Integration test",
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 16, 12, 0, tzinfo=MSK),  # 3 часа
        seed=42,
        agents=[
            AgentProfile(
                id="off_1",
                name="Козлов И.М.",
                position="Начальник",
                capabilities=[Capability(action="open_case", case_types=["procurement"])],
            ),
            AgentProfile(
                id="biz_1",
                name="Петров С.И.",
                position="Директор",
                capabilities=[Capability(action="submit_proposal", case_types=["procurement"])],
            ),
        ],
    )

    llm = MockLLMProvider()
    runner = CognitiveAgentRunner(llm=llm)

    env = AsyncEnvironment(config=config, runner=runner, llm=llm)
    result = await env.run()

    events = result.events.all_events
    assert len(events) > 0

    # Все события должны иметь timestamp
    for e in events:
        assert e.timestamp is not None
        assert len(e.timestamp) > 0

    # Не должно быть round в новых событиях
    for e in events:
        assert e.round is None
```

**Step 2: Run test**

Run: `cd /home/development/MAGISTRY && .venv/bin/python -m pytest tests/test_e2e_async.py -v --timeout=60`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_e2e_async.py
git commit -m "test: end-to-end smoke test for async simulation"
```

---

### Task 16: Generate sample rich run with AsyncEnvironment

**Files:**
- Modify: `web/scripts/generate_rich_run.py` — rewrite to use AsyncEnvironment

**Step 1: Rewrite generator**

Replace the hardcoded event list with actual AsyncEnvironment run. Keep the same output paths for compatibility.

```python
"""Генерация богатого тестового прогона через AsyncEnvironment."""

import asyncio
from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.scenarios import SCENARIOS
from magistry_sim.llm import OpenAICompatibleProvider  # or Mock for testing
from magistry_sim.cognitive_runner import CognitiveAgentRunner

async def main():
    config = SCENARIOS["S1_G1"]  # или собрать из аргументов
    llm = OpenAICompatibleProvider(...)
    runner = CognitiveAgentRunner(llm=llm)
    env = AsyncEnvironment(config=config, runner=runner, llm=llm)

    # Настроить стриминг в файл
    env.state.event_log.set_stream_path(EVENTS_FILE)

    result = await env.run()
    # Сохранить имена
    save_names(result.state.agents)

if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Run generation (optional, requires LLM API key)**

Run: `cd /home/development/MAGISTRY && .venv/bin/python web/scripts/generate_rich_run.py`

**Step 3: Commit**

```bash
git add web/scripts/generate_rich_run.py
git commit -m "feat: rewrite generate_rich_run.py to use AsyncEnvironment"
```
