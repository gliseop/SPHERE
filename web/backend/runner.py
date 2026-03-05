"""Обёртка для запуска симуляций через subprocess."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
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

# Порог «живости» для внешних прогонов (секунды).
# Если events-файл не обновлялся дольше этого интервала — считаем прогон завершённым.
_EXTERNAL_ALIVE_THRESHOLD = 60


class TooManyRunsError(RuntimeError):
    """Exceeded the max number of concurrent runs."""


def _env_truthy(name: str) -> bool:
    """True, если переменная окружения выставлена в «истинное» значение."""
    raw = os.environ.get(name)
    if raw is None:
        return False
    value = raw.strip().lower()
    return value not in ("", "0", "false", "no", "off")


def _maybe_add_parallel_flags(cmd: list[str]) -> None:
    """Добавить флаги параллельной симуляции из env (best-effort)."""
    if not _env_truthy("MAGISTRY_PARALLEL_AGENTS"):
        return
    cmd.append("--parallel-agents")

    raw_workers = os.environ.get("MAGISTRY_PARALLEL_WORKERS")
    if raw_workers:
        try:
            workers = int(raw_workers)
        except ValueError:
            workers = 0
        if workers > 0:
            cmd.extend(["--parallel-workers", str(workers)])

    raw_window = os.environ.get("MAGISTRY_PARALLEL_WINDOW")
    if raw_window:
        try:
            window = float(raw_window)
        except ValueError:
            window = 0.0
        if window > 0:
            cmd.extend(["--parallel-window", str(window)])


def _resolve_time_window(days: int) -> tuple[str, str]:
    """Преобразовать длительность (в днях) в start/end ISO-время для async-симуляции."""
    safe_days = max(1, int(days))
    now = datetime.now(timezone.utc)
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=safe_days)
    return start.isoformat(), end.isoformat()


def _write_names_json(run_name: str, scenario_id: str, governance: str) -> None:
    """Сгенерировать файл имён агентов из конфигурации сценария.

    Включает governance-агентов (аудитор, присяжные) для соответствующих
    режимов управления, чтобы они отображались на графе с именами.

    Args:
        run_name: Имя прогона (S1_G1_seed42).
        scenario_id: Идентификатор сценария (S0, S1, S2).
        governance: Идентификатор режима управления (G0-G3).
    """
    _logger.warning(
        "Cannot write names JSON for run %s: magistry_sim removed", run_name
    )


def _write_names_json_from_config(
    run_name: str, scenario_config: dict, governance: str
) -> None:
    """Сгенерировать файл имён агентов из JSON-конфига сценария."""
    try:
        names = {}
        agents = scenario_config.get("agents", [])
        for a in agents:
            if isinstance(a, dict):
                aid = a.get("id", "")
                aname = a.get("name", aid)
                if aid:
                    names[aid] = aname
        if names:
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
    *,
    personalities_dir: Path | None = None,
    interviews_dir: Path | None = None,
    parallel_agents: bool | None = None,
    parallel_workers: int | None = None,
    parallel_window: float | None = None,
) -> dict:
    """Запустить симуляцию на основе JSON-конфига ScenarioConfig.

    Args:
        parallel_agents: Включить параллельную генерацию решений.
            None = определить из env.
        parallel_workers: Максимум потоков. None = определить из env.
        parallel_window: Окно батчирования (симулированные секунды).
            None = определить из env.
    """
    raise RuntimeError(
        "Запуск через web/backend/runner отключён: legacy launcher magistry_sim удалён."
    )


def launch_simulation(
    scenario: str,
    governance: str,
    seed: int = 42,
    runner_type: str = "cognitive",
    rounds: int = 10,
    *,
    personalities_dir: Path | None = None,
    interviews_dir: Path | None = None,
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
    raise RuntimeError(
        "Запуск через web/backend/runner отключён: legacy launcher magistry_sim удалён."
    )


def _discover_external_runs() -> list[dict]:
    """Обнаружить внешние (CLI-запущенные) прогоны по файловой системе.

    Сканирует ``_RESULTS_DIR`` на предмет JSONL-файлов событий, для которых
    отсутствует summary-файл и которые были обновлены недавно. Такие прогоны
    считаются «живыми», но не управляются бэкендом (нет Popen-объекта).

    Returns:
        Список словарей с информацией о внешних прогонах.
    """
    if not _RESULTS_DIR.is_dir():
        return []

    now = time.time()
    active_names: set[str] = set(_active)
    external: list[dict] = []

    for events_path in _RESULTS_DIR.glob("*_events.jsonl"):
        stem = events_path.name  # e.g. "S2_G1_seed1_cognitive_events.jsonl"
        if not stem.endswith("_events.jsonl"):
            continue
        run_name = stem[: -len("_events.jsonl")]

        # Не дублировать API-запущенные прогоны
        if run_name in active_names:
            continue

        # Если summary уже есть — прогон завершён
        summary_path = _RESULTS_DIR / f"{run_name}_summary.json"
        if summary_path.exists():
            continue

        # Проверить свежесть файла
        try:
            mtime = events_path.stat().st_mtime
        except OSError:
            continue

        if (now - mtime) > _EXTERNAL_ALIVE_THRESHOLD:
            continue

        external.append(
            {
                "run_name": run_name,
                "pid": 0,
                "status": "running",
                "external": True,
            }
        )

    return external


def list_active() -> list[dict]:
    """Вернуть список активных прогонов.

    Включает как прогоны, запущенные через API (хранятся в ``_active``),
    так и внешние прогоны, обнаруженные по файловой системе.

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

        # Обнаружить внешние (CLI-запущенные) прогоны
        result.extend(_discover_external_runs())

        return result


def is_external_run(run_name: str) -> bool:
    """Проверить, является ли прогон внешним (CLI-запущенным).

    Args:
        run_name: Имя прогона.

    Returns:
        True если прогон обнаружен как внешний и активный.
    """
    for run in _discover_external_runs():
        if run["run_name"] == run_name:
            return True
    return False


def stop_simulation(run_name: str) -> Optional[dict]:
    """Остановить запущенную симуляцию.

    Args:
        run_name: Имя прогона.

    Returns:
        Словарь со статусом или None если прогон не найден.

    Raises:
        RuntimeError: Если прогон является внешним (нет Popen-объекта).
    """
    with _active_lock:
        proc = _active.get(run_name)
    if proc is None:
        if is_external_run(run_name):
            raise RuntimeError(
                f"Невозможно остановить внешний прогон «{run_name}»: "
                "процесс запущен вне API, Popen-объект отсутствует"
            )
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
