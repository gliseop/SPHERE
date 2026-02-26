"""FastAPI-сервер для веб-интерфейса симуляций MAGISTRY."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import os as _os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import aiofiles
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import (
    create_access_token,
    require_admin,
    require_viewer,
    verify_password,
    verify_ws_token,
)
from .database import User, get_user_by_username, init_db
from .graph_state import GraphStateBuilder

RESULTS_DIR = (Path(__file__).parent.parent.parent / "results").resolve()
SCENARIOS_DIR = (Path(__file__).parent.parent.parent / "scenarios").resolve()
SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
AGENT_TYPES_DIR = (Path(__file__).parent.parent.parent / "data" / "agent_types").resolve()
AGENT_TYPES_DIR.mkdir(parents=True, exist_ok=True)
PERSONALITIES_DIR = (Path(__file__).parent.parent.parent / "data" / "personalities").resolve()
PERSONALITIES_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_DIR = (RESULTS_DIR / "artifacts").resolve()
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

_MAX_SEED = 2_147_483_647
_MAX_ROUNDS = 1_000

try:
    _MAX_BODY_BYTES = max(
        1,
        int(_os.environ.get("MAGISTRY_MAX_BODY_BYTES", str(2 * 1024 * 1024))),
    )
except ValueError:
    _MAX_BODY_BYTES = 2 * 1024 * 1024

try:
    _WS_MAX_STR_CHARS = max(200, int(_os.environ.get("MAGISTRY_WS_MAX_STR_CHARS", "2500")))
except ValueError:
    _WS_MAX_STR_CHARS = 2500

try:
    _WS_PING_INTERVAL_S = max(2.0, float(_os.environ.get("MAGISTRY_WS_PING_INTERVAL_S", "15")))
except ValueError:
    _WS_PING_INTERVAL_S = 15.0

try:
    _LIVE_HISTORY_EVENTS = max(0, int(_os.environ.get("MAGISTRY_LIVE_HISTORY_EVENTS", "60")))
except ValueError:
    _LIVE_HISTORY_EVENTS = 60

try:
    _LIVE_GRAPH_THROTTLE_S = max(0.05, float(_os.environ.get("MAGISTRY_LIVE_GRAPH_THROTTLE_S", "0.25")))
except ValueError:
    _LIVE_GRAPH_THROTTLE_S = 0.25


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

_SCENARIO_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _validate_scenario_id(scenario_id: str) -> None:
    """Проверить идентификатор сценария (UUID или slug).

    Args:
        scenario_id: Строка-идентификатор.

    Raises:
        HTTPException 400: Если содержит недопустимые символы.
        HTTPException 400: Если итоговый путь выходит за пределы SCENARIOS_DIR.
    """
    from fastapi import HTTPException

    if not _SCENARIO_ID_RE.fullmatch(scenario_id):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")
    resolved = (SCENARIOS_DIR / f"{scenario_id}.json").resolve()
    if not str(resolved).startswith(str(SCENARIOS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid scenario ID")


def _validate_library_id(item_id: str, base_dir: Path, *, kind: str) -> None:
    """Проверить ID элемента (agent-type/personality) на безопасный путь."""
    from fastapi import HTTPException

    if not _SCENARIO_ID_RE.fullmatch(item_id):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} ID")
    resolved = (base_dir / f"{item_id}.json").resolve()
    if not str(resolved).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} ID")


def _resolve_seed(value: object | None) -> int:
    """Преобразовать seed из запроса/сценария в int.

    Если seed не задан (None) — генерируется случайный seed.

    Args:
        value: seed (int/str/None).

    Returns:
        seed как неотрицательный int.

    Raises:
        HTTPException 400: Если seed имеет неверный формат.
    """
    from fastapi import HTTPException

    if value is None:
        return secrets.randbelow(1_000_000_000)

    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Invalid seed")

    if isinstance(value, int):
        if value < 0:
            raise HTTPException(status_code=400, detail="Invalid seed")
        if value > _MAX_SEED:
            raise HTTPException(status_code=400, detail="Invalid seed")
        return value

    if isinstance(value, float):
        if not value.is_integer():
            raise HTTPException(status_code=400, detail="Invalid seed")
        seed = int(value)
        if seed < 0:
            raise HTTPException(status_code=400, detail="Invalid seed")
        if seed > _MAX_SEED:
            raise HTTPException(status_code=400, detail="Invalid seed")
        return seed

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return secrets.randbelow(1_000_000_000)
        try:
            seed = int(s)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid seed") from exc
        if seed < 0:
            raise HTTPException(status_code=400, detail="Invalid seed")
        if seed > _MAX_SEED:
            raise HTTPException(status_code=400, detail="Invalid seed")
        return seed

    raise HTTPException(status_code=400, detail="Invalid seed")


def _resolve_rounds(value: object | None, *, default: int = 10) -> int:
    """Преобразовать rounds в int и ограничить разумным максимумом."""
    from fastapi import HTTPException

    if value is None:
        rounds = default
    elif isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Invalid rounds")
    else:
        try:
            rounds = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid rounds") from exc

    if rounds < 1 or rounds > _MAX_ROUNDS:
        raise HTTPException(status_code=400, detail="Invalid rounds")
    return rounds


class ScenarioAgentPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=128)
    role: Literal["official", "business", "auditor"]
    initial_reputation: float = Field(ge=0.0, le=100.0)


class ScenarioPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)
    scenario: str = Field(default="S1", pattern=r"^S\d+$")
    governance: str = Field(default="G1", pattern=r"^G\d+$")
    rounds: int = Field(default=10, ge=1, le=_MAX_ROUNDS)
    seed: int | None = Field(default=None, ge=0, le=_MAX_SEED)
    runner: str | None = Field(default=None, max_length=64)
    agents: list[ScenarioAgentPayload] = Field(default_factory=list, max_length=200)
    sim_config: dict[str, Any] | None = None


class SecondaryAgentsPayload(BaseModel):
    """Запрос на генерацию/обновление вторичных агентов через LLM."""

    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(default="S1", pattern=r"^S\d+$")
    governance: str = Field(default="G1", pattern=r"^G\d+$")
    seed: int | None = Field(default=None, ge=0, le=_MAX_SEED)
    rounds: int | None = Field(default=None, ge=1, le=_MAX_ROUNDS)
    prompt: str = Field(min_length=1, max_length=10_000)
    family_count: int = Field(default=0, ge=0, le=20)
    society_count: int = Field(default=0, ge=0, le=20)
    replace_existing: bool = True
    sim_config: dict[str, Any] | None = None


class _BodySizeLimitMiddleware:
    """Ограничить размер тела HTTP-запроса, чтобы избежать DoS через большие JSON."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        for k, v in scope.get("headers", []):
            if k.lower() != b"content-length":
                continue
            try:
                if int(v) > self.max_bytes:
                    res = JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
                    await res(scope, receive, send)
                    return
            except ValueError:
                break

        received = 0
        class _RequestBodyTooLarge(Exception):
            pass

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                received += len(body)
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            res = JSONResponse({"detail": "Request body too large"}, status_code=413)
            await res(scope, receive, send)


