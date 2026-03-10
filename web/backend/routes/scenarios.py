"""CRUD-маршруты для пользовательских сценариев /api/scenarios."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from magistry_lc.config import ScenarioConfig
from magistry_lc.scenario import save_scenario

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.models import ScenarioPayload
from web.backend.settings import SCENARIOS_DIR
from web.backend.validators import S_NUM_RE, next_s_number, validate_scenario_id

router = APIRouter(tags=["scenarios"])
_ALLOCATE_ID_ATTEMPTS = 256
_SCENARIO_SUFFIXES = (".json", ".yaml", ".yml")
_DEFAULT_SCENARIO_TEMPLATE = "S1"
_DEFAULT_GOVERNANCE_MODE = "G1"


def _read_mapping(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_mapping(path: Path, data: dict[str, Any]) -> None:
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_scenario_path(scenario_id: str) -> Path | None:
    candidates = [SCENARIOS_DIR / f"{scenario_id}{suffix}" for suffix in _SCENARIO_SUFFIXES]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    return max(existing, key=lambda path: path.stat().st_mtime)


def _list_scenario_paths() -> list[Path]:
    chosen: dict[str, Path] = {}
    for suffix in _SCENARIO_SUFFIXES:
        for path in SCENARIOS_DIR.glob(f"*{suffix}"):
            current = chosen.get(path.stem)
            if current is None:
                chosen[path.stem] = path
                continue
            try:
                if path.stat().st_mtime >= current.stat().st_mtime:
                    chosen[path.stem] = path
            except OSError:
                continue
    return sorted(chosen.values(), key=lambda path: path.stem)


def _infer_governance_mode(cfg: ScenarioConfig) -> str:
    audit = cfg.governance.audit
    if not audit.enabled:
        return "G0"
    if audit.reputation_penalty_delta is not None or audit.reputation_freeze_enabled:
        return "G2"
    return _DEFAULT_GOVERNANCE_MODE


def _to_ui_agent_id(agent_id: str, *, fallback: str) -> str:
    if ":" not in agent_id:
        return agent_id or fallback
    _, _, slug = agent_id.partition(":")
    return slug or fallback


def _scenario_config_to_payload(cfg: ScenarioConfig, *, scenario_id: str) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    for index, agent in enumerate(cfg.agents, start=1):
        agents.append(
            {
                "id": _to_ui_agent_id(agent.agent_id, fallback=f"agent_{index}"),
                "name": agent.name,
                "role": agent.initial_title if agent.internal else "external",
                "initial_reputation": 0.0,
            }
        )

    scenario_template = scenario_id if S_NUM_RE.fullmatch(scenario_id) else _DEFAULT_SCENARIO_TEMPLATE
    return {
        "id": scenario_id,
        "name": cfg.title or scenario_id,
        "description": cfg.description or "",
        "scenario": scenario_template,
        "governance": _infer_governance_mode(cfg),
        "rounds": int(cfg.ticks),
        "seed": int(cfg.seed),
        "runner": "cognitive",
        "agents": agents,
        "sim_config": cfg.model_dump(mode="json"),
    }


def _read_scenario_payload(path: Path) -> dict[str, Any] | None:
    raw = _read_mapping(path)
    if raw is None:
        return None

    try:
        cfg = ScenarioConfig.model_validate(raw)
    except Exception:
        payload = dict(raw)
        payload["id"] = str(payload.get("id") or path.stem)
        return payload

    return _scenario_config_to_payload(cfg, scenario_id=path.stem)


def _save_payload(path: Path, payload: ScenarioPayload, *, scenario_id: str) -> dict[str, Any]:
    if payload.sim_config is not None:
        try:
            cfg = ScenarioConfig.model_validate(payload.sim_config)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid ScenarioConfig: {exc}") from exc

        cfg.title = payload.name.strip() or scenario_id
        cfg.description = payload.description or ""
        cfg.ticks = int(payload.rounds)
        if payload.seed is not None:
            cfg.seed = int(payload.seed)

        save_scenario(cfg, path)
        return _scenario_config_to_payload(cfg, scenario_id=scenario_id)

    data = payload.model_dump(mode="json")
    data["id"] = scenario_id
    _write_mapping(path, data)
    return data


@router.get("/api/scenarios")
async def list_scenarios(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список сценариев.

    Args:
        _user: Аутентифицированный пользователь (любая роль).
    """
    result = []
    for path in _list_scenario_paths():
        data = _read_scenario_payload(path)
        if data is not None:
            result.append(data)
    return result


@router.get("/api/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str, _user: User = Depends(require_viewer)) -> dict:
    """Вернуть сценарий по ID.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Словарь с данными сценария.
    """
    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    data = _read_scenario_payload(path)
    if data is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return data


@router.post("/api/scenarios", status_code=201)
async def create_scenario(payload: ScenarioPayload, _user: User = Depends(require_admin)) -> dict:
    """Создать новый сценарий.

    Автоматически присваивает S-номер (S7, S8, ...) на основе
    существующих встроенных сценариев (S0-S6) и файлов в SCENARIOS_DIR.

    Args:
        payload: Данные сценария.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Сохранённый сценарий с назначенным id.
    """
    for _ in range(_ALLOCATE_ID_ATTEMPTS):
        scenario_id = next_s_number()
        suffix = ".yaml" if payload.sim_config is not None else ".json"
        path = SCENARIOS_DIR / f"{scenario_id}{suffix}"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write("")
            return _save_payload(path, payload, scenario_id=scenario_id)
        except FileExistsError:
            continue
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to write scenario: {exc}") from exc

    raise HTTPException(status_code=409, detail="Failed to allocate scenario ID")


@router.put("/api/scenarios/{scenario_id}")
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
    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return _save_payload(path, payload, scenario_id=scenario_id)


@router.delete("/api/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> None:
    """Удалить сценарий.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь с ролью admin.
    """
    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    path.unlink()
