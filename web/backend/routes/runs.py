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
from web.backend.graph_state import GraphStateBuilder, normalize_event_compat
from web.backend.run_artifacts import (
    list_run_artifacts,
    parse_run_name,
    resolve_run_artifact,
    run_json_sidecar_candidates,
    run_jsonl_sidecar_candidates,
)
from web.backend.settings import ARTIFACTS_DIR, DOC_ID_RE, RESULTS_DIR
from web.backend.validators import validate_run_name

router = APIRouter(tags=["runs"])


def _trace_span_to_prompt_record(span: dict) -> dict:
    """Преобразовать LC trace-span в legacy-подобную запись prompt inspector."""
    agent_id = str(span.get("agent_id", span.get("name", "")) or "")
    round_value = span.get("round", span.get("tick"))
    return {
        "event_type": "llm_call",
        "agent_id": agent_id,
        "round": round_value,
        "tick": round_value,
        "timestamp": span.get("timestamp", ""),
        "system_prompt": span.get("system", ""),
        "user_prompt": span.get("user", ""),
        "response": span.get("response", ""),
        "role": span.get("role", ""),
        "name": span.get("name", ""),
        "model": span.get("model", ""),
        "usage": span.get("usage", {}) or {},
        "duration_ms": span.get("duration_ms", 0.0),
        "error": span.get("error"),
    }


async def _read_prompt_records_from_trace(
    path: Path,
    *,
    agent_id: str | None,
    round: int | None,
    timestamp: str | None,
    limit: int,
) -> list[dict]:
    results: list[dict] = []
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(span, dict):
                continue
            record = _trace_span_to_prompt_record(span)
            if agent_id and record.get("agent_id") != agent_id:
                continue
            if round is not None and record.get("round") != round:
                continue
            if timestamp and record.get("timestamp") != timestamp:
                continue
            results.append(record)
            if len(results) >= limit:
                break
    return results


async def _read_prompt_records_from_events(
    path: Path,
    *,
    agent_id: str | None,
    round: int | None,
    timestamp: str | None,
    limit: int,
) -> list[dict]:
    results: list[dict] = []
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event = normalize_event_compat(event)
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
    return results


@router.get("/api/runs")
async def list_runs(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список доступных прогонов.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Список словарей с метаданными прогонов.
    """
    runs = []
    for ref in list_run_artifacts(results_dir=RESULTS_DIR):
        try:
            stat = ref.events_path.stat()
        except OSError:
            continue
        meta = parse_run_name(ref.name)
        meta["name"] = ref.name
        if ref.format == "directory":
            meta["filename"] = f"{ref.name}/events.jsonl"
        else:
            meta["filename"] = ref.events_path.name
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
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        raise HTTPException(status_code=404, detail="Run not found")
    builder = GraphStateBuilder()
    events: list[dict] = []
    total = 0
    async with aiofiles.open(ref.events_path, encoding="utf-8") as f:
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
                events.append(normalize_event_compat(event))
            total += 1
    return {
        "name": name,
        "events": events,
        "offset": offset,
        "limit": limit,
        "total_events": total,
        "graph": builder.state(),
        "meta": parse_run_name(name),
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

    for ref in list_run_artifacts(results_dir=RESULTS_DIR):
        try:
            async with aiofiles.open(ref.events_path, encoding="utf-8") as handle:
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
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        raise HTTPException(status_code=404, detail="Run not found")

    events: list[dict] = []
    try:
        async with aiofiles.open(ref.events_path, encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    event = normalize_event_compat(event)
                events.append(event)
    except OSError:
        raise HTTPException(status_code=404, detail="Run file not found")

    def _read_json(suffix: str) -> dict | None:
        stem = suffix.removeprefix("_").removesuffix(".json")
        for p in run_json_sidecar_candidates(ref, stem, results_dir=RESULTS_DIR):
            if not p.exists():
                continue
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
        return None

    result = {
        "name": name,
        "meta": parse_run_name(name),
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
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        raise HTTPException(status_code=404, detail="Run not found")
    scenario_path: Path | None = None
    for candidate in run_json_sidecar_candidates(ref, "scenario", results_dir=RESULTS_DIR):
        if candidate.exists():
            scenario_path = candidate
            break
    if scenario_path is None:
        raise HTTPException(status_code=404, detail="Scenario config not found for this run")
    return json.loads(scenario_path.read_text(encoding="utf-8"))


@router.get("/api/run/{name}/prompts")
async def get_run_prompts(
    name: str,
    agent_id: str | None = None,
    round: int | None = None,
    timestamp: str | None = None,
    limit: int = 50,
    _user: User = Depends(require_admin),
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
    if limit < 0:
        raise HTTPException(status_code=400, detail="Invalid limit")
    if limit > 1_000:
        raise HTTPException(status_code=400, detail="Limit too large")

    validate_run_name(name)
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        raise HTTPException(status_code=404, detail="Run not found")

    try:
        for trace_path in run_jsonl_sidecar_candidates(ref, "trace", results_dir=RESULTS_DIR):
            if not trace_path.exists():
                continue
            return await _read_prompt_records_from_trace(
                trace_path,
                agent_id=agent_id,
                round=round,
                timestamp=timestamp,
                limit=limit,
            )
        return await _read_prompt_records_from_events(
            ref.events_path,
            agent_id=agent_id,
            round=round,
            timestamp=timestamp,
            limit=limit,
        )
    except OSError:
        raise HTTPException(status_code=404, detail="Run file not found")


@router.get("/api/debug/llm-log")
async def get_llm_debug_log(
    limit: int = 200,
    _user: User = Depends(require_admin),
) -> list[dict]:
    """Вернуть хвост debug-лога LLM (JSONL).

    Лог пишется провайдером ``magistry_lc.llm.OpenAICompatibleProvider``
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
