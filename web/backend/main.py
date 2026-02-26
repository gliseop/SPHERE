"""FastAPI-сервер для веб-интерфейса симуляций MAGISTRY."""

from __future__ import annotations

import asyncio
import json
import os as _os
import re
import secrets
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
AGENT_TYPES_DIR = (Path(__file__).parent.parent.parent / "data" / "agent_types").resolve()
AGENT_TYPES_DIR.mkdir(parents=True, exist_ok=True)
PERSONALITIES_DIR = (Path(__file__).parent.parent.parent / "data" / "personalities").resolve()
PERSONALITIES_DIR.mkdir(parents=True, exist_ok=True)
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
        return value

    if isinstance(value, float):
        if not value.is_integer():
            raise HTTPException(status_code=400, detail="Invalid seed")
        seed = int(value)
        if seed < 0:
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
        return seed

    raise HTTPException(status_code=400, detail="Invalid seed")


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

    try:
        if run_name:
            _validate_run_name(run_name)

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
                target_run = str(running[-1].get("run_name") or "")

            if not target_run:
                await asyncio.sleep(0.5)
                continue

            target_path = RESULTS_DIR / f"{target_run}_events.jsonl"
            if not target_path.exists():
                await asyncio.sleep(0.5)
                continue

            if target_run != watched_run or target_path != watched_path:
                watched_run = target_run
                watched_path = target_path
                file_pos = 0
                builder = GraphStateBuilder()
                meta = _parse_run_name(target_path.name)
                names = _load_names(target_run)
                await websocket.send_json(
                    {"type": "meta", **meta, "names": names, "run_name": target_run}
                )
                await websocket.send_json({"type": "graph_state", **builder.state()})

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
    from web.backend.runner import launch_simulation

    _validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario = json.loads(path.read_text(encoding="utf-8"))
    try:
        governance = scenario.get("governance", "G1")
        seed = _resolve_seed(scenario.get("seed"))
        runner_type = scenario.get("runner", "mock")
        rounds = scenario.get("rounds", 10)
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
    seed = _resolve_seed(data.get("seed"))
    runner_type = data.get("runner", "mock")
    rounds = data.get("rounds", 10)
    try:
        result = launch_simulation(
            scenario=scenario,
            governance=governance,
            seed=seed,
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
