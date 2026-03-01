"""Маршруты управления прогонами: запуск, активные, остановка, удаление."""

from __future__ import annotations

import asyncio
import json
import os as _os
import uuid

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.settings import RESULTS_DIR, SCENARIOS_DIR
from web.backend.validators import (
    resolve_rounds,
    resolve_seed,
    validate_run_name,
    validate_scenario_id,
)

router = APIRouter(tags=["run-control"])


@router.post("/api/scenarios/{scenario_id}/run", status_code=202)
async def run_scenario(scenario_id: str, _user: User = Depends(require_admin)) -> dict:
    """Запустить прогон по сценарию.

    Args:
        scenario_id: UUID строка.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь с run_name и статусом.
    """
    from web.backend.runner import TooManyRunsError, launch_simulation_from_config

    validate_scenario_id(scenario_id)
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario = json.loads(path.read_text(encoding="utf-8"))
    try:
        governance = scenario.get("governance", "G1")
        seed = resolve_seed(scenario.get("seed"))
        runner_type = "cognitive"
        rounds = resolve_rounds(scenario.get("rounds"), default=10)
        sim_config = scenario.get("sim_config")

        if not _os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

        from magistry_sim.config import ScenarioConfig
        from magistry_sim.enums import GovernanceMode, ScenarioId
        from magistry_sim.llm import create_embedding_provider, create_provider
        from magistry_sim.persona_generator import generate_personas_parallel
        from magistry_sim.scenarios import add_governance_agents, get_scenario

        if isinstance(sim_config, dict):
            base_cfg = ScenarioConfig.model_validate(sim_config)
        else:
            base_cfg = get_scenario(ScenarioId(scenario.get("scenario", "S1")))

        try:
            gov_mode = GovernanceMode(governance)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        base_cfg = add_governance_agents(base_cfg, gov_mode)

        max_workers = int(_os.environ.get("MAGISTRY_PERSONA_MAX_WORKERS", "5") or "5")
        personalities_dir = (RESULTS_DIR / f"assets_{uuid.uuid4().hex[:10]}_personalities").resolve()
        interviews_dir = (RESULTS_DIR / f"assets_{uuid.uuid4().hex[:10]}_interviews").resolve()

        llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
        embedder = create_embedding_provider(mock=False)
        cfg_with_personas, _artifacts = await asyncio.to_thread(
            generate_personas_parallel,
            base_cfg,
            llm=llm,
            embedder=embedder,
            out_personalities_dir=personalities_dir,
            out_interviews_dir=interviews_dir,
            max_workers=max_workers,
            seed=seed,
        )

        suffix = scenario_id.replace("-", "")[:8]
        result = launch_simulation_from_config(
            scenario_config=cfg_with_personas.model_dump(mode="json"),
            governance=governance,
            seed=seed,
            runner_type=runner_type,
            rounds=rounds,
            variant=f"scn{suffix}",
            personalities_dir=personalities_dir,
            interviews_dir=interviews_dir,
        )
        return {"status": "accepted", **result}
    except TooManyRunsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/api/runs/launch", status_code=202)
async def launch_run(data: dict, _user: User = Depends(require_admin)) -> dict:
    """Запустить встроенный прогон (S0-S2).

    Args:
        data: Словарь с ключами scenario, governance, seed, runner.
        _user: Аутентифицированный пользователь с ролью admin.

    Returns:
        Словарь с run_name и PID.
    """
    from web.backend.runner import TooManyRunsError, launch_simulation_from_config

    scenario = data.get("scenario", "S1")
    governance = data.get("governance", "G1")
    seed = resolve_seed(data.get("seed"))
    runner_type = "cognitive"
    rounds = resolve_rounds(data.get("rounds"), default=10)
    try:
        if not _os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(status_code=409, detail="OPENAI_API_KEY is not set")

        from magistry_sim.enums import GovernanceMode, ScenarioId
        from magistry_sim.llm import create_embedding_provider, create_provider
        from magistry_sim.persona_generator import generate_personas_parallel
        from magistry_sim.scenarios import add_governance_agents, get_scenario

        base_cfg = get_scenario(ScenarioId(str(scenario)))
        base_cfg = add_governance_agents(base_cfg, GovernanceMode(str(governance)))

        max_workers = int(_os.environ.get("MAGISTRY_PERSONA_MAX_WORKERS", "5") or "5")
        personalities_dir = (RESULTS_DIR / f"assets_{uuid.uuid4().hex[:10]}_personalities").resolve()
        interviews_dir = (RESULTS_DIR / f"assets_{uuid.uuid4().hex[:10]}_interviews").resolve()

        llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
        embedder = create_embedding_provider(mock=False)
        cfg_with_personas, _artifacts = await asyncio.to_thread(
            generate_personas_parallel,
            base_cfg,
            llm=llm,
            embedder=embedder,
            out_personalities_dir=personalities_dir,
            out_interviews_dir=interviews_dir,
            max_workers=max_workers,
            seed=seed,
        )

        result = launch_simulation_from_config(
            scenario_config=cfg_with_personas.model_dump(mode="json"),
            governance=governance,
            seed=seed,
            runner_type=runner_type,
            rounds=rounds,
            variant="launch",
            personalities_dir=personalities_dir,
            interviews_dir=interviews_dir,
        )
        return {"status": "accepted", **result}
    except TooManyRunsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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
