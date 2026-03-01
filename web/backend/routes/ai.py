"""Маршруты AI-генерации: личности, типы агентов, вторичные агенты."""

from __future__ import annotations

import json
import os as _os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin
from web.backend.database import User
from web.backend.models import (
    GenerateAgentTypePayload,
    GeneratePersonalityPayload,
    SecondaryAgentsPayload,
)
from web.backend.settings import PERSONALITIES_DIR
from web.backend.validators import validate_library_id

router = APIRouter(tags=["ai"])


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

    from magistry_sim.llm import create_provider
    from magistry_sim.personality import NeutralizationTechnique

    techniques = [t.value for t in NeutralizationTechnique]

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

    llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
    try:
        response = llm.generate_structured(
            system=system_prompt,
            user=user_prompt,
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

    from magistry_sim.llm import create_provider

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

    llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
    try:
        response = llm.generate_structured(
            system=system_prompt,
            user=user_prompt,
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
    """Сгенерировать вторичных агентов (fam_*/soc_*) и вернуть обновлённый ScenarioConfig."""
    if not _os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

    total = int(payload.family_count) + int(payload.society_count)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Nothing to generate")

    from magistry_sim.config import AgentProfile, Connection, ScenarioConfig
    from magistry_sim.enums import GovernanceMode, ScenarioId
    from magistry_sim.llm import create_provider
    from magistry_sim.personality import NeutralizationTechnique
    from magistry_sim.scenarios import add_governance_agents, get_scenario

    try:
        if payload.sim_config:
            base_cfg = ScenarioConfig.model_validate(payload.sim_config)
        else:
            sid = ScenarioId(payload.scenario)
            try:
                gov = GovernanceMode(payload.governance)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"\u0420\u0435\u0436\u0438\u043c \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f '{payload.governance}' \u043d\u0435 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0442\u0441\u044f \u0434\u0432\u0438\u0436\u043a\u043e\u043c. "
                    "\u041a\u0430\u0441\u0442\u043e\u043c\u043d\u044b\u0435 \u0440\u0435\u0436\u0438\u043c\u044b (G4+) \u043f\u043e\u043a\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b \u0442\u043e\u043b\u044c\u043a\u043e \u043a\u0430\u043a \u043c\u0435\u0442\u0430\u0434\u0430\u043d\u043d\u044b\u0435.",
                )
            base_cfg = add_governance_agents(get_scenario(sid), gov)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sim_config: {exc}") from exc

    updates: dict[str, Any] = {}
    if payload.rounds is not None:
        updates["max_rounds"] = int(payload.rounds)
    if payload.seed is not None:
        updates["seed"] = int(payload.seed)
    if updates:
        base_cfg = base_cfg.model_copy(update=updates)

    def _is_secondary(aid: str) -> bool:
        return aid.startswith(("fam_", "soc_"))

    if payload.replace_existing:
        kept: list[AgentProfile] = []
        for agent in base_cfg.agents:
            if _is_secondary(agent.id):
                continue
            if agent.connections:
                kept_conns = [
                    c for c in agent.connections if not _is_secondary(c.target_id)
                ]
                agent = agent.model_copy(update={"connections": kept_conns})
            kept.append(agent)
        base_cfg = base_cfg.model_copy(update={"agents": kept})

    existing_ids = {a.id for a in base_cfg.agents}

    def _next_id(prefix: str) -> str:
        n = 1
        while True:
            candidate = f"{prefix}_{n}"
            if candidate not in existing_ids:
                existing_ids.add(candidate)
                return candidate
            n += 1

    requested_family = [_next_id("fam") for _ in range(int(payload.family_count))]
    requested_society = [_next_id("soc") for _ in range(int(payload.society_count))]
    requested_ids = requested_family + requested_society

    primary_agents = [
        {"id": a.id, "name": a.name, "position": a.position}
        for a in base_cfg.agents
        if a.id not in requested_ids
    ]
    primary_ids = [a["id"] for a in primary_agents]

    techniques = [t.value for t in NeutralizationTechnique]

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "narrative_context": {"type": "string", "maxLength": 2000},
            "agents": {
                "type": "array",
                "minItems": total,
                "maxItems": total,
                "items": {"$ref": "#/$defs/agent"},
            },
        },
        "required": ["narrative_context", "agents"],
        "$defs": {
            "capability": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "maxLength": 64},
                    "case_types": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 64},
                    },
                },
                "required": ["action", "case_types"],
            },
            "connection": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_id": {"type": "string", "maxLength": 64},
                    "name": {"type": "string", "maxLength": 128},
                    "relation": {"type": "string", "maxLength": 128},
                    "strength": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 5.0,
                    },
                },
                "required": ["target_id", "name", "relation", "strength"],
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
            "personality": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "biography": {"type": "string", "maxLength": 800},
                    "hexaco": {"$ref": "#/$defs/hexaco"},
                    "dark_triad": {"$ref": "#/$defs/dark_triad"},
                    "neutralization_techniques": {
                        "type": "array",
                        "items": {"type": "string", "enum": techniques},
                    },
                },
                "required": [
                    "biography",
                    "hexaco",
                    "dark_triad",
                    "neutralization_techniques",
                ],
            },
            "resources": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "budget_limit": {"type": "number"},
                    "staffing_slots": {"type": "integer"},
                    "contract_capacity": {"type": "integer"},
                },
                "required": ["budget_limit", "staffing_slots", "contract_capacity"],
            },
            "agent": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "maxLength": 64},
                    "name": {"type": "string", "maxLength": 128},
                    "position": {"type": "string", "maxLength": 256},
                    "capabilities": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/capability"},
                    },
                    "greed": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "fear": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "honesty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "competence": {
                        "anyOf": [
                            {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            {"type": "null"},
                        ]
                    },
                    "immune": {"type": "boolean"},
                    "connections": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/connection"},
                    },
                    "personality": {"$ref": "#/$defs/personality"},
                    "initial_resources": {"$ref": "#/$defs/resources"},
                },
                "required": [
                    "id",
                    "name",
                    "position",
                    "capabilities",
                    "greed",
                    "fear",
                    "honesty",
                    "competence",
                    "immune",
                    "connections",
                    "personality",
                    "initial_resources",
                ],
            },
        },
    }

    system = (
        "\u0412\u044b \u2014 \u0433\u0435\u043d\u0435\u0440\u0430\u0442\u043e\u0440 \u0432\u0442\u043e\u0440\u0438\u0447\u043d\u044b\u0445 \u0430\u0433\u0435\u043d\u0442-\u043f\u0440\u043e\u0444\u0438\u043b\u0435\u0439 \u0434\u043b\u044f \u0441\u0438\u043c\u0443\u043b\u044f\u0446\u0438\u0438 MAGISTRY. "
        "\u0412\u0435\u0440\u043d\u0438\u0442\u0435 \u0422\u041e\u041b\u042c\u041a\u041e structured JSON \u043f\u043e \u0441\u0445\u0435\u043c\u0435."
    )
    user_prompt = (
        f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u0438:\n{base_cfg.narrative_context}\n\n"
        f"\u041f\u043e\u0436\u0435\u043b\u0430\u043d\u0438\u044f \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f (\u0441\u0440\u0435\u0434\u0430/\u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442):\n{payload.prompt}\n\n"
        "\u041e\u0441\u043d\u043e\u0432\u043d\u044b\u0435 \u0430\u0433\u0435\u043d\u0442\u044b (id, \u0438\u043c\u044f, \u0434\u043e\u043b\u0436\u043d\u043e\u0441\u0442\u044c):\n"
        + "\n".join(
            f"- {a['id']}: {a['name']} \u2014 {a['position']}" for a in primary_agents
        )
        + "\n\n"
        f"\u041d\u0443\u0436\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0432\u0442\u043e\u0440\u0438\u0447\u043d\u044b\u0445 \u0430\u0433\u0435\u043d\u0442\u043e\u0432. \u041d\u043e\u0432\u044b\u0435 id \u0414\u041e\u041b\u0416\u041d\u042b \u0431\u044b\u0442\u044c \u0441\u0442\u0440\u043e\u0433\u043e \u0442\u0430\u043a\u0438\u043c\u0438:\n{', '.join(requested_ids)}\n\n"
        "\u041f\u0440\u0430\u0432\u0438\u043b\u0430:\n"
        f"- connection.target_id \u0442\u043e\u043b\u044c\u043a\u043e \u0438\u0437: {', '.join(primary_ids)}\n"
        "- capabilities \u043e\u0441\u0442\u0430\u0432\u044c\u0442\u0435 \u043f\u0443\u0441\u0442\u044b\u043c \u043c\u0430\u0441\u0441\u0438\u0432\u043e\u043c []\n"
        "- initial_resources \u0437\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u043d\u0443\u043b\u044f\u043c\u0438\n"
        "- \u0443 \u043a\u0430\u0436\u0434\u043e\u0433\u043e \u0430\u0433\u0435\u043d\u0442\u0430 \u043c\u0438\u043d\u0438\u043c\u0443\u043c 1 connection \u043a \u043e\u0441\u043d\u043e\u0432\u043d\u043e\u043c\u0443 \u0430\u0433\u0435\u043d\u0442\u0443\n"
        "- biography 2\u20135 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0439, \u043e\u0442\u0440\u0430\u0436\u0430\u0435\u0442 \u043c\u043e\u0442\u0438\u0432\u0430\u0446\u0438\u044e/\u0434\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0441\u0440\u0435\u0434\u044b\n"
    )

    provider = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
    try:
        resp = provider.generate_structured(
            system=system,
            user=user_prompt,
            schema=schema,
            temperature=0.25,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    data = resp.data if isinstance(resp.data, dict) else {}
    narrative_context = str(data.get("narrative_context", "") or "").strip()
    if not narrative_context:
        narrative_context = base_cfg.narrative_context

    raw_agents = data.get("agents", [])
    if not isinstance(raw_agents, list):
        raise HTTPException(status_code=502, detail="LLM returned invalid agents")

    returned_ids = []
    validated: list[AgentProfile] = []
    for item in raw_agents:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id", "") or "")
        returned_ids.append(aid)
        if aid not in requested_ids:
            continue
        if aid in {a.id for a in base_cfg.agents}:
            continue
        conns = item.get("connections", [])
        if isinstance(conns, list):
            item["connections"] = [
                c
                for c in conns
                if isinstance(c, dict)
                and str(c.get("target_id", "") or "") in primary_ids
            ]
        try:
            validated.append(AgentProfile.model_validate(item))
        except Exception:
            continue

    if set(requested_ids) != {a.id for a in validated}:
        missing = sorted(set(requested_ids) - {a.id for a in validated})
        raise HTTPException(
            status_code=502,
            detail=f"LLM did not return all requested agents: {', '.join(missing)}",
        )

    by_id: dict[str, AgentProfile] = {a.id: a for a in base_cfg.agents}

    def _add_backlink(target_id: str, source: AgentProfile, conn: Connection) -> None:
        target = by_id.get(target_id)
        if target is None:
            return
        if any(c.target_id == source.id for c in target.connections):
            return
        backlink = Connection(
            target_id=source.id,
            name=source.name,
            relation=conn.relation,
            strength=conn.strength,
        )
        by_id[target_id] = target.model_copy(
            update={"connections": list(target.connections) + [backlink]}
        )

    for agent in validated:
        by_id[agent.id] = agent
        for conn in agent.connections:
            _add_backlink(conn.target_id, agent, conn)

    final_agents: list[AgentProfile] = []
    seen: set[str] = set()
    for a in base_cfg.agents:
        updated = by_id.get(a.id)
        if updated and updated.id not in seen:
            final_agents.append(updated)
            seen.add(updated.id)
    for a in validated:
        if a.id not in seen:
            final_agents.append(by_id[a.id])
            seen.add(a.id)

    cfg = base_cfg.model_copy(
        update={"agents": final_agents, "narrative_context": narrative_context}
    )
    return cfg.model_dump(mode="json")