app = FastAPI(title="MAGISTRY Graph UI")

app.add_middleware(_BodySizeLimitMiddleware, max_bytes=_MAX_BODY_BYTES)

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
        Словарь с полями scenario, governance, seed (если есть), variant (если есть).
    """
    m = re.match(
        r"(?P<scenario>S\d+)_(?P<governance>G\d+)(?:_seed(?P<seed>\d+))?(?:_(?P<variant>[A-Za-z0-9_\-]+))?_events\.jsonl",
        filename,
    )
    if m:
        return {
            "scenario": m.group("scenario"),
            "governance": m.group("governance"),
            "seed": int(m.group("seed")) if m.group("seed") else None,
            "variant": m.group("variant") or None,
        }
    # Нестандартное имя — извлекаем всё до _events как название
    m2 = re.match(r"(?P<name>.+?)_events\.jsonl$", filename)
    if m2:
        return {"scenario": m2.group("name"), "governance": "", "seed": None, "variant": None}
    return {"scenario": "?", "governance": "?", "seed": None, "variant": None}


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


def _seed_builder_from_names(builder: GraphStateBuilder, names: dict[str, str]) -> None:
    """Pre-create graph nodes so UI can render agents before events arrive."""
    for agent_id in names.keys():
        try:
            builder._ensure_agent(str(agent_id))
        except Exception:
            continue


def _truncate_json_value(value: Any, *, max_chars: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars] + "…", True

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


def _event_for_ws(event: dict[str, Any]) -> dict[str, Any]:
    """Shrink large text fields for WS transport (helps reverse-proxies)."""
    if _WS_MAX_STR_CHARS <= 0:
        return event
    trimmed, truncated = _truncate_json_value(event, max_chars=_WS_MAX_STR_CHARS)
    if isinstance(trimmed, dict):
        if truncated:
            trimmed["_truncated"] = True
        return trimmed
    return event


async def _bootstrap_graph_from_file(
    path: Path,
    builder: GraphStateBuilder,
    *,
    tail_events: int,
) -> tuple[int, bytes, list[dict]]:
    """Ingest existing JSONL into graph state and return last events for UI."""
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
    paths = sorted(
        RESULTS_DIR.glob("*_events.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in paths:
        meta = _parse_run_name(p.name)
        meta["name"] = p.stem.replace("_events", "")
        meta["filename"] = p.name
        meta["size_kb"] = round(p.stat().st_size / 1024, 1)
        runs.append(meta)
    return runs


@app.get("/api/run/{name}")
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
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Словарь с событиями и состоянием графа.
    """
    from fastapi import HTTPException

    if offset < 0:
        raise HTTPException(status_code=400, detail="Invalid offset")
    if limit < 0:
        raise HTTPException(status_code=400, detail="Invalid limit")
    if limit > 10_000:
        raise HTTPException(status_code=400, detail="Limit too large")

    _validate_run_name(name)
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
    _seed_builder_from_names(builder, names)
    await websocket.send_json({"type": "graph_state", **builder.state()})

    try:
        async for event in _stream_events_from_file(path, speed=speed):
            builder.ingest(event)
            await websocket.send_json({"type": "event", "data": _event_for_ws(event)})
            await websocket.send_json({"type": "graph_state", **builder.state()})

        await websocket.send_json({"type": "done"})
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/live")
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

    try:
        if run_name:
            try:
                _validate_run_name(run_name)
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
                        _validate_run_name(candidate)
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

            target_path = RESULTS_DIR / f"{target_run}_events.jsonl"

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
                meta = _parse_run_name(f"{target_run}_events.jsonl")
                await websocket.send_json(
                    {"type": "meta", **meta, "names": names, "run_name": target_run}
                )
                await websocket.send_json({"type": "graph_state", **builder.state()})
                last_send = time.monotonic()
                last_graph_send = last_send

            if watched_path and not watched_path.exists():
                now = time.monotonic()
                if now - last_send >= _WS_PING_INTERVAL_S:
                    await websocket.send_json({"type": "ping", "t": time.time()})
                    last_send = now
                await asyncio.sleep(0.5)
                continue

            if watched_path and not bootstrapped:
                try:
                    file_pos, leftover, tail_events = await _bootstrap_graph_from_file(
                        watched_path,
                        builder,
                        tail_events=_LIVE_HISTORY_EVENTS,
                    )
                except OSError:
                    await asyncio.sleep(0.5)
                    continue

                buf = bytearray(leftover)
                await websocket.send_json({"type": "graph_state", **builder.state()})
                last_send = time.monotonic()
                last_graph_send = last_send
                for ev in tail_events:
                    await websocket.send_json(
                        {"type": "event", "data": _event_for_ws(ev)}
                    )
                    last_send = time.monotonic()
                bootstrapped = True

            try:
                # Держим дескриптор открытым и читаем "хвост" до смены watched_run.
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
                                await websocket.send_json(
                                    {"type": "event", "data": _event_for_ws(event)}
                                )
                                last_send = time.monotonic()
                                graph_dirty = True

                        now = time.monotonic()
                        if graph_dirty and (now - last_graph_send) >= _LIVE_GRAPH_THROTTLE_S:
                            await websocket.send_json(
                                {"type": "graph_state", **builder.state()}
                            )
                            last_graph_send = now
                            last_send = now
                            graph_dirty = False

                        if now - last_send >= _WS_PING_INTERVAL_S:
                            await websocket.send_json({"type": "ping", "t": time.time()})
                            last_send = now

                        await asyncio.sleep(0.5)

                        # Проверяем, нужно ли переключиться на другой прогон.
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
                                    _validate_run_name(candidate)
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


