"""Движок симуляции MAGISTRY-LC."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .actions import Action
from .agent import AgentRunner, event_visible_to_agent
from .arbiter import Arbiter, ActionResult
from .config import ScenarioConfig
from .dao import DaoEngine
from .entities import EntityRecord, EntityRegistry
from .events import Event, EventLog
from .id_alloc import IdAllocator
from .ids import EntityKind, INTERNAL_AUDIENCE, PUBLIC_AUDIENCE, make_id
from .llm import LLMCaller, create_llm_provider
from .ops import CreateEntityOp, StateOp
from .state import AgentState, WorkItem, WorldState
from .tracing import TraceLog

from magistry_sim.llm.protocols import LLMProvider
from .worldgen import WorldGenerator


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunArtifacts:
    """Пути к артефактам прогона."""

    out_dir: Path
    events_path: Path
    trace_path: Path


@dataclass(slots=True)
class WorldEngine:
    """Основной orchestrator."""

    cfg: ScenarioConfig
    artifacts: RunArtifacts
    provider_override: LLMProvider | None = None

    def __post_init__(self) -> None:
        self.artifacts.out_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> WorldState:
        """Запустить симуляцию и вернуть финальный WorldState."""
        provider = self.provider_override or create_llm_provider(self.cfg.llm)
        trace = TraceLog(self.artifacts.trace_path, max_chars=self.cfg.llm.trace_max_chars)
        llm = LLMCaller(provider=provider, trace=trace)

        event_log = EventLog(self.artifacts.events_path)

        state = self._init_state(event_log=event_log)

        dao = DaoEngine(cfg=self.cfg.governance)
        id_alloc = IdAllocator()
        arbiter = Arbiter(
            llm=llm,
            governance=self.cfg.governance,
            id_alloc=id_alloc,
            dao=dao,
            temperature=self.cfg.llm.temperature,
        )
        worldgen = WorldGenerator(llm=llm, temperature=self.cfg.llm.temperature)

        runners: dict[str, AgentRunner] = {}
        for aid, agent in state.agents.items():
            runners[aid] = AgentRunner(llm=llm, runtime=self.cfg.runtime, temperature=self.cfg.llm.temperature)

        # История событий, доступная для агентов (для MVP храним в памяти).
        events_history: list[Event] = []

        graph_app = None
        if self.cfg.runtime.use_langgraph:
            try:
                from .graphs import build_world_graph

                async def _gather_node(gs: dict) -> dict:
                    proposed = await self._gather_actions(
                        state=gs["world"],
                        runners=runners,
                        events_history=gs["events_history"],
                    )
                    return {"proposed": proposed}

                async def _apply_node(gs: dict) -> dict:
                    tick_events = await self._apply_actions(
                        state=gs["world"],
                        arbiter=arbiter,
                        proposed=gs["proposed"],
                        event_log=event_log,
                    )
                    return {"tick_events": tick_events}

                graph_app = build_world_graph(
                    gather_actions_node=_gather_node,
                    apply_actions_node=_apply_node,
                    debug=self.cfg.runtime.langgraph_debug,
                )
            except Exception as exc:
                logger.warning("LangGraph disabled (%s): %s", exc.__class__.__name__, exc)

        for tick in range(self.cfg.ticks):
            state.tick = tick
            logger.info("tick=%s", tick)

            if graph_app is not None:
                out = await graph_app.ainvoke({"world": state, "events_history": events_history})
                tick_events = list(out.get("tick_events") or [])
            else:
                # Сбор действий агентов параллельно.
                proposed = await self._gather_actions(state=state, runners=runners, events_history=events_history)

                # Арбитраж + применение ops детерминированно.
                tick_events = await self._apply_actions(
                    state=state,
                    arbiter=arbiter,
                    proposed=proposed,
                    event_log=event_log,
                )

            # DAO: закрытие голосований и применение position_change.
            dao_ops = dao.close_votes(state)
            tick_events.extend(self._apply_ops(state=state, ops=dao_ops, event_log=event_log, origin="dao"))

            # Обновить память агентов (feedback loop).
            self._update_agent_memory(state=state, tick_events=tick_events)

            # Внешние события мира (без утечки промптов).
            if self.cfg.runtime.enable_worldgen and (tick % self.cfg.runtime.worldgen_every_ticks == 0):
                extra = await worldgen.generate(
                    tick=state.tick,
                    recent_events=tick_events,
                    language=self.cfg.runtime.language,
                )
                if extra:
                    event_log.extend(extra)
                    tick_events.extend(extra)

            events_history.extend(tick_events)
            # Ограничиваем историю для памяти.
            if len(events_history) > self.cfg.runtime.tick_events_history:
                events_history = events_history[-self.cfg.runtime.tick_events_history :]

        state.clamp_reputation()
        return state

    def _init_state(self, *, event_log: EventLog) -> WorldState:
        state = WorldState(tick=0, registry=EntityRegistry())

        # Channels: add default public channel if missing.
        public_channel_id = make_id(EntityKind.CHANNEL, "public")
        if not state.registry.exists(public_channel_id):
            op = CreateEntityOp(
                entity_id=public_channel_id,
                kind=EntityKind.CHANNEL,
                created_by=None,
                created_tick=0,
                meta={"title": "public"},
            )
            event_log.extend(op.apply(state))

        for ch in self.cfg.world.channels:
            op = CreateEntityOp(
                entity_id=ch.channel_id,
                kind=EntityKind.CHANNEL,
                created_by=None,
                created_tick=0,
                meta={"title": ch.title},
            )
            try:
                event_log.extend(op.apply(state))
            except Exception:
                pass

        for org in self.cfg.world.orgs:
            op = CreateEntityOp(
                entity_id=org.org_id,
                kind=EntityKind.ORG,
                created_by=None,
                created_tick=0,
                meta={"title": org.title},
            )
            try:
                event_log.extend(op.apply(state))
            except Exception:
                pass

        for a in self.cfg.agents:
            state.registry.register(
                EntityRecord(entity_id=a.agent_id, kind=EntityKind.AGENT, created_by=None, created_tick=0, meta={"name": a.name})
            )
            state.agents[a.agent_id] = AgentState(
                agent_id=a.agent_id,
                name=a.name,
                internal=a.internal,
                persona=a.persona,
                capabilities=list(a.capabilities),
                reputation=0.0,
                title=a.initial_title,
                wants_promotion=a.wants_promotion,
            )
            event_log.append(
                Event(
                    tick=0,
                    event_type="entity_created",
                    actor_id=None,
                    payload={"entity_id": a.agent_id, "kind": EntityKind.AGENT.value, "meta": {"name": a.name}},
                    audience=[INTERNAL_AUDIENCE],
                )
            )

        for w in self.cfg.world.work_items:
            state.registry.register(
                EntityRecord(entity_id=w.work_id, kind=EntityKind.WORK_ITEM, created_by=None, created_tick=0, meta={"work_type": w.work_type, "title": w.title})
            )
            state.work_items[w.work_id] = WorkItem(
                work_id=w.work_id,
                work_type=w.work_type,
                title=w.title,
                description=w.description,
                participants=list(w.participants),
            )
            event_log.append(
                Event(
                    tick=0,
                    event_type="work_item_created",
                    actor_id=None,
                    payload={"work_id": w.work_id, "work_type": w.work_type, "title": w.title, "description": w.description, "participants": list(w.participants)},
                    audience=[INTERNAL_AUDIENCE],
                )
            )

        return state

    async def _gather_actions(
        self,
        *,
        state: WorldState,
        runners: dict[str, AgentRunner],
        events_history: list[Event],
    ) -> dict[str, list[Action]]:
        async def _one(aid: str) -> tuple[str, list[Action]]:
            agent = state.agents[aid]
            visible = [ev for ev in events_history if event_visible_to_agent(ev, aid, internal=agent.internal)]
            acts = await runners[aid].propose_actions(agent=agent, state=state, visible_events=visible)
            return aid, acts

        tasks = [asyncio.create_task(_one(aid)) for aid in sorted(state.agents.keys())]
        results: dict[str, list[Action]] = {}
        for t in tasks:
            aid, acts = await t
            results[aid] = acts
        return results

    async def _apply_actions(
        self,
        *,
        state: WorldState,
        arbiter: Arbiter,
        proposed: dict[str, list[Action]],
        event_log: EventLog,
    ) -> list[Event]:
        tick_events: list[Event] = []

        # Сначала — арбитраж (может делать LLM для perform), но без изменения state.
        arbitration: dict[str, list[ActionResult]] = {}
        for aid in sorted(proposed.keys()):
            arbitration[aid] = await arbiter.arbitrate_actions(state=state, agent_id=aid, actions=proposed[aid])

        # Потом — детерминированное применение ops по порядку (agent_id, action_index).
        for aid in sorted(arbitration.keys()):
            for res in arbitration[aid]:
                action = proposed[aid][res.action_index] if res.action_index < len(proposed[aid]) else None
                if not res.approved:
                    ev = Event(
                        tick=state.tick,
                        event_type="arbiter_rejected",
                        actor_id=aid,
                        payload={"action_index": res.action_index, "reason": res.reason, "action": str(action)},
                        audience=[aid],
                    )
                    event_log.append(ev)
                    tick_events.append(ev)
                    continue

                ev_ok = Event(
                    tick=state.tick,
                    event_type="arbiter_approved",
                    actor_id=aid,
                    payload={"action_index": res.action_index, "reason": res.reason, "action": str(action), "ops": [type(o).__name__ for o in res.ops]},
                    audience=[INTERNAL_AUDIENCE],
                )
                event_log.append(ev_ok)
                tick_events.append(ev_ok)

                tick_events.extend(self._apply_ops(state=state, ops=res.ops, event_log=event_log, origin=f"{aid}:{res.action_index}"))

        return tick_events

    def _apply_ops(
        self,
        *,
        state: WorldState,
        ops: Iterable[object],
        event_log: EventLog,
        origin: str,
    ) -> list[Event]:
        events: list[Event] = []
        for op in ops:
            try:
                applied = op.apply(state)  # type: ignore[attr-defined]
                event_log.extend(applied)
                events.extend(applied)
            except Exception as exc:
                ev = Event(
                    tick=state.tick,
                    event_type="arbiter_op_failed",
                    actor_id=None,
                    payload={"origin": origin, "op": str(op), "error": {"type": exc.__class__.__name__, "message": str(exc)}},
                    audience=[INTERNAL_AUDIENCE],
                )
                event_log.append(ev)
                events.append(ev)
        return events

    def _update_agent_memory(self, *, state: WorldState, tick_events: list[Event]) -> None:
        for aid, agent in state.agents.items():
            visible = [ev for ev in tick_events if event_visible_to_agent(ev, aid, internal=agent.internal)]
            for ev in visible:
                if ev.event_type == "arbiter_rejected":
                    fact = f"[arbiter_rejected] {ev.payload.get('reason','')}"
                elif ev.event_type == "arbiter_op_failed":
                    fact = f"[arbiter_op_failed] {ev.payload.get('error',{})}"
                elif ev.event_type == "message_sent":
                    fact = f"[message_sent] to={ev.payload.get('to_id','')}"
                elif ev.event_type == "vote_opened":
                    fact = f"[vote_opened] vote={ev.payload.get('vote_id','')}"
                elif ev.event_type == "vote_closed":
                    fact = f"[vote_closed] vote={ev.payload.get('vote_id','')} result={ev.payload.get('result','')}"
                else:
                    fact = f"[{ev.event_type}]"
                # Дедуп и ограничение размера.
                if fact in agent.memory_events:
                    continue
                agent.memory_events.append(fact)
            if len(agent.memory_events) > 200:
                agent.memory_events = agent.memory_events[-200:]


def default_artifacts(out_dir: str | Path) -> RunArtifacts:
    """Собрать пути артефактов по выходной директории."""
    d = Path(out_dir)
    return RunArtifacts(
        out_dir=d,
        events_path=d / "events.jsonl",
        trace_path=d / "trace.jsonl",
    )
