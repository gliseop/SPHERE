"""Маршруты шаблонов: встроенные сценарии и режимы управления."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_viewer
from web.backend.database import User
from web.backend.settings import GOVERNANCE_MODES_DIR

router = APIRouter(tags=["templates"])


@router.get("/api/templates/scenarios")
async def list_template_scenarios(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список встроенных шаблонов сценариев (S*)."""
    from magistry_sim.scenarios import SCENARIOS

    items = []
    for sid, cfg in sorted(SCENARIOS.items(), key=lambda x: x[0].value):
        items.append(
            {
                "id": sid.value,
                "title": cfg.title,
                "description": cfg.description,
                "max_rounds": cfg.max_rounds,
                "seed": cfg.seed,
                "corruption_level": getattr(cfg, "corruption_level", 0.0),
            }
        )
    return items


@router.get("/api/templates/scenarios/{scenario_id}")
async def get_template_scenario(
    scenario_id: str,
    governance: str | None = None,
    _user: User = Depends(require_viewer),
) -> dict:
    """Вернуть полный конфиг встроенного сценария.

    Query params:
        governance: Если указан, добавить governance-агентов (auditor/jury) и
            установить режим управления в конфиге.
    """
    from magistry_sim.enums import GovernanceMode, ScenarioId
    from magistry_sim.scenarios import add_governance_agents, get_scenario

    try:
        sid = ScenarioId(scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown scenario") from exc

    cfg = get_scenario(sid)

    if governance:
        try:
            gov = GovernanceMode(governance)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Режим управления '{governance}' не поддерживается движком. "
                "Кастомные режимы (G4+) пока доступны только как метаданные.",
            )
        cfg = add_governance_agents(cfg, gov)

    return cfg.model_dump(mode="json")


@router.get("/api/templates/governance")
async def list_governance_templates(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список режимов управления (встроенные G0-G3 и пользовательские).

    Встроенные режимы перечислены статически, пользовательские загружаются
    из ``GOVERNANCE_MODES_DIR/*.json``.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Список словарей с полями id, label, description.
    """
    from magistry_sim.enums import GovernanceMode

    labels = {
        "G0": "Без контроля",
        "G1": "Аудитор (рекомендательный)",
        "G2": "Аудитор (санкции по репутации)",
        "G3": "Полный контроль (трибунал)",
    }
    descriptions = {
        "G0": "Нет надзора со стороны аудитора или трибунала.",
        "G1": "Аудитор может наблюдать и давать рекомендации.",
        "G2": "Аудитор может рекомендовать заморозку репутации участников.",
        "G3": "Аудитор может инициировать трибунал; решение принимает коллегия присяжных.",
    }

    result = [
        {
            "id": mode.value,
            "label": f"{mode.value} — {labels.get(mode.value, mode.value)}",
            "description": descriptions.get(mode.value, ""),
        }
        for mode in GovernanceMode
    ]

    for p in sorted(GOVERNANCE_MODES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return result
