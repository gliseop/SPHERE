"""Движок симуляции MAGISTRY-LC."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .actions import Action, SendMessageAction
from .agent import AgentRunner, event_visible_to_agent
from .arbiter import Arbiter, ActionResult
from .auditor import RuntimeAuditor
from .config import ScenarioConfig
from .dao import DaoEngine
from .embeddings import embed_texts_cached
from .entities import EntityRecord, EntityRegistry
from .evaluation import evaluate_run, save_evaluation
from .events import Event, EventLog
from .id_alloc import IdAllocator
from .ids import (
    EntityKind,
    INTERNAL_AUDIENCE,
    PUBLIC_AUDIENCE,
    make_id,
    make_unique_id,
    normalize_slug,
)
from .journal import WorldJournal
from .llm import LLMCaller, create_llm_provider
from .ops import CreateAgentOp, CreateEntityOp, SetReputationFreezeOp, StateOp
from .persona import (
    PersonaArtifact,
    PersonaGenerator,
    SocialGraphExtractor,
    SocialLink,
    chunk_text,
    social_link_match_key,
    social_link_name_key,
)
from .state import AgentState, WorkItem, WorldState
from .tracing import TraceLog
from .truth import TruthDetector, TruthLog
from .utils import redact_numbers
from .worldgen import SpawnSuggestion, WorldGenerator, WorldgenOutput

from .llm import EmbeddingProvider, LLMProvider, create_embedding_provider


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunArtifacts:
    """Пути к артефактам прогона."""

    out_dir: Path
    events_path: Path
    trace_path: Path
    truth_path: Path | None = None
    evaluation_path: Path | None = None


@dataclass(slots=True)
class WorldEngine:
    """Основной orchestrator."""

    cfg: ScenarioConfig
    artifacts: RunArtifacts
    provider_override: LLMProvider | None = None

    def __post_init__(self) -> None:
        self.artifacts.out_dir.mkdir(parents=True, exist_ok=True)
        if self.artifacts.truth_path is None:
            self.artifacts.truth_path = self.artifacts.out_dir / "truth.jsonl"
        if self.artifacts.evaluation_path is None:
            self.artifacts.evaluation_path = self.artifacts.out_dir / "evaluation.json"

    async def run(self) -> WorldState:
        """Запустить симуляцию и вернуть финальный WorldState."""
        provider = self.provider_override or create_llm_provider(self.cfg.llm)
        trace = TraceLog(self.artifacts.trace_path, max_chars=self.cfg.llm.trace_max_chars)
        llm = LLMCaller(provider=provider, trace=trace)

        event_log = EventLog(self.artifacts.events_path)
        truth_log = TruthLog(self.artifacts.truth_path)

        state = self._init_state(event_log=event_log)
        if self.cfg.runtime.enrich_personas:
            try:
                await self._enrich_personas(state=state, llm=llm)
            except Exception as exc:
                logger.warning(
                    "Persona enrichment disabled for this run (%s): %s",
                    exc.__class__.__name__,
                    exc,
                )
        if self.cfg.runtime.spawn_secondary:
            try:
                await self._spawn_secondary_agents(state=state, llm=llm, event_log=event_log)
            except Exception as exc:
                logger.warning(
                    "Secondary agent spawning disabled for this run (%s): %s",
                    exc.__class__.__name__,
                    exc,
                )
        journal = WorldJournal.from_state(state=state)
        truth_detector = TruthDetector(
            private_contact_window_ticks=self.cfg.governance.audit.private_contact_window_ticks
        )

        # Memory: embeddings provider (по умолчанию mock — без ключей API).
        env_key = self.cfg.memory.embeddings_api_key_env
        api_key = os.getenv(env_key) or None
        embedder: EmbeddingProvider | None = None
        if not self.cfg.memory.embeddings_mock and not api_key:
            logger.warning(
                "Embeddings enabled (embeddings_mock=false) but %s is not set; embeddings disabled (BM25-only).",
                env_key,
            )
        else:
            if self.cfg.memory.embeddings_mock and api_key:
                logger.warning(
                    "Embeddings in mock mode (embeddings_mock=true) while %s is set; set embeddings_mock=false to use real embeddings.",
                    env_key,
                )
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

        embed_cache: dict[str, list[float]] = {}

        dao = DaoEngine(cfg=self.cfg.governance)
        id_alloc = IdAllocator()
        arbiter = Arbiter(
            llm=llm,
            governance=self.cfg.governance,
            id_alloc=id_alloc,
            dao=dao,
            runtime=self.cfg.runtime,
            temperature=self.cfg.llm.temperature,
        )
        auditor = RuntimeAuditor(
            cfg=self.cfg.governance.audit,
            llm=llm if self.cfg.governance.audit.mode != "rules" else None,
        )
        worldgen = WorldGenerator(llm=llm, temperature=self.cfg.llm.temperature)

        runners: dict[str, AgentRunner] = {}
        for aid in sorted(state.agents.keys()):
            await self._register_agent_runner(
                agent_id=aid,
                state=state,
                runners=runners,
                llm=llm,
                embedder=embedder,
                embed_cache=embed_cache,
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
                        journal_yaml=journal.to_yaml(),
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
            journal.set_tick(tick)
            logger.info("tick=%s", tick)

            agent_order = self._agent_order(state=state, tick=tick)
            tick_events = self._expire_reputation_freezes(state=state, event_log=event_log)

            if graph_app is not None:
                out = await graph_app.ainvoke({"world": state, "events_history": events_history})
                tick_events.extend(list(out.get("tick_events") or []))
            else:
                # Сбор действий агентов параллельно.
                proposed, gather_errors = await self._gather_actions(
                    state=state,
                    runners=runners,
                    events_history=events_history,
                    agent_order=agent_order,
                )
                if gather_errors:
                    event_log.extend(gather_errors)
                    tick_events.extend(gather_errors)

                # Арбитраж + применение ops детерминированно.
                tick_events.extend(
                    await self._apply_actions(
                        state=state,
                        arbiter=arbiter,
                        proposed=proposed,
                        event_log=event_log,
                        agent_order=agent_order,
                        journal_yaml=journal.to_yaml(),
                    )
                )

            new_agents = self._detect_new_agents(state=state, runners=runners)
            if new_agents:
                await self._register_new_agents(
                    new_agent_ids=new_agents,
                    state=state,
                    runners=runners,
                    llm=llm,
                    embedder=embedder,
                    embed_cache=embed_cache,
                )

            # DAO: закрытие голосований и применение position_change.
            dao_ops = dao.close_votes(state)
            tick_events.extend(self._apply_ops(state=state, ops=dao_ops, event_log=event_log, origin="dao"))

            # Обновить память агентов (feedback loop).
            memory_errors = await self._update_agent_memory(
                state=state,
                tick_events=tick_events,
                llm=llm,
                embedder=embedder,
                embed_cache=embed_cache,
                event_log=event_log,
            )
            if memory_errors:
                tick_events.extend(memory_errors)

            # Внешние события мира (без утечки промптов).
            if self.cfg.runtime.enable_worldgen and (tick % self.cfg.runtime.worldgen_every_ticks == 0):
                try:
                    generated = await worldgen.generate(
                        tick=state.tick,
                        recent_events=tick_events,
                        language=self.cfg.runtime.language,
                    )
                except Exception as exc:
                    logger.warning("World generator failed on tick %s: %s", state.tick, exc)
                    generated = WorldgenOutput(
                        events=[
                            Event(
                                tick=state.tick,
                                event_type="worldgen_llm_error",
                                actor_id=None,
                                payload={"error": {"type": exc.__class__.__name__, "message": str(exc)}},
                                audience=[INTERNAL_AUDIENCE],
                            )
                        ],
                        spawns=[],
                    )
                if generated.events:
                    event_log.extend(generated.events)
                    tick_events.extend(generated.events)
                if generated.spawns:
                    spawn_events = self._apply_worldgen_spawns(
                        state=state,
                        spawns=generated.spawns,
                        event_log=event_log,
                    )
                    if spawn_events:
                        tick_events.extend(spawn_events)
                        created_ids = self._detect_new_agents(state=state, runners=runners)
                        if created_ids:
                            await self._register_new_agents(
                                new_agent_ids=created_ids,
                                state=state,
                                runners=runners,
                                llm=llm,
                                embedder=embedder,
                                embed_cache=embed_cache,
                            )

            truth_window = int(self.cfg.governance.audit.lookback_events)
            truth_recent = (events_history + tick_events)[-truth_window:] if truth_window > 0 else list(tick_events)
            truth_records = truth_detector.detect_tick(
                state=state,
                tick_events=tick_events,
                recent_events=truth_recent,
            )
            if truth_records:
                truth_log.extend(truth_records)

            if self.cfg.governance.audit.enabled:
                audit_window = int(self.cfg.governance.audit.lookback_events)
                combined_events = events_history + tick_events
                audit_recent = combined_events[-audit_window:] if audit_window > 0 else list(tick_events)
                try:
                    audit_outcome = await auditor.inspect_tick(
                        state=state,
                        tick_events=tick_events,
                        recent_events=audit_recent,
                    )
                except Exception as exc:
                    logger.warning("Runtime auditor failed on tick %s: %s", state.tick, exc)
                    audit_outcome = None
                    audit_err = Event(
                        tick=state.tick,
                        event_type="audit_runtime_error",
                        actor_id=None,
                        payload={"error": {"type": exc.__class__.__name__, "message": str(exc)}},
                        audience=[INTERNAL_AUDIENCE],
                    )
                    event_log.append(audit_err)
                    tick_events.append(audit_err)
                if audit_outcome is not None:
                    if audit_outcome.events:
                        event_log.extend(audit_outcome.events)
                        tick_events.extend(audit_outcome.events)
                    if audit_outcome.ops:
                        tick_events.extend(
                            self._apply_ops(
                                state=state,
                                ops=audit_outcome.ops,
                                event_log=event_log,
                                origin="runtime_audit",
                            )
                        )

            journal.apply_events(state=state, events=tick_events)

            events_history.extend(tick_events)
            # Ограничиваем историю для памяти.
            if len(events_history) > self.cfg.runtime.tick_events_history:
                events_history = events_history[-self.cfg.runtime.tick_events_history :]

        state.clamp_reputation()
        if self.artifacts.truth_path is not None and self.artifacts.evaluation_path is not None:
            summary = evaluate_run(
                events_path=self.artifacts.events_path,
                truth_path=self.artifacts.truth_path,
            )
            save_evaluation(summary, self.artifacts.evaluation_path)
        return state

    def _agent_order(self, *, state: WorldState, tick: int) -> list[str]:
        """Детерминированный порядок агентов на тик (использует seed)."""
        order = sorted(state.agents.keys())
        rnd = random.Random(int(self.cfg.seed) + int(tick))
        rnd.shuffle(order)
        return order

    async def _bootstrap_agent_memory(
        self,
        *,
        agent: AgentState,
        tick: int,
        embedder: EmbeddingProvider | None,
        embed_cache: dict[str, list[float]],
    ) -> None:
        if agent.memory is None:
            return

        docs_to_add: list[tuple[str, float, str, dict[str, Any]]] = []
        if agent.persona.summary.strip():
            docs_to_add.append(
                ("persona", 9.0, agent.persona.summary, {"source": "scenario", "part": "summary"})
            )
        for i, chunk in enumerate(chunk_text(agent.persona.biography, max_chars=900)):
            docs_to_add.append(
                ("persona", 8.0, chunk, {"source": "scenario", "part": "biography", "chunk": i})
            )
        for i, qa in enumerate(agent.persona.interview):
            q = (qa.question or "").strip()
            a = (qa.answer or "").strip()
            if not q or not a:
                continue
            docs_to_add.append(
                (
                    "interview",
                    7.0,
                    f"Q: {q}\nA: {a}",
                    {"source": "scenario", "part": "interview", "index": i},
                )
            )

        if not docs_to_add:
            return

        texts = [t for _, _, t, _ in docs_to_add]
        embeddings = [[] for _ in texts]
        if embedder is not None:
            embeddings = await embed_texts_cached(
                embedder,
                texts,
                cache=embed_cache,
                batch_size=self.cfg.memory.embeddings_batch_size,
            )
        for (kind, importance, text, meta), emb in zip(docs_to_add, embeddings, strict=False):
            agent.memory.add_doc(
                tick=tick,
                kind=kind,  # type: ignore[arg-type]
                importance=importance,
                text=text,
                cfg=self.cfg.memory,
                embedding=emb,
                meta=meta,
            )

    async def _register_agent_runner(
        self,
        *,
        agent_id: str,
        state: WorldState,
        runners: dict[str, AgentRunner],
        llm: LLMCaller,
        embedder: EmbeddingProvider | None,
        embed_cache: dict[str, list[float]],
    ) -> None:
        agent = state.agents.get(agent_id)
        if agent is None:
            return
        await self._bootstrap_agent_memory(
            agent=agent,
            tick=state.tick,
            embedder=embedder,
            embed_cache=embed_cache,
        )
        runners[agent_id] = AgentRunner(
            llm=llm,
            runtime=self.cfg.runtime,
            memory=self.cfg.memory,
            embedder=embedder,
            temperature=self.cfg.llm.temperature,
        )

    @staticmethod
    def _detect_new_agents(
        *,
        state: WorldState,
        runners: dict[str, AgentRunner],
    ) -> list[str]:
        return sorted([aid for aid in state.agents.keys() if aid not in runners])

    def _attach_relation_memory(
        self,
        *,
        agent: AgentState,
        tick: int,
        text: str,
        importance: float = 8.5,
    ) -> None:
        if agent.memory is None:
            return
        agent.memory.add_working(tick=tick, text=text)
        agent.memory.add_doc(
            tick=tick,
            kind="persona",
            importance=importance,
            text=text,
            cfg=self.cfg.memory,
            embedding=[],
            meta={"source": "social_link"},
        )

    async def _generate_personas_batch(
        self,
        *,
        state: WorldState,
        llm: LLMCaller,
        agent_ids: list[str],
        mode: str,
    ) -> dict[str, PersonaArtifact]:
        if not agent_ids:
            return {}

        generator = PersonaGenerator(llm=llm, temperature=self.cfg.llm.temperature)

        async def _one(agent_id: str) -> tuple[str, PersonaArtifact | None]:
            agent = state.agents[agent_id]
            try:
                if mode == "core":
                    persona = await generator.generate_core(
                        agent_id=agent.agent_id,
                        name=agent.name,
                        internal=agent.internal,
                        persona_hint=agent.persona.summary,
                        scenario_description=self.cfg.description,
                        language=self.cfg.runtime.language,
                    )
                else:
                    persona = await generator.generate(
                        agent_id=agent.agent_id,
                        name=agent.name,
                        internal=agent.internal,
                        persona_hint=agent.persona.summary,
                        scenario_description=self.cfg.description,
                        language=self.cfg.runtime.language,
                    )
                return agent_id, persona
            except Exception as exc:
                logger.warning("Persona enrichment failed for %s: %s", agent_id, exc)
                return agent_id, None

        results = await asyncio.gather(*[_one(aid) for aid in agent_ids])
        return {aid: persona for aid, persona in results if persona is not None}

    async def _spawn_secondary_agents(
        self,
        *,
        state: WorldState,
        llm: LLMCaller,
        event_log: EventLog,
    ) -> list[str]:
        if not self.cfg.runtime.spawn_secondary:
            return []
        if self.cfg.runtime.max_secondary_per_agent <= 0:
            return []
        if len(state.agents) >= self.cfg.runtime.max_agents:
            return []

        extractor = SocialGraphExtractor(llm=llm, temperature=self.cfg.llm.temperature)
        primary_ids = sorted(state.agents.keys())
        existing_names = [state.agents[aid].name for aid in primary_ids]

        async def _one(agent_id: str) -> tuple[str, list[SocialLink]]:
            agent = state.agents[agent_id]
            try:
                links = await extractor.extract(
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    persona=agent.persona,
                    existing_agent_names=existing_names,
                    max_links=self.cfg.runtime.max_secondary_per_agent,
                    scenario_description=self.cfg.description,
                    language=self.cfg.runtime.language,
                )
            except Exception as exc:
                logger.warning("Social graph extraction failed for %s: %s", agent_id, exc)
                links = []
            return agent_id, links

        extraction = await asyncio.gather(*[_one(aid) for aid in primary_ids])
        buckets: dict[str, dict[str, Any]] = {}
        order = 0
        for primary_id, links in extraction:
            primary = state.agents[primary_id]
            for link in links:
                raw_key = (
                    social_link_name_key(link.name)
                    or social_link_name_key(link.relation)
                    or normalize_slug(link.name, fallback="person")
                )
                key = social_link_match_key(raw_key, list(buckets.keys())) or raw_key
                bucket = buckets.get(key)
                if bucket is None:
                    bucket = {
                        "order": order,
                        "count": 0,
                        "link": link,
                        "sources": [],
                    }
                    buckets[key] = bucket
                bucket["count"] = int(bucket["count"]) + 1
                bucket["sources"].append((primary_id, primary.name, link.relation))
                order += 1

        if not buckets:
            return []

        ranked = sorted(
            buckets.values(),
            key=lambda item: (-int(item["count"]), int(item["order"]), str(item["link"].name)),
        )
        remaining_slots = max(0, self.cfg.runtime.max_agents - len(state.agents))
        if remaining_slots <= 0:
            return []

        existing_ids = set(state.registry.list_ids()) | set(state.agents.keys())
        created_meta: dict[str, dict[str, Any]] = {}
        created_ids: list[str] = []
        for bucket in ranked[:remaining_slots]:
            link: SocialLink = bucket["link"]
            entity_id = make_unique_id(
                EntityKind.AGENT,
                f"sec_{link.name}",
                existing_ids=existing_ids,
                fallback="sec_agent",
            )
            existing_ids.add(entity_id)
            capabilities = Arbiter._sanitize_spawn_capabilities(
                list(link.capabilities),
                internal=bool(link.internal),
            )
            op = CreateAgentOp(
                entity_id=entity_id,
                name=link.name,
                internal=bool(link.internal),
                persona_hint=link.persona_hint,
                capabilities=capabilities,
                created_by=None,
                created_tick=state.tick,
            )
            event_log.extend(op.apply(state))
            created_ids.append(entity_id)
            created_meta[entity_id] = {
                "link": link,
                "sources": list(bucket["sources"]),
            }

        if not created_ids:
            return []

        if self.cfg.runtime.enrich_personas:
            enriched = await self._generate_personas_batch(
                state=state,
                llm=llm,
                agent_ids=created_ids,
                mode="core",
            )
            for aid, persona in enriched.items():
                state.agents[aid].persona = persona

        for aid in created_ids:
            secondary = state.agents[aid]
            meta = created_meta[aid]
            for primary_id, primary_name, relation in meta["sources"]:
                primary = state.agents.get(primary_id)
                if primary is not None:
                    self._attach_relation_memory(
                        agent=primary,
                        tick=state.tick,
                        text=f"{relation}: {secondary.name} ({secondary.agent_id})",
                    )
                self._attach_relation_memory(
                    agent=secondary,
                    tick=state.tick,
                    text=f"Связь: {relation} агента {primary_name} ({primary_id})",
                )

        logger.info("Spawned %d secondary agents before tick 0", len(created_ids))
        return created_ids

    async def _register_new_agents(
        self,
        *,
        new_agent_ids: list[str],
        state: WorldState,
        runners: dict[str, AgentRunner],
        llm: LLMCaller,
        embedder: EmbeddingProvider | None,
        embed_cache: dict[str, list[float]],
    ) -> list[str]:
        pending = [aid for aid in sorted(new_agent_ids) if aid in state.agents and aid not in runners]
        if not pending:
            return []

        if self.cfg.runtime.enrich_personas:
            to_enrich = [aid for aid in pending if not state.agents[aid].persona.biography.strip()]
            enriched = await self._generate_personas_batch(
                state=state,
                llm=llm,
                agent_ids=to_enrich,
                mode=self.cfg.runtime.persona_enrich_mode,
            )
            for aid, persona in enriched.items():
                state.agents[aid].persona = persona

        for aid in pending:
            await self._register_agent_runner(
                agent_id=aid,
                state=state,
                runners=runners,
                llm=llm,
                embedder=embedder,
                embed_cache=embed_cache,
            )
        logger.info("Registered %d new runtime agents", len(pending))
        return pending

    def _apply_worldgen_spawns(
        self,
        *,
        state: WorldState,
        spawns: list[SpawnSuggestion],
        event_log: EventLog,
    ) -> list[Event]:
        if not self.cfg.runtime.allow_runtime_spawn:
            return []
        if not spawns:
            return []

        current_count = len(state.agents)
        existing_ids = set(state.registry.list_ids()) | set(state.agents.keys())
        ops: list[StateOp] = []
        for spawn in spawns:
            if current_count + len(ops) >= self.cfg.runtime.max_agents:
                break
            slug = normalize_slug(spawn.slug, fallback=spawn.name or "spawned")
            entity_id = make_id(EntityKind.AGENT, slug)
            if entity_id in existing_ids:
                continue
            existing_ids.add(entity_id)
            ops.append(
                CreateAgentOp(
                    entity_id=entity_id,
                    name=spawn.name,
                    internal=bool(spawn.internal),
                    persona_hint=spawn.persona_hint,
                    capabilities=Arbiter._sanitize_spawn_capabilities([], internal=bool(spawn.internal)),
                    created_by=None,
                    created_tick=state.tick,
                )
            )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="worldgen_spawn")

    def _personas_cache_path(self) -> Path:
        return self.artifacts.out_dir / "personas.json"

    def _personas_cache_input(self, *, state: WorldState) -> dict[str, Any]:
        agents = []
        for aid in sorted(state.agents.keys()):
            agent = state.agents[aid]
            agents.append(
                {
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "internal": bool(agent.internal),
                    "persona_summary": (agent.persona.summary or "").strip(),
                }
            )
        return {
            "seed": int(self.cfg.seed),
            "language": self.cfg.runtime.language,
            "persona_enrich_mode": self.cfg.runtime.persona_enrich_mode,
            "llm_model": self.cfg.llm.model,
            "llm_base_url": self.cfg.llm.base_url or "",
            "scenario_description": self.cfg.description,
            "agents": agents,
        }

    @staticmethod
    def _personas_cache_fingerprint(cache_input: dict[str, Any]) -> str:
        data = json.dumps(cache_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _load_personas_cache(
        self, *, state: WorldState, cache_input: dict[str, Any]
    ) -> dict[str, PersonaArtifact]:
        path = self._personas_cache_path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        meta = raw.get("meta")
        personas_raw = raw.get("personas")
        if not isinstance(meta, dict) or not isinstance(personas_raw, dict):
            return {}
        expected = self._personas_cache_fingerprint(cache_input)
        if str(meta.get("fingerprint") or "") != expected:
            return {}

        loaded: dict[str, PersonaArtifact] = {}
        for aid in sorted(state.agents.keys()):
            item = personas_raw.get(aid)
            if item is None:
                continue
            try:
                loaded[aid] = PersonaArtifact.model_validate(item)
            except Exception:
                continue
        return loaded

    def _save_personas_cache(
        self, *, cache_input: dict[str, Any], personas: dict[str, PersonaArtifact]
    ) -> None:
        path = self._personas_cache_path()
        payload = {
            "meta": {
                "version": 1,
                "fingerprint": self._personas_cache_fingerprint(cache_input),
                "input": cache_input,
            },
            "personas": {
                aid: persona.model_dump(mode="json")
                for aid, persona in sorted(personas.items())
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def _enrich_personas(self, *, state: WorldState, llm: LLMCaller) -> None:
        to_enrich_ids = [
            aid for aid, agent in sorted(state.agents.items()) if not agent.persona.biography.strip()
        ]
        if not to_enrich_ids:
            return

        cache_input = self._personas_cache_input(state=state)
        cached = self._load_personas_cache(state=state, cache_input=cache_input)

        pending: list[str] = []
        for aid in to_enrich_ids:
            cached_persona = cached.get(aid)
            if cached_persona is None or not cached_persona.biography.strip():
                pending.append(aid)
                continue
            state.agents[aid].persona = cached_persona

        if not pending:
            logger.info(
                "Loaded persona cache for %d agents from %s",
                len(to_enrich_ids),
                self._personas_cache_path(),
            )
            return

        enriched = await self._generate_personas_batch(
            state=state,
            llm=llm,
            agent_ids=pending,
            mode=self.cfg.runtime.persona_enrich_mode,
        )
        for aid, persona in enriched.items():
            state.agents[aid].persona = persona

        all_enriched = all(state.agents[aid].persona.biography.strip() for aid in to_enrich_ids)
        if not all_enriched:
            return
        try:
            personas = {aid: agent.persona for aid, agent in state.agents.items()}
            self._save_personas_cache(cache_input=cache_input, personas=personas)
        except Exception as exc:
            logger.warning("Failed to write personas cache: %s", exc)

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
            event_log.extend(
                [
                    Event(
                        tick=0,
                        event_type="entity_created",
                        actor_id=None,
                        payload={"entity_id": a.agent_id, "kind": EntityKind.AGENT.value, "meta": {"name": a.name}},
                        audience=[INTERNAL_AUDIENCE],
                    ),
                    Event(
                        tick=0,
                        event_type="reputation_snapshot",
                        actor_id=None,
                        payload={
                            "target_agent_id": a.agent_id,
                            "score": state.agents[a.agent_id].reputation,
                            "frozen": state.agents[a.agent_id].reputation_frozen,
                            "frozen_until_tick": state.agents[a.agent_id].reputation_frozen_until_tick,
                            "title": state.agents[a.agent_id].title,
                        },
                        audience=[INTERNAL_AUDIENCE],
                    ),
                ]
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
    ) -> tuple[dict[str, list[Action]], list[Event]]:
        async def _one(aid: str) -> tuple[str, list[Action], Event | None]:
            agent = state.agents[aid]
            visible = [ev for ev in events_history if event_visible_to_agent(ev, aid, internal=agent.internal)]
            try:
                acts = await runners[aid].propose_actions(agent=agent, state=state, visible_events=visible)
                return aid, acts, None
            except Exception as exc:
                logger.warning("Agent %s propose_actions failed on tick %s: %s", aid, state.tick, exc)
                return (
                    aid,
                    [],
                    Event(
                        tick=state.tick,
                        event_type="agent_llm_error",
                        actor_id=aid,
                        payload={"stage": "propose_actions", "error": {"type": exc.__class__.__name__, "message": str(exc)}},
                        audience=[INTERNAL_AUDIENCE],
                    ),
                )

        order = list(agent_order) if agent_order else sorted(state.agents.keys())
        pairs = await asyncio.gather(*[_one(aid) for aid in order])
        gathered: dict[str, list[Action]] = {}
        errors: list[Event] = []
        for aid, acts, err in pairs:
            gathered[aid] = acts
            if err is not None:
                errors.append(err)
        return gathered, errors

    async def _apply_actions(
        self,
        *,
        state: WorldState,
        arbiter: Arbiter,
        proposed: dict[str, list[Action]],
        event_log: EventLog,
        agent_order: list[str],
        journal_yaml: str,
    ) -> list[Event]:
        tick_events: list[Event] = []

        # Сначала — арбитраж (LLM только для perform), без изменения state.
        try:
            arbitration = await arbiter.arbitrate_tick(state=state, proposed=proposed, journal_yaml=journal_yaml)
        except Exception as exc:
            logger.warning("Arbiter failed on tick %s: %s", state.tick, exc)
            ev = Event(
                tick=state.tick,
                event_type="arbiter_llm_error",
                actor_id=None,
                payload={"stage": "arbitrate_tick", "error": {"type": exc.__class__.__name__, "message": str(exc)}},
                audience=[INTERNAL_AUDIENCE],
            )
            event_log.append(ev)
            tick_events.append(ev)
            return tick_events

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
                        payload={
                            "action_index": res.action_index,
                            "reason": res.reason,
                            "action": self._event_action_repr(action),
                        },
                        audience=[aid],
                    )
                    event_log.append(ev)
                    tick_events.append(ev)
                    continue

                ev_ok = Event(
                    tick=state.tick,
                    event_type="arbiter_approved",
                    actor_id=aid,
                    payload={
                        "action_index": res.action_index,
                        "reason": res.reason,
                        "action": self._event_action_repr(action),
                        "ops": [type(o).__name__ for o in res.ops],
                    },
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

    def _expire_reputation_freezes(
        self,
        *,
        state: WorldState,
        event_log: EventLog,
    ) -> list[Event]:
        """Снять истёкшие заморозки репутации в начале тика."""
        ops: list[StateOp] = []
        for aid in sorted(state.agents.keys()):
            agent = state.agents[aid]
            until_tick = agent.reputation_frozen_until_tick
            if not agent.reputation_frozen or until_tick is None:
                continue
            if state.tick < until_tick:
                continue
            ops.append(
                SetReputationFreezeOp(
                    actor_id=None,
                    target_agent_id=aid,
                    frozen=False,
                    reason="freeze_duration_elapsed",
                )
            )
        if not ops:
            return []
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="freeze_expire")

    @staticmethod
    def _event_action_repr(action: Action | None) -> str:
        """Безопасная строка действия для event payload."""
        if action is None:
            return ""
        action_payload = action.model_dump(mode="python")
        if isinstance(action, SendMessageAction) and bool(action_payload.get("private", True)):
            text = str(action_payload.get("text") or "")
            action_payload["text"] = "<redacted>"
            action_payload["text_len"] = len(text)
        return str(action_payload)

    async def _update_agent_memory(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        llm: LLMCaller,
        embedder: EmbeddingProvider | None,
        embed_cache: dict[str, list[float]],
        event_log: EventLog,
    ) -> list[Event]:
        errors: list[Event] = []

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
                return f"Операция провалилась: {redact_numbers(ev.payload.get('error',{}))}"
            if ev.event_type == "vote_opened":
                return f"Открыто голосование {ev.payload.get('vote_id','')} за {ev.payload.get('target_agent_id','')} -> {ev.payload.get('new_title','')}"
            if ev.event_type == "vote_closed":
                return f"Голосование закрыто {ev.payload.get('vote_id','')} result={ev.payload.get('result','')}"
            if ev.event_type == "position_changed":
                return f"Должность изменена: {ev.payload.get('target_agent_id','')} -> {ev.payload.get('new_title','')}"
            if ev.event_type == "audit_flagged":
                return f"Аудит пометил агента {ev.payload.get('target_agent_id','')}: {ev.payload.get('violation_type','')}"
            if ev.event_type == "audit_case_opened":
                return f"Открыт аудит-кейс {ev.payload.get('case_id','')} для {ev.payload.get('target_agent_id','')}"
            if ev.event_type == "audit_escalated":
                return f"Аудит эскалировал кейс {ev.payload.get('case_id','')} route={ev.payload.get('route','')}"
            if ev.event_type == "reputation_frozen":
                return f"Репутация заморожена для {ev.payload.get('target_agent_id','')}"
            if ev.event_type == "reputation_unfrozen":
                return f"Репутация разморожена для {ev.payload.get('target_agent_id','')}"
            if ev.event_type == "world_event":
                return f"Внешнее событие: {ev.payload.get('description','')}"

            # Фолбэк: тип события + компактный payload (без чисел).
            payload = redact_numbers(ev.payload or {})
            return f"{ev.event_type}: {payload}"

        def _importance_for_event(ev: Event) -> float:
            return float(
                self.cfg.memory.importance_by_event.get(
                    ev.event_type, self.cfg.memory.importance_default
                )
            )

        docs_to_embed: list[tuple[str, float, str, str, dict]] = []

        for aid, agent in state.agents.items():
            if agent.memory is None:
                continue
            visible = [ev for ev in tick_events if event_visible_to_agent(ev, aid, internal=agent.internal)]
            for ev in visible:
                text = _event_to_text(ev, aid)
                if not text:
                    continue
                agent.memory.add_working(tick=state.tick, text=text)

                importance = _importance_for_event(ev)
                kind = "result" if ev.actor_id == aid else "observation"
                if importance >= self.cfg.memory.importance_threshold:
                    docs_to_embed.append(
                        (
                            aid,
                            importance,
                            kind,
                            text,
                            {"event_type": ev.event_type},
                        )
                    )

        if docs_to_embed:
            texts = [t for _, _, _, t, _ in docs_to_embed]
            embeddings = [[] for _ in texts]
            if embedder is not None:
                try:
                    embeddings = await embed_texts_cached(
                        embedder,
                        texts,
                        cache=embed_cache,
                        batch_size=self.cfg.memory.embeddings_batch_size,
                    )
                except Exception as exc:
                    logger.warning("Memory embeddings failed on tick %s: %s", state.tick, exc)
                    ev = Event(
                        tick=state.tick,
                        event_type="memory_embedding_error",
                        actor_id=None,
                        payload={"error": {"type": exc.__class__.__name__, "message": str(exc)}},
                        audience=[INTERNAL_AUDIENCE],
                    )
                    event_log.append(ev)
                    errors.append(ev)
            for (aid, importance, kind, text, meta), emb in zip(
                docs_to_embed, embeddings, strict=False
            ):
                mem = state.agents[aid].memory
                if mem is None:
                    continue
                mem.add_doc(
                    tick=state.tick,
                    kind=kind,  # type: ignore[arg-type]
                    importance=importance,
                    text=text,
                    cfg=self.cfg.memory,
                    embedding=emb,
                    meta=meta,
                )

        for agent in state.agents.values():
            if agent.memory is None:
                continue
            try:
                await agent.memory.maybe_summarize_working(
                    llm=llm,
                    language=self.cfg.runtime.language,
                    cfg=self.cfg.memory,
                    tick=state.tick,
                    temperature=self.cfg.llm.temperature,
                )
            except Exception as exc:
                logger.warning("Memory summarization failed for %s on tick %s: %s", agent.agent_id, state.tick, exc)
                ev = Event(
                    tick=state.tick,
                    event_type="memory_llm_error",
                    actor_id=agent.agent_id,
                    payload={"stage": "summarize_working", "error": {"type": exc.__class__.__name__, "message": str(exc)}},
                    audience=[INTERNAL_AUDIENCE],
                )
                event_log.append(ev)
                errors.append(ev)

        return errors


def default_artifacts(out_dir: str | Path) -> RunArtifacts:
    """Собрать пути артефактов по выходной директории."""
    d = Path(out_dir)
    return RunArtifacts(
        out_dir=d,
        events_path=d / "events.jsonl",
        trace_path=d / "trace.jsonl",
        truth_path=d / "truth.jsonl",
        evaluation_path=d / "evaluation.json",
    )
