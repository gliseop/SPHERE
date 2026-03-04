"""Движок симуляции MAGISTRY-LC."""

from __future__ import annotations

import asyncio
import logging
import os
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
from .persona import chunk_text
from .state import AgentState, WorkItem, WorldState
from .tracing import TraceLog

from magistry_sim.llm.protocols import LLMProvider
from magistry_sim.llm.embeddings import EmbeddingProvider, create_embedding_provider
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

        # Memory: embeddings provider (по умолчанию mock — без ключей API).
        api_key = os.getenv(self.cfg.memory.embeddings_api_key_env) or None
        embedder: EmbeddingProvider | None = None
        try:
            embedder = create_embedding_provider(
                mock=bool(self.cfg.memory.embeddings_mock),
                model_name=self.cfg.memory.embeddings_model,
                api_key=api_key,
                base_url=self.cfg.memory.embeddings_base_url or self.cfg.llm.base_url,
            )
        except Exception as exc:
            logger.warning("Embeddings disabled (%s): %s", exc.__class__.__name__, exc)
            embedder = None

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
            # Bootstrap persona into long-term memory index.
            if agent.memory is not None:
                if agent.persona.summary.strip():
                    agent.memory.add_doc(
                        tick=state.tick,
                        kind="persona",
                        importance=9.0,
                        text=agent.persona.summary,
                        cfg=self.cfg.memory,
                        embedder=embedder,
                        meta={"source": "scenario", "part": "summary"},
                    )
                for i, chunk in enumerate(chunk_text(agent.persona.biography, max_chars=900)):
                    agent.memory.add_doc(
                        tick=state.tick,
                        kind="persona",
                        importance=8.0,
                        text=chunk,
                        cfg=self.cfg.memory,
                        embedder=embedder,
                        meta={"source": "scenario", "part": "biography", "chunk": i},
                    )
                for i, qa in enumerate(agent.persona.interview):
                    q = (qa.question or "").strip()
                    a = (qa.answer or "").strip()
                    if not q or not a:
                        continue
                    agent.memory.add_doc(
                        tick=state.tick,
                        kind="interview",
                        importance=7.0,
                        text=f"Q: {q}\nA: {a}",
                        cfg=self.cfg.memory,
                        embedder=embedder,
                        meta={"source": "scenario", "part": "interview", "index": i},
                    )
            runners[aid] = AgentRunner(
                llm=llm,
                runtime=self.cfg.runtime,
                memory=self.cfg.memory,
                embedder=embedder,
                temperature=self.cfg.llm.temperature,
            )

        # История событий, доступная для агентов (для MVP храним в памяти).
        events_history: list[Event] = []

        graph_app = None
        if self.cfg.runtime.use_langgraph:
            try:
                from .graphs import build_world_graph

                async def _gather_node(gs: dict) -> dict:
                    agent_order = self._agent_order(state=gs["world"], tick=gs["world"].tick)
                    proposed = await self._gather_actions(
                        state=gs["world"],
                        runners=runners,
                        events_history=gs["events_history"],
                        agent_order=agent_order,
                    )
                    return {"proposed": proposed}

                async def _apply_node(gs: dict) -> dict:
                    agent_order = self._agent_order(state=gs["world"], tick=gs["world"].tick)
                    tick_events = await self._apply_actions(
                        state=gs["world"],
                        arbiter=arbiter,
                        proposed=gs["proposed"],
                        event_log=event_log,
                        agent_order=agent_order,
                    )
                    return {"tick_events": tick_events}

                graph_app = build_world_graph(
                    gather_actions_node=_gather_node,
                    apply_actions_node=_apply_node,
                    debug=self.cfg.runtime.langgraph_debug,
                    checkpoint_path=self.artifacts.out_dir / "langgraph.sqlite",
                )
            except Exception as exc:
                logger.warning("LangGraph disabled (%s): %s", exc.__class__.__name__, exc)

        for tick in range(self.cfg.ticks):
            state.tick = tick
            logger.info("tick=%s", tick)

            agent_order = self._agent_order(state=state, tick=tick)

            if graph_app is not None:
                out = await graph_app.ainvoke({"world": state, "events_history": events_history})
                tick_events = list(out.get("tick_events") or [])
            else:
                # Сбор действий агентов параллельно.
                proposed = await self._gather_actions(
                    state=state,
                    runners=runners,
                    events_history=events_history,
                    agent_order=agent_order,
                )

                # Арбитраж + применение ops детерминированно.
                tick_events = await self._apply_actions(
                    state=state,
                    arbiter=arbiter,
                    proposed=proposed,
                    event_log=event_log,
                    agent_order=agent_order,
                )

            # DAO: закрытие голосований и применение position_change.
            dao_ops = dao.close_votes(state)
            tick_events.extend(self._apply_ops(state=state, ops=dao_ops, event_log=event_log, origin="dao"))

            # Обновить память агентов (feedback loop).
            await self._update_agent_memory(state=state, tick_events=tick_events, llm=llm, embedder=embedder)

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

    def _agent_order(self, *, state: WorldState, tick: int) -> list[str]:
        """Детерминированный порядок агентов на тик (использует seed)."""
        import random

        order = sorted(state.agents.keys())
        rnd = random.Random(int(self.cfg.seed) + int(tick))
        rnd.shuffle(order)
        return order

    def _init_state(self, *, event_log: EventLog) -> WorldState:
        state = WorldState(tick=0, registry=EntityRegistry())

        # Channels: first from scenario, then default public if missing.
        for ch in self.cfg.world.channels:
            if state.registry.exists(ch.channel_id):
                continue
            op = CreateEntityOp(
                entity_id=ch.channel_id,
                kind=EntityKind.CHANNEL,
                created_by=None,
                created_tick=0,
                meta={"title": ch.title},
            )
            event_log.extend(op.apply(state))

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

        for org in self.cfg.world.orgs:
            if state.registry.exists(org.org_id):
                continue
            op = CreateEntityOp(
                entity_id=org.org_id,
                kind=EntityKind.ORG,
                created_by=None,
                created_tick=0,
                meta={"title": org.title},
            )
            event_log.extend(op.apply(state))

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
        agent_order: list[str],
    ) -> dict[str, list[Action]]:
        async def _one(aid: str) -> tuple[str, list[Action]]:
            agent = state.agents[aid]
            visible = [ev for ev in events_history if event_visible_to_agent(ev, aid, internal=agent.internal)]
            acts = await runners[aid].propose_actions(agent=agent, state=state, visible_events=visible)
            return aid, acts

        order = list(agent_order) if agent_order else sorted(state.agents.keys())
        pairs = await asyncio.gather(*[_one(aid) for aid in order])
        return {aid: acts for aid, acts in pairs}

    async def _apply_actions(
        self,
        *,
        state: WorldState,
        arbiter: Arbiter,
        proposed: dict[str, list[Action]],
        event_log: EventLog,
        agent_order: list[str],
    ) -> list[Event]:
        tick_events: list[Event] = []

        # YAML-журнал строим один раз на тик и переиспользуем во всех `perform`.
        journal_yaml = state.journal_yaml()

        # Сначала — арбитраж (LLM только для perform), без изменения state.
        arbitration = await arbiter.arbitrate_tick(state=state, proposed=proposed, journal_yaml=journal_yaml)

        # Потом — детерминированное применение ops по порядку (agent_id, action_index).
        for aid in agent_order:
            if aid not in arbitration:
                continue
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
        ops: Iterable[StateOp],
        event_log: EventLog,
        origin: str,
    ) -> list[Event]:
        events: list[Event] = []
        for op in ops:
            try:
                applied = op.apply(state)
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

    async def _update_agent_memory(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        llm: LLMCaller,
        embedder: EmbeddingProvider | None,
    ) -> None:
        def _redact_numbers(obj):
            if isinstance(obj, dict):
                return {k: _redact_numbers(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_redact_numbers(v) for v in obj]
            if isinstance(obj, (int, float)):
                return "<num>"
            return obj

        def _event_to_text(ev: Event, agent_id: str) -> str:
            if ev.event_type == "message_sent":
                to_id = str(ev.payload.get("to_id") or "")
                private = bool(ev.payload.get("private", True))
                text = str(ev.payload.get("text") or "")
                if private:
                    if ev.actor_id == agent_id:
                        return f"Личное сообщение отправлено {to_id}: {text}"
                    return f"Личное сообщение от {ev.actor_id}: {text}"
                return f"Публичное сообщение {ev.actor_id} -> {to_id}: {text}"

            if ev.event_type == "arbiter_rejected":
                return f"Арбитр отклонил действие: {ev.payload.get('reason','')}"
            if ev.event_type == "arbiter_op_failed":
                return f"Операция провалилась: {_redact_numbers(ev.payload.get('error',{}))}"
            if ev.event_type == "vote_opened":
                return f"Открыто голосование {ev.payload.get('vote_id','')} за {ev.payload.get('target_agent_id','')} -> {ev.payload.get('new_title','')}"
            if ev.event_type == "vote_closed":
                return f"Голосование закрыто {ev.payload.get('vote_id','')} result={ev.payload.get('result','')}"
            if ev.event_type == "position_changed":
                return f"Должность изменена: {ev.payload.get('target_agent_id','')} -> {ev.payload.get('new_title','')}"
            if ev.event_type == "world_event":
                return f"Внешнее событие: {ev.payload.get('description','')}"

            # Фолбэк: тип события + компактный payload (без чисел).
            payload = _redact_numbers(ev.payload or {})
            return f"{ev.event_type}: {payload}"

        def _importance_for_event(ev: Event) -> float:
            if ev.event_type in ("arbiter_rejected", "arbiter_op_failed", "position_changed"):
                return 8.0
            if ev.event_type in ("vote_opened", "vote_closed"):
                return 7.0
            if ev.event_type == "message_sent":
                return 6.0
            if ev.event_type in ("work_item_created", "work_note_added", "work_proposal_submitted"):
                return 5.0
            if ev.event_type == "world_event":
                return 5.0
            return 3.0

        for aid, agent in state.agents.items():
            visible = [ev for ev in tick_events if event_visible_to_agent(ev, aid, internal=agent.internal)]
            for ev in visible:
                if agent.memory is None:
                    continue
                text = _event_to_text(ev, aid)
                if not text:
                    continue
                agent.memory.add_working(tick=state.tick, text=text)

                importance = _importance_for_event(ev)
                kind = "result" if ev.actor_id == aid else "observation"
                if importance >= 5.0:
                    agent.memory.add_doc(
                        tick=state.tick,
                        kind=kind,  # type: ignore[arg-type]
                        importance=importance,
                        text=text,
                        cfg=self.cfg.memory,
                        embedder=embedder,
                        meta={"event_type": ev.event_type},
                    )

            if agent.memory is not None:
                await agent.memory.maybe_summarize_working(
                    llm=llm,
                    language=self.cfg.runtime.language,
                    cfg=self.cfg.memory,
                    tick=state.tick,
                    temperature=self.cfg.llm.temperature,
                )


def default_artifacts(out_dir: str | Path) -> RunArtifacts:
    """Собрать пути артефактов по выходной директории."""
    d = Path(out_dir)
    return RunArtifacts(
        out_dir=d,
        events_path=d / "events.jsonl",
        trace_path=d / "trace.jsonl",
    )
