"""Маршруты управления прогонами: запуск, активные, остановка, удаление."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.settings import RESULTS_DIR, SCENARIOS_DIR
from web.backend.validators import (
    validate_run_name,
    validate_scenario_id,
)

router = APIRouter(tags=["run-control"])


def _prepare_existing_personalities(
    config_agents: list,
    run_personalities_dir: Path,
    run_interviews_dir: Path,
) -> tuple[set[str], set[str]]:
    """Копировать файлы личностей/интервью для агентов с назначенным архетипом.

    Файлы копируются как {agent_id}.json, чтобы cognitive_runner нашёл их
    по agent_id (через personality_archetype = agent_id).

    Args:
        config_agents: Список агентов из ScenarioConfig (AgentProfile или dict).
        run_personalities_dir: Директория личностей для данного прогона.
        run_interviews_dir: Директория интервью для данного прогона.

    Returns:
        Кортеж (need_generation, copied): множества agent_id для генерации
        и скопированных агентов соответственно.
    """
    need_generation: set[str] = set()
    copied: set[str] = set()
    run_personalities_dir.mkdir(parents=True, exist_ok=True)
    run_interviews_dir.mkdir(parents=True, exist_ok=True)

    for agent in config_agents:
        agent_id = agent.id if hasattr(agent, "id") else agent.get("id", "")
        archetype = (
            agent.personality_archetype
            if hasattr(agent, "personality_archetype")
            else agent.get("personality_archetype")
        )
        if not archetype or not agent_id:
            need_generation.add(agent_id)
            continue

        pers_src = PERSONALITIES_DIR / f"{archetype}.json"
        itv_src = INTERVIEWS_DIR / f"{archetype}.json"
        if not pers_src.exists():
            need_generation.add(agent_id)
            continue

        pers_dst = run_personalities_dir / f"{agent_id}.json"
        itv_dst = run_interviews_dir / f"{agent_id}.json"
        shutil.copy2(pers_src, pers_dst)
        if itv_src.exists():
            shutil.copy2(itv_src, itv_dst)
        copied.add(agent_id)

    return need_generation, copied


@router.post("/api/scenarios/{scenario_id}/run", status_code=202)
async def run_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> dict:
    """Запустить прогон по сценарию.

    Зависит от magistry_sim (scenarios, persona_generator, config) и пока недоступен.
    """
    raise HTTPException(
        status_code=501,
        detail="Запуск прогонов через magistry_sim недоступен: движок удалён.",
    )


@router.post("/api/runs/launch", status_code=202)
async def launch_run(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Запустить встроенный прогон (S0-S2).

    Зависит от magistry_sim (scenarios, persona_generator, config) и пока недоступен.
    """
    raise HTTPException(
        status_code=501,
        detail="Запуск прогонов через magistry_sim недоступен: движок удалён.",
    )


@router.get("/api/runs/active")
async def active_runs(_user: User = Depends(require_viewer)) -> list[dict]:
    """Вернуть список активных прогонов.

    Args:
        _user: Аутентифицированный пользователь (любая роль).

    Returns:
        Список словарей с run_name, pid и статусом.
    """
    from web.backend.runner import list_active
    return list_active()


@router.post("/api/runs/{run_name}/stop")
async def stop_run(run_name: str, _user: User = Depends(require_admin)) -> dict:
    """Остановить запущенный прогон.

    Args:
        run_name: Имя прогона.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь со статусом остановки.
    """
    from web.backend.runner import stop_simulation

    validate_run_name(run_name)
    try:
        result = stop_simulation(run_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found or not running")
    return result


@router.delete("/api/runs/{run_name}", status_code=204)
async def delete_run(run_name: str, _user: User = Depends(require_admin)) -> None:
    """Удалить сохранённый прогон и сопутствующие файлы.

    Args:
        run_name: Имя прогона (без суффикса _events.jsonl).
        _user: Аутентифицированный пользователь с ролью admin.
    """
    from web.backend.runner import list_active

    validate_run_name(run_name)

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
