"""Асинхронная среда симуляции с непрерывным временем."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from magistry_sim.context import build_situation
from magistry_sim.conversation import ChannelType, ConversationManager
from magistry_sim.document_forge import DocType, DocumentForge
from magistry_sim.scheduler import Scheduler
from magistry_sim.sim_clock import SimClock
from magistry_sim.state import ReputationRecord, WorldState
from magistry_sim.tools.communication import talk_to_threaded

if TYPE_CHECKING:
    from magistry_sim.agents import AgentRunner
    from magistry_sim.config import ScenarioConfig
    from magistry_sim.llm import LLMProvider


ACTION_DURATIONS_SEC: dict[str, tuple[int, int]] = {
    "talk_to": (60, 180),
    "create_document": (1800, 7200),
    "open_case": (1200, 3600),
    "submit_proposal": (1200, 3600),
    "resolve_case": (600, 1800),
    "file_report": (1200, 3600),
    "add_note": (120, 600),
    "move_to": (600, 2400),
    "default": (120, 900),
}


@dataclass
class AsyncSimulationResult:
    """Результат асинхронной симуляции."""

    scenario_id: str
    seed: int
    started_at: str
    ended_at: str
    events_count: int
    state: WorldState


class AsyncEnvironment:
    """Асинхронная среда симуляции с непрерывным временем."""

    def __init__(
        self,
        config: "ScenarioConfig",
        runner: "AgentRunner",
        llm: "LLMProvider",
    ) -> None:
        self._config = config
        self._runner = runner
        self._llm = llm
        self._rng = random.Random(config.seed)

        start = config.start_time or datetime(2026, 2, 16, 9, 0, tzinfo=timezone.utc)
        end = config.end_time or (start + timedelta(days=7))
        self._end_time = end

        self.clock = SimClock(start)
        self.scheduler = Scheduler()
        self.conversations = ConversationManager()
        self.documents = DocumentForge(llm=llm)
        self.state = WorldState()
        self.state.current_time = start

        self._init_state()
        self._schedule_initial_wakeups()

    def _init_state(self) -> None:
        """Инициализировать состояние мира из конфигурации."""
        for profile in self._config.agents:
            self.state.agents[profile.id] = profile
            self.state.graph.add_agent(profile.id)
            has_governance_capability = any(
                cap.action in ("audit", "vote")
                for cap in (profile.capabilities or [])
            )
            if not has_governance_capability:
                self.state.reputation[profile.id] = ReputationRecord()

            res = profile.initial_resources
            self.state.resources.init_agent(
                profile.id,
                budget_limit=res.budget_limit,
                staffing_slots=res.staffing_slots,
                contract_capacity=res.contract_capacity,
            )

            for conn in profile.connections:
                self.state.graph.add_connection(
                    profile.id,
                    conn.target_id,
                    relation=conn.relation,
                    strength=conn.strength,
                )

    def _schedule_initial_wakeups(self) -> None:
        """Запланировать первое пробуждение каждого агента."""
        for agent_id in self.state.agents:
            jitter_min = self._rng.randint(0, 15)
            self.scheduler.schedule(
                agent_id,
                self.clock.now + timedelta(minutes=jitter_min),
            )

    async def run(self) -> AsyncSimulationResult:
        """Запустить симуляцию до достижения предельного времени."""
        self.state.event_log.log(
            event_type="world_event",
            payload={
                "narrative": "Запущена асинхронная симуляция живого мира."
            },
            timestamp=self.clock.iso(),
        )

        while not self.scheduler.is_empty:
            next_time = self.scheduler.peek_time()
            if next_time is None or next_time > self._end_time:
                break

            agent_id, wake_time = self.scheduler.next()
            # Часы могли уйти вперёд (например, из-за talk_to_threaded),
            # поэтому продвигаем только если wake_time ещё в будущем.
            if wake_time > self.clock.now:
                self.clock.advance_to(wake_time)
            self.state.current_time = self.clock.now
            used_tools: list[str] = ["default"]
            try:
                used_tools = await self._run_agent_action(agent_id)
            except Exception as e:
                self.state.event_log.log(
                    event_type="agent_error",
                    agent_id=agent_id,
                    payload={"error": str(e)},
                    timestamp=self.clock.iso(),
                )
            finally:
                self._schedule_next(agent_id, used_tools)

        self.state.event_log.log(
            event_type="world_event",
            payload={"narrative": "Асинхронная симуляция завершена."},
            timestamp=self.clock.iso(),
        )
        return AsyncSimulationResult(
            scenario_id=self._config.id.value
            if hasattr(self._config.id, "value")
            else str(self._config.id),
            seed=self._config.seed,
            started_at=self._config.start_time.isoformat()
            if self._config.start_time
            else "",
            ended_at=self.clock.iso(),
            events_count=len(self.state.event_log.all_events),
            state=self.state,
        )

    async def _run_agent_action(self, agent_id: str) -> list[str]:
        """Пробудить агента и выполнить его действия.

        Returns:
            Список имён инструментов, использованных агентом.
        """
        if agent_id not in self.state.agents:
            return ["default"]

        situation = build_situation(agent_id, self.state)
        tools = [
            "talk_to",
            "create_document",
            "open_case",
            "submit_proposal",
            "resolve_case",
            "file_report",
            "add_note",
            "move_to",
        ]
        actions = await asyncio.to_thread(
            self._runner.run_turn,
            agent_id=agent_id,
            situation=situation,
            tools=tools,
            state=self.state,
        )

        if not actions:
            self.state.event_log.log(
                event_type="idle",
                agent_id=agent_id,
                payload={},
                timestamp=self.clock.iso(),
            )
            return ["default"]

        tool_names: list[str] = []
        for action in actions:
            tool = str(action.get("tool", "default"))
            args = action.get("args", {})
            if not isinstance(args, dict):
                args = {}
            await self._dispatch_action(agent_id, tool, args)
            tool_names.append(tool)

        return tool_names or ["default"]

    async def _dispatch_action(
        self,
        agent_id: str,
        tool: str,
        args: dict[str, Any],
    ) -> None:
        """Диспетчеризовать действие агента."""
        timestamp = self.clock.iso()

        if tool == "talk_to":
            target = str(args.get("agent_id", ""))
            message = str(args.get("message", "")).strip()
            if not target or not message:
                return

            channel = _parse_channel(str(args.get("channel", "telegram")))
            private = _parse_bool(args.get("private", None), default=True)
            talk_to_threaded(
                caller_id=agent_id,
                agent_id=target,
                message=message,
                channel=channel,
                private=private,
                state=self.state,
                runner=self._runner,
                conversation_manager=self.conversations,
                clock=self.clock,
            )
            return

        if tool == "create_document":
            doc_type = _parse_doc_type(str(args.get("doc_type", "memo")))
            context = str(args.get("context", "")).strip()
            profile = self.state.agents.get(agent_id)
            author_name = profile.name if profile else agent_id
            author_position = profile.position if profile else "участник"
            document = self.documents.generate(
                doc_type=doc_type,
                author_id=agent_id,
                author_name=author_name,
                author_position=author_position,
                context=context or "Подготовить документ по рабочей задаче.",
                case_id=str(args.get("case_id", "")),
                title=str(args.get("title", "")),
            )
            self.state.event_log.log(
                event_type="document_created",
                agent_id=agent_id,
                payload={
                    "doc_id": document.doc_id,
                    "doc_type": document.doc_type.value,
                    "title": document.title,
                    "case_id": document.case_id,
                    "content": document.content,
                    "visibility": "public",
                },
                timestamp=timestamp,
            )
            return

        # Для остальных действий пока логируем факт выполнения.
        self.state.event_log.log(
            event_type=tool,
            agent_id=agent_id,
            payload=args,
            timestamp=timestamp,
        )

    def _schedule_next(self, agent_id: str, tools: list[str]) -> None:
        """Запланировать следующее пробуждение агента.

        Args:
            agent_id: Идентификатор агента.
            tools: Список имён инструментов, использованных агентом.
                   Длительности суммируются для расчёта задержки.
        """
        total_delay = 0
        for tool in tools:
            low, high = ACTION_DURATIONS_SEC.get(
                tool, ACTION_DURATIONS_SEC["default"]
            )
            total_delay += self._rng.randint(low, high)
        next_wake = self.clock.now + timedelta(seconds=total_delay)
        if next_wake <= self._end_time:
            self.scheduler.schedule(agent_id, next_wake)


def _parse_channel(raw: str) -> ChannelType:
    """Разобрать строковое имя канала."""
    try:
        return ChannelType(raw)
    except ValueError:
        return ChannelType.TELEGRAM


def _parse_doc_type(raw: str) -> DocType:
    """Разобрать строковый тип документа."""
    try:
        return DocType(raw)
    except ValueError:
        return DocType.MEMO


def _parse_bool(value: Any, *, default: bool) -> bool:
    """Parse a bool-ish tool arg safely (LLM may emit strings like 'false')."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    return default
