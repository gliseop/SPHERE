"""CRUD-маршруты для пользовательских сценариев /api/scenarios."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.models import ScenarioPayload
from web.backend.settings import SCENARIOS_DIR
from web.backend.validators import next_s_number, validate_scenario_id

router = APIRouter(tags=["scenarios"])


@router.get("/api/scenarios")
async def list_scenarios(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список сценариев.

    Args:
        _user: Аутентифицированный пользователь (любая роль).
    """
    result = []
    for p in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return result


@router.get("/api/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Вернуть сценарий по ID.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Словарь с данными сценария.
    """
    validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/api/scenarios", status_code=201)
async def create_scenario(payload: ScenarioPayload, _user: User = Depends(require_admin)) -> dict:
    """Создать новый сценарий.

    Автоматически присваивает S-номер (S7, S8, ...) на основе
    существующих встроенных сценариев (S0-S6) и файлов в SCENARIOS_DIR.

    Args:
        payload: Данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Сохранённый сценарий с назначенным id.
    """
    scenario_id = next_s_number()
    data = payload.model_dump(mode="json")
    data["id"] = scenario_id
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.put("/api/scenarios/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    payload: ScenarioPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Обновить сценарий.

    Args:
        scenario_id: UUID строка.
        payload: Новые данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Обновлённый сценарий.
    """
    validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    data = payload.model_dump(mode="json")
    data["id"] = scenario_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/api/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить сценарий.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь с ролью admin.
    """
    validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    path.unlink()
