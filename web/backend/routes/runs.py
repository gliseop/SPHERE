"""Маршруты чтения прогонов, артефактов и debug-лога."""

from __future__ import annotations

import json
import os as _os
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.graph_state import GraphStateBuilder
from web.backend.settings import ARTIFACTS_DIR, DOC_ID_RE, RESULTS_DIR
from web.backend.validators import validate_run_name
from web.backend.websocket import _parse_run_name

router = APIRouter(tags=["runs"])


@router.get("/api/runs")
async def list_runs(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список доступных прогонов.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Список словарей с метаданными прогонов.
    """
    runs = []
    paths = sorted(
        RESULTS_DIR.glob("*_events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in paths:
        stat = p.stat()
        meta = _parse_run_name(p.name)
        meta["name"] = p.stem.replace("_events", "")
        meta["filename"] = p.name
        meta["size_kb"] = round(stat.st_size / 1024, 1)
        meta["created_at"] = stat.st_mtime
        runs.append(meta)
    return runs


@router.get("/api/run/{name}")
async def get_run(
    name: str,
    offset: int = 0,
    limit: int = 1_000,
    include_events: bool = True,
    _user: User = Depends(require_viewer),
) -> dict:
    """Вернуть все события прогона.

    Args:
        name: Имя прогона (без суффикса _events.jsonl).
        offset: Смещение в списке событий.
        limit: Максимальное количество событий.
        include_events: Включать ли события в ответ.
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Словарь с событиями и состоянием графа.
    """
    if offset < 0:
        raise HTTPException(status_code=400, detail="Invalid offset")
    if limit < 0:
        raise HTTPException(status_code=400, detail="Invalid limit")
    if limit > 10_000:
        raise HTTPException(status_code=400, detail="Limit too large")

    validate_run_name(name)
    path = RESULTS_DIR / f"{name}_events.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    builder = GraphStateBuilder()
    events: list[dict] = []
    total = 0
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            builder.ingest(event)
            if include_events and total >= offset and len(events) < limit:
                events.append(event)
            total += 1
    return {
        "name": name,
        "events": events,
        "offset": offset,
        "limit": limit,
        "total_events": total,
        "graph": builder.state(),
        "meta": _parse_run_name(f"{name}_events.jsonl"),
    }


@router.get("/api/artifacts/{doc_id}")
async def get_artifact(doc_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Получить документ-артефакт по ID.

    Args:
        doc_id: Идентификатор документа.
        _user: Аутентифицированный пользователь (любая роль).
    """
    if not DOC_ID_RE.fullmatch(doc_id):
        raise HTTPException(status_code=400, detail="Invalid doc_id")

    path = (ARTIFACTS_DIR / f"{doc_id}.md").resolve()
    if str(path).startswith(str(ARTIFACTS_DIR)) and path.exists():
        return {
            "doc_id": doc_id,
            "content": path.read_text(encoding="utf-8"),
        }

    for jsonl_path in sorted(
        RESULTS_DIR.glob("*_events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            async with aiofiles.open(jsonl_path, encoding="utf-8") as handle:
                async for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("event_type") != "document_created":
                        continue
                    payload = event.get("payload", {})
                    if payload.get("doc_id") == doc_id:
                        return {
                            "doc_id": doc_id,
                            "title": payload.get("title", ""),
                            "doc_type": payload.get("doc_type", ""),
                            "case_id": payload.get("case_id", ""),
                            "content": payload.get("content", ""),
                        }
        except (OSError, json.JSONDecodeError):
            continue

    raise HTTPException(status_code=404, detail="Artifact not found")


@router.get("/api/run/{name}/export")
async def export_run(name: str, _user: User = Depends(require_viewer)) -> JSONResponse:
    """Выгрузить полный прогон в виде единого JSON-файла.

    Args:
        name: Имя прогона.
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        JSON с полями events, scenario, names, summary и meta.
    """
    validate_run_name(name)
    events_path = RESULTS_DIR / f"{name}_events.jsonl"
    if not events_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    events: list[dict] = []
    try:
        async with aiofiles.open(events_path, encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        raise HTTPException(status_code=404, detail="Run file not found")

    def _read_json(suffix: str) -> dict | None:
        p = RESULTS_DIR / f"{name}{suffix}"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    result = {
        "name": name,
        "meta": _parse_run_name(f"{name}_events.jsonl"),
        "events": events,
        "scenario": _read_json("_scenario.json"),
        "names": _read_json("_names.json"),
        "summary": _read_json("_summary.json"),
    }

    return JSONResponse(
        content=result,
        headers={
            "Content-Disposition": f'attachment; filename="{name}.json"',
        },
    )


@router.get("/api/run/{name}/scenario")
async def get_run_scenario(name: str, _user: User = Depends(require_viewer)) -> dict:
    """Получить конфигурацию сценария для указанного прогона.

    Args:
        name: Имя прогона.
        _user: Аутентифицированный пользователь.

    Returns:
        Конфигурация сценария (ScenarioConfig).
    """
    validate_run_name(name)
    path = RESULTS_DIR / f"{name}_scenario.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario config not found for this run")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/api/run/{name}/prompts")
async def get_run_prompts(
    name: str,
    agent_id: str | None = None,
    round: int | None = None,
    timestamp: str | None = None,
    limit: int = 50,
    _user: User = Depends(require_viewer),
) -> list[dict]:
    """Получить записи вызовов LLM для указанного прогона.

    Args:
        name: Имя прогона.
        agent_id: Фильтр по агенту.
        round: Фильтр по раунду.
        timestamp: Точный timestamp для однозначного поиска события.
        limit: Максимальное количество записей.
        _user: Аутентифицированный пользователь.

    Returns:
        Список событий llm_call с полными данными промптов.
    """
    validate_run_name(name)
    path = RESULTS_DIR / f"{name}_events.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    results: list[dict] = []
    try:
        async with aiofiles.open(path, encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event_type") != "llm_call":
                    continue
                if agent_id and event.get("agent_id") != agent_id:
                    continue
                if round is not None and event.get("round") != round:
                    continue
                if timestamp and event.get("timestamp") != timestamp:
                    continue
                results.append(event)
                if len(results) >= limit:
                    break
    except OSError:
        raise HTTPException(status_code=404, detail="Run file not found")

    return results


@router.get("/api/debug/llm-log")
async def get_llm_debug_log(
    limit: int = 200,
    _user: User = Depends(require_admin),
) -> list[dict]:
    """Вернуть хвост debug-лога LLM (JSONL).

    Лог пишется провайдером ``magistry_sim.llm.OpenAICompatibleProvider``
    в файл ``results/llm_debug.jsonl`` (или ``MAGISTRY_LLM_LOG_PATH``).
    """
    try:
        limit = max(0, min(2_000, int(limit)))
    except Exception:
        limit = 200
    raw_path = (_os.environ.get("MAGISTRY_LLM_LOG_PATH") or "").strip()
    path = Path(raw_path).expanduser() if raw_path else (RESULTS_DIR / "llm_debug.jsonl")
    if limit <= 0 or not path.exists():
        return []

    try:
        with open(path, "rb") as f:  # noqa: WPS515
            f.seek(0, 2)
            pos = f.tell()
            buf = bytearray()
            need_lines = limit + 1
            while pos > 0 and buf.count(b"\n") < need_lines:
                step = min(64 * 1024, pos)
                pos -= step
                f.seek(pos)
                buf[:0] = f.read(step)
            lines = bytes(buf).splitlines()[-limit:]
    except OSError:
        return []

    out: list[dict] = []
    for line in lines:
        try:
            item = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out
