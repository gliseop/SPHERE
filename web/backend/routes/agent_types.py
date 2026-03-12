"""CRUD-маршруты для типов агентов /api/agent-types."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.settings import AGENT_TYPES_DIR
from web.backend.validators import validate_library_id

router = APIRouter(tags=["agent-types"])


@router.get("/api/agent-types")
async def list_agent_types(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список типов агентов (шаблоны)."""
    result = []
    for p in sorted(AGENT_TYPES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return result


@router.post("/api/agent-types", status_code=201)
async def create_agent_type(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Создать новый тип агента."""
    item_id = str(uuid.uuid4())
    data["id"] = item_id
    path = AGENT_TYPES_DIR / f"{item_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.put("/api/agent-types/{type_id}")
async def update_agent_type(type_id: str, data: dict, _user: User = Depends(require_admin)) -> dict:
    """Обновить тип агента."""
    validate_library_id(type_id, AGENT_TYPES_DIR, kind="agent-type")
    path = AGENT_TYPES_DIR / f"{type_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Agent type not found")
    data["id"] = type_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/api/agent-types/{type_id}", status_code=204)
async def delete_agent_type(type_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить тип агента."""
    validate_library_id(type_id, AGENT_TYPES_DIR, kind="agent-type")
    path = AGENT_TYPES_DIR / f"{type_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Agent type not found")
    path.unlink()
