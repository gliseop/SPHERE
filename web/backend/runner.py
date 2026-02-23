"""Обёртка для запуска симуляций через subprocess."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_DIR = _PROJECT_ROOT / "results"

# Хранилище активных процессов: run_name -> subprocess.Popen
_active: dict[str, subprocess.Popen] = {}


def launch_simulation(
    scenario: str,
    governance: str,
    seed: int = 42,
    runner_type: str = "mock",
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
    run_name = f"{scenario}_{governance}_seed{seed}"

    # Проверить, не запущен ли уже
    if run_name in _active:
        proc = _active[run_name]
        if proc.poll() is None:
            raise RuntimeError(f"Прогон {run_name} уже запущен (PID {proc.pid})")
        else:
            del _active[run_name]

    cmd = [
        sys.executable, "-m", "magistry_sim.cli",
        "run",
        "--scenario", scenario,
        "--governance", governance,
        "--seed", str(seed),
        "--runner", runner_type,
        "--rounds", str(rounds),
        "--output-dir", str(_RESULTS_DIR),
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(_PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _active[run_name] = proc

    return {"run_name": run_name, "pid": proc.pid}


def list_active() -> list[dict]:
    """Вернуть список активных прогонов.

    Returns:
        Список словарей с именем прогона, PID и статусом.
    """
    result = []
    finished = []
    for name, proc in _active.items():
        poll = proc.poll()
        if poll is None:
            result.append({"run_name": name, "pid": proc.pid, "status": "running"})
        else:
            result.append({"run_name": name, "pid": proc.pid, "status": "finished", "returncode": poll})
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
    proc = _active.get(run_name)
    if proc is None:
        return None
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    del _active[run_name]
    return {"run_name": run_name, "status": "stopped"}
