"""Маршруты чтения прогонов, артефактов и debug-лога."""

from __future__ import annotations

import json
import os as _os
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from web.backend.auth import require_admin, require_viewer
from web.backend.constants import GOVERNANCE_LABELS
from web.backend.database import User
from web.backend.graph_state import GraphStateBuilder, normalize_event_compat
from web.backend.routes.scenarios import _scenario_config_to_payload, load_scenario_config_for_web
from web.backend.run_artifacts import (
    list_run_artifacts,
    parse_run_name,
    resolve_run_artifact,
    run_json_sidecar_candidates,
    run_jsonl_sidecar_candidates,
    run_metadata_candidates,
)
from web.backend.settings import ARTIFACTS_DIR, DOC_ID_RE, RESULTS_DIR
from web.backend.validators import validate_run_name
from web.backend.visibility import sanitize_event_for_role

router = APIRouter(tags=["runs"])


def _read_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _runtime_payload_from_sim_config(sim_config: object) -> dict[str, Any] | None:
    if not isinstance(sim_config, dict):
        return None
    runtime = sim_config.get("runtime")
    if not isinstance(runtime, dict):
        return None
    try:
        tick_duration_days = max(1, int(runtime.get("tick_duration_days") or 1))
    except (TypeError, ValueError):
        tick_duration_days = 1
    return {
        "start_date": str(runtime.get("start_date") or "").strip() or None,
        "tick_granularity": str(runtime.get("tick_granularity") or "day"),
        "tick_duration_days": tick_duration_days,
    }


def _runtime_cfg_from_meta(meta: dict[str, Any]):
    runtime = meta.get("runtime")
    if not isinstance(runtime, dict):
        return None
    try:
        from sphere_lc.config import RuntimeConfig

        return RuntimeConfig.model_validate(
            {
                "start_date": runtime.get("start_date"),
                "tick_granularity": runtime.get("tick_granularity") or "day",
                "tick_duration_days": runtime.get("tick_duration_days") or 1,
            }
        )
    except Exception:
        return None


def _read_run_scenario_payload(ref) -> dict[str, Any] | None:
    for candidate in _iter_run_scenario_candidates(ref):
        if not candidate.exists():
            continue
        try:
            cfg = load_scenario_config_for_web(candidate, scenario_id=ref.name)
        except HTTPException:
            continue
        return _scenario_config_to_payload(cfg, scenario_id=ref.name)
    return None


def _governance_label(governance_id: str) -> str:
    normalized = governance_id.strip().upper()
    if not normalized:
        return ""
    return GOVERNANCE_LABELS.get(normalized, normalized)


def _read_run_meta(ref) -> dict[str, Any]:
    meta: dict[str, Any] = {
        **parse_run_name(ref.name),
        "run_name": ref.name,
        "display_name": ref.name,
        "scenario_title": ref.name,
        "governance_label": "",
    }

    for path in run_metadata_candidates(ref, results_dir=RESULTS_DIR):
        payload = _read_json_mapping(path)
        if payload is None:
            continue
        meta.update(payload)
        break

    scenario_payload = _read_run_scenario_payload(ref)
    if scenario_payload is not None:
        display_name = str(meta.get("display_name") or "").strip()
        scenario_title = str(scenario_payload.get("name") or "").strip() or ref.name
        if not display_name or display_name == ref.name:
            meta["display_name"] = scenario_title
        meta["scenario_title"] = str(meta.get("scenario_title") or "").strip() or scenario_title
        meta["scenario_id"] = meta.get("scenario_id") or scenario_payload.get("id")
        if not str(meta.get("governance") or "").strip():
            meta["governance"] = scenario_payload.get("governance") or ""
        if not meta.get("ticks_total"):
            meta["ticks_total"] = int(scenario_payload.get("rounds") or 0)
        if not isinstance(meta.get("runtime"), dict):
            runtime_payload = _runtime_payload_from_sim_config(scenario_payload.get("sim_config"))
            if runtime_payload is not None:
                meta["runtime"] = runtime_payload

    runtime_cfg = _runtime_cfg_from_meta(meta)
    if runtime_cfg is not None and runtime_cfg.start_date is not None:
        meta["simulated_start_date"] = str(meta.get("simulated_start_date") or runtime_cfg.start_date.isoformat())
        ticks_total = max(0, int(meta.get("ticks_total") or 0))
        last_tick = max(0, ticks_total - 1)
        simulated_end = runtime_cfg.simulated_date(last_tick)
        if simulated_end is not None:
            meta["simulated_end_date"] = str(meta.get("simulated_end_date") or simulated_end.isoformat())

    governance_id = str(meta.get("governance") or "").strip()
    meta["governance_label"] = _governance_label(governance_id)
    meta["run_name"] = ref.name
    return meta


