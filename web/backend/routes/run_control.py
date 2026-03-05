"""Маршруты управления прогонами: запуск, активные, остановка, удаление."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.settings import RESULTS_DIR
from web.backend.validators import validate_run_name

router = APIRouter(tags=["run-control"])


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
