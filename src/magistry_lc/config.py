"""Pydantic-конфиги для MAGISTRY-LC."""

from __future__ import annotations

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
    provider_order: list[str] = Field(default_factory=lambda: ["Groq"])
    temperature: float = 0.0
    use_tool_calls: bool = True
    trace_max_chars: int = 0


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
            "work_item_created": 5.0,
            "work_note_added": 5.0,
            "work_proposal_submitted": 5.0,
            "world_event": 5.0,
        }
    )

    # Embeddings provider config (по умолчанию: mock для воспроизводимости в тестах).
    embeddings_mock: bool = True
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
    max_actions_per_turn: int = 3
    tick_events_history: int = 200
    enable_worldgen: bool = False
    worldgen_every_ticks: int = 1
    use_langgraph: bool = False
    langgraph_debug: bool = False

    @field_validator("max_actions_per_turn")
    @classmethod
    def _validate_max_actions_per_turn(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_actions_per_turn must be > 0")
        if v > 10:
            raise ValueError("max_actions_per_turn too large")
        return v


class GovernanceConfig(BaseModel):
    """Механизм управления должностями и голосованиями."""

    model_config = ConfigDict(extra="forbid")

    position_policy: Literal["dao", "auto"] = "dao"
    dao_voters: list[str] | None = None
    quorum: float = 0.5
    pass_threshold: float = 0.5
    vote_duration_ticks: int = 3
    require_consent: bool = True

    @field_validator("quorum", "pass_threshold")
    @classmethod
    def _validate_ratio(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError("ratio must be in (0, 1]")
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

    @field_validator("ticks")
    @classmethod
    def _validate_ticks(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ticks must be > 0")
        return v
