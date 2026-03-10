"""WebSocket-обработчики и вспомогательные функции."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import math
import time
from pathlib import Path
from typing import Any, AsyncIterator

import aiofiles
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth import verify_ws_token
from .graph_state import GraphStateBuilder, normalize_event_compat
from .run_artifacts import (
    parse_run_name,
    resolve_run_artifact,
    run_json_sidecar_candidates,
)
from .settings import (
    LIVE_GRAPH_THROTTLE_S,
    LIVE_HISTORY_EVENTS,
    RESULTS_DIR,
    WS_DROP_EVENT_TYPES,
    WS_EVENT_BATCH_INTERVAL_S,
    WS_EVENT_BATCH_SIZE,
    WS_MAX_STR_CHARS,
    WS_PING_INTERVAL_S,
    RUN_NAME_RE,
)
from .validators import validate_run_name

router = APIRouter()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _load_names(run_name: str) -> dict[str, str]:
    """Загрузить имена агентов из сопроводительного файла.

    Args:
        run_name: Имя прогона без суффикса _events.jsonl.

    Returns:
        Словарь agent_id -> отображаемое имя. Пустой словарь если файл отсутствует.
    """
    ref = resolve_run_artifact(run_name, results_dir=RESULTS_DIR)
    if ref is None:
        candidate_paths = [RESULTS_DIR / f"{run_name}_names.json", RESULTS_DIR / run_name / "names.json"]
    else:
        primary, fallback = run_json_sidecar_candidates(ref, "names", results_dir=RESULTS_DIR)
        candidate_paths = [primary, fallback]

    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            return {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            continue
    return {}


def _seed_builder_from_names(builder: GraphStateBuilder, names: dict[str, str]) -> None:
    """Pre-create graph nodes so UI can render agents before events arrive.

    Args:
        builder: Экземпляр GraphStateBuilder.
        names: Словарь agent_id -> имя.
    """
    for agent_id in names.keys():
        try:
            builder._ensure_agent(str(agent_id))
        except Exception:
            continue


def _truncate_json_value(value: Any, *, max_chars: int) -> tuple[Any, bool]:
    """Рекурсивно обрезать длинные строковые значения в JSON-структуре.

    Args:
        value: Значение для обработки.
        max_chars: Максимальная длина строки.

    Returns:
        Кортеж (обработанное значение, был ли текст обрезан).
    """
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars] + "\u2026", True

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        truncated = False
        for k, v in value.items():
            nv, t = _truncate_json_value(v, max_chars=max_chars)
            out[k] = nv
            truncated = truncated or t
        return out, truncated

    if isinstance(value, list):
        out_list: list[Any] = []
        truncated = False
        for item in value:
            nv, t = _truncate_json_value(item, max_chars=max_chars)
            out_list.append(nv)
            truncated = truncated or t
        return out_list, truncated

    return value, False


def _event_for_ws(
    event: dict[str, Any],
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Shrink large text fields for WS transport (helps reverse-proxies).

    Для событий ``llm_call`` промпты заменяются на метаданные (длина строк),
    полные тексты доступны через REST ``GET /api/run/{name}/prompts``.

    Если передан словарь names, добавляет agent_name и to_name
    для удобства отображения на фронтенде.
    """
    event = normalize_event_compat(event)
    if event.get("event_type") == "llm_call":
        payload = event.get("payload", {})
        out: dict[str, Any] = {
            **{k: v for k, v in event.items() if k != "payload"},
            "payload": {
                "call_type": payload.get("call_type", ""),
                "system_prompt_len": len(payload.get("system_prompt", "")),
                "user_prompt_len": len(payload.get("user_prompt", "")),
                "response_len": len(payload.get("response", "")),
            },
        }
        if names:
            aid = event.get("agent_id", "")
            if aid and aid in names:
                out["agent_name"] = names[aid]
        return out

    result = dict(event)

    if names:
        aid = result.get("agent_id", "")
        if aid and aid in names:
            result["agent_name"] = names[aid]
        payload = result.get("payload")
        if isinstance(payload, dict):
            to_id = payload.get("to_id", "")
            if to_id and to_id in names:
                result.setdefault("payload", {})
                result["payload"] = {**payload, "to_name": names[to_id]}

    if WS_MAX_STR_CHARS <= 0:
        return result
    trimmed, truncated = _truncate_json_value(result, max_chars=WS_MAX_STR_CHARS)
    if isinstance(trimmed, dict):
        if truncated:
            trimmed["_truncated"] = True
        return trimmed
    return result


