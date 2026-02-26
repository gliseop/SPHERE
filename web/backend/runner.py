"""Обёртка для запуска симуляций через subprocess."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_DIR = _PROJECT_ROOT / "results"

# Хранилище активных процессов: run_name -> subprocess.Popen
_active: dict[str, subprocess.Popen] = {}
_active_lock = threading.Lock()

_logger = logging.getLogger(__name__)

try:
    _MAX_RUNNING = max(1, int(os.environ.get("MAGISTRY_MAX_RUNNING", "5")))
except ValueError:
    _MAX_RUNNING = 5


class TooManyRunsError(RuntimeError):
    """Exceeded the max number of concurrent runs."""


def _write_names_json(run_name: str, scenario_id: str, governance: str) -> None:
    """Сгенерировать файл имён агентов из конфигурации сценария.

    Включает governance-агентов (аудитор, присяжные) для соответствующих
    режимов управления, чтобы они отображались на графе с именами.

    Args:
        run_name: Имя прогона (S1_G1_seed42).
        scenario_id: Идентификатор сценария (S0, S1, S2).
        governance: Идентификатор режима управления (G0-G3).
    """
    try:
        from magistry_sim.enums import GovernanceMode, ScenarioId
        from magistry_sim.scenarios import add_governance_agents, get_scenario

        base = get_scenario(ScenarioId(scenario_id))
        full = add_governance_agents(base, GovernanceMode(governance))
        names = {agent.id: agent.name for agent in full.agents}
        path = _RESULTS_DIR / f"{run_name}_names.json"
        path.write_text(
            json.dumps(names, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        _logger.exception("Failed to write names JSON for run %s", run_name)


def _write_names_json_from_config(
    run_name: str, scenario_config: dict, governance: str
) -> None:
    """Сгенерировать файл имён агентов из JSON-конфига сценария."""
    try:
        from magistry_sim.config import ScenarioConfig
        from magistry_sim.enums import GovernanceMode
        from magistry_sim.scenarios import add_governance_agents

        cfg = ScenarioConfig.model_validate(scenario_config)
        full = add_governance_agents(cfg, GovernanceMode(governance))
        names = {agent.id: agent.name for agent in full.agents}
        path = _RESULTS_DIR / f"{run_name}_names.json"
        path.write_text(
            json.dumps(names, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        _logger.exception("Failed to write names JSON for run %s", run_name)


def launch_simulation_from_config(
    scenario_config: dict,
    governance: str,
    seed: int = 42,
    runner_type: str = "cognitive",
    rounds: int = 10,
    variant: str | None = None,
) -> dict:
    """Запустить симуляцию на основе JSON-конфига ScenarioConfig."""
    scenario_id = str(scenario_config.get("id", "S1") or "S1")
    safe_variant = "".join(
        ch if ch.isalnum() or ch in ("_", "-") else "-"
        for ch in (variant or "custom")
    )
    if runner_type == "mock":
        raise RuntimeError("mock runner is not supported; use cognitive")
    if runner_type in ("cognitive", "llm", "crewai") and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set; LLM runner requires it")

    run_name = f"{scenario_id}_{governance}_seed{seed}_{safe_variant}_{runner_type}"

    with _active_lock:
        running_count = 0
        finished = []
        for name, proc in _active.items():
            if proc.poll() is None:
                running_count += 1
            else:
                finished.append(name)
        for name in finished:
            del _active[name]

        if running_count >= _MAX_RUNNING:
            raise TooManyRunsError(
                f"Превышен лимит одновременных прогонов ({running_count}/{_MAX_RUNNING})"
            )

        if run_name in _active:
            proc = _active[run_name]
            raise RuntimeError(f"Прогон {run_name} уже запущен (PID {proc.pid})")

        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        jsonl_path = _RESULTS_DIR / f"{run_name}_events.jsonl"
        summary_path = _RESULTS_DIR / f"{run_name}_summary.json"
        stdout_path = _RESULTS_DIR / f"{run_name}_stdout.log"
        stderr_path = _RESULTS_DIR / f"{run_name}_stderr.log"
        scenario_path = _RESULTS_DIR / f"{run_name}_scenario.json"

        try:
            scenario_path.write_text(
                json.dumps(scenario_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            _logger.exception("Failed to write scenario JSON for run %s", run_name)

        _write_names_json_from_config(run_name, scenario_config, governance)

        cmd = [
            sys.executable,
            "-m",
            "magistry_sim.cli",
            "--scenario-json",
            str(scenario_path),
            "--governance",
            governance,
            "--seed",
            str(seed),
            "--runner",
            runner_type,
            "--rounds",
            str(rounds),
            "--jsonl",
            str(jsonl_path),
            "--summary-json",
            str(summary_path),
        ]

        with open(stdout_path, "wb") as stdout, open(stderr_path, "wb") as stderr:
            proc = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),
                stdout=stdout,
                stderr=stderr,
            )
        _active[run_name] = proc

    return {
        "run_name": run_name,
        "pid": proc.pid,
        "stdout_log": stdout_path.name,
        "stderr_log": stderr_path.name,
        "scenario_json": scenario_path.name,
    }


def launch_simulation(
    scenario: str,
    governance: str,
    seed: int = 42,
    runner_type: str = "cognitive",
    rounds: int = 10,
) -> dict:
    """Запустить симуляцию как subprocess.

    Args:
        scenario: Идентификатор сценария (S0, S1, S2).
        governance: Идентификатор режима управления (G0-G3).
        seed: Начальное значение для генератора случайных чисел.
        runner_type: Тип раннера (mock или cognitive).
        rounds: Количество раундов.

    Returns:
        Словарь с run_name и pid запущенного процесса.

    Raises:
        RuntimeError: Если прогон с таким именем уже запущен.
    """
    if runner_type == "mock":
        raise RuntimeError("mock runner is not supported; use cognitive")
    if runner_type in ("cognitive", "llm", "crewai") and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set; LLM runner requires it")

    run_name = f"{scenario}_{governance}_seed{seed}_{runner_type}"

    with _active_lock:
        running_count = 0
        finished = []
        for name, proc in _active.items():
            if proc.poll() is None:
                running_count += 1
            else:
                finished.append(name)
        for name in finished:
            del _active[name]

        if running_count >= _MAX_RUNNING:
            raise TooManyRunsError(
                f"Превышен лимит одновременных прогонов ({running_count}/{_MAX_RUNNING})"
            )

        # Проверить, не запущен ли уже
        if run_name in _active:
            proc = _active[run_name]
            raise RuntimeError(f"Прогон {run_name} уже запущен (PID {proc.pid})")

        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        jsonl_path = _RESULTS_DIR / f"{run_name}_events.jsonl"
        summary_path = _RESULTS_DIR / f"{run_name}_summary.json"
        stdout_path = _RESULTS_DIR / f"{run_name}_stdout.log"
        stderr_path = _RESULTS_DIR / f"{run_name}_stderr.log"

        _write_names_json(run_name, scenario, governance)

        cmd = [
            sys.executable,
            "-m",
            "magistry_sim.cli",
            "--scenario",
            scenario,
            "--governance",
            governance,
            "--seed",
            str(seed),
            "--runner",
            runner_type,
            "--rounds",
            str(rounds),
            "--jsonl",
            str(jsonl_path),
            "--summary-json",
            str(summary_path),
        ]

        # Важно: не использовать PIPE без чтения stdout/stderr — иначе процесс может
        # зависнуть при заполнении буфера (особенно для verbose-runner-ов).
        with open(stdout_path, "wb") as stdout, open(stderr_path, "wb") as stderr:
            proc = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),
                stdout=stdout,
                stderr=stderr,
            )
        _active[run_name] = proc

    return {
        "run_name": run_name,
        "pid": proc.pid,
        "stdout_log": stdout_path.name,
        "stderr_log": stderr_path.name,
    }


def list_active() -> list[dict]:
    """Вернуть список активных прогонов.

    Returns:
        Список словарей с именем прогона, PID и статусом.
    """
    with _active_lock:
        result = []
        finished = []
        for name, proc in _active.items():
            poll = proc.poll()
            if poll is None:
                result.append(
                    {
                        "run_name": name,
                        "pid": proc.pid,
                        "status": "running",
                        "stdout_log": f"{name}_stdout.log",
                        "stderr_log": f"{name}_stderr.log",
                    }
                )
            else:
                result.append(
                    {
                        "run_name": name,
                        "pid": proc.pid,
                        "status": "finished",
                        "returncode": poll,
                        "stdout_log": f"{name}_stdout.log",
                        "stderr_log": f"{name}_stderr.log",
                    }
                )
                finished.append(name)
        # Очистить завершённые
        for name in finished:
            del _active[name]
        return result


def stop_simulation(run_name: str) -> Optional[dict]:
    """Остановить запущенную симуляцию.

    Args:
        run_name: Имя прогона.

    Returns:
        Словарь со статусом или None если прогон не найден.
    """
    with _active_lock:
        proc = _active.get(run_name)
    if proc is None:
        return None
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    with _active_lock:
        if _active.get(run_name) is proc:
            del _active[run_name]
    return {"run_name": run_name, "status": "stopped"}
