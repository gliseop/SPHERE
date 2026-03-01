"""CRUD-маршруты для личностей и интервью."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.models import GenerateInterviewPayload
from web.backend.settings import INTERVIEWS_DIR, PERSONALITIES_DIR
from web.backend.validators import validate_library_id

router = APIRouter(tags=["personalities"])


@router.get("/api/personalities")
async def list_personalities(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список личностей (шаблоны)."""
    result = []
    for p in sorted(PERSONALITIES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            pid = data.get("id", "")
            interview_path = INTERVIEWS_DIR / f"{pid}.json"
            data["has_interview"] = interview_path.exists()
            result.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return result


@router.post("/api/personalities", status_code=201)
async def create_personality(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Создать новую личность."""
    item_id = str(uuid.uuid4())
    data["id"] = item_id
    path = PERSONALITIES_DIR / f"{item_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.put("/api/personalities/{personality_id}")
async def update_personality(personality_id: str, data: dict, _user: User = Depends(require_admin)) -> dict:
    """Обновить личность."""
    validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
    path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")
    data["id"] = personality_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/api/personalities/{personality_id}", status_code=204)
async def delete_personality(personality_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить личность."""
    validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
    path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")
    path.unlink()


# ---------------------------------------------------------------------------
# Interview CRUD (per-personality)
# ---------------------------------------------------------------------------


@router.get("/api/personalities/{personality_id}/interview")
async def get_interview(personality_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Получить интервью для личности (без embedding для экономии трафика)."""
    path = INTERVIEWS_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Interview not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("embedding", None)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/personalities/{personality_id}/interview/generate")
async def generate_personality_interview(
    personality_id: str,
    payload: GenerateInterviewPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Сгенерировать интервью для личности через LLM.

    Длительная операция (5 LLM-вызовов), выполняется в отдельном потоке.
    """
    pers_path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not pers_path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")

    try:
        raw = json.loads(pers_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    def _generate() -> dict:
        from magistry_sim.interviews import generate_interview, save_interview
        from magistry_sim.llm import create_embedding_provider, create_provider
        from magistry_sim.personality import (
            AgentPersonality,
            DarkTriadProfile,
            HEXACOProfile,
        )

        hexaco_raw = raw.get("hexaco", {})
        dt_raw = raw.get("dark_triad", {})
        personality = AgentPersonality(
            hexaco=HEXACOProfile(**hexaco_raw),
            dark_triad=DarkTriadProfile(**dt_raw),
            neutralization_techniques=raw.get("neutralization_techniques", []),
            biography=raw.get("biography", ""),
        )

        archetype = personality.classify_archetype()
        llm = create_provider(mock=False)
        embedder = create_embedding_provider(mock=False, provider="local")

        interview = generate_interview(
            personality=personality,
            role=payload.role,
            archetype=archetype,
            llm=llm,
            embedder=embedder,
            interview_id=personality_id,
            use_extended_protocol=True,
        )
        save_interview(interview, INTERVIEWS_DIR)
        result = interview.model_dump()
        result.pop("embedding", None)
        return result

    result = await asyncio.to_thread(_generate)
    return result


@router.delete("/api/personalities/{personality_id}/interview", status_code=204)
async def delete_interview(personality_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить интервью для перегенерации."""
    path = INTERVIEWS_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Interview not found")
    path.unlink()
