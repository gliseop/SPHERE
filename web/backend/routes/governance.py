"""CRUD-маршруты для пользовательских режимов управления /api/governance-modes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.settings import BUILTIN_GOVERNANCE_IDS, GOVERNANCE_MODES_DIR
from web.backend.validators import next_g_number, validate_library_id

router = APIRouter(tags=["governance"])


@router.get("/api/governance-modes")
async def list_governance_modes(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список всех режимов управления (встроенные + пользовательские).

    Встроенные режимы (G0-G3) формируются из перечисления GovernanceMode,
    пользовательские загружаются из ``GOVERNANCE_MODES_DIR/*.json``.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Список словарей с полями id, label, description.
    """
    from web.backend.constants import (
        GOVERNANCE_DESCRIPTIONS,
        GOVERNANCE_LABELS,
        GovernanceMode,
    )

    result: list[dict] = [
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


@router.post("/api/governance-modes", status_code=201)
async def create_governance_mode(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Создать пользовательский режим управления.

    Автоматически присваивает G-номер (G4, G5, ...) на основе
    существующих встроенных режимов (G0-G3) и файлов в GOVERNANCE_MODES_DIR.

    Args:
        data: Словарь с полями label и description.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Сохранённый режим управления с назначенным id.
    """
    mode_id = next_g_number()
    data["id"] = mode_id
    data["custom"] = True
    path = GOVERNANCE_MODES_DIR / f"{mode_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.put("/api/governance-modes/{mode_id}")
async def update_governance_mode(
    mode_id: str, data: dict, _user: User = Depends(require_admin),
) -> dict:
    """Обновить пользовательский режим управления.

    Args:
        mode_id: Идентификатор режима (G4, G5, ...).
        data: Новые данные режима.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Обновлённый режим управления.
    """
    validate_library_id(mode_id, GOVERNANCE_MODES_DIR, kind="governance-mode")
    if mode_id in BUILTIN_GOVERNANCE_IDS:
        raise HTTPException(status_code=403, detail="Cannot modify built-in governance mode")
    path = GOVERNANCE_MODES_DIR / f"{mode_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Governance mode not found")
    data["id"] = mode_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/api/governance-modes/{mode_id}", status_code=204)
async def delete_governance_mode(mode_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить пользовательский режим управления.

    Встроенные режимы (G0-G3) не могут быть удалены.

    Args:
        mode_id: Идентификатор режима (G4, G5, ...).
        _user: Аутентифицированный пользователь с ролью admin.
    """
    validate_library_id(mode_id, GOVERNANCE_MODES_DIR, kind="governance-mode")
    if mode_id in BUILTIN_GOVERNANCE_IDS:
        raise HTTPException(status_code=403, detail="Cannot delete built-in governance mode")
    path = GOVERNANCE_MODES_DIR / f"{mode_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Governance mode not found")
    path.unlink()