def _ws_should_send_event(event: dict[str, Any]) -> bool:
    """Определить, нужно ли отправлять событие по WebSocket.

    Args:
        event: Словарь события.

    Returns:
        True если событие не входит в список игнорируемых типов.
    """
    event_type = str(event.get("event_type") or "")
    if not event_type:
        return False
    return event_type not in WS_DROP_EVENT_TYPES


async def _ws_flush_events(
    websocket: WebSocket,
    pending: list[dict[str, Any]],
) -> None:
    """Отправить накопленные события по WebSocket.

    Args:
        websocket: WebSocket-соединение.
        pending: Список событий для отправки (очищается после отправки).
    """
    if not pending:
        return
    if len(pending) == 1:
        await websocket.send_json({"type": "event", "data": pending[0]})
    else:
        await websocket.send_json({"type": "events", "data": pending})
    pending.clear()


async def _bootstrap_graph_from_file(
    path: Path,
    builder: GraphStateBuilder,
    *,
    tail_events: int,
) -> tuple[int, bytes, list[dict]]:
    """Ingest existing JSONL into graph state and return last events for UI.

    Args:
        path: Путь к файлу событий.
        builder: Экземпляр GraphStateBuilder.
        tail_events: Количество последних событий для возврата клиенту.

    Returns:
        Кортеж (file_pos, leftover_bytes, tail_events_list).
    """
    keep_tail = deque(maxlen=tail_events) if tail_events > 0 else None
    buf = bytearray()
    file_pos = 0

    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(64 * 1024)
            if not chunk:
                file_pos = await f.tell()
                break
            buf.extend(chunk)
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line_bytes = bytes(buf[:nl])
                del buf[: nl + 1]
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                builder.ingest(event)
                if keep_tail is not None:
                    keep_tail.append(event)

    return file_pos, bytes(buf), (list(keep_tail) if keep_tail is not None else [])


async def _stream_events_from_file(
    path: Path, speed: float = 1.0
) -> AsyncIterator[dict]:
    """Читать события из JSONL-файла для playback.

    Args:
        path: Путь к файлу.
        speed: Множитель скорости (2.0 = в 2 раза быстрее реального времени).

    Yields:
        Словари событий.
    """
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed must be a finite positive number")
    delay = max(0.05, 0.3 / speed)
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
                await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