@app.get("/api/templates/scenarios")
async def list_template_scenarios(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список встроенных шаблонов сценариев (S*)."""
    from magistry_sim.scenarios import SCENARIOS

    items = []
    for sid, cfg in sorted(SCENARIOS.items(), key=lambda x: x[0].value):
        items.append(
            {
                "id": sid.value,
                "title": cfg.title,
                "description": cfg.description,
                "max_rounds": cfg.max_rounds,
                "seed": cfg.seed,
                "corruption_level": getattr(cfg, "corruption_level", 0.0),
            }
        )
    return items


@app.get("/api/templates/scenarios/{scenario_id}")
async def get_template_scenario(
    scenario_id: str,
    governance: str | None = None,
    _user: User = Depends(require_viewer),
) -> dict:
    """Вернуть полный конфиг встроенного сценария.

    Query params:
        governance: Если указан, добавить governance-агентов (auditor/jury) и
            установить режим управления в конфиге.
    """
    from fastapi import HTTPException
    from magistry_sim.enums import GovernanceMode, ScenarioId
    from magistry_sim.scenarios import add_governance_agents, get_scenario

    try:
        sid = ScenarioId(scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown scenario") from exc

    cfg = get_scenario(sid)

    if governance:
        try:
            gov = GovernanceMode(governance)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unknown governance") from exc
        cfg = add_governance_agents(cfg, gov)

    return cfg.model_dump(mode="json")


@app.get("/api/templates/governance")
async def list_governance_modes(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список режимов управления (G0-G3) с пояснениями."""
    from magistry_sim.enums import GovernanceMode

    labels = {
        "G0": "Без контроля",
        "G1": "Аудитор (рекомендательный)",
        "G2": "Аудитор (санкции по репутации)",
        "G3": "Полный контроль (трибунал)",
    }
    descriptions = {
        "G0": "Нет надзора со стороны аудитора или трибунала.",
        "G1": "Аудитор может наблюдать и давать рекомендации.",
        "G2": "Аудитор может рекомендовать заморозку репутации участников.",
        "G3": "Аудитор может инициировать трибунал; решение принимает коллегия присяжных.",
    }

    return [
        {
            "id": mode.value,
            "label": f"{mode.value} — {labels.get(mode.value, mode.value)}",
            "description": descriptions.get(mode.value, ""),
        }
        for mode in GovernanceMode
    ]


@app.post("/api/ai/secondary-agents")
async def generate_secondary_agents(
    payload: SecondaryAgentsPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Сгенерировать вторичных агентов (fam_*/soc_*) и вернуть обновлённый ScenarioConfig."""
    from fastapi import HTTPException

    if not _os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

    total = int(payload.family_count) + int(payload.society_count)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Nothing to generate")

    from magistry_sim.config import AgentProfile, Connection, ScenarioConfig
    from magistry_sim.enums import GovernanceMode, ScenarioId
    from magistry_sim.llm import create_provider
    from magistry_sim.personality import NeutralizationTechnique
    from magistry_sim.scenarios import add_governance_agents, get_scenario

    try:
        if payload.sim_config:
            base_cfg = ScenarioConfig.model_validate(payload.sim_config)
        else:
            sid = ScenarioId(payload.scenario)
            gov = GovernanceMode(payload.governance)
            base_cfg = add_governance_agents(get_scenario(sid), gov)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sim_config: {exc}") from exc

    updates: dict[str, Any] = {}
    if payload.rounds is not None:
        updates["max_rounds"] = int(payload.rounds)
    if payload.seed is not None:
        updates["seed"] = int(payload.seed)
    if updates:
        base_cfg = base_cfg.model_copy(update=updates)

    def _is_secondary(aid: str) -> bool:
        return aid.startswith(("fam_", "soc_"))

    if payload.replace_existing:
        kept: list[AgentProfile] = []
        for agent in base_cfg.agents:
            if _is_secondary(agent.id):
                continue
            if agent.connections:
                kept_conns = [
                    c for c in agent.connections if not _is_secondary(c.target_id)
                ]
                agent = agent.model_copy(update={"connections": kept_conns})
            kept.append(agent)
        base_cfg = base_cfg.model_copy(update={"agents": kept})

    existing_ids = {a.id for a in base_cfg.agents}

    def _next_id(prefix: str) -> str:
        n = 1
        while True:
            candidate = f"{prefix}_{n}"
            if candidate not in existing_ids:
                existing_ids.add(candidate)
                return candidate
            n += 1

    requested_family = [_next_id("fam") for _ in range(int(payload.family_count))]
    requested_society = [_next_id("soc") for _ in range(int(payload.society_count))]
    requested_ids = requested_family + requested_society

    primary_agents = [
        {"id": a.id, "name": a.name, "position": a.position}
        for a in base_cfg.agents
        if a.id not in requested_ids
    ]
    primary_ids = [a["id"] for a in primary_agents]

    techniques = [t.value for t in NeutralizationTechnique]

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "narrative_context": {"type": "string", "maxLength": 2000},
            "agents": {
                "type": "array",
                "minItems": total,
                "maxItems": total,
                "items": {"$ref": "#/$defs/agent"},
            },
        },
        "required": ["narrative_context", "agents"],
        "$defs": {
            "capability": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "maxLength": 64},
                    "case_types": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 64},
                    },
                },
                "required": ["action", "case_types"],
            },
            "connection": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_id": {"type": "string", "maxLength": 64},
                    "name": {"type": "string", "maxLength": 128},
                    "relation": {"type": "string", "maxLength": 128},
                    "strength": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 5.0,
                    },
                },
                "required": ["target_id", "name", "relation", "strength"],
            },
            "hexaco": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "honesty_humility": {"type": "integer", "minimum": 0, "maximum": 100},
                    "emotionality": {"type": "integer", "minimum": 0, "maximum": 100},
                    "extraversion": {"type": "integer", "minimum": 0, "maximum": 100},
                    "agreeableness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "conscientiousness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "openness": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": [
                    "honesty_humility",
                    "emotionality",
                    "extraversion",
                    "agreeableness",
                    "conscientiousness",
                    "openness",
                ],
            },
            "dark_triad": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "narcissism": {"type": "integer", "minimum": 0, "maximum": 100},
                    "machiavellianism": {"type": "integer", "minimum": 0, "maximum": 100},
                    "psychopathy": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["narcissism", "machiavellianism", "psychopathy"],
            },
            "personality": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "biography": {"type": "string", "maxLength": 800},
                    "hexaco": {"$ref": "#/$defs/hexaco"},
                    "dark_triad": {"$ref": "#/$defs/dark_triad"},
                    "neutralization_techniques": {
                        "type": "array",
                        "items": {"type": "string", "enum": techniques},
                    },
                },
                "required": [
                    "biography",
                    "hexaco",
                    "dark_triad",
                    "neutralization_techniques",
                ],
            },
            "resources": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "budget_limit": {"type": "number"},
                    "staffing_slots": {"type": "integer"},
                    "contract_capacity": {"type": "integer"},
                },
                "required": ["budget_limit", "staffing_slots", "contract_capacity"],
            },
            "agent": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "maxLength": 64},
                    "name": {"type": "string", "maxLength": 128},
                    "position": {"type": "string", "maxLength": 256},
                    "capabilities": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/capability"},
                    },
                    "greed": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "fear": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "honesty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "competence": {
                        "anyOf": [
                            {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            {"type": "null"},
                        ]
                    },
                    "immune": {"type": "boolean"},
                    "connections": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/connection"},
                    },
                    "personality": {"$ref": "#/$defs/personality"},
                    "initial_resources": {"$ref": "#/$defs/resources"},
                },
                "required": [
                    "id",
                    "name",
                    "position",
                    "capabilities",
                    "greed",
                    "fear",
                    "honesty",
                    "competence",
                    "immune",
                    "connections",
                    "personality",
                    "initial_resources",
                ],
            },
        },
    }

    system = (
        "Вы — генератор вторичных агент-профилей для симуляции MAGISTRY. "
        "Верните ТОЛЬКО structured JSON по схеме."
    )
    user_prompt = (
        f"Контекст организации:\n{base_cfg.narrative_context}\n\n"
        f"Пожелания пользователя (среда/контекст):\n{payload.prompt}\n\n"
        "Основные агенты (id, имя, должность):\n"
        + "\n".join(
            f"- {a['id']}: {a['name']} — {a['position']}" for a in primary_agents
        )
        + "\n\n"
        f"Нужно добавить вторичных агентов. Новые id ДОЛЖНЫ быть строго такими:\n{', '.join(requested_ids)}\n\n"
        "Правила:\n"
        f"- connection.target_id только из: {', '.join(primary_ids)}\n"
        "- capabilities оставьте пустым массивом []\n"
        "- initial_resources заполните нулями\n"
        "- у каждого агента минимум 1 connection к основному агенту\n"
        "- biography 2–5 предложений, отражает мотивацию/давление среды\n"
    )

    provider = create_provider(mock=False, cache_path=".llm_cache.db")
    try:
        resp = provider.generate_structured(
            system=system,
            user=user_prompt,
            schema=schema,
            temperature=0.25,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    data = resp.data if isinstance(resp.data, dict) else {}
    narrative_context = str(data.get("narrative_context", "") or "").strip()
    if not narrative_context:
        narrative_context = base_cfg.narrative_context

    raw_agents = data.get("agents", [])
    if not isinstance(raw_agents, list):
        raise HTTPException(status_code=502, detail="LLM returned invalid agents")

    returned_ids = []
    validated: list[AgentProfile] = []
    for item in raw_agents:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id", "") or "")
        returned_ids.append(aid)
        if aid not in requested_ids:
            continue
        if aid in {a.id for a in base_cfg.agents}:
            continue
        # Ensure no connections to unknown ids (keeps UI deterministic).
        conns = item.get("connections", [])
        if isinstance(conns, list):
            item["connections"] = [
                c
                for c in conns
                if isinstance(c, dict)
                and str(c.get("target_id", "") or "") in primary_ids
            ]
        try:
            validated.append(AgentProfile.model_validate(item))
        except Exception:
            continue

    if set(requested_ids) != {a.id for a in validated}:
        missing = sorted(set(requested_ids) - {a.id for a in validated})
        raise HTTPException(
            status_code=502,
            detail=f"LLM did not return all requested agents: {', '.join(missing)}",
        )

    by_id: dict[str, AgentProfile] = {a.id: a for a in base_cfg.agents}

    def _add_backlink(target_id: str, source: AgentProfile, conn: Connection) -> None:
        target = by_id.get(target_id)
        if target is None:
            return
        if any(c.target_id == source.id for c in target.connections):
            return
        backlink = Connection(
            target_id=source.id,
            name=source.name,
            relation=conn.relation,
            strength=conn.strength,
        )
        by_id[target_id] = target.model_copy(
            update={"connections": list(target.connections) + [backlink]}
        )

    for agent in validated:
        by_id[agent.id] = agent
        for conn in agent.connections:
            _add_backlink(conn.target_id, agent, conn)

    final_agents: list[AgentProfile] = []
    seen: set[str] = set()
    for a in base_cfg.agents:
        updated = by_id.get(a.id)
        if updated and updated.id not in seen:
            final_agents.append(updated)
            seen.add(updated.id)
    for a in validated:
        if a.id not in seen:
            final_agents.append(by_id[a.id])
            seen.add(a.id)

    base_cfg = base_cfg.model_copy(
        update={"agents": final_agents, "narrative_context": narrative_context}
    )
    return base_cfg.model_dump(mode="json")


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
async def create_scenario(payload: ScenarioPayload, _user: User = Depends(require_admin)) -> dict:
    """Создать новый сценарий.

    Args:
        payload: Данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Сохранённый сценарий с назначенным id.
    """
    scenario_id = str(uuid.uuid4())
    data = payload.model_dump(mode="json")
    data["id"] = scenario_id
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.put("/api/scenarios/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    payload: ScenarioPayload,
    _user: User = Depends(require_admin),
) -> dict:
    """Обновить сценарий.

    Args:
        scenario_id: UUID строка.
        payload: Новые данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Обновлённый сценарий.
    """
    from fastapi import HTTPException

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    data = payload.model_dump(mode="json")
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


@app.get("/api/agent-types")
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


@app.post("/api/agent-types", status_code=201)
async def create_agent_type(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Создать новый тип агента."""
    item_id = str(uuid.uuid4())
    data["id"] = item_id
    path = AGENT_TYPES_DIR / f"{item_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.put("/api/agent-types/{type_id}")
async def update_agent_type(type_id: str, data: dict, _user: User = Depends(require_admin)) -> dict:
    """Обновить тип агента."""
    from fastapi import HTTPException

    _validate_library_id(type_id, AGENT_TYPES_DIR, kind="agent-type")
    path = AGENT_TYPES_DIR / f"{type_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Agent type not found")
    data["id"] = type_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.delete("/api/agent-types/{type_id}", status_code=204)
async def delete_agent_type(type_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить тип агента."""
    from fastapi import HTTPException

    _validate_library_id(type_id, AGENT_TYPES_DIR, kind="agent-type")
    path = AGENT_TYPES_DIR / f"{type_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Agent type not found")
    path.unlink()


@app.get("/api/personalities")
async def list_personalities(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список личностей (шаблоны)."""
    result = []
    for p in sorted(PERSONALITIES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return result


@app.post("/api/personalities", status_code=201)
async def create_personality(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Создать новую личность."""
    item_id = str(uuid.uuid4())
    data["id"] = item_id
    path = PERSONALITIES_DIR / f"{item_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.put("/api/personalities/{personality_id}")
async def update_personality(personality_id: str, data: dict, _user: User = Depends(require_admin)) -> dict:
    """Обновить личность."""
    from fastapi import HTTPException

    _validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
    path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")
    data["id"] = personality_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.delete("/api/personalities/{personality_id}", status_code=204)
async def delete_personality(personality_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить личность."""
    from fastapi import HTTPException

    _validate_library_id(personality_id, PERSONALITIES_DIR, kind="personality")
    path = PERSONALITIES_DIR / f"{personality_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Personality not found")
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
    from web.backend.runner import TooManyRunsError, launch_simulation

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario = json.loads(path.read_text(encoding="utf-8"))
    try:
        governance = scenario.get("governance", "G1")
        seed = _resolve_seed(scenario.get("seed"))
        runner_type = "cognitive"
        rounds = _resolve_rounds(scenario.get("rounds"), default=10)
        sim_config = scenario.get("sim_config")
        if isinstance(sim_config, dict):
            from web.backend.runner import launch_simulation_from_config

            suffix = scenario_id.replace("-", "")[:8]
            result = launch_simulation_from_config(
                scenario_config=sim_config,
                governance=governance,
                seed=seed,
                runner_type=runner_type,
                rounds=rounds,
                variant=f"scn{suffix}",
            )
        else:
            result = launch_simulation(
                scenario=scenario.get("scenario", "S1"),
                governance=governance,
                seed=seed,
                runner_type=runner_type,
                rounds=rounds,
            )
        return {"status": "accepted", **result}
    except TooManyRunsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
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
    from web.backend.runner import TooManyRunsError, launch_simulation

    scenario = data.get("scenario", "S1")
    governance = data.get("governance", "G1")
    seed = _resolve_seed(data.get("seed"))
    runner_type = "cognitive"
    rounds = _resolve_rounds(data.get("rounds"), default=10)
    try:
        result = launch_simulation(
            scenario=scenario,
            governance=governance,
            seed=seed,
            runner_type=runner_type,
            rounds=rounds,
        )
        return {"status": "accepted", **result}
    except TooManyRunsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
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


@app.post("/api/runs/{run_name}/stop")
async def stop_run(run_name: str, _user: User = Depends(require_admin)) -> dict:
    """Остановить запущенный прогон.

    Args:
        run_name: Имя прогона.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь со статусом остановки.
    """
    from fastapi import HTTPException
    from web.backend.runner import stop_simulation

    _validate_run_name(run_name)
    result = stop_simulation(run_name)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found or not running")
    return result


@app.delete("/api/runs/{run_name}", status_code=204)
async def delete_run(run_name: str, _user: User = Depends(require_admin)) -> None:
    """Удалить сохранённый прогон и сопутствующие файлы.

    Args:
        run_name: Имя прогона (без суффикса _events.jsonl).
        _user: Аутентифицированный пользователь с ролью admin.
    """
    from fastapi import HTTPException
    from web.backend.runner import list_active

    _validate_run_name(run_name)

    active = list_active()
    if any(r.get("run_name") == run_name and r.get("status") == "running" for r in active):
        raise HTTPException(status_code=409, detail="Run is running")

    for suffix in (
        "_events.jsonl",
        "_names.json",
        "_summary.json",
        "_stdout.log",
        "_stderr.log",
    ):
        path = RESULTS_DIR / f"{run_name}{suffix}"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
