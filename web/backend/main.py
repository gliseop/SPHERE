"""FastAPI-сервер для веб-интерфейса симуляций MAGISTRY."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import AsyncIterator

import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

RESULTS_DIR = (Path(__file__).parent.parent.parent / "results").resolve()
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_run_name(name: str) -> None:
    """Проверить имя прогона на допустимые символы и path traversal.

    Args:
        name: Имя прогона из URL.

    Raises:
        HTTPException 400: Если имя содержит недопустимые символы.
        HTTPException 400: Если итоговый путь выходит за пределы RESULTS_DIR.
    """
    from fastapi import HTTPException

    if not _RUN_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid run name")
    resolved = (RESULTS_DIR / f"{name}_events.jsonl").resolve()
    if not str(resolved).startswith(str(RESULTS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid run name")

app = FastAPI(title="MAGISTRY Graph UI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _parse_run_name(filename: str) -> dict:
    """Разобрать имя файла прогона в метаданные.

    Args:
        filename: Имя файла вида S1_G2_events.jsonl или S1_G2_seed42_events.jsonl.

    Returns:
        Словарь с полями scenario, governance, seed (если есть).
    """
    m = re.match(
        r"(?P<scenario>S\d+)_(?P<governance>G\d+)(?:_seed(?P<seed>\d+))?_events\.jsonl",
        filename,
    )
    if not m:
        return {"scenario": "?", "governance": "?", "seed": None}
    return {
        "scenario": m.group("scenario"),
        "governance": m.group("governance"),
        "seed": int(m.group("seed")) if m.group("seed") else None,
    }


def _build_graph_state(events: list[dict]) -> dict:
    """Реконструировать состояние графа из событий.

    Args:
        events: Список событий до текущего момента.

    Returns:
        Словарь {"nodes": [...], "edges": [...]}.
    """
    agents: dict[str, dict] = {}
    edges: dict[tuple, float] = {}

    for e in events:
        aid = e.get("agent_id", "")
        if aid and aid != "system":
            if aid not in agents:
                agents[aid] = {"id": aid, "reputation": 10.0}

        if e.get("event_type") == "reputation_modified":
            target = e["payload"].get("target", aid)
            delta = e["payload"].get("delta", 0.0)
            if target not in agents:
                agents[target] = {"id": target, "reputation": 10.0}
            agents[target]["reputation"] = round(
                agents[target]["reputation"] + delta, 2
            )

        if e.get("event_type") == "graph_updated":
            a = e["payload"].get("agent_a", "")
            b = e["payload"].get("agent_b", "")
            delta = e["payload"].get("delta", 0.1)
            if a and b:
                # Гарантируем наличие обоих агентов в nodes,
                # даже если они не эмитировали событий напрямую (например, arbiter)
                if a not in agents:
                    agents[a] = {"id": a, "reputation": 10.0}
                if b not in agents:
                    agents[b] = {"id": b, "reputation": 10.0}
                key = tuple(sorted([a, b]))
                edges[key] = round(edges.get(key, 0.0) + delta, 2)

    nodes = list(agents.values())
    edge_list = [
        {"source": k[0], "target": k[1], "strength": v}
        for k, v in edges.items()
    ]
    return {"nodes": nodes, "edges": edge_list}


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
    delay = max(0.05, 0.3 / speed)
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
                await asyncio.sleep(delay)


@app.get("/api/runs")
async def list_runs() -> list[dict]:
    """Вернуть список доступных прогонов.

    Returns:
        Список словарей с метаданными прогонов.
    """
    runs = []
    for p in sorted(RESULTS_DIR.glob("*_events.jsonl")):
        meta = _parse_run_name(p.name)
        meta["name"] = p.stem.replace("_events", "")
        meta["filename"] = p.name
        meta["size_kb"] = round(p.stat().st_size / 1024, 1)
        runs.append(meta)
    return runs


@app.get("/api/run/{name}")
async def get_run(name: str) -> dict:
    """Вернуть все события прогона.

    Args:
        name: Имя прогона (без суффикса _events.jsonl).

    Returns:
        Словарь с событиями и состоянием графа.
    """
    from fastapi import HTTPException

    _validate_run_name(name)
    path = RESULTS_DIR / f"{name}_events.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    events = []
    async with aiofiles.open(path, encoding="utf-8") as f:
        async for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return {
        "name": name,
        "events": events,
        "graph": _build_graph_state(events),
        "meta": _parse_run_name(f"{name}_events.jsonl"),
    }


@app.websocket("/ws/playback/{name}")
async def ws_playback(websocket: WebSocket, name: str, speed: float = 1.0) -> None:
    """WebSocket для воспроизведения записанного прогона.

    Args:
        websocket: WebSocket-соединение.
        name: Имя прогона.
        speed: Скорость воспроизведения.
    """
    await websocket.accept()
    try:
        _validate_run_name(name)
    except Exception:
        await websocket.send_json({"type": "error", "message": "Invalid run name"})
        await websocket.close()
        return
    path = RESULTS_DIR / f"{name}_events.jsonl"
    if not path.exists():
        await websocket.send_json({"type": "error", "message": "Run not found"})
        await websocket.close()
        return

    events_so_far: list[dict] = []
    current_round = -1
    meta = _parse_run_name(f"{name}_events.jsonl")
    await websocket.send_json({"type": "meta", **meta})

    try:
        async for event in _stream_events_from_file(path, speed=speed):
            events_so_far.append(event)
            await websocket.send_json({"type": "event", "data": event})

            if event.get("round", current_round) != current_round:
                current_round = event.get("round", current_round)
                graph = _build_graph_state(events_so_far)
                await websocket.send_json({"type": "graph_state", **graph})

        await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """WebSocket для мониторинга текущего прогона.

    Следит за самым свежим *_events.jsonl файлом и пушит новые строки.
    """
    await websocket.accept()

    events_so_far: list[dict] = []
    current_round = -1
    watched_path: Path | None = None
    file_pos = 0

    try:
        while True:
            candidates = sorted(
                RESULTS_DIR.glob("*_events.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                await asyncio.sleep(1.0)
                continue

            latest = candidates[0]
            if latest != watched_path:
                watched_path = latest
                file_pos = 0
                events_so_far = []
                current_round = -1
                meta = _parse_run_name(latest.name)
                await websocket.send_json({"type": "meta", **meta})

            # Открываем в бинарном режиме для точного отслеживания байтовой позиции
            async with aiofiles.open(watched_path, "rb") as f:
                await f.seek(file_pos)
                raw = await f.read()
                file_pos += len(raw)
            new_lines = raw.decode("utf-8", errors="replace")

            for line in new_lines.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events_so_far.append(event)
                await websocket.send_json({"type": "event", "data": event})

                if event.get("round", current_round) != current_round:
                    current_round = event.get("round", current_round)
                    graph = _build_graph_state(events_so_far)
                    await websocket.send_json({"type": "graph_state", **graph})

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