@router.websocket("/ws/playback/{name}")
async def ws_playback(
    websocket: WebSocket,
    name: str,
    token: str | None = None,
    speed: float = 1.0,
) -> None:
    """WebSocket для воспроизведения записанного прогона.

    Args:
        websocket: WebSocket-соединение.
        name: Имя прогона.
        token: JWT-токен из query-параметра.
        speed: Скорость воспроизведения.
    """
    await websocket.accept()
    if verify_ws_token(token) is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=1008)
        return
    if not math.isfinite(speed) or speed <= 0:
        await websocket.send_json({"type": "error", "message": "Invalid speed"})
        await websocket.close(code=1008)
        return
    try:
        validate_run_name(name)
    except Exception:
        await websocket.send_json({"type": "error", "message": "Invalid run name"})
        await websocket.close()
        return
    ref = resolve_run_artifact(name, results_dir=RESULTS_DIR)
    if ref is None:
        await websocket.send_json({"type": "error", "message": "Run not found"})
        await websocket.close()
        return

    path = ref.events_path
    meta = parse_run_name(name)
    names = _load_names(name)
    await websocket.send_json({"type": "meta", **meta, "names": names, "run_name": name})
    builder = GraphStateBuilder()
    _seed_builder_from_names(builder, names)
    await websocket.send_json({"type": "graph_state", **builder.state()})

    last_graph_send = time.monotonic()
    graph_dirty = False
    pending: list[dict[str, Any]] = []
    pending_since = time.monotonic()
    try:
        async for event in _stream_events_from_file(path, speed=speed):
            builder.ingest(event)
            if _ws_should_send_event(event):
                pending.append(_event_for_ws(event, names))
            graph_dirty = True
            now = time.monotonic()
            if pending and (
                len(pending) >= WS_EVENT_BATCH_SIZE
                or (now - pending_since) >= WS_EVENT_BATCH_INTERVAL_S
            ):
                await _ws_flush_events(websocket, pending)
                pending_since = now
            if now - last_graph_send >= LIVE_GRAPH_THROTTLE_S:
                await _ws_flush_events(websocket, pending)
                pending_since = now
                await websocket.send_json({"type": "graph_state", **builder.state()})
                last_graph_send = now
                graph_dirty = False

        await _ws_flush_events(websocket, pending)
        if graph_dirty:
            await websocket.send_json({"type": "graph_state", **builder.state()})
        await websocket.send_json({"type": "done"})
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/live")
async def ws_live(
    websocket: WebSocket,
    token: str | None = None,
    run_name: str | None = None,
) -> None:
    """WebSocket для мониторинга текущего прогона.

    Следит за самым свежим *_events.jsonl файлом и пушит новые строки.

    Args:
        websocket: WebSocket-соединение.
        token: JWT-токен из query-параметра.
        run_name: Имя конкретного прогона (опционально).
    """
    await websocket.accept()
    if verify_ws_token(token) is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=1008)
        return

    from web.backend.runner import list_active

    builder = GraphStateBuilder()
    watched_run: str | None = None
    watched_path: Path | None = None
    file_pos = 0
    buf = bytearray()
    bootstrapped = False
    last_send = time.monotonic()
    last_graph_send = 0.0
    graph_dirty = False
    pending: list[dict[str, Any]] = []
    pending_since = time.monotonic()

    try:
        if run_name:
            try:
                validate_run_name(run_name)
            except Exception:
                await websocket.send_json({"type": "error", "message": "Invalid run name"})
                await websocket.close()
                return

        while True:
            active = list_active()
            running = [r for r in active if r.get("status") == "running"]

            target_run: str | None = None
            if run_name:
                target_run = run_name
                if not any(r.get("run_name") == run_name for r in running):
                    await websocket.send_json({"type": "done"})
                    await websocket.close(code=1000)
                    return
            else:
                if not running:
                    await websocket.send_json({"type": "done"})
                    await websocket.close(code=1000)
                    return
                for r in reversed(running):
                    candidate = str(r.get("run_name") or "")
                    if not candidate:
                        continue
                    try:
                        validate_run_name(candidate)
                    except Exception:
                        continue
                    target_run = candidate
                    break
                if not target_run:
                    await websocket.send_json({"type": "done"})
                    await websocket.close(code=1000)
                    return

            if not target_run:
                await asyncio.sleep(0.5)
                continue

            resolved = resolve_run_artifact(target_run, results_dir=RESULTS_DIR)
            target_path = resolved.events_path if resolved is not None else (RESULTS_DIR / f"{target_run}_events.jsonl")

            if target_run != watched_run or target_path != watched_path:
                watched_run = target_run
                watched_path = target_path
                file_pos = 0
                buf = bytearray()
                builder = GraphStateBuilder()
                names = _load_names(target_run)
                _seed_builder_from_names(builder, names)
                bootstrapped = False
                graph_dirty = False
                pending.clear()
                meta = parse_run_name(target_run)
                await websocket.send_json(
                    {"type": "meta", **meta, "names": names, "run_name": target_run}
                )
                await websocket.send_json({"type": "graph_state", **builder.state()})
                last_send = time.monotonic()
                last_graph_send = last_send
                pending_since = last_send

            if watched_path and not watched_path.exists():
                now = time.monotonic()
                if now - last_send >= WS_PING_INTERVAL_S:
                    await websocket.send_json({"type": "ping", "t": time.time()})
                    last_send = now
                await asyncio.sleep(0.5)
                continue

            if watched_path and not bootstrapped:
                try:
                    file_pos, leftover, tail_events = await _bootstrap_graph_from_file(
                        watched_path,
                        builder,
                        tail_events=LIVE_HISTORY_EVENTS,
                    )
                except OSError:
                    await asyncio.sleep(0.5)
                    continue

                buf = bytearray(leftover)
                await websocket.send_json({"type": "graph_state", **builder.state()})
                last_send = time.monotonic()
                last_graph_send = last_send
                pending.clear()
                pending_since = last_send
                for ev in tail_events:
                    if _ws_should_send_event(ev):
                        pending.append(_event_for_ws(ev, names))
                    now = time.monotonic()
                    if pending and (
                        len(pending) >= WS_EVENT_BATCH_SIZE
                        or (now - pending_since) >= WS_EVENT_BATCH_INTERVAL_S
                    ):
                        await _ws_flush_events(websocket, pending)
                        last_send = time.monotonic()
                        pending_since = last_send
                if pending:
                    await _ws_flush_events(websocket, pending)
                    last_send = time.monotonic()
                    pending_since = last_send
                bootstrapped = True

            try:
                assert watched_path is not None
                async with aiofiles.open(watched_path, "rb") as f:
                    while True:
                        await f.seek(file_pos)
                        chunk = await f.read(64 * 1024)
                        if chunk:
                            file_pos += len(chunk)
                            buf.extend(chunk)

                            while True:
                                nl = buf.find(b"\n")
                                if nl < 0:
                                    break
                                line_bytes = bytes(buf[:nl])
                                del buf[: nl + 1]
                                line = line_bytes.decode("utf-8", errors="replace").strip()
                                if not line:
                                    continue
                                try:
                                    event = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                builder.ingest(event)
                                if _ws_should_send_event(event):
                                    pending.append(_event_for_ws(event, names))
                                    if len(pending) >= WS_EVENT_BATCH_SIZE:
                                        await _ws_flush_events(websocket, pending)
                                        last_send = time.monotonic()
                                        pending_since = last_send
                                graph_dirty = True

                        now = time.monotonic()
                        if pending and (now - pending_since) >= WS_EVENT_BATCH_INTERVAL_S:
                            await _ws_flush_events(websocket, pending)
                            last_send = time.monotonic()
                            pending_since = last_send
                        if graph_dirty and (now - last_graph_send) >= LIVE_GRAPH_THROTTLE_S:
                            await _ws_flush_events(websocket, pending)
                            await websocket.send_json(
                                {"type": "graph_state", **builder.state()}
                            )
                            last_graph_send = now
                            last_send = now
                            graph_dirty = False

                        if now - last_send >= WS_PING_INTERVAL_S:
                            await websocket.send_json({"type": "ping", "t": time.time()})
                            last_send = now

                        await asyncio.sleep(0.5)

                        active = list_active()
                        running = [r for r in active if r.get("status") == "running"]

                        next_run: str | None = None
                        if run_name:
                            next_run = run_name
                            if not any(r.get("run_name") == run_name for r in running):
                                await websocket.send_json({"type": "done"})
                                await websocket.close(code=1000)
                                return
                        else:
                            if not running:
                                await websocket.send_json({"type": "done"})
                                await websocket.close(code=1000)
                                return
                            for r in reversed(running):
                                candidate = str(r.get("run_name") or "")
                                if not candidate:
                                    continue
                                try:
                                    validate_run_name(candidate)
                                except Exception:
                                    continue
                                next_run = candidate
                                break
                            if not next_run:
                                await websocket.send_json({"type": "done"})
                                await websocket.close(code=1000)
                                return

                        if next_run != watched_run:
                            break
                        if watched_path and not watched_path.exists():
                            bootstrapped = False
                            break
            except OSError:
                await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass
