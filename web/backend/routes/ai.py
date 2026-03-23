"""Маршруты AI-генерации: личности, типы агентов, вторичные агенты."""

from __future__ import annotations

import asyncio
import json
import os as _os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from magistry_lc.config import AgentConfig, ScenarioConfig
from magistry_lc.ids import EntityKind, make_unique_id
from magistry_lc.llm import create_provider
from magistry_lc.persona import social_link_match_key, social_link_name_key
from magistry_lc.utils import (
    looks_like_machine_name,
    looks_like_role_label,
    normalize_agent_display_name,
)

from web.backend.auth import require_admin
from web.backend.database import User
from web.backend.models import (
    GenerateAgentTypePayload,
    GeneratePersonalityPayload,
    SecondaryAgentsPayload,
)
from web.backend.routes.scenarios import (
    _scenario_config_to_payload,
    load_template_config_for_web,
)
from web.backend.settings import PERSONALITIES_DIR
from web.backend.validators import validate_library_id

router = APIRouter(tags=["ai"])

_SECONDARY_ALLOWED_CAPABILITIES = frozenset({"message", "work"})
_SECONDARY_FAMILY_KEYWORDS = (
    "сем",
    "родств",
    "брат",
    "сест",
    "жена",
    "муж",
    "дочь",
    "сын",
    "мать",
    "отец",
    "family",
    "relative",
)
_SECONDARY_SOCIETY_KEYWORDS = (
    "обще",
    "медиа",
    "журналист",
    "пресс",
    "активист",
    "избират",
    "жител",
    "пациент",
    "родител",
    "public",
    "media",
    "journal",
    "press",
    "community",
    "citizen",
)


async def _generate_structured_via_provider(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    temperature: float,
):
    """Выполнить structured LLM-вызов вне event loop."""

    def _call():
        llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
        return llm.generate_structured(
            system=system_prompt,
            user=user_prompt,
            schema=schema,
            temperature=temperature,
        )

    return await asyncio.to_thread(_call)


def _load_secondary_base_config(payload: SecondaryAgentsPayload) -> ScenarioConfig:
    """Материализовать базовый ScenarioConfig для secondary-agent генерации."""

    if payload.sim_config is not None:
        try:
            cfg = ScenarioConfig.model_validate(payload.sim_config)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid ScenarioConfig: {exc}") from exc
    else:
        cfg = load_template_config_for_web(payload.scenario, governance=payload.governance)
    cfg = cfg.model_copy(deep=True)
    if payload.seed is not None:
        cfg.seed = int(payload.seed)
    if payload.rounds is not None:
        cfg.ticks = int(payload.rounds)
    return cfg


def _secondary_generation_schema(
    *,
    family_count: int,
    society_count: int,
    anchor_ids: list[str],
) -> dict[str, Any]:
    """Schema structured-output для secondary-agent generation."""

    total = max(0, int(family_count)) + max(0, int(society_count))
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "agents": {
                "type": "array",
                "maxItems": max(total, 1),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": ["family", "society"]},
                        "name": {"type": "string", "minLength": 2, "maxLength": 128},
                        "anchor_agent_id": {"type": "string", "enum": anchor_ids or ["agent:none"]},
                        "relation": {"type": "string", "minLength": 2, "maxLength": 128},
                        "persona_hint": {"type": "string", "minLength": 20, "maxLength": 1200},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string", "enum": sorted(_SECONDARY_ALLOWED_CAPABILITIES)},
                            "maxItems": len(_SECONDARY_ALLOWED_CAPABILITIES),
                        },
                    },
                    "required": ["kind", "name", "anchor_agent_id", "relation", "persona_hint", "capabilities"],
                },
            }
        },
        "required": ["agents"],
    }


def _secondary_agents_system_prompt(*, family_count: int, society_count: int) -> str:
    """System prompt для генерации вторичных акторов."""

    return (
        "Ты — модуль генерации вторичных акторов для MAGISTRY.\n"
        "Нужно предложить concrete human agents вокруг уже существующих участников сценария.\n"
        "Категории только две:\n"
        "- family: родственники, супруги, близкие друзья семьи, люди кланового давления;\n"
        "- society: журналисты, активисты, жители, родители, пациенты, общественные посредники.\n"
        "Строго запрещено:\n"
        "- возвращать должности вместо людей;\n"
        "- возвращать машинные имена, role-label, slug или ID;\n"
        "- дублировать уже существующих агентов.\n"
        "Каждый новый актор должен быть полезен для симуляции: оказывать давление, приносить сигналы, создавать репутационные или бытовые дилеммы.\n"
        f"Сгенерируй до {family_count} family-акторов и до {society_count} society-акторов.\n"
        "Верни только JSON по схеме."
    )


