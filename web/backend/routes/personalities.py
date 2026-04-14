"""CRUD-маршруты для личностей и интервью."""

from __future__ import annotations

import json
import os as _os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from sphere_lc.persona import INTERVIEW_QUESTIONS_V2
from sphere_lc.prompts import render_prompt

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.models import GenerateInterviewPayload
from web.backend.routes.ai import _generate_structured_via_provider
from web.backend.settings import INTERVIEWS_DIR, PERSONALITIES_DIR
from web.backend.validators import validate_library_id

router = APIRouter(tags=["personalities"])


def _interview_generation_schema() -> dict[str, Any]:
    """Structured schema для interview generation."""

    question_count = len(INTERVIEW_QUESTIONS_V2)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "interview": {
                "type": "array",
                "minItems": question_count,
                "maxItems": question_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {"type": "string", "minLength": 5, "maxLength": 400},
                        "answer": {"type": "string", "minLength": 20, "maxLength": 2500},
                    },
                    "required": ["question", "answer"],
                },
            },
            "expert_psychologist": {"type": "string", "minLength": 60, "maxLength": 4000},
            "expert_economist": {"type": "string", "minLength": 60, "maxLength": 4000},
        },
        "required": ["interview", "expert_psychologist", "expert_economist"],
    }


def _interview_generation_prompts(
    *,
    personality_id: str,
    payload: GenerateInterviewPayload,
    personality: dict[str, Any],
) -> tuple[str, str]:
    """Построить system/user prompt для генерации интервью личности."""

    questions = "\n".join(f"{idx + 1}. {question}" for idx, question in enumerate(INTERVIEW_QUESTIONS_V2))
    biography = str(personality.get("biography") or "").strip()
    description = str(personality.get("description") or "").strip()
    name = str(personality.get("name") or personality_id).strip() or personality_id
    prototypes = personality.get("prototypes")
    prototypes_text = ", ".join(str(item).strip() for item in prototypes if str(item).strip()) if isinstance(prototypes, list) else ""
    hexaco = json.dumps(personality.get("hexaco") or {}, ensure_ascii=False)
    dark_triad = json.dumps(personality.get("dark_triad") or {}, ensure_ascii=False)
    techniques = ", ".join(str(item).strip() for item in list(personality.get("neutralization_techniques") or []) if str(item).strip())

    system_prompt = render_prompt("web.ai.generate_interview.system")
    user_prompt = render_prompt(
        "web.ai.generate_interview.user",
        name=name,
        personality_id=personality_id,
        role=payload.role.strip(),
        description=description or "(пусто)",
        prototypes_text=prototypes_text or "(нет)",
        biography=biography or "(пусто)",
        hexaco=hexaco,
        dark_triad=dark_triad,
        techniques=techniques or "(нет)",
        questions=questions,
    )
    return system_prompt, user_prompt


def _normalize_generated_interview(
    *,
    personality_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Нормализовать LLM-ответ к хранимому interview-формату."""

    raw_items = data.get("interview")
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=502, detail="LLM returned invalid interview payload")

    answers_by_question: dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            continue
        answers_by_question[question] = answer

    ordered_interview: dict[str, str] = {}
    for question in INTERVIEW_QUESTIONS_V2:
        answer = answers_by_question.get(question, "").strip()
        if not answer:
            raise HTTPException(status_code=502, detail="LLM interview payload is incomplete")
        ordered_interview[question] = answer

    return {
        "id": personality_id,
        "protocol_version": "v2",
        "interview": ordered_interview,
        "expert_psychologist": str(data.get("expert_psychologist") or "").strip(),
        "expert_economist": str(data.get("expert_economist") or "").strip(),
    }


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
    validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
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
    """Сгенерировать интервью для личности через LLM и сохранить его в `data/interviews`."""
    validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
    if not _os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

    personality_path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not personality_path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")
    try:
        personality = json.loads(personality_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read personality: {exc}") from exc
    if not isinstance(personality, dict):
        raise HTTPException(status_code=500, detail="Invalid personality JSON")

    system_prompt, user_prompt = _interview_generation_prompts(
        personality_id=personality_id,
        payload=payload,
        personality=personality,
    )

    try:
        response = await _generate_structured_via_provider(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=_interview_generation_schema(),
            temperature=0.3,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    data = response.data if isinstance(response.data, dict) else {}
    interview_record = _normalize_generated_interview(personality_id=personality_id, data=data)
    interview_path = INTERVIEWS_DIR / f"{personality_id}.json"
    try:
        interview_path.write_text(
            json.dumps(interview_record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write interview: {exc}") from exc
    return interview_record


@router.delete("/api/personalities/{personality_id}/interview", status_code=204)
async def delete_interview(personality_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить интервью для перегенерации."""
    validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
    path = INTERVIEWS_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Interview not found")
    path.unlink()
