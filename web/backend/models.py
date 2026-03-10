"""Pydantic-модели запросов/ответов для API MAGISTRY."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .settings import MAX_ROUNDS, MAX_SEED


class ScenarioAgentPayload(BaseModel):
    """Агент внутри сценария."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=256)
    initial_reputation: float = Field(ge=0.0, le=100.0)
    capabilities: list[str] | None = Field(default=None, max_length=16)


class ScenarioPayload(BaseModel):
    """Тело запроса на создание/обновление сценария."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)
    scenario: str = Field(default="S1", pattern=r"^S\d+$")
    governance: str = Field(default="G1", pattern=r"^G\d+$")
    rounds: int = Field(default=10, ge=1, le=MAX_ROUNDS)
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    runner: str | None = Field(default=None, max_length=64)
    parallel_agents: bool | None = None
    parallel_workers: int | None = Field(default=None, ge=1, le=128)
    parallel_window: float | None = Field(default=None, ge=0.0, le=86_400.0)
    agents: list[ScenarioAgentPayload] = Field(default_factory=list, max_length=200)
    sim_config: dict[str, Any] | None = None


class SecondaryAgentsPayload(BaseModel):
    """Запрос на генерацию/обновление вторичных агентов через LLM."""

    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(default="S1", pattern=r"^S\d+$")
    governance: str = Field(default="G1", pattern=r"^G\d+$")
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    rounds: int | None = Field(default=None, ge=1, le=MAX_ROUNDS)
    prompt: str = Field(min_length=1, max_length=10_000)
    family_count: int = Field(default=0, ge=0, le=20)
    society_count: int = Field(default=0, ge=0, le=20)
    replace_existing: bool = True
    sim_config: dict[str, Any] | None = None


class GeneratePersonalityPayload(BaseModel):
    """Запрос на генерацию профиля личности через LLM."""

    model_config = ConfigDict(strict=False)
    description: str = Field(..., min_length=5, max_length=2000)
    system_prompt: str | None = Field(default=None, max_length=10_000)
    user_prompt: str | None = Field(default=None, max_length=10_000)


class GenerateAgentTypePayload(BaseModel):
    """Запрос на генерацию типа агента через LLM (по личности + описанию)."""

    model_config = ConfigDict(strict=False)
    personality_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    description: str = Field(..., min_length=5, max_length=2000)
    system_prompt: str | None = Field(default=None, max_length=10_000)
    user_prompt: str | None = Field(default=None, max_length=10_000)


class GenerateInterviewPayload(BaseModel):
    """Параметры генерации интервью."""

    model_config = ConfigDict(strict=False)
    role: str = Field(default="чиновник", min_length=1, max_length=128)