def _event_with_simulated_time(event: dict[str, Any], *, meta: dict[str, Any]) -> dict[str, Any]:
    result = normalize_event_compat(event)
    runtime_cfg = _runtime_cfg_from_meta(meta)
    if runtime_cfg is None:
        return result
    tick_raw = result.get("tick", result.get("round"))
    try:
        tick = int(tick_raw)
    except (TypeError, ValueError):
        return result
    simulated = runtime_cfg.simulated_datetime(tick)
    if simulated is None:
        return result
    result["simulated_date"] = simulated.date().isoformat()
    result["simulated_time"] = simulated.strftime("%H:%M")
    result["simulated_timestamp"] = simulated.isoformat()
    return result


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
    if limit <= 0:
        return []
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
    if limit <= 0:
        return []
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


def _read_trace_records(
    path: Path,
    *,
    agent_id: str | None,
    role: str | None,
    tick_from: int | None,
    tick_to: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        row = _trace_span_to_prompt_record(item)
        tick_value = row.get("tick")
        if agent_id and str(row.get("agent_id") or "") != agent_id:
            continue
        if role and str(row.get("role") or "") != role:
            continue
        if tick_from is not None:
            try:
                if int(tick_value) < tick_from:
                    continue
            except (TypeError, ValueError):
                continue
        if tick_to is not None:
            try:
                if int(tick_value) > tick_to:
                    continue
            except (TypeError, ValueError):
                continue
        rows.append(row)
    return rows[-limit:]


def _trace_records_to_markdown(records: list[dict[str, Any]], *, meta: dict[str, Any]) -> str:
    title = str(meta.get("display_name") or meta.get("scenario_title") or meta.get("run_name") or "Прогон").strip()
    lines = [
        f"# Трейс прогона `{meta.get('run_name') or ''}`",
        "",
        f"- Название: {title}",
        f"- Управление: {meta.get('governance_label') or meta.get('governance') or 'не указано'}",
        f"- Симуляционный диапазон: {meta.get('simulated_start_date') or '—'} .. {meta.get('simulated_end_date') or '—'}",
        "",
    ]
    if not records:
        lines.append("_Записей не найдено._")
        return "\n".join(lines) + "\n"

    for index, record in enumerate(records, start=1):
        agent_id = str(record.get("agent_id") or record.get("name") or "unknown")
        tick_value = record.get("tick")
        role = str(record.get("role") or "agent")
        model = str(record.get("model") or "")
        timestamp = str(record.get("timestamp") or "")
        lines.extend(
            [
                f"## {index}. {agent_id}",
                "",
                f"- Роль trace: `{role}`",
                f"- Tick: `{tick_value}`",
                f"- Timestamp: `{timestamp or '—'}`",
                f"- Model: `{model or '—'}`",
                "",
                "### System",
                "```text",
                str(record.get('system_prompt') or ''),
                "```",
                "",
                "### User",
                "```text",
                str(record.get('user_prompt') or ''),
                "```",
                "",
                "### Response",
                "```text",
                str(record.get('response') or ''),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


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
        meta = _read_run_meta(ref)
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
    meta = _read_run_meta(ref)
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
            if not isinstance(event, dict):
                continue
            event = sanitize_event_for_role(event, role=_user.role)
            if event is None:
                continue
            builder.ingest(event)
            if include_events and total >= offset and len(events) < limit:
                events.append(_event_with_simulated_time(event, meta=meta))
            total += 1
    return {
        "name": name,
        "events": events,
        "offset": offset,
        "limit": limit,
        "total_events": total,
        "graph": builder.state(),
        "environment": _read_run_sidecar_json(ref, "environment_summary"),
        "meta": meta,
    }


@router.get("/api/run/{name}/snapshot")
async def get_run_snapshot(
    name: str,
    tail_limit: int = 250,
    _user: User = Depends(require_viewer),
) -> dict:
    """Вернуть финальное состояние прогона и хвост событий для monitor snapshot."""
    if tail_limit < 0:
        raise HTTPException(status_code=400, detail="Invalid tail_limit")
    if tail_limit > 2_000:
        raise HTTPException(status_code=400, detail="tail_limit too large")

    validate_run_name(name)
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        raise HTTPException(status_code=404, detail="Run not found")

    from collections import deque

    meta = _read_run_meta(ref)
    builder = GraphStateBuilder()
    tail = deque(maxlen=tail_limit if tail_limit > 0 else None)
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
            if not isinstance(event, dict):
                continue
            event = sanitize_event_for_role(event, role=_user.role)
            if event is None:
                continue
            builder.ingest(event)
            if tail_limit > 0:
                tail.append(_event_with_simulated_time(event, meta=meta))
            total += 1

    events = list(tail)
    current_round = None
    for item in reversed(events):
        value = item.get("round")
        if isinstance(value, int):
            current_round = value
            break
    if current_round is None:
        try:
            ticks_total = int(meta.get("ticks_total") or 0)
        except (TypeError, ValueError):
            ticks_total = 0
        current_round = max(0, ticks_total - 1) if ticks_total > 0 else None

    return {
        "name": name,
        "events": events,
        "total_events": total,
        "graph": builder.state(),
        "environment": _read_run_sidecar_json(ref, "environment_summary"),
        "names": _read_run_sidecar_json(ref, "names") or {},
        "meta": meta,
        "current_round": current_round,
    }


@router.get("/api/artifacts/{doc_id}")
async def get_artifact(doc_id: str, _user: User = Depends(require_admin)) -> dict:
    """Получить документ-артефакт по ID.

    Args:
        doc_id: Идентификатор документа.
        _user: Аутентифицированный пользователь с ролью admin.
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
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
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
        except OSError:
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
    meta = _read_run_meta(ref)
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
                if not isinstance(event, dict):
                    continue
                event = sanitize_event_for_role(event, role=_user.role)
                if event is None:
                    continue
                events.append(_event_with_simulated_time(event, meta=meta))
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

    def _read_scenario_json() -> dict | None:
        for p in _iter_run_scenario_candidates(ref):
            if not p.exists():
                continue
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
        return None

    result = {
        "name": name,
        "meta": meta,
        "events": events,
        "scenario": _read_scenario_json(),
        "names": _read_json("_names.json"),
        "summary": _read_json("_summary.json"),
        "environment": _read_json("_environment_summary.json"),
        "environment_timeline": _read_run_sidecar_jsonl(ref, "environment_timeline"),
    }

    return JSONResponse(
        content=result,
        headers={
            "Content-Disposition": f'attachment; filename="{name}.json"',
        },
    )


def _iter_run_scenario_candidates(ref) -> list[Path]:
    """Вернуть кандидаты scenario-sidecar для сохранённого или живого прогона."""
    candidates = list(run_json_sidecar_candidates(ref, "scenario", results_dir=RESULTS_DIR))
    if ref.format == "directory":
        candidates.append(ref.events_path.parent / "_input_scenario.json")
    return candidates


def _read_run_sidecar_json(ref, stem: str) -> dict | None:
    for path in run_json_sidecar_candidates(ref, stem, results_dir=RESULTS_DIR):
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _read_run_sidecar_jsonl(ref, stem: str) -> list[dict]:
    for path in run_jsonl_sidecar_candidates(ref, stem, results_dir=RESULTS_DIR):
        if not path.exists():
            continue
        try:
            rows: list[dict] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
            return rows
        except (json.JSONDecodeError, OSError):
            continue
    return []


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
    for candidate in _iter_run_scenario_candidates(ref):
        if candidate.exists():
            scenario_path = candidate
            break
    if scenario_path is None:
        raise HTTPException(status_code=404, detail="Scenario config not found for this run")
    cfg = load_scenario_config_for_web(scenario_path, scenario_id=name)
    return _scenario_config_to_payload(cfg, scenario_id=name)


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


@router.get("/api/run/{name}/trace.md")
async def get_run_trace_markdown(
    name: str,
    agent_id: str | None = None,
    role: str | None = None,
    tick_from: int | None = None,
    tick_to: int | None = None,
    limit: int = 200,
    _user: User = Depends(require_admin),
) -> PlainTextResponse:
    """Вернуть trace прогона в Markdown."""
    if limit < 0:
        raise HTTPException(status_code=400, detail="Invalid limit")
    if limit > 2_000:
        raise HTTPException(status_code=400, detail="Limit too large")

    validate_run_name(name)
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        raise HTTPException(status_code=404, detail="Run not found")

    trace_path: Path | None = None
    for candidate in run_jsonl_sidecar_candidates(ref, "trace", results_dir=RESULTS_DIR):
        if candidate.exists():
            trace_path = candidate
            break
    if trace_path is not None:
        records = _read_trace_records(
            trace_path,
            agent_id=agent_id,
            role=role,
            tick_from=tick_from,
            tick_to=tick_to,
            limit=limit,
        )
    else:
        prompt_records = await _read_prompt_records_from_events(
            ref.events_path,
            agent_id=agent_id,
            round=None,
            timestamp=None,
            limit=limit,
        )
        records = [
            {
                "agent_id": item.get("agent_id"),
                "tick": item.get("tick"),
                "timestamp": item.get("timestamp"),
                "system_prompt": item.get("system_prompt", ""),
                "user_prompt": item.get("user_prompt", ""),
                "response": item.get("response", ""),
                "role": item.get("role", "agent"),
                "model": item.get("model", ""),
            }
            for item in prompt_records
            if tick_from is None or int(item.get("tick") or 0) >= tick_from
            if tick_to is None or int(item.get("tick") or 0) <= tick_to
        ]
    markdown = _trace_records_to_markdown(records, meta=_read_run_meta(ref))
    return PlainTextResponse(
        markdown,
        headers={"Content-Disposition": f'attachment; filename="{name}.trace.md"'},
    )


@router.get("/api/debug/llm-log")
async def get_llm_debug_log(
    limit: int = 200,
    _user: User = Depends(require_admin),
) -> list[dict]:
    """Вернуть хвост debug-лога LLM (JSONL).

    Лог пишется провайдером ``sphere_lc.llm.OpenAICompatibleProvider``
    в файл ``results/llm_debug.jsonl`` (или ``SPHERE_LLM_LOG_PATH``).
    """
    try:
        limit = max(0, min(2_000, int(limit)))
    except Exception:
        limit = 200
    raw_path = (_os.environ.get("SPHERE_LLM_LOG_PATH") or "").strip()
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
