"""Pydantic-конфиги для MAGISTRY-LC."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ids import EntityKind, ensure_kind, parse_typed_id
from .persona import PersonaArtifact


class LLMConfig(BaseModel):
    """Настройки LLM-провайдера."""

    model_config = ConfigDict(extra="forbid")

    model: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    provider_order: list[str] = Field(default_factory=list)
    temperature: float = 0.0
    use_tool_calls: bool = True
    trace_max_chars: int = 0

    @field_validator("provider_order")
    @classmethod
    def _normalize_provider_order(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v or []:
            value = str(raw or "").strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(value)
        return out


class MemoryWeights(BaseModel):
    """Веса гибридного retrieval (embeddings + BM25 + recency + importance)."""

    model_config = ConfigDict(extra="forbid")

    recency: float = 0.3
    vector: float = 0.3
    bm25: float = 0.2
    importance: float = 0.2

    @field_validator("recency", "vector", "bm25", "importance")
    @classmethod
    def _validate_non_negative(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("weight must be >= 0")
        return v


class MemoryConfig(BaseModel):
    """Настройки памяти агентов."""

    model_config = ConfigDict(extra="forbid")

    # Working buffer (аналог ConversationSummaryBufferMemory).
    working_max_entries: int = 40
    working_summarize_batch: int = 20

    # Long-term hybrid index.
    long_term_max_docs: int = 800
    retrieval_top_k: int = 12
    dedup_cosine_threshold: float = 0.92
    recency_decay: float = 0.995
    weights: MemoryWeights = Field(default_factory=MemoryWeights)

    importance_default: float = 3.0
    importance_threshold: float = 5.0
    importance_by_event: dict[str, float] = Field(
        default_factory=lambda: {
            "arbiter_approved": 6.0,
            "arbiter_rejected": 8.0,
            "arbiter_op_failed": 8.0,
            "entity_created": 5.0,
            "position_changed": 8.0,
            "vote_opened": 7.0,
            "vote_cast": 5.0,
            "vote_closed": 7.0,
            "message_sent": 6.0,
            "reputation_modified": 6.0,
            "reputation_frozen": 7.0,
            "reputation_unfrozen": 5.0,
            "reputation_gain_blocked": 6.0,
            "work_item_created": 5.0,
            "work_note_added": 5.0,
            "work_proposal_submitted": 5.0,
            "world_event": 5.0,
            "audit_flagged": 7.0,
            "audit_case_opened": 7.0,
            "audit_escalated": 7.0,
            "audit_case_closed": 5.0,
            "audit_runtime_error": 7.0,
        }
    )

    # Embeddings provider config (по умолчанию: реальные embeddings; mock — для тестов/лёгких прогонов).
    embeddings_mock: bool = False
    embeddings_model: str | None = None
    embeddings_base_url: str | None = None
    embeddings_api_key_env: str = "OPENAI_API_KEY"
    embeddings_batch_size: int = 64

    @field_validator("working_max_entries", "working_summarize_batch", "long_term_max_docs", "retrieval_top_k")
    @classmethod
    def _validate_positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("value must be > 0")
        return v

    @field_validator("embeddings_batch_size")
    @classmethod
    def _validate_embeddings_batch_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("embeddings_batch_size must be > 0")
        return v

    @field_validator("dedup_cosine_threshold")
    @classmethod
    def _validate_cosine_threshold(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError("dedup_cosine_threshold must be in (0, 1]")
        return v

    @field_validator("importance_default", "importance_threshold")
    @classmethod
    def _validate_importance(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("importance must be >= 0")
        return float(v)

    @field_validator("importance_by_event")
    @classmethod
    def _validate_importance_by_event(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, value in (v or {}).items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("importance_by_event keys must be non-empty strings")
            if value < 0.0:
                raise ValueError("importance_by_event values must be >= 0")
            out[key] = float(value)
        return out

    @field_validator("recency_decay")
    @classmethod
    def _validate_recency_decay(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError("recency_decay must be in (0, 1]")
        return v


class RuntimeConfig(BaseModel):
    """Настройки исполнения."""

    model_config = ConfigDict(extra="forbid")

    language: str = "ru"
    start_date: date | None = None
    tick_granularity: Literal["hour", "half_day", "day", "week"] = "day"
    tick_duration_days: int = 1
    max_actions_per_turn: int = 3
    tick_events_history: int = 200
    enable_worldgen: bool = False
    worldgen_every_ticks: int = 1
    worldgen_pre_tick: bool = False
    worldgen_event_budget_per_tick: int = 3
    agent_context_budget_per_tick: int = 1
    max_scene_changes_per_tick: int = 2
    max_new_actors_per_window: int = 2
    worldgen_context_scope: Literal["core", "all"] = "core"
    use_langgraph: bool = False
    langgraph_debug: bool = False
    enrich_personas: bool = False
    persona_enrich_mode: Literal["full", "core"] = "full"
    spawn_secondary: bool = False
    max_secondary_per_agent: int = 2
    max_agents: int = 15
    allow_runtime_spawn: bool = False
    request_entity_internal_only: bool = True
    ecology_activation_window_ticks: int = 2
    worldgen_allow_internal_spawns: bool = False
    freeform_truth_enabled: bool = False
    freeform_truth_window_ticks: int = 5
    parallel_agents: bool = True
    parallel_workers: int | None = None
    parallel_window_seconds: float | None = None
    temporal_past_slack_days: int = 1
    temporal_future_horizon_days: int = 120

    @field_validator("max_actions_per_turn")
    @classmethod
    def _validate_max_actions_per_turn(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_actions_per_turn must be > 0")
        if v > 10:
            raise ValueError("max_actions_per_turn too large")
        return v

    @field_validator("tick_duration_days")
    @classmethod
    def _validate_tick_duration_days(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tick_duration_days must be > 0")
        return v

    @field_validator(
        "tick_events_history",
        "worldgen_event_budget_per_tick",
        "agent_context_budget_per_tick",
        "max_scene_changes_per_tick",
        "max_new_actors_per_window",
        "ecology_activation_window_ticks",
        "freeform_truth_window_ticks",
    )
    @classmethod
    def _validate_non_negative_runtime_budget(cls, v: int) -> int:
        if v < 0:
            raise ValueError("value must be >= 0")
        return v

    @field_validator("worldgen_every_ticks")
    @classmethod
    def _validate_worldgen_every_ticks(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("worldgen_every_ticks must be > 0")
        return v

    @field_validator("max_secondary_per_agent")
    @classmethod
    def _validate_secondary_limit(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_secondary_per_agent must be >= 0")
        return v

    @field_validator("max_agents")
    @classmethod
    def _validate_max_agents(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_agents must be > 0")
        return v

    @field_validator("parallel_workers")
    @classmethod
    def _validate_parallel_workers(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v <= 0:
            raise ValueError("parallel_workers must be > 0")
        return v

    @field_validator("parallel_window_seconds")
    @classmethod
    def _validate_parallel_window_seconds(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v < 0.0:
            raise ValueError("parallel_window_seconds must be >= 0")
        return float(v)

    @field_validator("temporal_past_slack_days", "temporal_future_horizon_days")
    @classmethod
    def _validate_temporal_window(cls, v: int) -> int:
        if v < 0:
            raise ValueError("temporal validation window must be >= 0")
        return v

    def tick_duration_delta(self) -> timedelta:
        """Каноническая длительность одного тика."""
        units = int(self.tick_duration_days)
        if self.tick_granularity == "hour":
            return timedelta(hours=units)
        if self.tick_granularity == "half_day":
            return timedelta(hours=12 * units)
        if self.tick_granularity == "week":
            return timedelta(days=7 * units)
        return timedelta(days=units)

    def simulated_datetime(self, tick: int) -> datetime | None:
        """Каноническая дата и время симуляции для данного тика."""
        if self.start_date is None:
            return None
        base = datetime.combine(self.start_date, time(hour=9, minute=0))
        return base + int(tick) * self.tick_duration_delta()

    def simulated_date(self, tick: int) -> date | None:
        """Каноническая дата симуляции для данного тика."""
        simulated = self.simulated_datetime(tick)
        return simulated.date() if simulated is not None else None

    def tick_duration_label(self) -> str:
        """Человеко-читаемое описание размера тика."""
        units = int(self.tick_duration_days)
        if self.tick_granularity == "hour":
            return f"{units} ч."
        if self.tick_granularity == "half_day":
            hours = 12 * units
            return f"{hours} ч."
        if self.tick_granularity == "week":
            weeks = units
            return f"{weeks} нед."
        return f"{units} дн."


class AuditRuntimeConfig(BaseModel):
    """Настройки runtime-аудита в governance-слое."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    actor_id: str | None = None
    mode: Literal["rules", "hybrid", "llm"] = "llm"
    lookback_events: int = 120
    private_contact_window_ticks: int = 3
    max_findings_per_tick: int = 8
    access_policy: Literal["metadata_only", "internal", "full_internal"] = "internal"
    min_confidence_to_flag: float = 0.6
    min_confidence_to_open_case: float = 0.6
    min_confidence_to_freeze: float = 0.85
    min_confidence_to_review: float = 0.72
    freeze_duration_ticks: int = 3
    reputation_freeze_enabled: bool = True
    reputation_penalty_delta: float | None = None
    collegial_review_enabled: bool = True
    review_jury_size: int = 3

    @field_validator("actor_id")
    @classmethod
    def _validate_actor_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        ensure_kind(v, EntityKind.AGENT)
        return v

    @field_validator(
        "lookback_events",
        "private_contact_window_ticks",
        "max_findings_per_tick",
        "freeze_duration_ticks",
        "review_jury_size",
    )
    @classmethod
    def _validate_non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("value must be >= 0")
        return v

    @field_validator(
        "min_confidence_to_flag",
        "min_confidence_to_open_case",
        "min_confidence_to_freeze",
        "min_confidence_to_review",
    )
    @classmethod
    def _validate_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence threshold must be in [0, 1]")
        return float(v)

    @field_validator("reputation_penalty_delta")
    @classmethod
    def _validate_penalty_delta(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v > 0.0:
            raise ValueError("reputation_penalty_delta must be <= 0")
        return float(v)


class GovernanceConfig(BaseModel):
    """Механизм управления должностями и голосованиями."""

    model_config = ConfigDict(extra="forbid")

    position_policy: Literal["dao", "auto"] = "dao"
    dao_voters: list[str] | None = None
    quorum: float = 0.5
    pass_threshold: float = 0.5
    vote_duration_ticks: int = 3
    require_consent: bool = True
    allow_self_nomination: bool = False
    allow_target_self_vote: bool = False
    audit: AuditRuntimeConfig = Field(default_factory=AuditRuntimeConfig)

    @field_validator("quorum", "pass_threshold")
    @classmethod
    def _validate_ratio(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError("ratio must be in (0, 1]")
        return v

    @field_validator("position_policy")
    @classmethod
    def _validate_position_policy_supported(cls, v: Literal["dao", "auto"]) -> Literal["dao", "auto"]:
        if v != "dao":
            raise ValueError("position_policy='auto' is not implemented yet; use 'dao'")
        return v

    @field_validator("vote_duration_ticks")
    @classmethod
    def _validate_vote_duration(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("vote_duration_ticks must be > 0")
        return v


class AgentConfig(BaseModel):
    """Описание агента для сценария."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    internal: bool = True
    persona: PersonaArtifact = Field(default_factory=PersonaArtifact)
    capabilities: list[str] = Field(default_factory=list)
    initial_reputation: float = 0.0
    initial_title: str = "специалист"
    wants_promotion: bool = True

    @field_validator("persona", mode="before")
    @classmethod
    def _coerce_persona(cls, v):
        # Backward compatibility: allow `persona: "..."` string.
        if v is None:
            return {}
        if isinstance(v, str):
            return {"summary": v}
        return v

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, v: str) -> str:
        ensure_kind(v, EntityKind.AGENT)
        return v

    @field_validator("initial_reputation")
    @classmethod
    def _validate_initial_reputation(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("initial_reputation must be >= 0")
        return float(v)

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v or []:
            cap = str(raw or "").strip()
            if not cap or cap in seen:
                continue
            if cap == "audit":
                raise ValueError(
                    "narrative agents with capability 'audit' are not supported; use governance.audit runtime layer instead"
                )
            seen.add(cap)
            out.append(cap)
        return out


class ChannelConfig(BaseModel):
    """Канал коммуникации (например, public)."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    title: str = ""

    @field_validator("channel_id")
    @classmethod
    def _validate_channel_id(cls, v: str) -> str:
        ensure_kind(v, EntityKind.CHANNEL)
        return v


class OrgConfig(BaseModel):
    """Внешняя организация (НКО, компания и т.п.)."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    title: str = ""

    @field_validator("org_id")
    @classmethod
    def _validate_org_id(cls, v: str) -> str:
        ensure_kind(v, EntityKind.ORG)
        return v


class WorkItemConfig(BaseModel):
    """Начальный work item (дело/проект/задача)."""

    model_config = ConfigDict(extra="forbid")

    work_id: str
    work_type: str
    title: str
    description: str = ""
    participants: list[str] = Field(default_factory=list)

    @field_validator("work_id")
    @classmethod
    def _validate_work_id(cls, v: str) -> str:
        ensure_kind(v, EntityKind.WORK_ITEM)
        return v

    @field_validator("participants")
    @classmethod
    def _validate_participants(cls, v: list[str]) -> list[str]:
        for pid in v:
            parsed = parse_typed_id(pid)
            if parsed.kind != EntityKind.AGENT:
                raise ValueError(f"participant must be agent id, got: {pid!r}")
        return v


class WorldConfig(BaseModel):
    """Начальная структура мира."""

    model_config = ConfigDict(extra="forbid")

    channels: list[ChannelConfig] = Field(default_factory=list)
    orgs: list[OrgConfig] = Field(default_factory=list)
    work_items: list[WorkItemConfig] = Field(default_factory=list)


class ScriptedEventConfig(BaseModel):
    """Предопределённое внешнее событие мира."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = ""
    tick: int | None = None
    if_event_types: list[str] = Field(default_factory=list)
    if_work_ids_open: list[str] = Field(default_factory=list)
    audience: Literal["public", "internal"] = "internal"
    description: str
    once: bool = True

    @field_validator("tick")
    @classmethod
    def _validate_tick(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v < 0:
            raise ValueError("scripted event tick must be >= 0")
        return v

    @field_validator("if_event_types", "if_work_ids_open")
    @classmethod
    def _normalize_non_empty_strings(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v or []:
            value = str(item or "").strip()
            if value:
                out.append(value)
        return out


class ScenarioConfig(BaseModel):
    """Корневой конфиг сценария MAGISTRY-LC."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    title: str = "Untitled"
    description: str = ""
    seed: int = 42
    ticks: int = 25

    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)

    agents: list[AgentConfig] = Field(default_factory=list)
    world: WorldConfig = Field(default_factory=WorldConfig)
    scripted_events: list[ScriptedEventConfig] = Field(default_factory=list)

    @field_validator("ticks")
    @classmethod
    def _validate_ticks(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ticks must be > 0")
        return v