def _secondary_agents_user_prompt(payload: SecondaryAgentsPayload, cfg: ScenarioConfig) -> str:
    """Собрать user prompt для генерации вторичных агентов."""

    lines: list[str] = []
    for agent in cfg.agents:
        persona = agent.persona
        summary = (persona.summary or "").strip()
        biography = (persona.biography or "").strip()
        hint = biography or summary or agent.initial_title or agent.name
        lines.append(
            "\n".join(
                [
                    f"- id: {agent.agent_id}",
                    f"  name: {agent.name}",
                    f"  internal: {agent.internal}",
                    f"  title: {agent.initial_title}",
                    f"  capabilities: {', '.join(agent.capabilities) or '(нет)'}",
                    f"  persona: {hint[:400]}",
                ]
            )
        )

    return (
        f"Сценарий: {cfg.title}\n"
        f"Описание сценария: {(cfg.description or '(пусто)')[:1200]}\n"
        f"Ticks: {cfg.ticks}\n"
        f"Пользовательский фокус:\n{payload.prompt.strip()}\n\n"
        "Уже существующие агенты:\n"
        f"{chr(10).join(lines) or '(нет)'}\n\n"
        "Нужно расширить окружающую агентную среду так, чтобы новые акторы были конкретными людьми, а не абстрактными ролями. "
        "Family-акторы должны быть plausibly привязаны к одному из существующих агентов. "
        "Society-акторы должны быть способны запускать внешний сигнал, жалобу или публикацию."
    )


def _normalize_secondary_kind(kind: str, relation: str) -> str:
    """Нормализовать категорию secondary-актора."""

    normalized_kind = (kind or "").strip().casefold()
    if normalized_kind in {"family", "society"}:
        return normalized_kind

    relation_key = social_link_name_key(relation)
    if any(token in relation_key for token in _SECONDARY_FAMILY_KEYWORDS):
        return "family"
    if any(token in relation_key for token in _SECONDARY_SOCIETY_KEYWORDS):
        return "society"
    return "society"


def _secondary_title(kind: str, relation: str) -> str:
    """Человекочитаемый title для нового secondary-агента."""

    relation_text = (relation or "").strip()
    if relation_text:
        return relation_text[:96]
    if kind == "family":
        return "семейное окружение"
    return "общественное окружение"


def _sanitize_secondary_capabilities(raw_caps: list[str]) -> list[str]:
    """Оставить только безопасные capability для generated secondary actors."""

    out: list[str] = []
    seen: set[str] = set()
    for item in raw_caps or []:
        value = str(item or "").strip()
        if not value or value not in _SECONDARY_ALLOWED_CAPABILITIES or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out or ["message"]


def _agent_name_keys(cfg: ScenarioConfig) -> set[str]:
    keys: set[str] = set()
    for agent in cfg.agents:
        key = social_link_name_key(agent.name)
        if key:
            keys.add(key)
    return keys


def _prune_existing_secondary_agents(cfg: ScenarioConfig) -> None:
    """Удалить уже сгенерированных family/society агентов перед регенерацией."""

    kept: list[AgentConfig] = []
    for agent in cfg.agents:
        slug = agent.agent_id.split(":", 1)[1] if ":" in agent.agent_id else agent.agent_id
        if slug.startswith(("fam_", "soc_")):
            continue
        kept.append(agent)
    cfg.agents = kept


def _secondary_kind_prefix(kind: str) -> str:
    return "fam" if kind == "family" else "soc"


