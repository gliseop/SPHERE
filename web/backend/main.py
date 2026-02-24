"""FastAPI-сервер для веб-интерфейса симуляций MAGISTRY."""

from __future__ import annotations

import asyncio
import json
import os as _os
import re
import uuid
from pathlib import Path
from typing import AsyncIterator

import aiofiles
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles

from .auth import (
    create_access_token,
    require_admin,
    require_viewer,
    verify_password,
    verify_ws_token,
)
from .database import User, get_user_by_username, init_db
from .graph_state import GraphStateBuilder, build_graph_state

RESULTS_DIR = (Path(__file__).parent.parent.parent / "results").resolve()
SCENARIOS_DIR = (Path(__file__).parent.parent.parent / "scenarios").resolve()
SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_DIR = (RESULTS_DIR / "artifacts").resolve()
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


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

_SCENARIO_ID_RE = re.compile(r"^[0-9a-f\-]{36}$")


def _validate_scenario_id(scenario_id: str) -> None:
    """Проверить UUID сценария.

    Args:
        scenario_id: UUID строка.

    Raises:
        HTTPException 400: Если не UUID формат.
        HTTPException 400: Если итоговый путь выходит за пределы SCENARIOS_DIR.
    """
    from fastapi import HTTPException

    if not _SCENARIO_ID_RE.fullmatch(scenario_id):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")
    resolved = (SCENARIOS_DIR / f"{scenario_id}.json").resolve()
    if not str(resolved).startswith(str(SCENARIOS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")


app = FastAPI(title="MAGISTRY Graph UI")

_ALLOWED_ORIGIN = _os.environ.get("ALLOWED_ORIGIN", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


@app.post("/api/auth/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    """Аутентификация пользователя.

    Args:
        form_data: username и password из form-encoded тела.

    Returns:
        Словарь с access_token и token_type.

    Raises:
        HTTPException 401: При неверных учётных данных.
    """
    from fastapi import HTTPException

    user = get_user_by_username(form_data.username)
    if user is None or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return {
        "access_token": create_access_token(user.username, user.role),
        "token_type": "bearer",
    }


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


def _load_names(run_name: str) -> dict[str, str]:
    """Загрузить имена агентов из сопроводительного файла.

    Args:
        run_name: Имя прогона без суффикса _events.jsonl.

    Returns:
        Словарь agent_id -> отображаемое имя. Пустой словарь если файл отсутствует.
    """
    path = RESULTS_DIR / f"{run_name}_names.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}


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
async def list_runs(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список доступных прогонов.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

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
async def get_run(name: str, _user: User = Depends(require_viewer)) -> dict:
    """Вернуть все события прогона.

    Args:
        name: Имя прогона (без суффикса _events.jsonl).
        _user: Аутентифицированный пользователь (любая роль).

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
        "graph": build_graph_state(events),
        "meta": _parse_run_name(f"{name}_events.jsonl"),
    }


@app.get("/api/artifacts/{doc_id}")
async def get_artifact(doc_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Получить документ-артефакт по ID.

    Args:
        doc_id: Идентификатор документа.
        _user: Аутентифицированный пользователь (любая роль).
    """
    from fastapi import HTTPException

    if not _DOC_ID_RE.fullmatch(doc_id):
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


@app.websocket("/ws/playback/{name}")
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

    meta = _parse_run_name(f"{name}_events.jsonl")
    names = _load_names(name)
    await websocket.send_json({"type": "meta", **meta, "names": names})
    builder = GraphStateBuilder()
    await websocket.send_json({"type": "graph_state", **builder.state()})

    try:
        async for event in _stream_events_from_file(path, speed=speed):
            await websocket.send_json({"type": "event", "data": event})
            builder.ingest(event)
            await websocket.send_json({"type": "graph_state", **builder.state()})

        await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket, token: str | None = None) -> None:
    """WebSocket для мониторинга текущего прогона.

    Следит за самым свежим *_events.jsonl файлом и пушит новые строки.

    Args:
        websocket: WebSocket-соединение.
        token: JWT-токен из query-параметра.
    """
    await websocket.accept()
    if verify_ws_token(token) is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=1008)
        return

    builder = GraphStateBuilder()
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
                builder = GraphStateBuilder()
                meta = _parse_run_name(latest.name)
                names = _load_names(latest.stem.replace("_events", ""))
                await websocket.send_json({"type": "meta", **meta, "names": names})
                await websocket.send_json(
                    {"type": "graph_state", **builder.state()}
                )

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
                await websocket.send_json({"type": "event", "data": event})
                builder.ingest(event)
                await websocket.send_json(
                    {"type": "graph_state", **builder.state()}
                )

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass


@app.get("/api/scenarios")
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


@app.get("/api/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Вернуть сценарий по ID.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Словарь с данными сценария.
    """
    from fastapi import HTTPException

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/scenarios", status_code=201)
async def create_scenario(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Создать новый сценарий.

    Args:
        data: Данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Сохранённый сценарий с назначенным id.
    """
    scenario_id = str(uuid.uuid4())
    data["id"] = scenario_id
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.put("/api/scenarios/{scenario_id}")
async def update_scenario(scenario_id: str, data: dict, _user: User = Depends(require_admin)) -> dict:
    """Обновить сценарий.

    Args:
        scenario_id: UUID строка.
        data: Новые данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Обновлённый сценарий.
    """
    from fastapi import HTTPException

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    data["id"] = scenario_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.delete("/api/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить сценарий.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь с ролью admin.
    """
    from fastapi import HTTPException

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    path.unlink()


@app.post("/api/scenarios/{scenario_id}/run", status_code=202)
async def run_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> dict:
    """Запустить прогон по сценарию.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь с run_name и статусом.
    """
    from fastapi import HTTPException
    from web.backend.runner import launch_simulation

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario = json.loads(path.read_text(encoding="utf-8"))
    try:
        result = launch_simulation(
            scenario=scenario.get("scenario", "S1"),
            governance=scenario.get("governance", "G1"),
            seed=scenario.get("seed") or 42,
            runner_type=scenario.get("runner", "mock"),
            rounds=scenario.get("rounds", 10),
        )
        return {"status": "accepted", **result}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/runs/launch", status_code=202)
async def launch_run(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Запустить встроенный прогон (S0-S2).

    Args:
        data: Словарь с ключами scenario, governance, seed, runner.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь с run_name и PID.
    """
    from fastapi import HTTPException
    from web.backend.runner import launch_simulation

    scenario = data.get("scenario", "S1")
    governance = data.get("governance", "G1")
    seed = data.get("seed", 42)
    runner_type = data.get("runner", "mock")
    rounds = data.get("rounds", 10)
    try:
        result = launch_simulation(
            scenario=scenario,
            governance=governance,
            seed=int(seed),
            runner_type=runner_type,
            rounds=int(rounds),
        )
        return {"status": "accepted", **result}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/runs/active")
async def active_runs(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список активных прогонов.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Список словарей с run_name, pid и статусом.
    """
    from web.backend.runner import list_active
    return list_active()


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
