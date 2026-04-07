"""Движок симуляции SPHERE-LC."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from .evaluation import augment_evaluation_with_semantic_judge, evaluate_run, save_evaluation
from .events import Event, EventLog
from .fidelity import augment_fidelity_with_semantic_judge, evaluate_fidelity, save_fidelity
from .governance_modes import infer_builtin_governance_mode
from .history_markdown import write_world_history_markdown
from .id_alloc import IdAllocator
from .ids import (
    EntityKind,
    INTERNAL_AUDIENCE,
    PUBLIC_AUDIENCE,
    make_id,
    make_unique_id,
    parse_typed_id,
    normalize_slug,
)
from .journal import WorldJournal
from .llm import LLMCaller, create_llm_provider
from .oracle import FreeformTruthRecorder, save_freeform_truth
from .ops import (
    CreateArtifactOp,
    CreateAgentOp,
    CreateEntityOp,
    CreateWorkItemOp,
    ModifyReputationOp,
    AddInformationSignalOp,
    ResolvePendingInteractionOp,
    SetReputationFreezeOp,
    StateOp,
    UpdateInformationClimateOp,
    UpsertPendingInteractionOp,
    UpsertInformalLinkOp,
    UpdateInstitutionRegimeOp,
    UpdateResourcePoolOp,
    UpdateArtifactOp,
    UpdateZoneStateOp,
)
from .persona import (
    INTERVIEW_QUESTIONS_V2,
    PersonaArtifact,
    PersonaGenerator,
    SocialGraphExtractor,
    SocialLink,
    chunk_text,
    social_link_match_key,
    social_link_name_key,
)
from .state import AgentState, WorkItem, WorldState
from .state import (
    EnvironmentState,
    InformationClimateState,
    InformalLinkState,
    InstitutionRegimeState,
    PendingInteractionState,
    ResourcePoolState,
    ZoneState,
    informal_link_key,
)
from .tracing import TraceLog
from .truth import TruthDetector, TruthLog
from .utils import (
    looks_like_machine_name,
    normalize_agent_display_name,
    redact_numbers,
)
from .worldgen import (
    AgentDailyContext,
    EnvironmentUpdates,
    SceneHook,
    SpawnSuggestion,
    WorldGenerator,
    WorldgenOutput,
)

from .llm import EmbeddingProvider, LLMProvider, create_embedding_provider


logger = logging.getLogger(__name__)

_SAME_TICK_PENDING_CATEGORIES = frozenset(
    {
        "reply",
        "artifact_follow_up",
        "resource_pressure",
    }
)


@dataclass(slots=True)
class RunArtifacts:
    """Пути к артефактам прогона."""

    out_dir: Path
    events_path: Path
    trace_path: Path
    scenario_path: Path | None = None
    names_path: Path | None = None
    status_path: Path | None = None
    truth_path: Path | None = None
    truth_freeform_path: Path | None = None
    evaluation_path: Path | None = None
    fidelity_path: Path | None = None
    summary_path: Path | None = None
    environment_summary_path: Path | None = None
    environment_timeline_path: Path | None = None
    perf_summary_path: Path | None = None
    world_history_path: Path | None = None


@dataclass(slots=True)
class WorldEngine:
    """Основной orchestrator."""

    cfg: ScenarioConfig
    artifacts: RunArtifacts
    provider_override: LLMProvider | None = None
    _local_perf_events: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.artifacts.out_dir.mkdir(parents=True, exist_ok=True)
        if self.artifacts.scenario_path is None:
            self.artifacts.scenario_path = self.artifacts.out_dir / "scenario.json"
        if self.artifacts.names_path is None:
            self.artifacts.names_path = self.artifacts.out_dir / "names.json"
        if self.artifacts.status_path is None:
            self.artifacts.status_path = self.artifacts.out_dir / "status.json"
        if self.artifacts.truth_path is None:
            self.artifacts.truth_path = self.artifacts.out_dir / "truth.jsonl"
        if self.artifacts.truth_freeform_path is None:
            self.artifacts.truth_freeform_path = self.artifacts.out_dir / "truth_freeform.jsonl"
        if self.artifacts.evaluation_path is None:
            self.artifacts.evaluation_path = self.artifacts.out_dir / "evaluation.json"
        if self.artifacts.fidelity_path is None:
            self.artifacts.fidelity_path = self.artifacts.out_dir / "fidelity.json"
        if self.artifacts.summary_path is None:
            self.artifacts.summary_path = self.artifacts.out_dir / "summary.json"
        if self.artifacts.environment_summary_path is None:
            self.artifacts.environment_summary_path = self.artifacts.out_dir / "environment_summary.json"
        if self.artifacts.environment_timeline_path is None:
            self.artifacts.environment_timeline_path = self.artifacts.out_dir / "environment_timeline.jsonl"
        if self.artifacts.perf_summary_path is None:
            self.artifacts.perf_summary_path = self.artifacts.out_dir / "perf_summary.json"
        if self.artifacts.world_history_path is None:
            self.artifacts.world_history_path = self.artifacts.out_dir / "world_history.md"

    async def run(self) -> WorldState:
        """Запустить симуляцию и вернуть финальный WorldState."""
        self._write_status_sidecar(state="running", tick=None)
        try:
            return await self._run_inner()
        except Exception as exc:
            self._write_status_sidecar(
                state="failed",
                tick=None,
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise

    async def _run_inner(self) -> WorldState:
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
        self._refresh_story_states(state=state, tick_events=[])
        self._write_run_sidecars(state=state, include_scenario=True)
        journal = WorldJournal.from_state(state=state)
        truth_detector = TruthDetector(
            private_contact_window_ticks=self.cfg.governance.audit.private_contact_window_ticks
        )

        # Memory: embeddings provider (по умолчанию реальные embeddings; без ключа остаётся BM25-only).
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
            llm=llm,
            temperature=self.cfg.llm.temperature,
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
                    proposed, gather_errors = await self._gather_actions(
                        state=gs["world"],
                        runners=runners,
                        events_history=gs["events_history"],
                        agent_order=agent_order,
                        daily_contexts=gs.get("daily_contexts"),
                        scene_hooks_by_agent=gs.get("scene_hooks_by_agent"),
                    )
                    return {"proposed": proposed, "gather_errors": gather_errors}

                async def _apply_node(gs: dict) -> dict:
                    agent_order = self._agent_order(state=gs["world"], tick=gs["world"].tick)
                    tick_events = list(gs.get("gather_errors") or [])
                    if tick_events:
                        event_log.extend(tick_events)
                    tick_events.extend(
                        await self._apply_actions(
                            state=gs["world"],
                            arbiter=arbiter,
                            proposed=gs["proposed"],
                            event_log=event_log,
                            agent_order=agent_order,
                            journal_yaml=journal.to_yaml(),
                        )
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
            self._write_status_sidecar(state="running", tick=tick)

            agent_order = self._agent_order(state=state, tick=tick)
            tick_events = self._expire_reputation_freezes(state=state, event_log=event_log)
            pending_expired = self._expire_pending_interactions(state=state, event_log=event_log)
            if pending_expired:
                tick_events.extend(pending_expired)
            daily_contexts: dict[str, AgentDailyContext] = {}
            scene_hooks_by_agent: dict[str, list[SceneHook]] = {}
            already_acted: set[str] = set()
            current_date, current_time = self._world_time_labels(tick=state.tick)

            pending_due_events = self._emit_pending_interaction_due_events(
                state=state,
                event_log=event_log,
            )
            if pending_due_events:
                tick_events.extend(pending_due_events)

            visible_history = list(events_history) + list(tick_events)
            if (
                self.cfg.runtime.enable_worldgen
                and self.cfg.runtime.worldgen_pre_tick
                and (tick % self.cfg.runtime.worldgen_every_ticks == 0)
            ):
                try:
                    generated_pre = await worldgen.generate(
                        tick=state.tick,
                        recent_events=visible_history,
                        language=self.cfg.runtime.language,
                        current_date=current_date,
                        current_time=current_time,
                        tick_duration_days=self.cfg.runtime.tick_duration_days,
                        tick_granularity=self.cfg.runtime.tick_granularity,
                        allow_internal_spawns=False,
                        phase="pre",
                        scenario_description=self.cfg.description,
                        state_snapshot=self._build_worldgen_state_snapshot(state=state),
                        agent_briefs=self._build_worldgen_agent_briefs(
                            state=state,
                            scope=self.cfg.runtime.worldgen_context_scope,
                        ),
                        worldgen_event_budget_per_tick=self.cfg.runtime.worldgen_event_budget_per_tick,
                        agent_context_budget_per_tick=max(
                            len(state.agents),
                            self.cfg.runtime.agent_context_budget_per_tick,
                        ),
                        max_scene_changes_per_tick=self.cfg.runtime.max_scene_changes_per_tick,
                        max_new_actors_per_window=0,
                    )
                except Exception as exc:
                    logger.warning("Pre-tick world generator failed on tick %s: %s", state.tick, exc)
                    generated_pre = WorldgenOutput(
                        events=[
                            Event(
                                tick=state.tick,
                                event_type="worldgen_llm_error",
                                actor_id=None,
                                payload={
                                    "phase": "pre",
                                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                                },
                                audience=[INTERNAL_AUDIENCE],
                            )
                        ],
                        spawns=[],
                    )
                if generated_pre.events:
                    event_log.extend(generated_pre.events)
                    tick_events.extend(generated_pre.events)
                entity_events = self._apply_worldgen_entity_changes(
                    state=state,
                    creations=[
                        {
                            "entity_id": item.entity_id,
                            "kind": item.kind,
                            "title": item.title,
                            "description": item.description,
                            "zone_type": item.zone_type,
                            "primary_org_id": item.primary_org_id,
                            "owner_org_id": item.owner_org_id,
                            "unit": item.unit,
                        }
                        for item in generated_pre.entity_creations
                    ],
                    event_log=event_log,
                )
                if entity_events:
                    tick_events.extend(entity_events)
                env_events = self._apply_worldgen_environment_updates(
                    state=state,
                    updates=generated_pre.environment_updates,
                    event_log=event_log,
                )
                if env_events:
                    tick_events.extend(env_events)
                consequence_events = self._apply_environment_material_consequences(
                    state=state,
                    tick_events=env_events,
                    event_log=event_log,
                )
                if consequence_events:
                    tick_events.extend(consequence_events)
                scene_events = self._materialize_scene_hook_events(
                    tick=state.tick,
                    scene_hooks=generated_pre.scene_hooks,
                )
                if scene_events:
                    event_log.extend(scene_events)
                    tick_events.extend(scene_events)
                daily_contexts = self._inject_fallback_daily_contexts(
                    state=state,
                    tick_events=tick_events,
                    daily_contexts=generated_pre.agent_contexts,
                )
                scene_hooks_by_agent = self._group_scene_hooks_by_agent(
                    state=state,
                    scene_hooks=generated_pre.scene_hooks,
                )
                visible_history = list(events_history) + list(tick_events)

            if graph_app is not None:
                out = await graph_app.ainvoke(
                    {
                        "world": state,
                        "events_history": visible_history,
                        "daily_contexts": daily_contexts,
                        "scene_hooks_by_agent": scene_hooks_by_agent,
                    }
                )
                tick_events.extend(list(out.get("tick_events") or []))
                tick_events.extend(
                    self._apply_interaction_network_updates(
                        state=state,
                        tick_events=tick_events,
                        event_log=event_log,
                    )
                )
            else:
                # Сбор действий агентов параллельно.
                proposed, gather_errors = await self._gather_actions(
                    state=state,
                    runners=runners,
                    events_history=visible_history,
                    agent_order=agent_order,
                    daily_contexts=daily_contexts,
                    scene_hooks_by_agent=scene_hooks_by_agent,
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

                already_acted = {aid for aid, acts in proposed.items() if acts}
                tick_events.extend(
                    await self._run_micro_reaction_rounds(
                        state=state,
                        runners=runners,
                        arbiter=arbiter,
                        event_log=event_log,
                        events_history=events_history,
                        tick_events=tick_events,
                        already_acted=already_acted,
                    )
                )
                tick_events.extend(
                    self._apply_interaction_network_updates(
                        state=state,
                        tick_events=tick_events,
                        event_log=event_log,
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
                self._write_names_sidecar(state=state)

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
                        current_date=current_date,
                        current_time=current_time,
                        tick_duration_days=self.cfg.runtime.tick_duration_days,
                        tick_granularity=self.cfg.runtime.tick_granularity,
                        allow_internal_spawns=self.cfg.runtime.worldgen_allow_internal_spawns,
                        phase="post",
                        scenario_description=self.cfg.description,
                        state_snapshot=self._build_worldgen_state_snapshot(state=state),
                        agent_briefs=self._build_worldgen_agent_briefs(state=state),
                        worldgen_event_budget_per_tick=self.cfg.runtime.worldgen_event_budget_per_tick,
                        agent_context_budget_per_tick=self.cfg.runtime.agent_context_budget_per_tick,
                        max_scene_changes_per_tick=self.cfg.runtime.max_scene_changes_per_tick,
                        max_new_actors_per_window=self.cfg.runtime.max_new_actors_per_window,
                    )
                except Exception as exc:
                    logger.warning("World generator failed on tick %s: %s", state.tick, exc)
                    generated = WorldgenOutput(
                        events=[
                            Event(
                                tick=state.tick,
                                event_type="worldgen_llm_error",
                                actor_id=None,
                                payload={
                                    "phase": "post",
                                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                                },
                                audience=[INTERNAL_AUDIENCE],
                            )
                        ],
                        spawns=[],
                    )
                if generated.events:
                    event_log.extend(generated.events)
                    tick_events.extend(generated.events)
                entity_events = self._apply_worldgen_entity_changes(
                    state=state,
                    creations=[
                        {
                            "entity_id": item.entity_id,
                            "kind": item.kind,
                            "title": item.title,
                            "description": item.description,
                            "zone_type": item.zone_type,
                            "primary_org_id": item.primary_org_id,
                            "owner_org_id": item.owner_org_id,
                            "unit": item.unit,
                        }
                        for item in generated.entity_creations
                    ],
                    event_log=event_log,
                )
                if entity_events:
                    tick_events.extend(entity_events)
                env_events = self._apply_worldgen_environment_updates(
                    state=state,
                    updates=generated.environment_updates,
                    event_log=event_log,
                )
                if env_events:
                    tick_events.extend(env_events)
                consequence_events = self._apply_environment_material_consequences(
                    state=state,
                    tick_events=env_events,
                    event_log=event_log,
                )
                if consequence_events:
                    tick_events.extend(consequence_events)
                artifact_events = self._apply_worldgen_artifact_changes(
                    state=state,
                    creations=[
                        {
                            "artifact_id": item.artifact_id,
                            "artifact_type": item.artifact_type,
                            "title": item.title,
                            "summary": item.summary,
                            "owner_org_id": item.owner_org_id,
                            "zone_id": item.zone_id,
                            "related_work_id": item.related_work_id,
                            "visibility": item.visibility,
                            "status": item.status,
                            "tags": list(item.tags or []),
                        }
                        for item in generated.artifact_creations
                    ],
                    updates=[
                        {
                            "artifact_id": item.artifact_id,
                            "title": item.title,
                            "summary": item.summary,
                            "visibility": item.visibility,
                            "status": item.status,
                            "tags": list(item.tags or []) if item.tags is not None else None,
                        }
                        for item in generated.artifact_updates
                    ],
                    event_log=event_log,
                )
                if artifact_events:
                    tick_events.extend(artifact_events)
                blueprint_events = self._apply_population_blueprints(
                    state=state,
                    event_log=event_log,
                    activation="environment_change",
                )
                if blueprint_events:
                    tick_events.extend(blueprint_events)
                scene_events = self._materialize_scene_hook_events(
                    tick=state.tick,
                    scene_hooks=generated.scene_hooks,
                )
                if scene_events:
                    event_log.extend(scene_events)
                    tick_events.extend(scene_events)
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
                            self._write_names_sidecar(state=state)

            truth_window = int(self.cfg.governance.audit.lookback_events)
            truth_recent = events_history[-truth_window:] if truth_window > 0 else list(events_history)
            truth_records = truth_detector.detect_tick(
                state=state,
                tick_events=tick_events,
                recent_events=truth_recent,
            )
            if truth_records:
                truth_log.extend(truth_records)

            if self.cfg.governance.audit.enabled:
                audit_window = int(self.cfg.governance.audit.lookback_events)
                audit_recent = events_history[-audit_window:] if audit_window > 0 else list(events_history)
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

            reputation_events = self._apply_reputation_consequences(
                state=state,
                tick_events=tick_events,
                event_log=event_log,
            )
            if reputation_events:
                tick_events.extend(reputation_events)

            pending_update_events = self._apply_pending_interaction_updates(
                state=state,
                tick_events=list(tick_events),
                event_log=event_log,
            )
            if pending_update_events:
                tick_events.extend(pending_update_events)
            same_tick_followups = await self._run_same_tick_pending_followups(
                state=state,
                runners=runners,
                arbiter=arbiter,
                event_log=event_log,
                events_history=events_history,
                tick_events=tick_events,
            )
            if same_tick_followups:
                tick_events.extend(same_tick_followups)

            self._refresh_story_states(state=state, tick_events=tick_events)

            journal.apply_events(state=state, events=tick_events)
            self._append_environment_timeline(state=state, tick_events=tick_events)

            events_history.extend(tick_events)
            # Ограничиваем историю для памяти.
            if len(events_history) > self.cfg.runtime.tick_events_history:
                events_history = events_history[-self.cfg.runtime.tick_events_history :]

        state.clamp_reputation()
        governance_summary = None
        fidelity_summary = None
        freeform_truth_total = 0
        freeform_truth_path_for_eval: Path | None = None
        if self.cfg.runtime.freeform_truth_enabled and self.artifacts.truth_freeform_path is not None:
            try:
                recorder = FreeformTruthRecorder(
                    llm=llm,
                    window_ticks=int(self.cfg.runtime.freeform_truth_window_ticks),
                    temperature=self.cfg.llm.temperature,
                )
                freeform_records = await recorder.analyze_events(
                    events_path=self.artifacts.events_path,
                    scenario_description=self.cfg.description,
                )
                save_freeform_truth(freeform_records, self.artifacts.truth_freeform_path)
                freeform_truth_total = len(freeform_records)
                freeform_truth_path_for_eval = self.artifacts.truth_freeform_path
            except Exception as exc:
                logger.warning("Freeform truth recorder failed: %s", exc)
        if self.artifacts.truth_path is not None and self.artifacts.evaluation_path is not None:
            governance_summary = evaluate_run(
                events_path=self.artifacts.events_path,
                truth_path=self.artifacts.truth_path,
                truth_freeform_path=freeform_truth_path_for_eval,
            )
            governance_summary = await augment_evaluation_with_semantic_judge(
                summary=governance_summary,
                llm=llm,
                events_path=self.artifacts.events_path,
                truth_path=self.artifacts.truth_path,
                truth_freeform_path=freeform_truth_path_for_eval,
                scenario_description=self.cfg.description,
                temperature=self.cfg.llm.temperature,
            )
            save_evaluation(governance_summary, self.artifacts.evaluation_path)
        if self.artifacts.fidelity_path is not None:
            fidelity_summary = evaluate_fidelity(
                events_path=self.artifacts.events_path,
                start_date=self.cfg.runtime.start_date,
                tick_duration_days=self.cfg.runtime.tick_duration_days,
                temporal_past_slack_days=self.cfg.runtime.temporal_past_slack_days,
                temporal_future_horizon_days=self.cfg.runtime.temporal_future_horizon_days,
            )
            fidelity_summary = await augment_fidelity_with_semantic_judge(
                summary=fidelity_summary,
                llm=llm,
                events_path=self.artifacts.events_path,
                scenario_description=self.cfg.description,
                temperature=self.cfg.llm.temperature,
            )
            save_fidelity(fidelity_summary, self.artifacts.fidelity_path)
        if self.artifacts.summary_path is not None:
            payload = {
                "governance": governance_summary.model_dump(mode="json") if governance_summary is not None else None,
                "fidelity": fidelity_summary.model_dump(mode="json") if fidelity_summary is not None else None,
                "freeform_truth_total": freeform_truth_total,
            }
            self.artifacts.summary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        self._write_environment_summary(state=state)
        self._write_perf_summary()
        self._write_names_sidecar(state=state)
        self._write_world_history_markdown()
        self._write_status_sidecar(state="finished", tick=state.tick)
        return state

    def _write_run_sidecars(self, *, state: WorldState, include_scenario: bool) -> None:
        if include_scenario and self.artifacts.scenario_path is not None:
            self.artifacts.scenario_path.write_text(
                json.dumps(self.cfg.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        self._write_names_sidecar(state=state)

    def _write_names_sidecar(self, *, state: WorldState) -> None:
        if self.artifacts.names_path is None:
            return
        names = {
            aid: agent.name
            for aid, agent in sorted(state.agents.items())
            if agent.name.strip()
        }
        self.artifacts.names_path.write_text(
            json.dumps(names, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _environment_timeline_entry(self, *, state: WorldState, tick_events: list[Event]) -> dict[str, Any]:
        pending_by_category: dict[str, int] = {}
        for interaction in state.pending_interactions.values():
            if interaction.status != "open":
                continue
            category = str(interaction.category or "").strip() or "other"
            pending_by_category[category] = pending_by_category.get(category, 0) + 1
        return {
            "tick": int(state.tick),
            "environment": state.environment.snapshot_dict(),
            "pending_interactions_open": len([item for item in state.pending_interactions.values() if item.status == "open"]),
            "pending_interactions_by_category": pending_by_category,
            "tick_event_types": sorted({str(event.event_type or "") for event in tick_events if str(event.event_type or "").strip()}),
        }

    def _append_environment_timeline(self, *, state: WorldState, tick_events: list[Event]) -> None:
        if self.artifacts.environment_timeline_path is None:
            return
        entry = self._environment_timeline_entry(state=state, tick_events=tick_events)
        self.artifacts.environment_timeline_path.parent.mkdir(parents=True, exist_ok=True)
        with self.artifacts.environment_timeline_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_environment_summary(self, *, state: WorldState) -> None:
        if self.artifacts.environment_summary_path is None:
            return
        open_pending = [item for item in state.pending_interactions.values() if item.status == "open"]
        completed_pending = [item for item in state.pending_interactions.values() if item.status == "completed"]
        expired_pending = [item for item in state.pending_interactions.values() if item.status == "expired"]
        spawn_sources: dict[str, int] = {}
        for agent in state.agents.values():
            if agent.spawn_source:
                spawn_sources[agent.spawn_source] = spawn_sources.get(agent.spawn_source, 0) + 1
        payload = {
            "tick": int(state.tick),
            "environment": state.environment.snapshot_dict(),
            "pending_interactions": {
                "open": len(open_pending),
                "completed": len(completed_pending),
                "expired": len(expired_pending),
                "by_category_open": {
                    category: len([item for item in open_pending if item.category == category])
                    for category in sorted({item.category for item in open_pending if item.category})
                },
            },
            "spawn_sources": spawn_sources,
        }
        self.artifacts.environment_summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_perf_summary(self) -> None:
        if self.artifacts.perf_summary_path is None:
            return
        payload = self._build_perf_summary()
        self.artifacts.perf_summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_world_history_markdown(self) -> None:
        if self.artifacts.world_history_path is None:
            return
        try:
            write_world_history_markdown(
                path=self.artifacts.world_history_path,
                run_name=self.artifacts.out_dir.name,
                scenario_title=self.cfg.title,
                governance_label=infer_builtin_governance_mode(self.cfg),
                runtime=self.cfg.runtime,
                events_path=self.artifacts.events_path,
                trace_path=self.artifacts.trace_path,
            )
        except Exception as exc:
            logger.warning("World history markdown export failed: %s", exc)

    def _record_local_perf_call(
        self,
        phase: str,
        tick: int | None,
        duration_ms: float,
        input_count: int,
        input_chars: int,
    ) -> None:
        self._local_perf_events.append(
            {
                "phase": phase,
                "tick": tick,
                "duration_ms": float(duration_ms),
                "input_count": int(input_count),
                "input_chars": int(input_chars),
            }
        )

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return float(values[0])
        ordered = sorted(float(v) for v in values)
        idx = max(0.0, min(float(len(ordered) - 1), (len(ordered) - 1) * q))
        lo = int(idx)
        hi = min(len(ordered) - 1, lo + 1)
        frac = idx - lo
        return ordered[lo] * (1.0 - frac) + ordered[hi] * frac

    def _build_perf_summary(self) -> dict[str, Any]:
        role_stats: dict[str, dict[str, Any]] = {}
        tick_stats: dict[int, dict[str, Any]] = {}
        local_phase_stats: dict[str, dict[str, Any]] = {}
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        slow_calls: list[dict[str, Any]] = []

        def _touch(container: dict[Any, dict[str, Any]], key: Any) -> dict[str, Any]:
            bucket = container.get(key)
            if bucket is None:
                bucket = {
                    "calls": 0,
                    "duration_ms": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "durations_ms": [],
                    "retries_used": 0,
                    "timeout_count": 0,
                    "error_count": 0,
                }
                container[key] = bucket
            return bucket

        if self.artifacts.trace_path.exists():
            for line in self.artifacts.trace_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = str(row.get("role") or "unknown")
                tick = row.get("tick")
                duration_ms = float(row.get("duration_ms") or 0.0)
                usage = row.get("usage") or {}
                meta = row.get("meta") or {}
                error = row.get("error") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)

                role_bucket = _touch(role_stats, role)
                role_bucket["calls"] = int(role_bucket["calls"]) + 1
                role_bucket["duration_ms"] = float(role_bucket["duration_ms"]) + duration_ms
                role_bucket["prompt_tokens"] = int(role_bucket["prompt_tokens"]) + prompt_tokens
                role_bucket["completion_tokens"] = int(role_bucket["completion_tokens"]) + completion_tokens
                role_bucket["durations_ms"].append(duration_ms)
                role_bucket["retries_used"] = int(role_bucket["retries_used"]) + int(meta.get("retries_used") or 0)
                if error:
                    role_bucket["error_count"] = int(role_bucket["error_count"]) + 1
                    if str(error.get("type") or "") == "TimeoutError":
                        role_bucket["timeout_count"] = int(role_bucket["timeout_count"]) + 1

                if tick is not None:
                    tick_bucket = _touch(tick_stats, int(tick))
                    tick_bucket["calls"] = int(tick_bucket["calls"]) + 1
                    tick_bucket["duration_ms"] = float(tick_bucket["duration_ms"]) + duration_ms
                    tick_bucket["prompt_tokens"] = int(tick_bucket["prompt_tokens"]) + prompt_tokens
                    tick_bucket["completion_tokens"] = int(tick_bucket["completion_tokens"]) + completion_tokens
                    tick_bucket["durations_ms"].append(duration_ms)
                    tick_bucket["retries_used"] = int(tick_bucket["retries_used"]) + int(meta.get("retries_used") or 0)
                    if error:
                        tick_bucket["error_count"] = int(tick_bucket["error_count"]) + 1
                        if str(error.get("type") or "") == "TimeoutError":
                            tick_bucket["timeout_count"] = int(tick_bucket["timeout_count"]) + 1

                raw_ts = str(row.get("timestamp") or "").strip()
                if raw_ts:
                    try:
                        parsed_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    except ValueError:
                        parsed_ts = None
                    if parsed_ts is not None:
                        first_ts = parsed_ts if first_ts is None or parsed_ts < first_ts else first_ts
                        last_ts = parsed_ts if last_ts is None or parsed_ts > last_ts else last_ts
                slow_calls.append(
                    {
                        "kind": "llm",
                        "phase": role,
                        "name": str(row.get("name") or ""),
                        "tick": tick,
                        "duration_ms": round(duration_ms, 3),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                        "retries_used": int(meta.get("retries_used") or 0),
                        "error_type": str(error.get("type") or "") or None,
                    }
                )

        for row in self._local_perf_events:
            phase = str(row.get("phase") or "unknown")
            tick = row.get("tick")
            duration_ms = float(row.get("duration_ms") or 0.0)
            bucket = local_phase_stats.get(phase)
            if bucket is None:
                bucket = {
                    "calls": 0,
                    "duration_ms": 0.0,
                    "input_count": 0,
                    "input_chars": 0,
                    "durations_ms": [],
                }
                local_phase_stats[phase] = bucket
            bucket["calls"] = int(bucket["calls"]) + 1
            bucket["duration_ms"] = float(bucket["duration_ms"]) + duration_ms
            bucket["input_count"] = int(bucket["input_count"]) + int(row.get("input_count") or 0)
            bucket["input_chars"] = int(bucket["input_chars"]) + int(row.get("input_chars") or 0)
            bucket["durations_ms"].append(duration_ms)
            slow_calls.append(
                {
                    "kind": "local",
                    "phase": phase,
                    "name": phase,
                    "tick": tick,
                    "duration_ms": round(duration_ms, 3),
                    "input_count": int(row.get("input_count") or 0),
                    "input_chars": int(row.get("input_chars") or 0),
                }
            )

        total_prompt = sum(int(bucket["prompt_tokens"]) for bucket in role_stats.values())
        total_completion = sum(int(bucket["completion_tokens"]) for bucket in role_stats.values())
        sum_duration_ms = sum(float(bucket["duration_ms"]) for bucket in role_stats.values())
        total_retries = sum(int(bucket["retries_used"]) for bucket in role_stats.values())
        total_timeouts = sum(int(bucket["timeout_count"]) for bucket in role_stats.values())
        total_errors = sum(int(bucket["error_count"]) for bucket in role_stats.values())
        trace_span_seconds = (
            max(0.0, (last_ts - first_ts).total_seconds())
            if first_ts is not None and last_ts is not None
            else 0.0
        )

        def _format_bucket(bucket: dict[str, Any]) -> dict[str, float | int | None]:
            total_tokens = int(bucket["prompt_tokens"]) + int(bucket["completion_tokens"])
            duration_ms = float(bucket["duration_ms"])
            duration_s = duration_ms / 1000.0
            durations = [float(item) for item in bucket.get("durations_ms", [])]
            return {
                "calls": int(bucket["calls"]),
                "duration_ms": round(duration_ms, 3),
                "duration_s": round(duration_s, 3),
                "prompt_tokens": int(bucket["prompt_tokens"]),
                "completion_tokens": int(bucket["completion_tokens"]),
                "total_tokens": total_tokens,
                "avg_duration_ms": round(duration_ms / max(int(bucket["calls"]), 1), 3),
                "avg_total_tokens": round(total_tokens / max(int(bucket["calls"]), 1), 3),
                "tokens_per_second": round(total_tokens / duration_s, 3) if duration_s > 0 else None,
                "p50_duration_ms": round(self._percentile(durations, 0.5), 3) if durations else None,
                "p95_duration_ms": round(self._percentile(durations, 0.95), 3) if durations else None,
                "max_duration_ms": round(max(durations), 3) if durations else None,
                "retries_used_total": int(bucket.get("retries_used") or 0),
                "timeout_count": int(bucket.get("timeout_count") or 0),
                "error_count": int(bucket.get("error_count") or 0),
            }

        def _format_local_bucket(bucket: dict[str, Any]) -> dict[str, float | int | None]:
            duration_ms = float(bucket["duration_ms"])
            duration_s = duration_ms / 1000.0
            durations = [float(item) for item in bucket.get("durations_ms", [])]
            return {
                "calls": int(bucket["calls"]),
                "duration_ms": round(duration_ms, 3),
                "duration_s": round(duration_s, 3),
                "avg_duration_ms": round(duration_ms / max(int(bucket["calls"]), 1), 3),
                "p50_duration_ms": round(self._percentile(durations, 0.5), 3) if durations else None,
                "p95_duration_ms": round(self._percentile(durations, 0.95), 3) if durations else None,
                "max_duration_ms": round(max(durations), 3) if durations else None,
                "input_count": int(bucket["input_count"]),
                "input_chars": int(bucket["input_chars"]),
            }

        slow_calls.sort(key=lambda item: float(item.get("duration_ms") or 0.0), reverse=True)

        return {
            "overall": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
                "sum_llm_duration_ms": round(sum_duration_ms, 3),
                "sum_llm_duration_s": round(sum_duration_ms / 1000.0, 3),
                "trace_span_s": round(trace_span_seconds, 3),
                "retries_used_total": total_retries,
                "timeout_count": total_timeouts,
                "error_count": total_errors,
                "local_phase_duration_s": round(
                    sum(float(bucket["duration_ms"]) for bucket in local_phase_stats.values()) / 1000.0,
                    3,
                ),
                "overlap_ratio": round((sum_duration_ms / 1000.0) / trace_span_seconds, 3)
                if trace_span_seconds > 0
                else None,
            },
            "by_phase": {
                role: _format_bucket(bucket)
                for role, bucket in sorted(
                    role_stats.items(),
                    key=lambda item: float(item[1]["duration_ms"]),
                    reverse=True,
                )
            },
            "by_local_phase": {
                phase: _format_local_bucket(bucket)
                for phase, bucket in sorted(
                    local_phase_stats.items(),
                    key=lambda item: float(item[1]["duration_ms"]),
                    reverse=True,
                )
            },
            "by_tick": {
                str(tick): _format_bucket(bucket)
                for tick, bucket in sorted(tick_stats.items())
            },
            "slowest_calls": slow_calls[:20],
        }

    def _write_status_sidecar(
        self,
        *,
        state: str,
        tick: int | None,
        error: dict[str, str] | None = None,
    ) -> None:
        if self.artifacts.status_path is None:
            return
        payload = {
            "state": state,
            "pid": os.getpid(),
            "tick": tick,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }
        try:
            self.artifacts.status_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Failed to write status sidecar: %s", exc)

    def _agent_order(self, *, state: WorldState, tick: int) -> list[str]:
        """Детерминированный порядок агентов на тик."""
        return sorted(
            state.agents.keys(),
            key=lambda agent_id: hashlib.sha1(f"{tick}|{agent_id}".encode("utf-8")).hexdigest(),
        )

    def _world_time_labels(self, *, tick: int) -> tuple[str | None, str | None]:
        simulated = self.cfg.runtime.simulated_datetime(tick)
        if simulated is None:
            return None, None
        return simulated.date().isoformat(), simulated.strftime("%H:%M")

    @staticmethod
    def _compact_text(text: str, *, max_chars: int = 220) -> str:
        normalized = " ".join((text or "").split()).strip()
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max(0, max_chars - 1)].rstrip() + "…"

    def _event_describes_risky_pressure(self, *, event: Event) -> bool:
        return event.event_type in {
            "world_event",
            "audit_flagged",
            "audit_case_opened",
            "audit_case_updated",
            "audit_escalated",
            "pending_interaction_due",
            "pending_interaction_expired",
            "reputation_frozen",
            "vote_opened",
        }

    def _fallback_daily_context_for_agent(
        self,
        *,
        agent: AgentState,
        event: Event,
    ) -> AgentDailyContext:
        description = self._compact_text(str((event.payload or {}).get("description") or ""), max_chars=320)
        if agent.internal:
            where_day_starts = "Начало рабочего дня, разбор входящих сигналов и требований по текущему делу."
            private_pressure = "Полная эскалация может создать личные и организационные издержки уже сегодня."
            opportunity = "Можно удержать контроль над ситуацией, если сначала решать вопрос в закрытом порядке."
            exposure_risk = "Если скрытая часть истории выйдет наружу, удар придётся по репутации и должности."
        else:
            where_day_starts = "Начало рабочего дня, просмотр входящих сигналов от связанных участников и внешних запросов."
            private_pressure = "Публичная эскалация угрожает текущим договорённостям и деловой позиции."
            opportunity = "Есть шанс сохранить выгодную позицию, если сначала договориться до публичной реакции."
            exposure_risk = "Если обходной путь раскроется, пострадают репутация и доступ к процессу."

        social_encounter = (
            "Есть окно для закрытого разговора с участником, на которого это давление влияет сильнее всего."
        )
        personal_pressure = description
        ambient_signal = description
        today_hook = (
            "Простой административный ответ может не снять напряжение; нужно решить, раскрывать ли проблему полностью, "
            "обсудить её приватно или удерживать внутри до уточнения."
        )
        return AgentDailyContext(
            where_day_starts=where_day_starts,
            personal_pressure=personal_pressure,
            social_encounter=social_encounter,
            ambient_signal=ambient_signal,
            private_pressure=private_pressure,
            opportunity=opportunity,
            exposure_risk=exposure_risk,
            today_hook=today_hook,
        )

    def _inject_fallback_daily_contexts(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        daily_contexts: dict[str, AgentDailyContext],
    ) -> dict[str, AgentDailyContext]:
        out = dict(daily_contexts)
        risky_events = [event for event in tick_events if self._event_describes_risky_pressure(event=event)]
        if not risky_events:
            return out

        core_ids = sorted(self._primary_agent_ids() & set(state.agents.keys()))
        if not core_ids:
            return out
        anchor_event = risky_events[0]
        for aid in core_ids:
            if aid in out:
                continue
            out[aid] = self._fallback_daily_context_for_agent(agent=state.agents[aid], event=anchor_event)
        return out

    def _build_worldgen_state_snapshot(self, *, state: WorldState) -> dict[str, Any]:
        primary_ids = self._primary_agent_ids()
        work_items = []
        for wid in sorted(state.work_items.keys())[:12]:
            work = state.work_items[wid]
            work_items.append(
                {
                    "work_id": wid,
                    "title": work.title,
                    "status": work.status,
                    "participants": list(work.participants[:6]),
                }
            )
        artifacts = []
        for artifact_id in sorted(state.artifacts.keys())[:12]:
            artifact = state.artifacts[artifact_id]
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact.artifact_type,
                    "title": artifact.title,
                    "summary": self._compact_text(artifact.summary, max_chars=180),
                    "owner_org_id": artifact.owner_org_id,
                    "zone_id": artifact.zone_id,
                    "related_work_id": artifact.related_work_id,
                    "visibility": artifact.visibility,
                    "status": artifact.status,
                    "tags": list(artifact.tags[:4]),
                }
            )
        population_targets = []
        for blueprint in self.cfg.world.environment.population_blueprints:
            current = len(
                [agent for agent in state.agents.values() if agent.blueprint_id == blueprint.blueprint_id]
            )
            population_targets.append(
                {
                    "blueprint_id": blueprint.blueprint_id,
                    "role_label": blueprint.role_label,
                    "desired_count": int(blueprint.desired_count),
                    "current_count": current,
                    "activation": blueprint.activation,
                    "internal": bool(blueprint.internal),
                    "org_id": blueprint.org_id,
                    "zone_id": blueprint.zone_id,
                }
            )
        pending_interactions = []
        for interaction in self._open_pending_interactions(state=state):
            pending_interactions.append(
                {
                    "interaction_id": interaction.interaction_id,
                    "target_agent_id": interaction.target_agent_id,
                    "source_agent_id": interaction.source_agent_id,
                    "category": interaction.category,
                    "summary": self._compact_text(interaction.summary, max_chars=180),
                    "earliest_tick": interaction.earliest_tick,
                    "due_tick": interaction.due_tick,
                    "priority": interaction.priority,
                    "trigger_event_type": interaction.trigger_event_type,
                    "related_work_id": interaction.related_work_id,
                    "artifact_id": interaction.artifact_id,
                    "org_id": interaction.org_id,
                    "zone_id": interaction.zone_id,
                }
            )
        votes = []
        for vid, vote in sorted(state.votes.items()):
            if vote.status != "open":
                continue
            votes.append(
                {
                    "vote_id": vid,
                    "target_agent_id": vote.target_agent_id,
                    "new_title": vote.new_title,
                    "closes_tick": vote.closes_tick,
                }
            )
        secondary_agents = []
        for aid, agent in sorted(state.agents.items()):
            if aid in primary_ids:
                continue
            secondary_agents.append(
                {
                    "agent_id": aid,
                    "name": agent.name,
                    "internal": bool(agent.internal),
                    "title": agent.title if agent.internal else "",
                }
            )
        return {
            "tick": state.tick,
            "environment": state.environment.snapshot_dict(
                max_institutions=10,
                max_zones=10,
                max_resource_pools=10,
                max_informal_links=16,
            ),
            "open_work_items": work_items,
            "artifacts": artifacts,
            "pending_interactions": pending_interactions[:16],
            "population_targets": population_targets,
            "open_votes": votes,
            "secondary_agents": secondary_agents[:20],
            "agent_count": len(state.agents),
            "max_agents": self.cfg.runtime.max_agents,
        }

    def _primary_agent_ids(self) -> set[str]:
        return {agent.agent_id for agent in self.cfg.agents}

    @staticmethod
    def _pending_priority_rank(priority: str) -> int:
        ranks = {
            "critical": 0,
            "high": 1,
            "normal": 2,
            "low": 3,
        }
        return ranks.get((priority or "").strip().lower(), 2)

    def _open_pending_interactions(
        self,
        *,
        state: WorldState,
        agent_id: str | None = None,
        tick: int | None = None,
    ) -> list[PendingInteractionState]:
        current_tick = state.tick if tick is None else int(tick)
        out: list[PendingInteractionState] = []
        for interaction in state.pending_interactions.values():
            if interaction.status != "open":
                continue
            if agent_id is not None and interaction.target_agent_id != agent_id:
                continue
            if int(interaction.earliest_tick) > current_tick:
                continue
            if interaction.due_tick is not None and current_tick > int(interaction.due_tick):
                continue
            out.append(interaction)
        out.sort(
            key=lambda item: (
                self._pending_priority_rank(item.priority),
                item.due_tick if item.due_tick is not None else 10**9,
                item.earliest_tick,
                item.created_tick,
                item.interaction_id,
            )
        )
        return out

    @staticmethod
    def _event_mentions_agent(*, event: Event, agent_id: str) -> bool:
        payload = event.payload or {}
        if event.event_type == "arbiter_approved" and str(payload.get("reason") or "") == "noop":
            return False
        if event.actor_id == agent_id:
            return True
        if agent_id in event.audience:
            return True
        if not isinstance(payload, dict):
            return False
        if str(payload.get("to_id") or "") == agent_id:
            return True
        if str(payload.get("target_agent_id") or "") == agent_id:
            return True
        if str(payload.get("entity_id") or "") == agent_id:
            return True
        participants = payload.get("participants") or payload.get("agents") or []
        if isinstance(participants, list) and agent_id in [str(item) for item in participants]:
            return True
        return False

    def _should_activate_agent(
        self,
        *,
        state: WorldState,
        agent_id: str,
        events_history: list[Event],
        daily_contexts: dict[str, AgentDailyContext] | None,
        scene_hooks_by_agent: dict[str, list[SceneHook]] | None,
    ) -> bool:
        if agent_id in self._primary_agent_ids():
            return True
        window = int(self.cfg.runtime.ecology_activation_window_ticks)
        if window <= 0:
            return True
        if agent_id in (daily_contexts or {}):
            return True
        if (scene_hooks_by_agent or {}).get(agent_id):
            return True

        low_tick = max(0, int(state.tick) - window)
        agent = state.agents.get(agent_id)
        if agent is None:
            return False
        if self._open_pending_interactions(state=state, agent_id=agent_id):
            return True
        for event in reversed(events_history):
            if int(event.tick) < low_tick:
                break
            if self._event_mentions_agent(event=event, agent_id=agent_id):
                return True
            if self._event_touches_agent_environment(state=state, event=event, agent=agent):
                return True
        return False

    @staticmethod
    def _event_touches_agent_environment(*, state: WorldState, event: Event, agent: AgentState) -> bool:
        payload = event.payload or {}
        if not isinstance(payload, dict):
            return False
        if event.event_type == "environment_institution_updated":
            return bool(agent.org_id) and str(payload.get("org_id") or "") == agent.org_id
        if event.event_type == "environment_zone_updated":
            return bool(agent.zone_id) and str(payload.get("zone_id") or "") == agent.zone_id
        if event.event_type == "environment_resource_updated":
            resource_id = str(payload.get("resource_id") or "")
            if not resource_id:
                return False
            pool = state.environment.resource_pools.get(resource_id)
            if pool is None:
                return False
            return bool(agent.org_id) and pool.owner_org_id == agent.org_id
        if event.event_type == "environment_information_climate_updated":
            return True
        if event.event_type == "environment_informal_link_updated":
            return agent.agent_id in {str(payload.get("agent_a_id") or ""), str(payload.get("agent_b_id") or "")}
        return False

    def _build_worldgen_agent_briefs(self, *, state: WorldState, scope: str = "all") -> list[dict[str, Any]]:
        briefs: list[dict[str, Any]] = []
        allowed_ids = set(state.agents.keys())
        if scope == "core":
            allowed_ids = self._primary_agent_ids() & set(state.agents.keys())
        for aid in sorted(allowed_ids):
            agent = state.agents[aid]
            briefs.append(
                {
                    "agent_id": aid,
                    "name": agent.name,
                    "internal": bool(agent.internal),
                    "title": agent.title if agent.internal else "",
                    "org_id": agent.org_id,
                    "zone_id": agent.zone_id,
                    "capabilities": list(agent.capabilities),
                    "story_state": self._compact_text(agent.story_state, max_chars=320),
                    "pending_interaction_count": len(
                        self._open_pending_interactions(state=state, agent_id=aid)
                    ),
                }
            )
        return briefs

    def _group_scene_hooks_by_agent(
        self,
        *,
        state: WorldState,
        scene_hooks: list[SceneHook],
    ) -> dict[str, list[SceneHook]]:
        grouped: dict[str, list[SceneHook]] = {}
        for hook in scene_hooks:
            for aid in hook.agents:
                if aid not in state.agents:
                    continue
                grouped.setdefault(aid, []).append(hook)
        return grouped

    def _materialize_scene_hook_events(
        self,
        *,
        tick: int,
        scene_hooks: list[SceneHook],
    ) -> list[Event]:
        events: list[Event] = []
        for hook in scene_hooks:
            if not hook.mandatory:
                continue
            audience = list(dict.fromkeys([aid for aid in hook.agents if aid])) or [INTERNAL_AUDIENCE]
            events.append(
                Event(
                    tick=tick,
                    event_type="scene_occurred",
                    actor_id=None,
                    payload={
                        "kind": hook.kind,
                        "description": hook.description,
                        "agents": list(hook.agents),
                    },
                    audience=audience,
                )
            )
        return events

    def _story_event_text(self, *, event: Event) -> str:
        payload = event.payload or {}
        if event.event_type == "world_event":
            return str(payload.get("description") or "").strip()
        if event.event_type == "scene_occurred":
            return str(payload.get("description") or "").strip()
        if event.event_type == "message_sent":
            to_id = str(payload.get("to_id") or "")
            if bool(payload.get("private", True)):
                return f"был закрытый контакт с {to_id}"
            return f"было публичное сообщение в {to_id}"
        if event.event_type == "audit_flagged":
            return f"аудит отметил риск для {payload.get('target_agent_id', '')}"
        if event.event_type == "audit_case_opened":
            return f"открыт аудит-кейс {payload.get('case_id', '')}"
        if event.event_type == "vote_opened":
            return f"открыто голосование {payload.get('vote_id', '')}"
        if event.event_type == "reputation_frozen":
            return "репутация заморожена"
        if event.event_type == "reputation_modified":
            return f"репутация изменилась: {payload.get('reason', '')}"
        return self._compact_text(f"{event.event_type}: {redact_numbers(payload)}", max_chars=180)

    def _refresh_story_states(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        agent_ids: list[str] | None = None,
    ) -> None:
        pending = agent_ids or sorted(state.agents.keys())
        for aid in pending:
            agent = state.agents.get(aid)
            if agent is None:
                continue
            visible = [ev for ev in tick_events if event_visible_to_agent(ev, aid, internal=agent.internal)]
            if not visible and agent.story_state.strip():
                continue

            base = self._compact_text(agent.persona.summary or agent.persona.biography or agent.name, max_chars=240)
            lines = [f"Базовая линия: {base or agent.name}."]
            if agent.internal:
                lines.append(f"Текущая позиция: {agent.title}.")
            else:
                lines.append("Текущая позиция: внешний участник процесса.")
            if agent.reputation_frozen:
                lines.append("Репутационный риск активен: манёвр сужен.")

            recent_bits = [self._story_event_text(event=ev) for ev in visible[-4:]]
            recent_bits = [item for item in recent_bits if item]
            if recent_bits:
                lines.append("Последние сдвиги: " + "; ".join(recent_bits[:3]) + ".")
            elif agent.story_state.strip():
                lines.append("Недавний контекст сохраняется без резких изменений.")

            agent.story_state = self._compact_text(" ".join(lines), max_chars=700)

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
        for i, reflection in enumerate(agent.persona.reflections):
            expert = (reflection.expert or "").strip()
            summary = (reflection.summary or "").strip()
            if not expert or not summary:
                continue
            docs_to_add.append(
                (
                    "reflection",
                    8.5,
                    f"{expert}: {summary}",
                    {
                        "source": "scenario",
                        "part": "reflection",
                        "index": i,
                        "evidence_indices": list(reflection.evidence_indices),
                    },
                )
            )

        if not docs_to_add:
            return

        texts = [t for _, _, t, _ in docs_to_add]
        embeddings = [[] for _ in texts]
        if embedder is not None:
            started = time.monotonic()
            embeddings = await embed_texts_cached(
                embedder,
                texts,
                cache=embed_cache,
                batch_size=self.cfg.memory.embeddings_batch_size,
            )
            self._record_local_perf_call(
                "embeddings_bootstrap",
                tick,
                (time.monotonic() - started) * 1000.0,
                len(texts),
                sum(len(text) for text in texts),
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
            perf_hook=self._record_local_perf_call,
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

    @staticmethod
    def _match_existing_agent_for_social_link(
        *,
        state: WorldState,
        link_name: str,
    ) -> str | None:
        """Найти уже существующего агента по имени social-link.

        Это защищает симуляцию от онтологических дублей вида
        "agent:contractor" + "agent:sec_petrov_d_n", когда extractor
        повторно выделяет уже существующего участника мира.
        """
        normalized_link_name = normalize_agent_display_name(link_name)
        if not normalized_link_name:
            return None

        for aid, agent in state.agents.items():
            if normalize_agent_display_name(agent.name) == normalized_link_name:
                return aid
        return None

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
        existing_refs: dict[str, dict[str, Any]] = {}
        order = 0
        for primary_id, links in extraction:
            primary = state.agents[primary_id]
            for link in links:
                normalized_link_name = normalize_agent_display_name(link.name)
                if not normalized_link_name:
                    continue
                link = SocialLink(
                    name=normalized_link_name,
                    relation=link.relation,
                    relevance=link.relevance,
                    persona_hint=link.persona_hint,
                    internal=link.internal,
                    capabilities=link.capabilities,
                )
                matched_agent_id = self._match_existing_agent_for_social_link(
                    state=state,
                    link_name=link.name,
                )
                if matched_agent_id is not None:
                    # Не создаём "теневых" клонов уже существующих агентов.
                    if matched_agent_id == primary_id:
                        logger.info(
                            "Skipping self-link secondary spawn for %s -> %s",
                            primary_id,
                            link.name,
                        )
                        continue
                    ref_bucket = existing_refs.setdefault(
                        matched_agent_id,
                        {
                            "link_name": link.name,
                            "sources": [],
                        },
                    )
                    ref_bucket["sources"].append((primary_id, primary.name, link.relation))
                    logger.info(
                        "Reusing existing agent %s for social link %r from %s",
                        matched_agent_id,
                        link.name,
                        primary_id,
                    )
                    continue
                if looks_like_machine_name(link.name):
                    logger.info(
                        "Skipping machine-like secondary social link %r (%s) from %s",
                        link.name,
                        link.relation,
                        primary_id,
                    )
                    continue
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

        for existing_agent_id, meta in existing_refs.items():
            existing_agent = state.agents.get(existing_agent_id)
            if existing_agent is None:
                continue
            for primary_id, primary_name, relation in meta["sources"]:
                primary = state.agents.get(primary_id)
                if primary is not None:
                    self._attach_relation_memory(
                        agent=primary,
                        tick=state.tick,
                        text=f"{relation}: {existing_agent.name} ({existing_agent.agent_id})",
                    )
                self._attach_relation_memory(
                    agent=existing_agent,
                    tick=state.tick,
                    text=f"Связь: {relation} агента {primary_name} ({primary_id})",
                )

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
                mode=self.cfg.runtime.persona_enrich_mode,
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

        self._refresh_story_states(state=state, tick_events=[], agent_ids=pending)

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
        reserved_name_keys = {
            social_link_name_key(agent.name)
            for agent in state.agents.values()
            if social_link_name_key(agent.name)
        }
        ops: list[StateOp] = []
        for spawn in spawns:
            if current_count + len(ops) >= self.cfg.runtime.max_agents:
                break
            if spawn.internal and not self.cfg.runtime.worldgen_allow_internal_spawns:
                continue
            display_name = normalize_agent_display_name(spawn.name, fallback=spawn.slug)
            if not display_name:
                continue
            if self._match_existing_agent_for_social_link(state=state, link_name=display_name) is not None:
                continue
            display_name_key = social_link_name_key(display_name)
            matched_reserved_key = (
                social_link_match_key(display_name_key, list(reserved_name_keys))
                if display_name_key
                else ""
            )
            if matched_reserved_key and matched_reserved_key in reserved_name_keys:
                continue
            slug = normalize_slug(spawn.slug, fallback=display_name or "spawned")
            entity_id = make_id(EntityKind.AGENT, slug)
            if entity_id in existing_ids:
                continue
            existing_ids.add(entity_id)
            if display_name_key:
                reserved_name_keys.add(display_name_key)
            org_id = str(spawn.org_id or "").strip() or None
            zone_id = str(spawn.zone_id or "").strip() or None
            if org_id and not state.registry.exists(org_id):
                continue
            if zone_id and not state.registry.exists(zone_id):
                continue
            ops.append(
                CreateAgentOp(
                    entity_id=entity_id,
                    name=display_name,
                    internal=bool(spawn.internal),
                    persona_hint=spawn.persona_hint,
                    capabilities=Arbiter._sanitize_spawn_capabilities([], internal=bool(spawn.internal)),
                    org_id=org_id,
                    zone_id=zone_id,
                    spawn_source="worldgen",
                    created_by=None,
                    created_tick=state.tick,
                )
            )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="worldgen_spawn")

    def _apply_population_blueprints(
        self,
        *,
        state: WorldState,
        event_log: EventLog,
        activation: str,
    ) -> list[Event]:
        current_count = len(state.agents)
        if current_count >= self.cfg.runtime.max_agents:
            return []

        existing_ids = set(state.registry.list_ids()) | set(state.agents.keys())
        reserved_name_keys = {
            social_link_name_key(agent.name)
            for agent in state.agents.values()
            if social_link_name_key(agent.name)
        }
        ops: list[StateOp] = []

        for blueprint in self.cfg.world.environment.population_blueprints:
            if blueprint.activation != activation or int(blueprint.desired_count) <= 0:
                continue
            current_blueprint_agents = [
                agent for agent in state.agents.values() if agent.blueprint_id == blueprint.blueprint_id
            ]
            missing = int(blueprint.desired_count) - len(current_blueprint_agents)
            if missing <= 0:
                continue
            if blueprint.org_id and not state.registry.exists(blueprint.org_id):
                continue
            if blueprint.zone_id and not state.registry.exists(blueprint.zone_id):
                continue

            used_names = {
                social_link_name_key(agent.name)
                for agent in current_blueprint_agents
                if social_link_name_key(agent.name)
            }
            pool = [name for name in blueprint.name_pool if social_link_name_key(name) not in used_names]
            next_index = len(current_blueprint_agents) + 1

            for _ in range(missing):
                if current_count + len(ops) >= self.cfg.runtime.max_agents:
                    break
                current_label_index = next_index
                next_index += 1
                if pool:
                    display_name = normalize_agent_display_name(pool.pop(0), fallback=blueprint.role_label)
                else:
                    display_name = normalize_agent_display_name(
                        f"{blueprint.role_label} {current_label_index}",
                        fallback=blueprint.role_label,
                    )
                if not display_name:
                    continue
                display_name_key = social_link_name_key(display_name)
                if display_name_key and display_name_key in reserved_name_keys:
                    continue
                slug = normalize_slug(
                    f"{blueprint.blueprint_id}_{current_label_index}",
                    fallback=blueprint.blueprint_id or blueprint.role_label,
                )
                entity_id = make_unique_id(EntityKind.AGENT, slug, existing_ids=existing_ids, fallback="peripheral")
                existing_ids.add(entity_id)
                if display_name_key:
                    reserved_name_keys.add(display_name_key)
                ops.append(
                    CreateAgentOp(
                        entity_id=entity_id,
                        name=display_name,
                        internal=bool(blueprint.internal),
                        persona_hint=blueprint.persona_hint,
                        capabilities=Arbiter._sanitize_spawn_capabilities(
                            list(blueprint.capabilities),
                            internal=bool(blueprint.internal),
                        ),
                        created_by=None,
                        created_tick=state.tick,
                        org_id=blueprint.org_id,
                        zone_id=blueprint.zone_id,
                        spawn_source="population_blueprint",
                        blueprint_id=blueprint.blueprint_id,
                        population_role=blueprint.role_label,
                    )
                )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="population_blueprint")

    def _apply_worldgen_environment_updates(
        self,
        *,
        state: WorldState,
        updates: EnvironmentUpdates,
        event_log: EventLog,
    ) -> list[Event]:
        if (
            not updates.institutions
            and not updates.zones
            and not updates.resource_pools
            and updates.information_climate is None
            and not updates.informal_links
        ):
            return []

        ops: list[StateOp] = []
        for item in updates.institutions:
            ops.append(
                UpdateInstitutionRegimeOp(
                    org_id=item.org_id,
                    operating_mode=item.operating_mode,
                    transparency_mode=item.transparency_mode,
                    access_mode=item.access_mode,
                    security_mode=item.security_mode,
                    capture_risk=item.capture_risk,
                    linked_zone_ids=list(item.linked_zone_ids) if item.linked_zone_ids is not None else None,
                )
            )
        for item in updates.zones:
            ops.append(
                UpdateZoneStateOp(
                    zone_id=item.zone_id,
                    access_mode=item.access_mode,
                    transparency_mode=item.transparency_mode,
                    security_level=item.security_level,
                )
            )
        for item in updates.resource_pools:
            ops.append(
                UpdateResourcePoolOp(
                    resource_id=item.resource_id,
                    quantity=item.quantity,
                    status=item.status,
                    pressure=item.pressure,
                )
            )
        climate = updates.information_climate
        if climate is not None:
            ops.append(
                UpdateInformationClimateOp(
                    public_mood=climate.public_mood,
                    oversight_attention=climate.oversight_attention,
                    media_pressure=climate.media_pressure,
                    narrative_temperature=climate.narrative_temperature,
                    active_signals=list(climate.active_signals) if climate.active_signals is not None else None,
                )
            )
        for item in updates.informal_links:
            ops.append(
                UpsertInformalLinkOp(
                    actor_id=None,
                    agent_a_id=item.agent_a_id,
                    agent_b_id=item.agent_b_id,
                    link_type=item.link_type,
                    strength=item.strength,
                    visibility=item.visibility,
                    pressure=item.pressure,
                    source=item.source or "worldgen",
                )
            )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="worldgen_environment")

    def _apply_worldgen_entity_changes(
        self,
        *,
        state: WorldState,
        creations: list[dict[str, Any]],
        event_log: EventLog,
    ) -> list[Event]:
        """Материализовать новые внешние сущности, предложенные worldgen."""

        if not creations:
            return []

        kind_map = {
            "org": EntityKind.ORG,
            "chan": EntityKind.CHANNEL,
            "zone": EntityKind.ZONE,
            "res": EntityKind.RESOURCE,
        }
        priority = {
            "org": 0,
            "chan": 1,
            "zone": 2,
            "res": 3,
        }
        ops: list[StateOp] = []
        existing_ids = set(state.registry.list_ids())

        for item in sorted(creations, key=lambda data: (priority.get(str(data.get("kind") or "").strip(), 99), str(data.get("entity_id") or ""))):
            entity_id = str(item.get("entity_id") or "").strip()
            kind_raw = str(item.get("kind") or "").strip()
            title = str(item.get("title") or "").strip()
            if not entity_id or not kind_raw or not title:
                continue
            kind = kind_map.get(kind_raw)
            if kind is None:
                continue
            try:
                parsed = parse_typed_id(entity_id)
            except ValueError:
                entity_id = make_id(kind, normalize_slug(entity_id, fallback=kind_raw))
            else:
                if parsed.kind != kind:
                    entity_id = make_id(kind, normalize_slug(parsed.slug, fallback=kind_raw))
            if entity_id in existing_ids:
                continue
            existing_ids.add(entity_id)
            meta: dict[str, Any] = {
                "title": title,
                "description": str(item.get("description") or "").strip(),
            }
            if kind == EntityKind.ZONE:
                meta["zone_type"] = str(item.get("zone_type") or "office").strip() or "office"
                meta["primary_org_id"] = str(item.get("primary_org_id") or "").strip() or None
            if kind == EntityKind.RESOURCE:
                meta["owner_org_id"] = str(item.get("owner_org_id") or "").strip() or None
                meta["unit"] = str(item.get("unit") or "").strip()
            ops.append(
                CreateEntityOp(
                    entity_id=entity_id,
                    kind=kind,
                    created_by=None,
                    created_tick=state.tick,
                    meta=meta,
                )
            )

        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="worldgen_entity")

    def _apply_worldgen_artifact_changes(
        self,
        *,
        state: WorldState,
        creations: list[dict[str, Any]],
        updates: list[dict[str, Any]],
        event_log: EventLog,
    ) -> list[Event]:
        if not creations and not updates:
            return []

        ops: list[StateOp] = []
        existing_ids = set(state.registry.list_ids()) | set(state.artifacts.keys())

        def _normalize_worldgen_artifact_id(raw_id: Any) -> str:
            artifact_id = str(raw_id or "").strip()
            if not artifact_id:
                return ""
            if artifact_id.startswith("artifact:"):
                artifact_id = f"art:{artifact_id.split(':', 1)[1]}"
            try:
                parsed = parse_typed_id(artifact_id)
            except ValueError:
                slug = artifact_id.split(":", 1)[1] if ":" in artifact_id else artifact_id
                return make_id(EntityKind.ARTIFACT, normalize_slug(slug, fallback="artifact"))
            if parsed.kind == EntityKind.ARTIFACT:
                return artifact_id
            return make_id(EntityKind.ARTIFACT, normalize_slug(parsed.slug, fallback="artifact"))

        for item in creations:
            artifact_id = _normalize_worldgen_artifact_id(item.get("artifact_id"))
            artifact_type = str(item.get("artifact_type") or "").strip()
            title = str(item.get("title") or "").strip()
            if not artifact_id or not artifact_type or not title or artifact_id in existing_ids:
                continue
            existing_ids.add(artifact_id)
            ops.append(
                CreateArtifactOp(
                    created_by=None,
                    artifact_id=artifact_id,
                    artifact_type=artifact_type,
                    title=title,
                    summary=str(item.get("summary") or "").strip(),
                    owner_org_id=str(item.get("owner_org_id") or "").strip() or None,
                    zone_id=str(item.get("zone_id") or "").strip() or None,
                    related_work_id=str(item.get("related_work_id") or "").strip() or None,
                    visibility=str(item.get("visibility") or "internal").strip() or "internal",
                    status=str(item.get("status") or "active").strip() or "active",
                    tags=[str(tag).strip() for tag in item.get("tags") or [] if str(tag).strip()],
                )
            )

        for item in updates:
            artifact_id = _normalize_worldgen_artifact_id(item.get("artifact_id"))
            if not artifact_id or artifact_id not in state.artifacts:
                continue
            ops.append(
                UpdateArtifactOp(
                    actor_id=None,
                    artifact_id=artifact_id,
                    title=str(item.get("title") or "").strip() or None,
                    summary=str(item.get("summary") or "").strip() or None,
                    status=str(item.get("status") or "").strip() or None,
                    visibility=str(item.get("visibility") or "").strip() or None,
                    tags=(
                        [str(tag).strip() for tag in item.get("tags") or [] if str(tag).strip()]
                        if item.get("tags") is not None
                        else None
                    ),
                )
            )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="worldgen_artifact")


    def _apply_environment_material_consequences(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        event_log: EventLog,
    ) -> list[Event]:
        ops: list[StateOp] = []
        emitted: list[Event] = []
        handled_resources: set[str] = set()
        stable_statuses = {"stable", "normal", "ok", "routine"}

        for event in tick_events:
            if event.event_type != "environment_resource_updated":
                continue
            resource_id = str((event.payload or {}).get("resource_id") or "").strip()
            if not resource_id or resource_id in handled_resources:
                continue
            handled_resources.add(resource_id)

            pool = state.environment.resource_pools.get(resource_id)
            if pool is None:
                continue

            parsed = parse_typed_id(resource_id)
            alert_id = make_id(
                EntityKind.ARTIFACT,
                f"resource_alert_{normalize_slug(parsed.slug, fallback='resource')}",
            )
            title = f"Сигнал по ресурсу: {pool.title or resource_id}"
            summary = (
                f"Ресурс {pool.title or resource_id} перешёл в состояние {pool.status or 'strained'} "
                f"при остатке {pool.quantity}{(' ' + pool.unit) if pool.unit else ''}."
            ).strip()
            if pool.pressure:
                summary = f"{summary} Давление: {pool.pressure}".strip()

            is_pressure = bool((pool.pressure or "").strip()) or pool.quantity <= 0 or (pool.status or "").strip().lower() not in stable_statuses
            if is_pressure:
                if alert_id in state.artifacts:
                    ops.append(
                        UpdateArtifactOp(
                            actor_id=None,
                            artifact_id=alert_id,
                            title=title,
                            summary=summary,
                            status="active",
                            visibility="internal",
                            tags=["resource", "pressure"],
                        )
                    )
                else:
                    ops.append(
                        CreateArtifactOp(
                            created_by=None,
                            artifact_id=alert_id,
                            artifact_type="resource_alert",
                            title=title,
                            summary=summary,
                            owner_org_id=pool.owner_org_id,
                            related_work_id=None,
                            visibility="internal",
                            status="active",
                            tags=["resource", "pressure"],
                        )
                    )
                emitted.append(
                    Event(
                        tick=state.tick,
                        event_type="world_event",
                        actor_id=None,
                        payload={
                            "source": "resource_pressure",
                            "resource_id": resource_id,
                            "description": summary,
                        },
                        audience=[INTERNAL_AUDIENCE],
                    )
                )
            elif alert_id in state.artifacts and state.artifacts[alert_id].status != "resolved":
                ops.append(
                    UpdateArtifactOp(
                        actor_id=None,
                        artifact_id=alert_id,
                        summary=f"Ресурс {pool.title or resource_id} вернулся в стабильное состояние.",
                        status="resolved",
                        visibility="internal",
                    )
                )
                emitted.append(
                    Event(
                        tick=state.tick,
                        event_type="world_event",
                        actor_id=None,
                        payload={
                            "source": "resource_recovery",
                            "resource_id": resource_id,
                            "description": f"Ресурс {pool.title or resource_id} стабилизирован.",
                        },
                        audience=[INTERNAL_AUDIENCE],
                    )
                )

        if ops:
            emitted = self._apply_ops(
                state=state,
                ops=ops,
                event_log=event_log,
                origin="environment_material",
            ) + emitted
        if emitted:
            world_events = [event for event in emitted if event.event_type == "world_event"]
            if world_events:
                event_log.extend(world_events)
        return emitted

    @staticmethod
    def _make_pending_interaction_id(*parts: str) -> str:
        basis = "||".join(str(part or "").strip() for part in parts)
        digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
        slug_parts = [normalize_slug(str(part or "")) for part in parts[:4] if str(part or "").strip()]
        slug = "_".join(part for part in slug_parts if part)[:48].strip("_") or "item"
        return f"pend:{slug}_{digest}"

    def _pending_schedule_for_category(
        self,
        *,
        state: WorldState,
        category: str,
    ) -> tuple[int, int]:
        """Вернуть окно реакции для pending-interaction текущего тика."""
        current_tick = int(state.tick)
        horizon = max(0, int(self.cfg.runtime.pending_interaction_horizon_ticks))
        same_tick_categories = _SAME_TICK_PENDING_CATEGORIES
        earliest_tick = current_tick if category in same_tick_categories else current_tick + 1
        due_tick = earliest_tick + horizon + (1 if category in same_tick_categories else 0)
        return earliest_tick, due_tick


    def _expire_pending_interactions(
        self,
        *,
        state: WorldState,
        event_log: EventLog,
    ) -> list[Event]:
        ops: list[StateOp] = []
        for interaction in state.pending_interactions.values():
            if interaction.status != "open":
                continue
            if interaction.due_tick is None:
                continue
            if int(state.tick) <= int(interaction.due_tick):
                continue
            ops.append(
                ResolvePendingInteractionOp(
                    actor_id=None,
                    interaction_id=interaction.interaction_id,
                    status="expired",
                    reason="deadline_passed",
                )
            )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="pending_expire")

    def _emit_pending_interaction_due_events(
        self,
        *,
        state: WorldState,
        event_log: EventLog,
    ) -> list[Event]:
        events: list[Event] = []
        for interaction in self._open_pending_interactions(state=state):
            if interaction.last_notified_tick is not None:
                continue
            interaction.last_notified_tick = int(state.tick)
            payload = {
                "interaction_id": interaction.interaction_id,
                "target_agent_id": interaction.target_agent_id,
                "source_agent_id": interaction.source_agent_id,
                "category": interaction.category,
                "summary": interaction.summary,
                "earliest_tick": interaction.earliest_tick,
                "due_tick": interaction.due_tick,
                "priority": interaction.priority,
                "related_work_id": interaction.related_work_id,
                "artifact_id": interaction.artifact_id,
                "org_id": interaction.org_id,
                "zone_id": interaction.zone_id,
            }
            events.append(
                Event(
                    tick=state.tick,
                    event_type="pending_interaction_due",
                    actor_id=interaction.source_agent_id,
                    payload=payload,
                    audience=[interaction.target_agent_id],
                )
            )
        if events:
            event_log.extend(events)
        return events

    def _pending_interaction_ops_from_event(
        self,
        *,
        state: WorldState,
        event: Event,
    ) -> list[StateOp]:
        payload = event.payload or {}
        ops: list[StateOp] = []

        if event.event_type == "message_sent" and bool(payload.get("private", True)):
            actor_id = str(event.actor_id or "").strip()
            target_id = str(payload.get("to_id") or "").strip()
            if actor_id in state.agents and target_id in state.agents and actor_id != target_id:
                earliest_tick, due_tick = self._pending_schedule_for_category(state=state, category="reply")
                interaction_id = self._make_pending_interaction_id("reply", target_id, actor_id)
                ops.append(
                    UpsertPendingInteractionOp(
                        actor_id=actor_id,
                        interaction_id=interaction_id,
                        target_agent_id=target_id,
                        source_agent_id=actor_id,
                        category="reply",
                        summary=f"Нужно отреагировать на личное сообщение от {actor_id}.",
                        earliest_tick=earliest_tick,
                        due_tick=due_tick,
                        priority="high",
                        trigger_event_type=event.event_type,
                        org_id=state.agents[target_id].org_id,
                        zone_id=state.agents[target_id].zone_id,
                    )
                )
            return ops

        if event.event_type in {"artifact_created", "artifact_updated"}:
            artifact_id = str(payload.get("artifact_id") or "").strip()
            artifact = state.artifacts.get(artifact_id)
            if artifact is None:
                return ops
            target_ids: set[str] = set()
            if artifact.related_work_id:
                work = state.work_items.get(artifact.related_work_id)
                if work is not None:
                    target_ids.update(str(aid) for aid in work.participants if str(aid) in state.agents)
            if not target_ids:
                for aid, agent in sorted(state.agents.items()):
                    if "work" not in agent.capabilities:
                        continue
                    if artifact.owner_org_id and agent.org_id == artifact.owner_org_id:
                        target_ids.add(aid)
                    if artifact.zone_id and agent.zone_id == artifact.zone_id:
                        target_ids.add(aid)
            target_ids.discard(str(event.actor_id or "").strip())
            priority = "high" if artifact.status in {"new", "revised", "urgent"} else "normal"
            earliest_tick, due_tick = self._pending_schedule_for_category(state=state, category="artifact_follow_up")
            for target_id in sorted(target_ids):
                interaction_id = self._make_pending_interaction_id("artifact", target_id, artifact_id)
                ops.append(
                    UpsertPendingInteractionOp(
                        actor_id=event.actor_id,
                        interaction_id=interaction_id,
                        target_agent_id=target_id,
                        source_agent_id=str(event.actor_id or "").strip() or None,
                        category="artifact_follow_up",
                        summary=f"Нужно отреагировать на документ {artifact_id}: {artifact.title}.",
                        earliest_tick=earliest_tick,
                        due_tick=due_tick,
                        priority=priority,
                        trigger_event_type=event.event_type,
                        related_work_id=artifact.related_work_id,
                        artifact_id=artifact_id,
                        org_id=artifact.owner_org_id,
                        zone_id=artifact.zone_id,
                    )
                )
            return ops

        if event.event_type == "environment_resource_updated":
            resource_id = str(payload.get("resource_id") or "").strip()
            pool = state.environment.resource_pools.get(resource_id)
            if pool is None or not pool.owner_org_id:
                return ops
            is_pressure = bool((pool.pressure or "").strip()) or pool.quantity <= 0 or pool.status not in {
                "stable",
                "normal",
                "ok",
                "routine",
            }
            if not is_pressure:
                return ops
            priority = "high" if pool.quantity <= 0 or pool.status in {"critical", "depleted", "exhausted"} else "normal"
            earliest_tick, due_tick = self._pending_schedule_for_category(state=state, category="resource_pressure")
            for aid, agent in sorted(state.agents.items()):
                if agent.org_id != pool.owner_org_id or "work" not in agent.capabilities:
                    continue
                interaction_id = self._make_pending_interaction_id("resource", aid, resource_id)
                ops.append(
                    UpsertPendingInteractionOp(
                        actor_id=event.actor_id,
                        interaction_id=interaction_id,
                        target_agent_id=aid,
                        category="resource_pressure",
                        summary=(
                            f"Нужно отреагировать на изменение ресурса {resource_id}: "
                            f"статус={pool.status}, остаток={pool.quantity}{(' ' + pool.unit) if pool.unit else ''}."
                            f"{(' Давление: ' + pool.pressure) if pool.pressure else ''}"
                        ),
                        earliest_tick=earliest_tick,
                        due_tick=due_tick,
                        priority=priority,
                        trigger_event_type=event.event_type,
                        org_id=pool.owner_org_id,
                    )
                )
            return ops

        if event.event_type in {"audit_explanation_requested", "audit_documents_requested"}:
            target_id = str(payload.get("subject_agent_id") or payload.get("target_agent_id") or "").strip()
            if target_id in state.agents:
                earliest_tick, due_tick = self._pending_schedule_for_category(state=state, category="audit_response")
                audit_due_tick = payload.get("response_due_tick")
                interaction_id = self._make_pending_interaction_id("audit", target_id, str(payload.get("case_id") or event.event_type))
                ops.append(
                    UpsertPendingInteractionOp(
                        actor_id=event.actor_id,
                        interaction_id=interaction_id,
                        target_agent_id=target_id,
                        category="audit_response",
                        summary=str(payload.get("summary") or "Нужно ответить на запрос аудитора.").strip(),
                        earliest_tick=earliest_tick,
                        due_tick=int(audit_due_tick) if audit_due_tick is not None else due_tick,
                        priority="high",
                        trigger_event_type=event.event_type,
                        org_id=state.agents[target_id].org_id,
                        zone_id=state.agents[target_id].zone_id,
                    )
                )
            return ops

        return ops

    @staticmethod
    def _event_resolves_pending_interaction(
        *,
        interaction: PendingInteractionState,
        event: Event,
    ) -> bool:
        if interaction.status != "open":
            return False
        if str(event.actor_id or "").strip() != interaction.target_agent_id:
            return False
        payload = event.payload or {}
        if interaction.category == "reply":
            return (
                event.event_type == "message_sent"
                and interaction.source_agent_id is not None
                and str(payload.get("to_id") or "").strip() == interaction.source_agent_id
            )
        if interaction.category == "artifact_follow_up":
            if interaction.artifact_id and event.event_type == "artifact_updated":
                return str(payload.get("artifact_id") or "").strip() == interaction.artifact_id
            if interaction.related_work_id and event.event_type in {"work_note_added", "work_proposal_submitted"}:
                return str(payload.get("work_id") or "").strip() == interaction.related_work_id
            return event.event_type == "message_sent"
        if interaction.category == "resource_pressure":
            return event.event_type in {
                "work_item_created",
                "work_note_added",
                "work_proposal_submitted",
                "artifact_updated",
                "message_sent",
            }
        if interaction.category == "audit_response":
            return event.event_type in {
                "message_sent",
                "artifact_updated",
                "work_note_added",
                "work_proposal_submitted",
            }
        return event.event_type in {"message_sent", "work_note_added", "work_proposal_submitted", "artifact_updated"}

    def _apply_pending_interaction_updates(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        event_log: EventLog,
    ) -> list[Event]:
        if not tick_events:
            return []

        create_ops: list[StateOp] = []
        for event in tick_events:
            create_ops.extend(self._pending_interaction_ops_from_event(state=state, event=event))

        emitted: list[Event] = []
        if create_ops:
            emitted.extend(
                self._apply_ops(
                    state=state,
                    ops=create_ops,
                    event_log=event_log,
                    origin="pending_create",
                )
            )

        resolve_ops: list[StateOp] = []
        for interaction in list(state.pending_interactions.values()):
            if interaction.status != "open":
                continue
            for event in tick_events:
                if self._event_resolves_pending_interaction(interaction=interaction, event=event):
                    resolve_ops.append(
                        ResolvePendingInteractionOp(
                            actor_id=interaction.target_agent_id,
                            interaction_id=interaction.interaction_id,
                            status="completed",
                            reason=f"matched:{event.event_type}",
                        )
                    )
                    break
        if resolve_ops:
            emitted.extend(
                self._apply_ops(
                    state=state,
                    ops=resolve_ops,
                    event_log=event_log,
                    origin="pending_resolve",
                )
            )
        return emitted

    def _apply_interaction_network_updates(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        event_log: EventLog,
    ) -> list[Event]:
        ops: list[StateOp] = []
        for event in tick_events:
            payload = event.payload or {}
            if event.event_type == "message_sent" and bool(payload.get("private", True)):
                actor_id = str(event.actor_id or "").strip()
                to_id = str(payload.get("to_id") or "").strip()
                if actor_id.startswith("agent:") and to_id.startswith("agent:") and actor_id != to_id:
                    ops.append(
                        UpsertInformalLinkOp(
                            actor_id=actor_id,
                            agent_a_id=actor_id,
                            agent_b_id=to_id,
                            link_type="private_contact",
                            strength_delta=0.1,
                            visibility="latent",
                            source="interaction",
                        )
                    )
            if event.event_type in {"work_note_added", "work_proposal_submitted"}:
                actor_id = str(event.actor_id or "").strip()
                work_id = str(payload.get("work_id") or "").strip()
                work = state.work_items.get(work_id)
                if work is None or not actor_id.startswith("agent:"):
                    continue
                for participant_id in work.participants:
                    participant_id = str(participant_id).strip()
                    if not participant_id.startswith("agent:") or participant_id == actor_id:
                        continue
                    ops.append(
                        UpsertInformalLinkOp(
                            actor_id=actor_id,
                            agent_a_id=actor_id,
                            agent_b_id=participant_id,
                            link_type="coordination",
                            strength_delta=0.05,
                            visibility="latent",
                            source="interaction",
                        )
                    )
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="interaction_network")

    def _personas_cache_path(self) -> Path:
        return self.artifacts.out_dir / "personas.json"

    def _personas_global_cache_path(self, *, cache_input: dict[str, Any]) -> Path:
        fingerprint = self._personas_cache_fingerprint(cache_input)
        return self.artifacts.out_dir.parent / "_persona_cache" / f"{fingerprint}.json"

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

    def _persona_is_cache_complete(self, persona: PersonaArtifact) -> bool:
        if not persona.biography.strip():
            return False
        if self.cfg.runtime.persona_enrich_mode != "full":
            return True
        if len([qa for qa in persona.interview if (qa.question or "").strip() and (qa.answer or "").strip()]) < len(
            INTERVIEW_QUESTIONS_V2
        ):
            return False
        if len([item for item in persona.reflections if (item.expert or "").strip() and (item.summary or "").strip()]) < 2:
            return False
        return True

    def _load_personas_cache(
        self, *, state: WorldState, cache_input: dict[str, Any]
    ) -> dict[str, PersonaArtifact]:
        expected = self._personas_cache_fingerprint(cache_input)
        cache_paths = [self._personas_cache_path(), self._personas_global_cache_path(cache_input=cache_input)]

        for path in cache_paths:
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            meta = raw.get("meta")
            personas_raw = raw.get("personas")
            if not isinstance(meta, dict) or not isinstance(personas_raw, dict):
                continue
            if str(meta.get("fingerprint") or "") != expected:
                continue

            loaded: dict[str, PersonaArtifact] = {}
            for aid in sorted(state.agents.keys()):
                item = personas_raw.get(aid)
                if item is None:
                    continue
                try:
                    persona = PersonaArtifact.model_validate(item)
                except Exception:
                    continue
                if not self._persona_is_cache_complete(persona):
                    continue
                loaded[aid] = persona
            if loaded:
                return loaded
        return {}

    def _save_personas_cache(
        self, *, cache_input: dict[str, Any], personas: dict[str, PersonaArtifact]
    ) -> None:
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
        for path in (self._personas_cache_path(), self._personas_global_cache_path(cache_input=cache_input)):
            path.parent.mkdir(parents=True, exist_ok=True)
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
            if cached_persona is None or not self._persona_is_cache_complete(cached_persona):
                pending.append(aid)
                continue
            state.agents[aid].persona = cached_persona

        if not pending:
            logger.info(
                "Loaded persona cache for %d agents from %s",
                len(to_enrich_ids),
                self._personas_global_cache_path(cache_input=cache_input),
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

        all_enriched = all(self._persona_is_cache_complete(state.agents[aid].persona) for aid in to_enrich_ids)
        if not all_enriched:
            return
        try:
            personas = {aid: agent.persona for aid, agent in state.agents.items()}
            self._save_personas_cache(cache_input=cache_input, personas=personas)
        except Exception as exc:
            logger.warning("Failed to write personas cache: %s", exc)

    def _init_state(self, *, event_log: EventLog) -> WorldState:
        state = WorldState(tick=0, registry=EntityRegistry(), environment=EnvironmentState())

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

        for zone in self.cfg.world.environment.zones:
            if zone.primary_org_id is not None and not state.registry.exists(zone.primary_org_id):
                raise ValueError(f"Unknown primary_org_id for zone {zone.zone_id!r}: {zone.primary_org_id!r}")
            if state.registry.exists(zone.zone_id):
                continue
            op = CreateEntityOp(
                entity_id=zone.zone_id,
                kind=EntityKind.ZONE,
                created_by=None,
                created_tick=0,
                meta={
                    "title": zone.title,
                    "zone_type": zone.zone_type,
                    "primary_org_id": zone.primary_org_id,
                },
            )
            event_log.extend(op.apply(state))
            state.environment.zones[zone.zone_id] = ZoneState(
                zone_id=zone.zone_id,
                title=zone.title,
                zone_type=zone.zone_type,
                primary_org_id=zone.primary_org_id,
                access_mode=zone.access_mode,
                transparency_mode=zone.transparency_mode,
                security_level=zone.security_level,
            )

        for org in self.cfg.world.orgs:
            state.environment.institutions[org.org_id] = InstitutionRegimeState(org_id=org.org_id)
        for mode in self.cfg.world.environment.institution_modes:
            if not state.registry.exists(mode.org_id):
                raise ValueError(f"Unknown org_id in institution_modes: {mode.org_id!r}")
            for zone_id in mode.linked_zone_ids:
                if zone_id not in state.environment.zones:
                    raise ValueError(f"Unknown linked zone for institution {mode.org_id!r}: {zone_id!r}")
            state.environment.institutions[mode.org_id] = InstitutionRegimeState(
                org_id=mode.org_id,
                operating_mode=mode.operating_mode,
                transparency_mode=mode.transparency_mode,
                access_mode=mode.access_mode,
                security_mode=mode.security_mode,
                capture_risk=mode.capture_risk,
                linked_zone_ids=list(mode.linked_zone_ids),
            )

        for pool in self.cfg.world.environment.resource_pools:
            if pool.owner_org_id is not None and not state.registry.exists(pool.owner_org_id):
                raise ValueError(f"Unknown owner_org_id for resource pool {pool.resource_id!r}: {pool.owner_org_id!r}")
            if state.registry.exists(pool.resource_id):
                continue
            op = CreateEntityOp(
                entity_id=pool.resource_id,
                kind=EntityKind.RESOURCE,
                created_by=None,
                created_tick=0,
                meta={"title": pool.title, "owner_org_id": pool.owner_org_id, "unit": pool.unit},
            )
            event_log.extend(op.apply(state))
            state.environment.resource_pools[pool.resource_id] = ResourcePoolState(
                resource_id=pool.resource_id,
                title=pool.title,
                owner_org_id=pool.owner_org_id,
                quantity=float(pool.quantity),
                unit=pool.unit,
                status=pool.status,
                pressure=pool.pressure,
            )

        info = self.cfg.world.environment.information_climate
        state.environment.information_climate = InformationClimateState(
            public_mood=info.public_mood,
            oversight_attention=info.oversight_attention,
            media_pressure=info.media_pressure,
            narrative_temperature=info.narrative_temperature,
            active_signals=list(info.active_signals),
        )
        configured_agent_ids = {agent.agent_id for agent in self.cfg.agents}
        for link in self.cfg.world.environment.informal_links:
            if link.agent_a_id not in configured_agent_ids:
                raise ValueError(f"Unknown agent_a_id in informal_links: {link.agent_a_id!r}")
            if link.agent_b_id not in configured_agent_ids:
                raise ValueError(f"Unknown agent_b_id in informal_links: {link.agent_b_id!r}")
            link_id = informal_link_key(link.agent_a_id, link.agent_b_id, link.link_type)
            left, right = sorted([link.agent_a_id, link.agent_b_id])
            state.environment.informal_links[link_id] = InformalLinkState(
                link_id=link_id,
                agent_a_id=left,
                agent_b_id=right,
                link_type=link.link_type,
                strength=float(link.strength),
                visibility=link.visibility,
                pressure=link.pressure,
                source=link.source,
                last_updated_tick=0,
            )

        for a in self.cfg.agents:
            agent_meta = {
                "name": a.name,
                "internal": a.internal,
                "capabilities": list(a.capabilities),
                "org_id": a.org_id,
                "zone_id": a.zone_id,
                "spawn_source": "scenario",
            }
            state.registry.register(
                EntityRecord(
                    entity_id=a.agent_id,
                    kind=EntityKind.AGENT,
                    created_by=None,
                    created_tick=0,
                    meta=agent_meta,
                )
            )
            state.agents[a.agent_id] = AgentState(
                agent_id=a.agent_id,
                name=a.name,
                internal=a.internal,
                persona=a.persona,
                capabilities=list(a.capabilities),
                org_id=a.org_id,
                zone_id=a.zone_id,
                spawn_source="scenario",
                reputation=float(a.initial_reputation),
                title=a.initial_title,
                wants_promotion=a.wants_promotion,
            )
            event_log.extend(
                [
                    Event(
                        tick=0,
                        event_type="entity_created",
                        actor_id=None,
                        payload={
                            "entity_id": a.agent_id,
                            "kind": EntityKind.AGENT.value,
                            "meta": agent_meta,
                        },
                        audience=[INTERNAL_AUDIENCE],
                    ),
                    Event(
                        tick=0,
                        event_type="reputation_snapshot",
                        actor_id=None,
                        payload={
                            "target_agent_id": a.agent_id,
                            "score": state.agents[a.agent_id].reputation,
                            "internal": state.agents[a.agent_id].internal,
                            "frozen": state.agents[a.agent_id].reputation_frozen,
                            "frozen_until_tick": state.agents[a.agent_id].reputation_frozen_until_tick,
                            "title": state.agents[a.agent_id].title,
                        },
                        audience=[INTERNAL_AUDIENCE],
                    ),
                ]
            )

        for w in self.cfg.world.work_items:
            op = CreateWorkItemOp(
                created_by=None,
                work_id=w.work_id,
                work_type=w.work_type,
                title=w.title,
                description=w.description,
                participants=list(w.participants),
            )
            event_log.extend(op.apply(state))

        for artifact in self.cfg.world.artifacts:
            op = CreateArtifactOp(
                created_by=None,
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type,
                title=artifact.title,
                summary=artifact.summary,
                owner_org_id=artifact.owner_org_id,
                zone_id=artifact.zone_id,
                related_work_id=artifact.related_work_id,
                visibility=artifact.visibility,
                status=artifact.status,
                tags=list(artifact.tags),
            )
            event_log.extend(op.apply(state))

        bootstrap_population_events = self._apply_population_blueprints(
            state=state,
            event_log=event_log,
            activation="bootstrap",
        )
        if bootstrap_population_events:
            logger.info("Bootstrapped %d blueprint-driven peripheral actors", len(bootstrap_population_events))

        return state

    async def _gather_actions(
        self,
        *,
        state: WorldState,
        runners: dict[str, AgentRunner],
        events_history: list[Event],
        agent_order: list[str],
        daily_contexts: dict[str, AgentDailyContext] | None = None,
        scene_hooks_by_agent: dict[str, list[SceneHook]] | None = None,
        agent_filter: list[str] | None = None,
        max_actions_override: int | None = None,
        turn_note: str | None = None,
    ) -> tuple[dict[str, list[Action]], list[Event]]:
        async def _one(aid: str) -> tuple[str, list[Action], Event | None]:
            agent = state.agents[aid]
            visible = [ev for ev in events_history if event_visible_to_agent(ev, aid, internal=agent.internal)]
            try:
                acts = await runners[aid].propose_actions(
                    agent=agent,
                    state=state,
                    visible_events=visible,
                    daily_context=(daily_contexts or {}).get(aid),
                    scene_hooks=(scene_hooks_by_agent or {}).get(aid, []),
                    max_actions_override=max_actions_override,
                    turn_note=turn_note,
                )
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

        if agent_filter is not None:
            order = [aid for aid in agent_filter if aid in state.agents]
        else:
            order = list(agent_order) if agent_order else sorted(state.agents.keys())
            order = [
                aid
                for aid in order
                if self._should_activate_agent(
                    state=state,
                    agent_id=aid,
                    events_history=events_history,
                    daily_contexts=daily_contexts,
                    scene_hooks_by_agent=scene_hooks_by_agent,
                )
            ]
        if not self.cfg.runtime.parallel_agents or len(order) <= 1:
            pairs = []
            for aid in order:
                pairs.append(await _one(aid))
        else:
            max_parallel = self.cfg.runtime.parallel_workers
            if max_parallel is not None:
                sem = asyncio.Semaphore(max_parallel)

                async def _one_limited(aid: str) -> tuple[str, list[Action], Event | None]:
                    async with sem:
                        return await _one(aid)

                tasks = [_one_limited(aid) for aid in order]
            else:
                tasks = [_one(aid) for aid in order]
            pairs = await asyncio.gather(*tasks)
        gathered: dict[str, list[Action]] = {}
        errors: list[Event] = []
        for aid, acts, err in pairs:
            gathered[aid] = acts
            if err is not None:
                errors.append(err)
        return gathered, errors

    def _select_reactive_agent_ids(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        already_acted: set[str],
        limit: int,
    ) -> list[str]:
        if limit <= 0:
            return []

        ordered: list[str] = []
        seen: set[str] = set(already_acted)

        def _push(aid: str) -> None:
            if not aid or aid in seen or aid not in state.agents:
                return
            seen.add(aid)
            ordered.append(aid)

        for event in tick_events:
            payload = event.payload or {}
            if event.event_type == "message_sent":
                to_id = str(payload.get("to_id") or "")
                if bool(payload.get("private", True)):
                    _push(to_id)
                elif to_id and to_id.startswith("org:"):
                    for aid, agent in sorted(state.agents.items()):
                        if agent.org_id == to_id:
                            _push(aid)
                continue

            if event.event_type == "scene_occurred":
                for aid in payload.get("agents") or []:
                    _push(str(aid))
                continue

            if event.event_type == "work_item_created":
                for aid in payload.get("participants") or []:
                    _push(str(aid))
                continue

            if event.event_type in {"work_note_added", "work_proposal_submitted"}:
                work_id = str(payload.get("work_id") or "")
                work = state.work_items.get(work_id)
                if work is not None:
                    for aid in work.participants:
                        _push(str(aid))
                continue

            if event.event_type in {"artifact_created", "artifact_updated"}:
                artifact_id = str(payload.get("artifact_id") or "")
                artifact = state.artifacts.get(artifact_id)
                if artifact is None:
                    continue
                if artifact.owner_org_id:
                    for aid, agent in sorted(state.agents.items()):
                        if agent.org_id == artifact.owner_org_id:
                            _push(aid)
                if artifact.zone_id:
                    for aid, agent in sorted(state.agents.items()):
                        if agent.zone_id == artifact.zone_id:
                            _push(aid)
                if artifact.related_work_id:
                    work = state.work_items.get(artifact.related_work_id)
                    if work is not None:
                        for aid in work.participants:
                            _push(str(aid))
                continue

            if event.event_type in {"vote_opened", "audit_case_opened", "audit_case_updated", "reputation_frozen"}:
                _push(str(payload.get("target_agent_id") or payload.get("subject_agent_id") or ""))
                continue

            if event.event_type == "pending_interaction_due":
                _push(str(payload.get("target_agent_id") or ""))
                continue

            if event.event_type == "environment_institution_updated":
                org_id = str(payload.get("org_id") or "")
                for aid, agent in sorted(state.agents.items()):
                    if agent.org_id == org_id:
                        _push(aid)
                continue

            if event.event_type == "environment_zone_updated":
                zone_id = str(payload.get("zone_id") or "")
                for aid, agent in sorted(state.agents.items()):
                    if agent.zone_id == zone_id:
                        _push(aid)
                continue

            if event.event_type == "environment_resource_updated":
                resource_id = str(payload.get("resource_id") or "")
                pool = state.environment.resource_pools.get(resource_id)
                if pool is not None and pool.owner_org_id:
                    for aid, agent in sorted(state.agents.items()):
                        if agent.org_id == pool.owner_org_id:
                            _push(aid)
                continue

            if event.event_type == "environment_information_climate_updated":
                for aid, agent in sorted(state.agents.items()):
                    if agent.org_id or agent.zone_id:
                        _push(aid)

            if len(ordered) >= limit:
                break

        return ordered[:limit]

    async def _run_micro_reaction_rounds(
        self,
        *,
        state: WorldState,
        runners: dict[str, AgentRunner],
        arbiter: Arbiter,
        event_log: EventLog,
        events_history: list[Event],
        tick_events: list[Event],
        already_acted: set[str],
        rounds_override: int | None = None,
    ) -> list[Event]:
        rounds = int(self.cfg.runtime.micro_reaction_rounds if rounds_override is None else rounds_override)
        limit = int(self.cfg.runtime.micro_reaction_max_agents_per_round)
        if rounds <= 0 or limit <= 0:
            return []

        emitted: list[Event] = []
        for round_index in range(rounds):
            candidates = self._select_reactive_agent_ids(
                state=state,
                tick_events=tick_events,
                already_acted=already_acted,
                limit=limit,
            )
            if not candidates:
                break

            visible_history = list(events_history) + list(tick_events)
            proposed, gather_errors = await self._gather_actions(
                state=state,
                runners=runners,
                events_history=visible_history,
                agent_order=candidates,
                agent_filter=candidates,
                max_actions_override=1,
                turn_note=(
                    f"Это локальное окно реакции внутри того же тика. "
                    f"Ты реагируешь на уже произошедшие события текущего тика. "
                    f"Сделай не больше одного короткого уместного шага. Раунд реакции: {round_index + 1}."
                ),
            )
            if gather_errors:
                event_log.extend(gather_errors)
                tick_events.extend(gather_errors)
                emitted.extend(gather_errors)

            if not any(proposed.get(aid) for aid in candidates):
                already_acted.update(candidates)
                continue

            reaction_events = await self._apply_actions(
                state=state,
                arbiter=arbiter,
                proposed=proposed,
                event_log=event_log,
                agent_order=candidates,
                journal_yaml=WorldJournal.from_state(state=state).to_yaml(),
            )
            if reaction_events:
                tick_events.extend(reaction_events)
                emitted.extend(reaction_events)

            already_acted.update([aid for aid, acts in proposed.items() if acts])

        return emitted

    async def _run_same_tick_pending_followups(
        self,
        *,
        state: WorldState,
        runners: dict[str, AgentRunner],
        arbiter: Arbiter,
        event_log: EventLog,
        events_history: list[Event],
        tick_events: list[Event],
    ) -> list[Event]:
        rounds = int(self.cfg.runtime.micro_reaction_rounds)
        if rounds <= 0:
            return []

        emitted: list[Event] = []
        for _ in range(rounds):
            prior_emitted = list(emitted)
            due_events = self._emit_pending_interaction_due_events(
                state=state,
                event_log=event_log,
            )
            if not due_events:
                break
            emitted.extend(due_events)

            reaction_events = await self._run_micro_reaction_rounds(
                state=state,
                runners=runners,
                arbiter=arbiter,
                event_log=event_log,
                events_history=list(events_history) + list(tick_events) + prior_emitted,
                tick_events=due_events,
                already_acted=set(),
                rounds_override=1,
            )
            if reaction_events:
                emitted.extend(reaction_events)
                network_events = self._apply_interaction_network_updates(
                    state=state,
                    tick_events=reaction_events,
                    event_log=event_log,
                )
                if network_events:
                    emitted.extend(network_events)
                pending_events = self._apply_pending_interaction_updates(
                    state=state,
                    tick_events=list(reaction_events) + list(network_events),
                    event_log=event_log,
                )
                if pending_events:
                    emitted.extend(pending_events)

        return emitted

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
                if self._should_ignore_op_failure(origin=origin, op=op, exc=exc):
                    logger.info("Ignoring idempotent op failure (%s): %s", origin, exc)
                    continue
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

    @staticmethod
    def _should_ignore_op_failure(*, origin: str, op: StateOp, exc: Exception) -> bool:
        if origin != "runtime_audit":
            return False
        name = type(op).__name__
        message = str(exc)
        if name == "OpenAuditCaseOp" and message.startswith("Audit case already exists:"):
            return True
        if name == "UpdateAuditCaseOp" and (
            message.startswith("Audit case already closed:")
            or message.startswith("Audit case not found:")
        ):
            return True
        if name == "CloseAuditCaseOp" and message.startswith("Audit case already closed:"):
            return True
        if name == "OpenVoteOp" and message.startswith("Vote already exists:"):
            return True
        return False

    def _apply_reputation_consequences(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        event_log: EventLog,
    ) -> list[Event]:
        """Применить детерминированный цикл репутации для governance-мильстоунов."""
        rewards: dict[str, float] = {}
        rewarded_keys: set[tuple[str, str]] = set()
        vote_target_rewards: set[str] = set()

        for ev in tick_events:
            if ev.event_type == "vote_target_consented":
                aid = str(ev.actor_id or "")
                if aid in state.agents and (aid, ev.event_type) not in rewarded_keys:
                    rewarded_keys.add((aid, ev.event_type))
                    rewards[aid] = rewards.get(aid, 0.0) + 0.1
                continue

            if ev.event_type == "vote_closed":
                payload = ev.payload or {}
                if str(payload.get("result") or "") != "passed":
                    continue
                vote_id = str(payload.get("vote_id") or "")
                if vote_id in vote_target_rewards:
                    continue
                vote = state.votes.get(vote_id)
                if vote is None:
                    continue
                vote_target_rewards.add(vote_id)
                target_id = vote.target_agent_id
                if target_id in state.agents:
                    rewards[target_id] = rewards.get(target_id, 0.0) + 0.75

        ops: list[StateOp] = []
        for aid, delta in sorted(rewards.items()):
            if delta <= 0.0:
                continue
            ops.append(
                ModifyReputationOp(
                    actor_id=None,
                    target_agent_id=aid,
                    delta=min(delta, 0.75),
                    reason="governance_positive_contribution",
                )
            )

        if not ops:
            return []
        return self._apply_ops(state=state, ops=ops, event_log=event_log, origin="reputation_cycle")

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
                reason = str(ev.payload.get("reason", "") or "")
                action = str(ev.payload.get("action", "") or "")
                next_step = ""
                if reason.startswith("unknown work_id: "):
                    next_step = " Следующее действие: выбери существующий work_id из списка открытых дел."
                elif reason.startswith("duplicate_open_work_item:"):
                    next_step = " Следующее действие: продолжай уже существующее дело, а не открывай клон."
                elif reason == "self_nomination_disabled":
                    next_step = " Следующее действие: номинируй другого агента или используй respond_nomination для своей кандидатуры."
                elif reason == "target_self_vote_disabled":
                    next_step = " Следующее действие: цель голосования должна ответить через respond_nomination, а не голосовать за себя."
                elif reason.startswith("temporal_date_"):
                    next_step = " Следующее действие: укажи срок внутри разумного окна от текущей канонической даты."
                if action:
                    return f"Арбитр отклонил действие {action}: {reason}.{next_step}".strip()
                return f"Арбитр отклонил действие: {reason}.{next_step}".strip()
            if ev.event_type == "arbiter_op_failed":
                return f"Операция провалилась: {redact_numbers(ev.payload.get('error',{}))}"
            if ev.event_type == "vote_opened":
                return f"Открыто голосование {ev.payload.get('vote_id','')} за {ev.payload.get('target_agent_id','')} -> {ev.payload.get('new_title','')}"
            if ev.event_type == "vote_closed":
                return (
                    f"Голосование закрыто {ev.payload.get('vote_id','')} "
                    f"result={ev.payload.get('result','')} reason={ev.payload.get('reason','')}"
                )
            if ev.event_type == "position_changed":
                return f"Должность изменена: {ev.payload.get('target_agent_id','')} -> {ev.payload.get('new_title','')}"
            if ev.event_type == "audit_flagged":
                subject_id = ev.payload.get("subject_agent_id", "") or ev.payload.get("target_agent_id", "")
                target_id = ev.payload.get("target_agent_id", "") or ev.payload.get("counterparty_agent_id", "")
                return f"Аудит пометил агента {subject_id}: {ev.payload.get('violation_type','')} -> {target_id}".rstrip(" -> ")
            if ev.event_type == "audit_case_opened":
                subject_id = ev.payload.get("subject_agent_id", "") or ev.payload.get("target_agent_id", "")
                return f"Открыт аудит-кейс {ev.payload.get('case_id','')} для {subject_id}"
            if ev.event_type == "audit_case_updated":
                return f"Аудит обновил кейс {ev.payload.get('case_id','')} episode_count={ev.payload.get('episode_count','')}"
            if ev.event_type == "audit_escalated":
                return f"Аудит эскалировал кейс {ev.payload.get('case_id','')} route={ev.payload.get('route','')}"
            if ev.event_type == "audit_explanation_requested":
                return f"Аудит запросил объяснение по кейсу {ev.payload.get('case_id','')}"
            if ev.event_type == "audit_documents_requested":
                return f"Аудит запросил документы по кейсу {ev.payload.get('case_id','')}"
            if ev.event_type == "audit_monitoring_enabled":
                return f"Аудит включил усиленное наблюдение по кейсу {ev.payload.get('case_id','')}"
            if ev.event_type == "review_case_opened":
                return f"Открыто коллегиальное review по кейсу {ev.payload.get('metadata',{}).get('case_id','')}"
            if ev.event_type == "review_case_closed":
                return f"Коллегиальное review закрыто {ev.payload.get('metadata',{}).get('case_id','')}"
            if ev.event_type == "reputation_modified":
                return (
                    f"Репутация изменена для {ev.payload.get('target_agent_id','')}: "
                    f"delta={ev.payload.get('delta','')} reason={ev.payload.get('reason','')}"
                )
            if ev.event_type == "reputation_frozen":
                return f"Репутация заморожена для {ev.payload.get('target_agent_id','')}"
            if ev.event_type == "reputation_unfrozen":
                return f"Репутация разморожена для {ev.payload.get('target_agent_id','')}"
            if ev.event_type == "world_event":
                return f"Внешнее событие: {ev.payload.get('description','')}"
            if ev.event_type == "scene_occurred":
                return f"Сцена мира: {ev.payload.get('description','')}"

            if ev.event_type == "narrative_action":
                desc = str(ev.payload.get("description") or "")
                kind = str(ev.payload.get("action_kind") or "")
                zone = str(ev.payload.get("zone_id") or "")
                witnesses = ev.payload.get("witnesses") or []
                parts = [desc]
                if zone:
                    parts.append(f"({zone})")
                if witnesses:
                    parts.append(f"в присутствии {', '.join(str(w) for w in witnesses)}")
                if ev.actor_id == agent_id:
                    return " ".join(parts)
                prefix = f"{ev.actor_id}: " if ev.actor_id else ""
                return f"{prefix}{' '.join(parts)}"

            if ev.event_type == "artifact_created":
                title = str(ev.payload.get("title") or "")
                a_type = str(ev.payload.get("artifact_type") or "документ")
                a_id = str(ev.payload.get("artifact_id") or "")
                if ev.actor_id == agent_id:
                    return f"Создан {a_type} {a_id}: {title}"
                return f"{ev.actor_id or 'Система'} создал(а) {a_type} {a_id}: {title}"

            if ev.event_type == "artifact_updated":
                a_id = str(ev.payload.get("artifact_id") or "")
                title = str(ev.payload.get("title") or "")
                status = str(ev.payload.get("status") or "")
                return f"Обновлён документ {a_id}: {title}" + (f" [{status}]" if status else "")

            if ev.event_type == "pending_interaction_created":
                summary = str(ev.payload.get("summary") or "")
                target = str(ev.payload.get("target_agent_id") or "")
                source = str(ev.payload.get("source_agent_id") or "")
                category = str(ev.payload.get("category") or "")
                if target == agent_id:
                    return f"Ожидается действие от тебя ({category}): {summary}" + (f" (от {source})" if source else "")
                return f"Ожидается действие от {target} ({category}): {summary}"

            if ev.event_type == "pending_interaction_completed":
                summary = str(ev.payload.get("summary") or "")
                target = str(ev.payload.get("target_agent_id") or "")
                return f"Обязательство выполнено ({target}): {summary}"

            if ev.event_type == "pending_interaction_expired":
                summary = str(ev.payload.get("summary") or "")
                target = str(ev.payload.get("target_agent_id") or "")
                return f"Обязательство просрочено ({target}): {summary}"

            if ev.event_type == "pending_interaction_due":
                summary = str(ev.payload.get("summary") or "")
                target = str(ev.payload.get("target_agent_id") or "")
                if target == agent_id:
                    return f"Срок по обязательству наступил: {summary}"
                return f"Наступил срок обязательства для {target}: {summary}"

            if ev.event_type == "environment_informal_link_updated":
                return ""

            if ev.event_type == "environment_information_climate_updated":
                signals = ev.payload.get("active_signals") or []
                mood = str(ev.payload.get("public_mood") or "")
                if signals:
                    return f"Информационный фон изменился: {', '.join(str(s) for s in signals[:3])}"
                if mood:
                    return f"Информационный фон: настроение={mood}"
                return ""

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
                    started = time.monotonic()
                    embeddings = await embed_texts_cached(
                        embedder,
                        texts,
                        cache=embed_cache,
                        batch_size=self.cfg.memory.embeddings_batch_size,
                    )
                    self._record_local_perf_call(
                        "embeddings_memory",
                        state.tick,
                        (time.monotonic() - started) * 1000.0,
                        len(texts),
                        sum(len(text) for text in texts),
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

        summarize_targets = [agent for agent in state.agents.values() if agent.memory is not None]
        sem: asyncio.Semaphore | None = None
        if self.cfg.runtime.parallel_workers is not None:
            sem = asyncio.Semaphore(self.cfg.runtime.parallel_workers)

        async def _summarize(agent: AgentState) -> Event | None:
            async def _run_once() -> None:
                await agent.memory.maybe_summarize_working(
                    llm=llm,
                    language=self.cfg.runtime.language,
                    cfg=self.cfg.memory,
                    tick=state.tick,
                    temperature=self.cfg.llm.temperature,
                )

            try:
                if sem is not None:
                    async with sem:
                        await _run_once()
                else:
                    await _run_once()
                return None
            except Exception as exc:
                logger.warning("Memory summarization failed for %s on tick %s: %s", agent.agent_id, state.tick, exc)
                return Event(
                    tick=state.tick,
                    event_type="memory_llm_error",
                    actor_id=agent.agent_id,
                    payload={"stage": "summarize_working", "error": {"type": exc.__class__.__name__, "message": str(exc)}},
                    audience=[INTERNAL_AUDIENCE],
                )

        summary_errors = await asyncio.gather(*[_summarize(agent) for agent in summarize_targets])
        for ev in summary_errors:
            if ev is None:
                continue
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
        status_path=d / "status.json",
        truth_path=d / "truth.jsonl",
        truth_freeform_path=d / "truth_freeform.jsonl",
        evaluation_path=d / "evaluation.json",
        perf_summary_path=d / "perf_summary.json",
        world_history_path=d / "world_history.md",
    )


