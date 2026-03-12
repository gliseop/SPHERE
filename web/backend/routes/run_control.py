"""Маршруты управления прогонами: запуск, активные, остановка, удаление."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.run_artifacts import (
    resolve_run_artifact,
    run_json_sidecar_candidates,
    run_log_sidecar_candidates,
)
from web.backend.settings import RESULTS_DIR
from web.backend.validators import resolve_rounds, resolve_seed, validate_run_name, validate_scenario_id
from .scenarios import _resolve_scenario_path, load_scenario_config_for_web

router = APIRouter(tags=["run-control"])


@router.post("/api/scenarios/{scenario_id}/run", status_code=202)
async def run_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> dict:
    """Запустить прогон по сценарию.

    Сценарий должен быть сохранён как полноценный ``ScenarioConfig``.
    """
    from web.backend.runner import TooManyRunsError, launch_simulation_from_config

    validate_scenario_id(scenario_id)
    path = _resolve_scenario_path(scenario_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    cfg = load_scenario_config_for_web(path, scenario_id=scenario_id)
    from .scenarios import _infer_governance_mode

    governance = _infer_governance_mode(cfg)
    try:
        payload = cfg.model_dump(mode="json")
        payload["scenario_id"] = scenario_id
        return launch_simulation_from_config(
            scenario_config=payload,
            governance=governance,
            seed=int(cfg.seed),
            rounds=int(cfg.ticks),
            runner_type="web",
            parallel_agents=cfg.runtime.parallel_agents,
            parallel_workers=cfg.runtime.parallel_workers,
            parallel_window=cfg.runtime.parallel_window_seconds,
        )
    except TooManyRunsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/runs/launch", status_code=202)
async def launch_run(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Запустить встроенный прогон (S0-S2).

    Поддерживает шаблоны из ``/api/templates/scenarios`` с overrides по governance/seed/rounds.
    """
    from web.backend.runner import TooManyRunsError, launch_simulation

    scenario = str(data.get("scenario") or "").strip()
    if not scenario:
        raise HTTPException(status_code=400, detail="Scenario is required")
    validate_scenario_id(scenario)

    governance = str(data.get("governance") or "G1").strip() or "G1"
    seed = resolve_seed(data.get("seed"))
    rounds = resolve_rounds(data.get("rounds"), default=25)
    parallel_agents = data.get("parallel_agents")
    if parallel_agents is not None and not isinstance(parallel_agents, bool):
        raise HTTPException(status_code=400, detail="Invalid parallel_agents")
    parallel_workers_raw = data.get("parallel_workers")
    if parallel_workers_raw is None:
        parallel_workers = None
    else:
        try:
            parallel_workers = int(parallel_workers_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid parallel_workers") from exc
        if parallel_workers <= 0:
            raise HTTPException(status_code=400, detail="Invalid parallel_workers")
    parallel_window_raw = data.get("parallel_window")
    if parallel_window_raw is None:
        parallel_window = None
    else:
        try:
            parallel_window = float(parallel_window_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid parallel_window") from exc
        if parallel_window < 0.0:
            raise HTTPException(status_code=400, detail="Invalid parallel_window")

    try:
        return launch_simulation(
            scenario=scenario,
            governance=governance,
            seed=seed,
            runner_type="web",
            rounds=rounds,
            parallel_agents=parallel_agents,
            parallel_workers=parallel_workers,
            parallel_window=parallel_window,
        )
    except TooManyRunsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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

    ref = resolve_run_artifact(run_name, results_dir=RESULTS_DIR)
    removed_any = False
    errors: list[str] = []

    legacy_candidates = [
        RESULTS_DIR / f"{run_name}_events.jsonl",
        RESULTS_DIR / f"{run_name}_trace.jsonl",
        RESULTS_DIR / f"{run_name}_truth.jsonl",
        RESULTS_DIR / f"{run_name}_evaluation.json",
        RESULTS_DIR / f"{run_name}_fidelity.json",
        RESULTS_DIR / f"{run_name}_analysis.md",
        RESULTS_DIR / f"{run_name}_defects.md",
        RESULTS_DIR / f"{run_name}_detailed_observations.md",
        RESULTS_DIR / f"{run_name}_codex_independent_analysis.md",
    ]
    if ref is not None:
        for stem in ("names", "summary", "scenario"):
            legacy_or_dir = run_json_sidecar_candidates(ref, stem, results_dir=RESULTS_DIR)
            legacy_candidates.extend(legacy_or_dir)
        for stem in ("stdout", "stderr"):
            legacy_or_dir = run_log_sidecar_candidates(ref, stem, results_dir=RESULTS_DIR)
            legacy_candidates.extend(legacy_or_dir)
    else:
        legacy_candidates.extend(
            [
                RESULTS_DIR / f"{run_name}_names.json",
                RESULTS_DIR / f"{run_name}_summary.json",
                RESULTS_DIR / f"{run_name}_scenario.json",
                RESULTS_DIR / f"{run_name}_truth.jsonl",
                RESULTS_DIR / f"{run_name}_evaluation.json",
                RESULTS_DIR / f"{run_name}_fidelity.json",
                RESULTS_DIR / f"{run_name}_stdout.log",
                RESULTS_DIR / f"{run_name}_stderr.log",
            ]
        )

    seen: set[str] = set()
    for path in legacy_candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            path.unlink()
            removed_any = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")

    run_dir = RESULTS_DIR / run_name
    if run_dir.is_dir():
        try:
            shutil.rmtree(run_dir)
            removed_any = True
        except OSError as exc:
            errors.append(f"{run_dir.name}/: {exc}")

    if errors:
        raise HTTPException(status_code=500, detail="; ".join(errors))
    if not removed_any:
        raise HTTPException(status_code=404, detail="Run not found")
