"""Маршруты AI-генерации: личности, типы агентов, вторичные агенты."""

from __future__ import annotations

import asyncio
import json
import os as _os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from magistry_lc.llm import create_provider

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
    """Сгенерировать вторичных агентов (fam_*/soc_*) и вернуть обновлённый ScenarioConfig.

    Зависит от magistry_sim (config, scenarios, personality) и пока недоступен.
    """
    raise HTTPException(
        status_code=501,
        detail="Генерация вторичных агентов недоступна: движок magistry_sim удалён.",
    )
