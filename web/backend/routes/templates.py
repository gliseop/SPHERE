"""Маршруты шаблонов: встроенные сценарии и режимы управления."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_viewer
from web.backend.constants import (
    GOVERNANCE_DESCRIPTIONS,
    GOVERNANCE_LABELS,
    GovernanceMode,
)
from web.backend.database import User
from web.backend.settings import GOVERNANCE_MODES_DIR
from .scenarios import _LEGACY_TEMPLATE_FILE_MAP, load_template_config_for_web

router = APIRouter(tags=["templates"])


@router.get("/api/templates/scenarios")
async def list_template_scenarios(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список встроенных шаблонов сценариев (S*).

    Шаблоны собираются из поддерживаемых seed-сценариев репозитория.
    """
    result: list[dict] = []
    for scenario_id in sorted(_LEGACY_TEMPLATE_FILE_MAP):
        try:
            cfg = load_template_config_for_web(scenario_id)
        except HTTPException:
            continue
        result.append(
            {
                "id": scenario_id,
                "title": cfg.title or scenario_id,
                "description": cfg.description or "",
            }
        )
    return result


@router.get("/api/templates/scenarios/{scenario_id}")
async def get_template_scenario(
    scenario_id: str,
    governance: str | None = None,
    _user: User = Depends(require_viewer),
) -> dict:
    """Вернуть полный конфиг встроенного сценария.

    Возвращает валидный ``ScenarioConfig`` для запуска/редактирования в web UI.
    """
    cfg = load_template_config_for_web(scenario_id, governance=governance)
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
    result = [
        {
            "id": mode.value,
            "label": f"{mode.value} — {GOVERNANCE_LABELS.get(mode.value, mode.value)}",
            "description": GOVERNANCE_DESCRIPTIONS.get(mode.value, ""),
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
