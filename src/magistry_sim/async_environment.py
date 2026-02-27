"""Асинхронная среда симуляции с непрерывным временем."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from magistry_sim.cases import Case
from magistry_sim.cognitive_runner import CognitiveAgentRunner
from magistry_sim.context import build_situation
from magistry_sim.conversation import ChannelType, ConversationManager
from magistry_sim.document_forge import DocType, DocumentForge
from magistry_sim.environment import TOOL_DISPATCH, _OBSERVABLE_EVENT_TYPES, format_observation
from magistry_sim.locations import Location, LocationManager
from magistry_sim.narrator import WorldNarrator
from magistry_sim.reputation import (
    POSITION_THRESHOLDS,
    apply_decay,
    apply_growth,
    compute_round_growth,
    freeze,
)
from magistry_sim.resources import apply_maintenance
from magistry_sim.scheduler import Scheduler
from magistry_sim.sim_clock import SimClock, WorkSchedule
from magistry_sim.state import ReputationRecord, WorldState
from magistry_sim.state_ops import apply_state_op
from magistry_sim.tools import current_agent_id, current_runner, current_state
from magistry_sim.tools.communication import talk_to_threaded
from magistry_sim.world_generator import WorldGenerator

if TYPE_CHECKING:
    from magistry_sim.agents import AgentRunner
    from magistry_sim.config import ScenarioConfig
    from magistry_sim.llm import LLMProvider

logger = logging.getLogger(__name__)


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
    cases: dict = field(default_factory=dict)
    final_reputation: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    round_summaries: list = field(default_factory=list)
    agents: list = field(default_factory=list)


class AsyncEnvironment:
    """Асинхронная среда симуляции с непрерывным временем."""

    def __init__(
        self,
        config: "ScenarioConfig",
        runner: "AgentRunner",
        llm: "LLMProvider",
        narrator: WorldNarrator | None = None,
        world_generator: WorldGenerator | None = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._llm = llm
        self._rng = random.Random(config.seed)
        self._narrator = narrator
        self._world_generator = world_generator

        start = config.start_time or datetime(2026, 2, 16, 9, 0, tzinfo=timezone.utc)
        end = config.end_time or (start + timedelta(days=7))
        self._end_time = end

        # Рабочее расписание из конфигурации сценария
        sched_cfg = config.schedule
        holidays: list[date] = []
        for h in sched_cfg.holidays:
            try:
                holidays.append(date.fromisoformat(h))
            except ValueError:
                pass
        self._work_schedule = WorkSchedule(
            work_start_hour=sched_cfg.work_start_hour,
            work_end_hour=sched_cfg.work_end_hour,
            work_days=sched_cfg.work_days,
            holidays=holidays,
        )

        self.clock = SimClock(start)
        self.scheduler = Scheduler()
        self.conversations = ConversationManager()
        self.documents = DocumentForge(llm=llm)
        self.state = WorldState()
        self.state.current_time = start
        self._turn_index: int = 0
        self._last_wakeup: dict[str, datetime] = {}
        self._round_summaries: list[dict[str, Any]] = []
        self._last_world_tick: datetime = start
        self._last_reputation_tick: datetime = start
        self._last_narrator_date: date | None = None

        self._init_state()
        self._last_wakeup = {
            aid: start - timedelta(seconds=1)
            for aid in self.state.agents
        }
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
                initial_score = (
                    float(profile.initial_reputation)
                    if profile.initial_reputation is not None
                    else 10.0
                )
                self.state.reputation[profile.id] = ReputationRecord(
                    score=initial_score
                )

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

        # Инициализация физических локаций (как в sync Environment)
        locations = LocationManager()
        locations.add_location(
            Location(
                id="office",
                name="Кабинет",
                public=False,
                available_actions=[
                    "talk_to",
                    "open_case",
                    "resolve_case",
                    "create_document",
                    "add_note",
                    "file_report",
                    "move_to",
                ],
            )
        )
        locations.add_location(
            Location(
                id="meeting_room",
                name="Зал заседаний",
                public=True,
                available_actions=["talk_to", "cast_vote", "move_to"],
            )
        )
        locations.add_location(
            Location(
                id="restaurant",
                name="Ресторан",
                public=False,
                suspicion_modifier=0.3,
                available_actions=["talk_to", "move_to"],
            )
        )
        locations.add_location(
            Location(
                id="corridor",
                name="Коридор",
                public=True,
                available_actions=["talk_to", "move_to"],
            )
        )
        self.state.locations = locations

        # Размещение агентов в стартовых локациях
        for profile in self._config.agents:
            locations.place_agent(profile.id, "office")

    def _schedule_initial_wakeups(self) -> None:
        """Запланировать первое пробуждение каждого агента."""
        for agent_id in self.state.agents:
            jitter_min = self._rng.randint(0, 15)
            self.scheduler.schedule(
                agent_id,
                self.clock.now + timedelta(minutes=jitter_min),
            )

    def _generate_needs(self, turn_index: int) -> None:
        """Активировать потребности сценария по turn_index.

        Для совместимости используем поле Need.appear_round как turn_index.
        """
        for need in self._config.needs:
            if need.appear_round != turn_index:
                continue
            already = any(
                n.case_type == need.case_type
                and n.target_agent_id == need.target_agent_id
                for n in self.state.active_needs
            )
            if not already:
                self.state.active_needs.append(need)

    def _deliver_observations(self, agent_id: str) -> None:
        """Доставить агенту наблюдения о событиях с прошлого пробуждения."""
        if not isinstance(self._runner, CognitiveAgentRunner):
            return
        if agent_id not in self.state.agents:
            return

        since = self._last_wakeup.get(agent_id)
        if since is None:
            since = self.clock.now - timedelta(seconds=1)

        now = self.clock.now
        for ev in self.state.event_log.all_events:
            try:
                ts = datetime.fromisoformat(ev.timestamp)
            except ValueError:
                continue

            if (ts.tzinfo is None) != (now.tzinfo is None):
                continue
            if ts <= since or ts > now:
                continue

            if ev.event_type not in _OBSERVABLE_EVENT_TYPES:
                continue
            if ev.agent_id == agent_id:
                continue

            payload = ev.payload if isinstance(ev.payload, dict) else {}
            is_private = payload.get("private", False)
            if is_private:
                to_id = payload.get("to_id", "")
                if to_id != agent_id:
                    continue

            event_dict = {
                "event_type": ev.event_type,
                "agent_id": ev.agent_id,
                "payload": payload,
            }
            text = format_observation(
                event_dict,
                self.state,
                observer_id=agent_id,
            )
            if not text:
                continue
            self._runner.observe(agent_id, text, self.state.round)

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
            if wake_time > self.clock.now:
                self.clock.advance_to(wake_time)
            self.state.current_time = self.clock.now

            self.state.round = self._turn_index
            if agent_id in self.state.agents:
                self._generate_needs(self.state.round)
                self._deliver_observations(agent_id)

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
                if agent_id in self.state.agents:
                    self._last_wakeup[agent_id] = self.clock.now
                self._turn_index += 1
                self._schedule_next(agent_id, used_tools)

            # Периодические тики
            self._try_world_tick()
            self._try_reputation_tick()
            self._try_narrator_tick()
            self._try_memory_summarization(agent_id)

        self.state.event_log.log(
            event_type="world_event",
            payload={"narrative": "Асинхронная симуляция завершена."},
            timestamp=self.clock.iso(),
        )
        return self._build_result()

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
            "cast_vote",
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

        token_state = current_state.set(self.state)
        token_agent = current_agent_id.set(agent_id)
        token_runner = current_runner.set(self._runner)
        try:
            func = TOOL_DISPATCH.get(tool)
            if func is None:
                # Неизвестный инструмент: логируем факт выполнения для отладки.
                self.state.event_log.log(
                    event_type=tool,
                    agent_id=agent_id,
                    payload=args,
                    timestamp=timestamp,
                )
                return
            try:
                func(**args, timestamp=timestamp)
            except TypeError:
                # Обратная совместимость: инструменты без аргумента timestamp.
                func(**args)
        finally:
            current_state.reset(token_state)
            current_agent_id.reset(token_agent)
            current_runner.reset(token_runner)

    def _schedule_next(self, agent_id: str, tools: list[str]) -> None:
        """Запланировать следующее пробуждение агента.

        Учитывает рабочее расписание: если время пробуждения выпадает
        на нерабочие часы, переносит на начало следующего рабочего дня.

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
        next_wake = self._work_schedule.next_work_time(next_wake)
        if next_wake <= self._end_time:
            self.scheduler.schedule(agent_id, next_wake)


    # ------------------------------------------------------------------
    # Периодические тики
    # ------------------------------------------------------------------

    def _try_world_tick(self) -> None:
        """Запустить генератор мировых событий каждые 8 симулированных часов."""
        if self._world_generator is None:
            return
        elapsed = (self.clock.now - self._last_world_tick).total_seconds()
        if elapsed < 8 * 3600:
            return

        self._last_world_tick = self.clock.now
        round_events = [
            {
                "agent_id": e.agent_id,
                "event_type": e.event_type,
                "payload": e.payload,
            }
            for e in self.state.event_log.all_events[-50:]
        ]

        try:
            result = self._world_generator.generate(
                self.state,
                self._turn_index,
                round_events,
                org_context=getattr(self._config, "narrative_context", ""),
            )
            for op in result.ops:
                apply_state_op(op, self.state, self._turn_index)
            if result.narrative:
                self.state.event_log.log(
                    event_type="world_event",
                    payload={"narrative": result.narrative},
                    timestamp=self.clock.iso(),
                )
        except Exception as exc:
            logger.warning("Ошибка мирового генератора: %s", exc)

    def _try_reputation_tick(self) -> None:
        """Пересчитать репутацию каждые 24 симулированных часа."""
        elapsed = (self.clock.now - self._last_reputation_tick).total_seconds()
        if elapsed < 24 * 3600:
            return

        self._last_reputation_tick = self.clock.now
        governance = self._config.governance.mode
        decay_factor = self._config.governance.reputation_decay

        for agent_id in self.state.agents:
            res = self.state.resources.get(agent_id)
            if res is not None:
                apply_maintenance(res)

        for agent_id, rep in self.state.reputation.items():
            before_level = int(rep.position_level)
            apply_decay(rep, decay_factor=decay_factor)

            cases_resolved = len(
                self.state.event_log.get_events(
                    event_type="case_resolved",
                    agent_id=agent_id,
                )
            )
            growth = compute_round_growth(rep, cases_resolved=cases_resolved)
            apply_growth(rep, growth)

            position_level = 0
            current_title = POSITION_THRESHOLDS[0][1]
            for idx, (threshold, title) in enumerate(POSITION_THRESHOLDS):
                if rep.score >= threshold:
                    current_title = title
                    position_level = idx

            rep.position_level = position_level
            if position_level > before_level:
                self.state.event_log.log(
                    event_type="position_promoted",
                    agent_id=agent_id,
                    payload={
                        "title": current_title,
                        "level": position_level,
                    },
                    timestamp=self.clock.iso(),
                )

            self.state.event_log.log(
                event_type="reputation_snapshot",
                agent_id=agent_id,
                payload={
                    "score": float(rep.score),
                    "frozen": bool(rep.frozen),
                    "title": current_title,
                },
                timestamp=self.clock.iso(),
            )

        # Трибуналы
        from magistry_sim.enums import GovernanceMode

        if governance in (GovernanceMode.G2, GovernanceMode.G3):
            reports = self.state.event_log.get_events(
                event_type="report_filed",
            )
            for report in reports:
                rec = report.payload.get("recommendation", "")
                case_id = report.payload.get("case_id", "")
                case = self.state.cases.get(case_id)
                if case is None:
                    continue
                if rec == "frozen":
                    owner_rep = self.state.reputation.get(case.owner_id)
                    if owner_rep and not owner_rep.frozen:
                        freeze(owner_rep)

        required_votes = max(1, self._config.governance.jury_size)
        for case in self.state.cases.values():
            if case.stage == "tribunal" and case.closed_at is None:
                if len(case.votes) >= required_votes:
                    case.stage = "verdict"
                    guilty = sum(
                        1 for v in case.votes
                        if v.verdict.strip().lower() == "виновен"
                    )
                    not_guilty = sum(
                        1 for v in case.votes
                        if v.verdict.strip().lower() == "невиновен"
                    )
                    verdict = "виновен" if guilty > not_guilty else "невиновен"
                    case.decision = verdict
                    self.state.event_log.log(
                        event_type="tribunal_verdict",
                        payload={
                            "case_id": case.id,
                            "verdict": verdict,
                            "guilty_votes": guilty,
                            "not_guilty_votes": not_guilty,
                        },
                        timestamp=self.clock.iso(),
                    )

    def _try_narrator_tick(self) -> None:
        """Запустить нарратора в конце каждого рабочего дня."""
        if self._narrator is None:
            return

        current_date = self.clock.now.date()
        end_hour = self._work_schedule.work_end_hour
        if self.clock.now.hour < end_hour:
            return
        if self._last_narrator_date == current_date:
            return

        self._last_narrator_date = current_date
        today_start = self.clock.now.replace(
            hour=self._work_schedule.work_start_hour,
            minute=0, second=0, microsecond=0,
        )
        events = [
            {
                "agent_id": e.agent_id,
                "event_type": e.event_type,
                "payload": e.payload,
            }
            for e in self.state.event_log.all_events
            if self._event_after(e, today_start)
        ]

        agent_ids = list(self.state.agents.keys())
        try:
            summary = self._narrator.summarize_round(
                round_num=self._turn_index,
                events=events,
                agent_ids=agent_ids,
                llm=self._llm,
            )
            self._round_summaries.append({
                "date": current_date.isoformat(),
                "events_summary": summary.events_summary,
                "key_decisions": summary.key_decisions,
                "tensions": summary.tensions,
                "agent_motivations": summary.agent_motivations,
            })
        except Exception as exc:
            logger.warning("Ошибка нарратора: %s", exc)

    def _event_after(self, event: Any, after: datetime) -> bool:
        """Проверить, произошло ли событие после указанного времени."""
        try:
            ts = datetime.fromisoformat(event.timestamp)
        except (ValueError, AttributeError):
            return False
        if (ts.tzinfo is None) != (after.tzinfo is None):
            return False
        return ts >= after

    def _try_memory_summarization(self, agent_id: str) -> None:
        """Запустить суммаризацию памяти агента при необходимости."""
        if not isinstance(self._runner, CognitiveAgentRunner):
            return
        stream = self._runner.get_or_create_memory(agent_id)
        if len(stream) > 100:
            stream.summarize_old(self._llm)

    # ------------------------------------------------------------------
    # Построение результата
    # ------------------------------------------------------------------

    def _build_result(self) -> AsyncSimulationResult:
        """Построить полный результат симуляции."""
        cases_data = {
            cid: case.model_dump()
            for cid, case in self.state.cases.items()
        }
        rep_data = {
            aid: {"score": r.score, "frozen": r.frozen}
            for aid, r in self.state.reputation.items()
        }
        msg_data = [m.model_dump() for m in self.state.messages]

        return AsyncSimulationResult(
            scenario_id=(
                self._config.id.value
                if hasattr(self._config.id, "value")
                else str(self._config.id)
            ),
            seed=self._config.seed,
            started_at=(
                self._config.start_time.isoformat()
                if self._config.start_time
                else ""
            ),
            ended_at=self.clock.iso(),
            events_count=len(self.state.event_log.all_events),
            state=self.state,
            cases=cases_data,
            final_reputation=rep_data,
            messages=msg_data,
            round_summaries=self._round_summaries,
            agents=[a.id for a in self._config.agents],
        )


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
