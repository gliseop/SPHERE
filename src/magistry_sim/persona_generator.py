"""Параллельная генерация персон (личность + интервью) для агентов.

Мотивация: по Park et al. (2024) «персона» должна быть якорена на
интервью/транскрипте; короткая биография и числовые трэйты сами по себе
дают однотипные профили. Этот модуль генерирует:
  1) seed-данные (одним LLM-вызовом на N агентов),
  2) профиль личности (1 LLM-вызов на агента),
  3) расширенное интервью (несколько LLM-вызовов на агента),
и делает это параллельно по агентам.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .config import AgentProfile, ScenarioConfig
from .interviews import generate_interview, save_interview
from .personality import (
    AgentPersonality,
    DarkTriadProfile,
    HEXACOProfile,
    NeutralizationTechnique,
)

if TYPE_CHECKING:
    from .llm import EmbeddingProvider, LLMProvider


class PersonalitySeed(BaseModel, extra="forbid"):
    """Seed-данные для разнообразия и биографического якоря."""

    age: int = Field(ge=20, le=65)
    gender: str
    education: str
    origin: str
    family_situation: str
    life_events: list[str] = Field(min_length=2, max_length=3)
    temperament_hint: str = ""


def generate_seeds(
    llm: "LLMProvider",
    *,
    scenario_context: str,
    roles: list[str],
    seed: int = 42,
) -> list[PersonalitySeed]:
    """Один LLM-вызов: N seed-профилей для заданных ролей."""
    count = len(roles)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "seeds": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "age": {"type": "integer", "minimum": 20, "maximum": 65},
                        "gender": {"type": "string", "minLength": 2, "maxLength": 16},
                        "education": {"type": "string", "minLength": 2, "maxLength": 120},
                        "origin": {"type": "string", "minLength": 2, "maxLength": 160},
                        "family_situation": {"type": "string", "minLength": 2, "maxLength": 160},
                        "life_events": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 3,
                            "items": {"type": "string", "minLength": 5, "maxLength": 180},
                        },
                        "temperament_hint": {"type": "string", "maxLength": 120},
                    },
                    "required": [
                        "age",
                        "gender",
                        "education",
                        "origin",
                        "family_situation",
                        "life_events",
                        "temperament_hint",
                    ],
                },
            }
        },
        "required": ["seeds"],
    }

    roles_block = "\n".join(f"- {r}" for r in roles)
    resp = llm.generate_structured(
        system=(
            "Ты — сценарист и социолог. По контексту организации создай "
            "разнообразные демографические seed-профили людей. Верни ТОЛЬКО JSON."
        ),
        user=(
            f"Контекст сценария/организации:\n{scenario_context.strip()}\n\n"
            f"Нужно создать seed-профили для {count} ролей:\n{roles_block}\n\n"
            "Требования:\n"
            "- профили максимально разнообразны (возраст, образование, происхождение, семья, опыт)\n"
            "- life_events: 2–3 события, конкретные и разные между персонажами\n"
            f"- используй seed={seed} как ориентир на воспроизводимость\n"
        ),
        schema=schema,
        temperature=0.35,
    )

    data = resp.data if isinstance(resp.data, dict) else {}
    raw = data.get("seeds", [])
    if not isinstance(raw, list):
        return []

    seeds: list[PersonalitySeed] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            seeds.append(PersonalitySeed.model_validate(item))
        except Exception:
            continue

    # Best-effort: если модель недовыдала, дублируем последние.
    if not seeds:
        return []
    while len(seeds) < count:
        seeds.append(seeds[-1])
    return seeds[:count]


def generate_personality_from_seed(
    llm: "LLMProvider",
    *,
    scenario_context: str,
    agent: AgentProfile,
    seed: PersonalitySeed,
) -> AgentPersonality:
    """Один LLM-вызов: AgentPersonality (биография + трэйты) из seed и роли."""
    techniques = [t.value for t in NeutralizationTechnique]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "biography": {"type": "string", "minLength": 80, "maxLength": 2000},
            "hexaco": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "honesty_humility": {"type": "integer", "minimum": 0, "maximum": 100},
                    "emotionality": {"type": "integer", "minimum": 0, "maximum": 100},
                    "extraversion": {"type": "integer", "minimum": 0, "maximum": 100},
                    "agreeableness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "conscientiousness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "openness": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": [
                    "honesty_humility",
                    "emotionality",
                    "extraversion",
                    "agreeableness",
                    "conscientiousness",
                    "openness",
                ],
            },
            "dark_triad": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "narcissism": {"type": "integer", "minimum": 0, "maximum": 100},
                    "machiavellianism": {"type": "integer", "minimum": 0, "maximum": 100},
                    "psychopathy": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["narcissism", "machiavellianism", "psychopathy"],
            },
            "neutralization_techniques": {
                "type": "array",
                "items": {"type": "string", "enum": techniques},
                "minItems": 0,
                "maxItems": len(techniques),
            },
        },
        "required": ["biography", "hexaco", "dark_triad", "neutralization_techniques"],
    }

    seed_json = seed.model_dump_json(indent=2)
    resp = llm.generate_structured(
        system=(
            "Ты — организационный психолог и сценарист. "
            "Сгенерируй психологический профиль персонажа для симуляции. "
            "Верни ТОЛЬКО JSON по схеме."
        ),
        user=(
            f"Контекст сценария/организации:\n{scenario_context.strip()}\n\n"
            f"Агент: {agent.name} ({agent.id})\n"
            f"Роль/должность: {agent.position}\n\n"
            f"Seed-профиль:\n{seed_json}\n\n"
            "Правила:\n"
            "- биография 3–5 абзацев, конкретные детали, но без реальных имён людей\n"
            "- трэйты должны быть согласованы с биографией и ролью\n"
            "- техники нейтрализации выбирай только если они реально уместны персонажу\n"
        ),
        schema=schema,
        temperature=0.35,
    )

    data = resp.data if isinstance(resp.data, dict) else {}
    hexaco = data.get("hexaco", {}) if isinstance(data.get("hexaco", {}), dict) else {}
    dark = data.get("dark_triad", {}) if isinstance(data.get("dark_triad", {}), dict) else {}
    bio = str(data.get("biography", "") or "")
    techniques_raw = data.get("neutralization_techniques", [])
    if not isinstance(techniques_raw, list):
        techniques_raw = []

    return AgentPersonality(
        hexaco=HEXACOProfile(**hexaco),
        dark_triad=DarkTriadProfile(**dark),
        neutralization_techniques=techniques_raw,
        biography=bio,
    )


def _scenario_context(config: ScenarioConfig) -> str:
    parts = []
    if config.title:
        parts.append(f"Название: {config.title}")
    if config.description:
        parts.append(f"Описание: {config.description}")
    if config.narrative_context:
        parts.append(f"Контекст: {config.narrative_context}")
    return "\n".join(parts).strip()


def _write_personality_file(
    *,
    out_dir: Path,
    personality_id: str,
    agent: AgentProfile,
    seed: PersonalitySeed,
    personality: AgentPersonality,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{personality_id}.json"
    payload = {
        "id": personality_id,
        "name": agent.name,
        "description": f"Сгенерировано для роли: {agent.position}",
        "prototypes": [],
        "seed": seed.model_dump(),
        "biography": personality.biography,
        "hexaco": personality.hexaco.model_dump(),
        "dark_triad": personality.dark_triad.model_dump(),
        "neutralization_techniques": [
            t.value if hasattr(t, "value") else str(t)
            for t in personality.neutralization_techniques
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class PersonaArtifacts:
    agent_id: str
    personality_id: str
    personality_path: Path
    interview_path: Path


def generate_personas_parallel(
    config: ScenarioConfig,
    *,
    llm: "LLMProvider",
    embedder: "EmbeddingProvider",
    out_personalities_dir: Path,
    out_interviews_dir: Path,
    agent_ids: set[str] | None = None,
    max_workers: int = 5,
    seed: int = 42,
    force: bool = False,
) -> tuple[ScenarioConfig, list[PersonaArtifacts]]:
    """Сгенерировать персоны параллельно и вернуть обновлённый ScenarioConfig.

    Персона привязывается к agent_id: personality_archetype = agent_id, а файлы
    пишутся как {agent_id}.json в указанные директории.
    """
    scenario_context = _scenario_context(config)

    targets: list[AgentProfile] = []
    for a in config.agents:
        if agent_ids is not None and a.id not in agent_ids:
            continue
        if force or not a.personality_archetype:
            targets.append(a)

    if not targets:
        return config, []

    roles = [a.position for a in targets]
    seeds = generate_seeds(
        llm,
        scenario_context=scenario_context,
        roles=roles,
        seed=seed,
    )
    if targets and not seeds:
        raise RuntimeError("Seed generation returned no seeds")
    if seeds and len(seeds) != len(targets):
        # Best-effort fallback: если модель недовыдала, дублируем последние.
        while len(seeds) < len(targets):
            seeds.append(seeds[-1])
        seeds = seeds[: len(targets)]

    locked_embedder: EmbeddingProvider = embedder

    def _job(agent: AgentProfile, seed_obj: PersonalitySeed) -> PersonaArtifacts:
        # Для сгенерированных персон используем personality_id == agent_id.
        personality_id = agent.id
        pers_path = out_personalities_dir / f"{personality_id}.json"
        itv_path = out_interviews_dir / f"{personality_id}.json"

        if not force and pers_path.exists() and itv_path.exists():
            return PersonaArtifacts(
                agent_id=agent.id,
                personality_id=personality_id,
                personality_path=pers_path,
                interview_path=itv_path,
            )

        personality = generate_personality_from_seed(
            llm,
            scenario_context=scenario_context,
            agent=agent,
            seed=seed_obj,
        )
        _write_personality_file(
            out_dir=out_personalities_dir,
            personality_id=personality_id,
            agent=agent,
            seed=seed_obj,
            personality=personality,
        )

        archetype = personality.classify_archetype()
        interview = generate_interview(
            personality=personality,
            role=agent.position,
            archetype=archetype,
            llm=llm,
            embedder=locked_embedder,
            interview_id=personality_id,
            use_extended_protocol=True,
        )
        out_interviews_dir.mkdir(parents=True, exist_ok=True)
        save_interview(interview, out_interviews_dir)

        return PersonaArtifacts(
            agent_id=agent.id,
            personality_id=personality_id,
            personality_path=pers_path,
            interview_path=itv_path,
        )

    artifacts: list[PersonaArtifacts] = []
    errors: list[Exception] = []

    # Параллелим по агентам.
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as ex:
        futures = {}
        for idx, agent in enumerate(targets):
            if not seeds:
                break
            futures[ex.submit(_job, agent, seeds[idx])] = agent.id

        for fut in as_completed(futures):
            try:
                artifacts.append(fut.result())
            except Exception as exc:
                errors.append(exc)

    if errors:
        # Поднимаем первую ошибку, чтобы вызывающий мог решить, что делать.
        raise RuntimeError(f"Persona generation failed: {errors[0]}") from errors[0]

    # Обновляем agents: проставляем personality_archetype только тем, у кого её не было
    # (или если force=True). personality не трогаем для уже заданных archetype-ов.
    updated_agents: list[AgentProfile] = []
    for a in config.agents:
        if agent_ids is not None and a.id not in agent_ids:
            updated_agents.append(a)
            continue
        if not force and a.personality_archetype:
            updated_agents.append(a)
            continue
        updated_agents.append(
            a.model_copy(
                update={
                    "personality_archetype": a.id,
                    # personality не вкладываем — пусть раннер грузит из файлов.
                    "personality": None,
                }
            )
        )

    return config.model_copy(update={"agents": updated_agents}), artifacts