def _scenario_payload_with_secondary_meta(
    *,
    cfg: ScenarioConfig,
    scenario_id: str,
    added_agents: list[dict[str, Any]],
    skipped_agents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Вернуть web-friendly payload плюс метаданные secondary generation."""

    result = _scenario_config_to_payload(cfg, scenario_id=scenario_id)
    result["added_agents"] = added_agents
    result["skipped_agents"] = skipped_agents
    result["secondary_generation"] = {
        "added_count": len(added_agents),
        "skipped_count": len(skipped_agents),
        "family_count": sum(1 for item in added_agents if item.get("kind") == "family"),
        "society_count": sum(1 for item in added_agents if item.get("kind") == "society"),
    }
    return result


@router.post("/api/ai/generate-personality")
async def generate_personality(
    payload: GeneratePersonalityPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Сгенерировать профиль личности (биография, HEXACO, темная триада, техники) по описанию.

    Args:
        payload: Описание желаемой личности (свободный текст).
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь с полями biography, hexaco, dark_triad, neutralization_techniques.
    """
    if not _os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

    from magistry_lc.llm import create_provider
    from web.backend.constants import NEUTRALIZATION_TECHNIQUES

    techniques = list(NEUTRALIZATION_TECHNIQUES)

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "prototypes": {
                "type": "array",
                "items": {"type": "string", "minLength": 2, "maxLength": 120},
                "minItems": 1,
                "maxItems": 3,
                "description": (
                    "1\u20133 \u0440\u0435\u0430\u043b\u044c\u043d\u044b\u0445 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f\u0430 (\u0438\u0441\u0442\u043e\u0440\u0438\u0447\u0435\u0441\u043a\u0438\u0435/\u043f\u0443\u0431\u043b\u0438\u0447\u043d\u044b\u0435 \u043b\u0438\u0447\u043d\u043e\u0441\u0442\u0438), \u043d\u0430 \u043a\u043e\u0442\u043e\u0440\u044b\u0445 \u043e\u0441\u043d\u043e\u0432\u0430\u043d \u043e\u0431\u0440\u0430\u0437. "
                    "\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 \u0436\u0438\u0432\u0443\u0449\u0438\u0445 \u043f\u0435\u0440\u0441\u043e\u043d. \u041f\u0440\u043e\u0442\u043e\u0442\u0438\u043f\u044b \u2014 \u0442\u043e\u043b\u044c\u043a\u043e \u0432\u0434\u043e\u0445\u043d\u043e\u0432\u0435\u043d\u0438\u0435; \u043d\u0435 \u0434\u0435\u043b\u0430\u0439\u0442\u0435 \u0443\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0439 "
                    "\u043e \u043d\u0435\u0437\u0430\u043a\u043e\u043d\u043d\u043e\u0439 \u0434\u0435\u044f\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f\u043e\u0432."
                ),
            },
            "biography": {
                "type": "string",
                "minLength": 50,
                "maxLength": 2000,
                "description": "\u0420\u0430\u0437\u0432\u0451\u0440\u043d\u0443\u0442\u0430\u044f \u0431\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u044f \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u0436\u0430: \u043f\u0440\u043e\u0438\u0441\u0445\u043e\u0436\u0434\u0435\u043d\u0438\u0435, \u043a\u0430\u0440\u044c\u0435\u0440\u0430, \u043c\u043e\u0442\u0438\u0432\u0430\u0446\u0438\u044f, \u0441\u043b\u0430\u0431\u043e\u0441\u0442\u0438, \u0441\u043b\u0435\u043f\u044b\u0435 \u0437\u043e\u043d\u044b.",
            },
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
                    "honesty_humility", "emotionality", "extraversion",
                    "agreeableness", "conscientiousness", "openness",
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
                "description": "\u0422\u0435\u0445\u043d\u0438\u043a\u0438 \u043d\u0435\u0439\u0442\u0440\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u0438 (Sykes & Matza), \u043a\u043e\u0442\u043e\u0440\u044b\u043c\u0438 \u0432\u043b\u0430\u0434\u0435\u0435\u0442 \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u0436.",
            },
        },
        "required": ["prototypes", "biography", "hexaco", "dark_triad", "neutralization_techniques"],
    }

    system_prompt = (
        "\u0422\u044b \u2014 \u044d\u043a\u0441\u043f\u0435\u0440\u0442 \u043f\u043e \u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u043e\u043d\u043d\u043e\u0439 \u043f\u0441\u0438\u0445\u043e\u043b\u043e\u0433\u0438\u0438 \u0438 \u043a\u0440\u0438\u043c\u0438\u043d\u043e\u043b\u043e\u0433\u0438\u0438. "
        "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043e\u043f\u0438\u0441\u044b\u0432\u0430\u0435\u0442 \u0436\u0435\u043b\u0430\u0435\u043c\u044b\u0439 \u0442\u0438\u043f\u0430\u0436 \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u0436\u0430 \u0434\u043b\u044f \u0441\u0438\u043c\u0443\u043b\u044f\u0446\u0438\u0438 \u043a\u043e\u0440\u0440\u0443\u043f\u0446\u0438\u0438 \u0432 \u0433\u043e\u0441\u043e\u0440\u0433\u0430\u043d\u0430\u0445. "
        "\u0421\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0439 \u043f\u043e\u043b\u043d\u044b\u0439 \u043f\u0441\u0438\u0445\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c: \u0431\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u044e, \u043f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b HEXACO (0-100), "
        "\u0442\u0451\u043c\u043d\u0443\u044e \u0442\u0440\u0438\u0430\u0434\u0443 (0-100) \u0438 \u043f\u043e\u0434\u0445\u043e\u0434\u044f\u0449\u0438\u0435 \u0442\u0435\u0445\u043d\u0438\u043a\u0438 \u043d\u0435\u0439\u0442\u0440\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u0438. "
        "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON \u0431\u0435\u0437 \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u0439 \u0438 \u043f\u0440\u0435\u0444\u0438\u043a\u0441\u043e\u0432. "
        "\u0411\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u044f \u0434\u043e\u043b\u0436\u043d\u0430 \u043e\u043f\u0438\u0440\u0430\u0442\u044c\u0441\u044f \u043d\u0430 1\u20133 \u0440\u0435\u0430\u043b\u044c\u043d\u044b\u0445 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f\u0430 (\u0438\u0441\u0442\u043e\u0440\u0438\u0447\u0435\u0441\u043a\u0438\u0435/\u043f\u0443\u0431\u043b\u0438\u0447\u043d\u044b\u0435 \u043b\u0438\u0447\u043d\u043e\u0441\u0442\u0438; \u043f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0438\u0442\u0435\u043b\u044c\u043d\u043e \u0443\u043c\u0435\u0440\u0448\u0438\u0435). "
        "\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u0436 \u043f\u0440\u0438 \u044d\u0442\u043e\u043c \u043e\u0441\u0442\u0430\u0451\u0442\u0441\u044f \u0432\u044b\u043c\u044b\u0448\u043b\u0435\u043d\u043d\u044b\u043c: \u043d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 \u0440\u0435\u0430\u043b\u044c\u043d\u044b\u0435 \u0438\u043c\u0435\u043d\u0430 \u0432 \u0442\u0435\u043a\u0441\u0442\u0435 \u0431\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u0438. "
        "\u041f\u0440\u043e\u0442\u043e\u0442\u0438\u043f\u044b \u043f\u0435\u0440\u0435\u0447\u0438\u0441\u043b\u0438 \u0432 \u043f\u043e\u043b\u0435 prototypes (\u043c\u0430\u0441\u0441\u0438\u0432 \u0441\u0442\u0440\u043e\u043a). "
        "\u0411\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u044f \u0434\u043e\u043b\u0436\u043d\u0430 \u0431\u044b\u0442\u044c \u043d\u0430 \u0440\u0443\u0441\u0441\u043a\u043e\u043c \u044f\u0437\u044b\u043a\u0435, 3-5 \u0430\u0431\u0437\u0430\u0446\u0435\u0432. "
        "\u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b \u0434\u043e\u043b\u0436\u043d\u044b \u0431\u044b\u0442\u044c \u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438 \u0441\u043e\u0433\u043b\u0430\u0441\u043e\u0432\u0430\u043d\u044b \u0441 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u043c \u0438 \u0431\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u0435\u0439."
    )
    system_prompt = (payload.system_prompt or system_prompt).strip()
    user_prompt = (payload.user_prompt or f"\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u0436\u0430:\n{payload.description}").strip()

    try:
        response = await _generate_structured_via_provider(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=0.25,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    return response.data if isinstance(response.data, dict) else {}


@router.post("/api/ai/generate-agent-type")
async def generate_agent_type(
    payload: GenerateAgentTypePayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Сгенерировать тип агента (name/description/id_prefix) по выбранной личности и описанию."""
    if not _os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

    validate_library_id(payload.personality_id, PERSONALITIES_DIR, kind="personality")
    p_path = PERSONALITIES_DIR / f"{payload.personality_id}.json"
    if not p_path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")

    try:
        personality = json.loads(p_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read personality: {exc}") from exc
    if not isinstance(personality, dict):
        raise HTTPException(status_code=500, detail="Invalid personality JSON")

    from magistry_lc.llm import create_provider

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 128},
            "description": {"type": "string", "minLength": 50, "maxLength": 2000},
            "id_prefix": {"type": "string", "maxLength": 16},
        },
        "required": ["name", "description", "id_prefix"],
    }

    system_prompt_default = (
        "\u0422\u044b \u2014 \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0441\u0442 \u0438 \u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u043e\u043d\u043d\u044b\u0439 \u043f\u0441\u0438\u0445\u043e\u043b\u043e\u0433. "
        "\u041d\u0443\u0436\u043d\u043e \u043e\u043f\u0438\u0441\u0430\u0442\u044c \u0442\u0438\u043f \u0430\u0433\u0435\u043d\u0442\u0430 \u0434\u043b\u044f \u0441\u0438\u043c\u0443\u043b\u044f\u0446\u0438\u0438 MAGISTRY. "
        "\u041d\u0430 \u0432\u0445\u043e\u0434\u0435: \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u0430\u044f \u043b\u0438\u0447\u043d\u043e\u0441\u0442\u044c (HEXACO + \u0442\u0451\u043c\u043d\u0430\u044f \u0442\u0440\u0438\u0430\u0434\u0430 + \u0431\u0438\u043e\u0433\u0440\u0430\u0444\u0438\u044f + \u0442\u0435\u0445\u043d\u0438\u043a\u0438) \u0438 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u0440\u043e\u043b\u0438/\u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430. "
        "\u041d\u0430 \u0432\u044b\u0445\u043e\u0434\u0435: JSON \u0441 \u043f\u043e\u043b\u044f\u043c\u0438 name, description, id_prefix. "
        "\u0412\u0430\u0436\u043d\u043e: \u041d\u0415 \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0439 \u0431\u044e\u0434\u0436\u0435\u0442/\u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b/\u043f\u043e\u043b\u043d\u043e\u043c\u043e\u0447\u0438\u044f/\u043a\u043e\u043d\u0442\u0440\u0430\u043a\u0442\u044b \u2014 \u044d\u0442\u043e \u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0435\u0442 \u0434\u0432\u0438\u0436\u043e\u043a \u043c\u0438\u0440\u0430."
    )

    def _safe_json(value: object, max_len: int = 4000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            text = str(value)
        return text[:max_len]

    user_prompt_default = (
        "## \u0412\u044b\u0431\u0440\u0430\u043d\u043d\u0430\u044f \u043b\u0438\u0447\u043d\u043e\u0441\u0442\u044c\n"
        f"{_safe_json(personality)}\n\n"
        "## \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u0442\u0438\u043f\u0430/\u0440\u043e\u043b\u0438 (\u043f\u043e\u0436\u0435\u043b\u0430\u043d\u0438\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f)\n"
        f"{payload.description}\n\n"
        "\u0421\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0439 \u0442\u0438\u043f \u0430\u0433\u0435\u043d\u0442\u0430. description \u2014 \u043d\u0430 \u0440\u0443\u0441\u0441\u043a\u043e\u043c, 3\u20137 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0439, "
        "\u0432\u043a\u043b\u044e\u0447\u0438 \u043c\u043e\u0442\u0438\u0432\u0430\u0446\u0438\u044e/\u0440\u0438\u0441\u043a\u0438/\u043f\u043e\u0432\u0435\u0434\u0435\u043d\u0447\u0435\u0441\u043a\u0438\u0435 \u043f\u0430\u0442\u0442\u0435\u0440\u043d\u044b. "
        "id_prefix \u2014 \u043a\u043e\u0440\u043e\u0442\u043a\u0438\u0439 \u043b\u0430\u0442\u0438\u043d\u0441\u043a\u0438\u0439 \u043f\u0440\u0435\u0444\u0438\u043a\u0441 (\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 off/biz/aud/hr)."
    )

    system_prompt = (payload.system_prompt or system_prompt_default).strip()
    user_prompt = (payload.user_prompt or user_prompt_default).strip()

    try:
        response = await _generate_structured_via_provider(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=0.35,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    return response.data if isinstance(response.data, dict) else {}


@router.post("/api/ai/secondary-agents")
async def generate_secondary_agents(
    payload: SecondaryAgentsPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Сгенерировать вторичных агентов (fam_*/soc_*) и вернуть обновлённый web-payload."""
    if not _os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

    cfg = _load_secondary_base_config(payload)
    requested_total = int(payload.family_count) + int(payload.society_count)
    if requested_total <= 0:
        return _scenario_payload_with_secondary_meta(
            cfg=cfg,
            scenario_id=payload.scenario,
            added_agents=[],
            skipped_agents=[],
        )

    if payload.replace_existing:
        _prune_existing_secondary_agents(cfg)

    schema = _secondary_generation_schema(
        family_count=payload.family_count,
        society_count=payload.society_count,
        anchor_ids=[agent.agent_id for agent in cfg.agents],
    )
    system_prompt = _secondary_agents_system_prompt(
        family_count=payload.family_count,
        society_count=payload.society_count,
    )
    user_prompt = _secondary_agents_user_prompt(payload, cfg)

    try:
        response = await _generate_structured_via_provider(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=0.35,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    raw_agents = response.data.get("agents") if isinstance(response.data, dict) else None
    if not isinstance(raw_agents, list):
        raise HTTPException(status_code=502, detail="LLM returned invalid secondary-agents payload")

    existing_ids = {agent.agent_id for agent in cfg.agents}
    used_name_keys = _agent_name_keys(cfg)
    family_slots = max(0, int(payload.family_count))
    society_slots = max(0, int(payload.society_count))
    added_agents: list[dict[str, Any]] = []
    skipped_agents: list[dict[str, Any]] = []

    for item in raw_agents:
        if family_slots <= 0 and society_slots <= 0:
            break
        if not isinstance(item, dict):
            continue

        kind = _normalize_secondary_kind(
            str(item.get("kind") or ""),
            str(item.get("relation") or ""),
        )
        if kind == "family" and family_slots <= 0:
            skipped_agents.append({"reason": "family quota reached", "candidate": item})
            continue
        if kind == "society" and society_slots <= 0:
            skipped_agents.append({"reason": "society quota reached", "candidate": item})
            continue

        display_name = normalize_agent_display_name(str(item.get("name") or ""))
        if not display_name or looks_like_machine_name(display_name) or looks_like_role_label(display_name):
            skipped_agents.append({"reason": "invalid display name", "candidate": item})
            continue

        display_name_key = social_link_name_key(display_name)
        matched_name_key = social_link_match_key(display_name_key, list(used_name_keys)) if display_name_key else ""
        if matched_name_key and matched_name_key in used_name_keys:
            skipped_agents.append({"reason": "duplicate name", "candidate": item})
            continue

        anchor_agent_id = str(item.get("anchor_agent_id") or "").strip()
        anchor_agent = next((agent for agent in cfg.agents if agent.agent_id == anchor_agent_id), None)
        if anchor_agent is None:
            skipped_agents.append({"reason": "unknown anchor agent", "candidate": item})
            continue

        prefix = _secondary_kind_prefix(kind)
        entity_id = make_unique_id(
            EntityKind.AGENT,
            f"{prefix}_{display_name}",
            existing_ids=existing_ids,
            fallback=f"{prefix}_actor",
        )
        existing_ids.add(entity_id)
        if display_name_key:
            used_name_keys.add(display_name_key)

        capabilities = _sanitize_secondary_capabilities(list(item.get("capabilities") or []))
        relation = str(item.get("relation") or "").strip()
        persona_hint = str(item.get("persona_hint") or "").strip()
        new_agent = AgentConfig.model_validate(
            {
                "agent_id": entity_id,
                "name": display_name,
                "internal": False,
                "persona": {
                    "summary": persona_hint[:280] or relation or display_name,
                    "biography": persona_hint or relation or display_name,
                },
                "capabilities": capabilities,
                "org_id": anchor_agent.org_id,
                "zone_id": anchor_agent.zone_id,
                "initial_reputation": 0.0,
                "initial_title": _secondary_title(kind, relation),
                "wants_promotion": False,
            }
        )
        cfg.agents.append(new_agent)
        added_agents.append(
            {
                "agent_id": entity_id,
                "name": display_name,
                "kind": kind,
                "relation": relation,
                "anchor_agent_id": anchor_agent.agent_id,
                "anchor_agent_name": anchor_agent.name,
                "capabilities": capabilities,
                "org_id": anchor_agent.org_id,
                "zone_id": anchor_agent.zone_id,
            }
        )
        if kind == "family":
            family_slots -= 1
        else:
            society_slots -= 1

    return _scenario_payload_with_secondary_meta(
        cfg=cfg,
        scenario_id=payload.scenario,
        added_agents=added_agents,
        skipped_agents=skipped_agents,
    )
