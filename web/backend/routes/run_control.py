"""Маршруты управления прогонами: запуск, активные, остановка, удаление."""

from __future__ import annotations

import asyncio
import json
import os as _os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from web.backend.auth import require_admin, require_viewer
from web.backend.database import User
from web.backend.settings import INTERVIEWS_DIR, PERSONALITIES_DIR, RESULTS_DIR, SCENARIOS_DIR
from web.backend.validators import (
    resolve_rounds,
    resolve_seed,
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

        # Параметры параллелизации из сценария
        p_agents = scenario.get("parallel_agents")
        p_workers = scenario.get("parallel_workers")
        p_window = scenario.get("parallel_window")

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

        # Перенести personality_archetype из UI-агентов в base_cfg
        ui_archetypes: dict[str, str] = {}
        for ui_agent in scenario.get("agents", []):
            arch = ui_agent.get("personality_archetype")
            if arch and ui_agent.get("id"):
                ui_archetypes[ui_agent["id"]] = arch
        if ui_archetypes:
            updated = []
            for a in base_cfg.agents:
                if a.id in ui_archetypes and not a.personality_archetype:
                    updated.append(a.model_copy(update={"personality_archetype": ui_archetypes[a.id]}))
                else:
                    updated.append(a)
            base_cfg = base_cfg.model_copy(update={"agents": updated})

        max_workers = int(_os.environ.get("MAGISTRY_PERSONA_MAX_WORKERS", "5") or "5")
        personalities_dir = (RESULTS_DIR / f"assets_{uuid.uuid4().hex[:10]}_personalities").resolve()
        interviews_dir = (RESULTS_DIR / f"assets_{uuid.uuid4().hex[:10]}_interviews").resolve()

        need_generation, copied = _prepare_existing_personalities(
            base_cfg.agents, personalities_dir, interviews_dir,
        )

        # Для скопированных агентов: personality_archetype -> agent_id
        # (файлы скопированы как {agent_id}.json, runner ищет по archetype).
        if copied:
            patched = []
            for a in base_cfg.agents:
                if a.id in copied:
                    patched.append(a.model_copy(update={"personality_archetype": a.id}))
                else:
                    patched.append(a)
            base_cfg = base_cfg.model_copy(update={"agents": patched})

        llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
        embedder = create_embedding_provider(mock=False)
        if need_generation:
            cfg_with_personas, _artifacts = await asyncio.to_thread(
                generate_personas_parallel,
                base_cfg,
                llm=llm,
                embedder=embedder,
                out_personalities_dir=personalities_dir,
                out_interviews_dir=interviews_dir,
                agent_ids=need_generation,
                max_workers=max_workers,
                seed=seed,
            )
        else:
            cfg_with_personas = base_cfg

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
            parallel_agents=p_agents if isinstance(p_agents, bool) else None,
            parallel_workers=int(p_workers) if p_workers is not None else None,
            parallel_window=float(p_window) if p_window is not None else None,
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
    p_agents = data.get("parallel_agents")
    p_workers = data.get("parallel_workers")
    p_window = data.get("parallel_window")
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

        need_generation, copied = _prepare_existing_personalities(
            base_cfg.agents, personalities_dir, interviews_dir,
        )

        if copied:
            patched = []
            for a in base_cfg.agents:
                if a.id in copied:
                    patched.append(a.model_copy(update={"personality_archetype": a.id}))
                else:
                    patched.append(a)
            base_cfg = base_cfg.model_copy(update={"agents": patched})

        llm = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
        embedder = create_embedding_provider(mock=False)
        if need_generation:
            cfg_with_personas, _artifacts = await asyncio.to_thread(
                generate_personas_parallel,
                base_cfg,
                llm=llm,
                embedder=embedder,
                out_personalities_dir=personalities_dir,
                out_interviews_dir=interviews_dir,
                agent_ids=need_generation,
                max_workers=max_workers,
                seed=seed,
            )
        else:
            cfg_with_personas = base_cfg

        result = launch_simulation_from_config(
            scenario_config=cfg_with_personas.model_dump(mode="json"),
            governance=governance,
            seed=seed,
            runner_type=runner_type,
            rounds=rounds,
            variant="launch",
            personalities_dir=personalities_dir,
            interviews_dir=interviews_dir,
            parallel_agents=p_agents if isinstance(p_agents, bool) else None,
            parallel_workers=int(p_workers) if p_workers is not None else None,
            parallel_window=float(p_window) if p_window is not None else None,
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
