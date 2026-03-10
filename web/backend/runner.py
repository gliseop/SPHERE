"""Обёртка для запуска симуляций через subprocess."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .run_artifacts import list_run_artifacts, run_json_sidecar_candidates

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_DIR = _PROJECT_ROOT / "results"

# Хранилище активных процессов: run_name -> subprocess.Popen
_active: dict[str, subprocess.Popen] = {}
_reserved_run_names: set[str] = set()
_active_lock = threading.Lock()

_logger = logging.getLogger(__name__)

try:
    _MAX_RUNNING = max(1, int(os.environ.get("MAGISTRY_MAX_RUNNING", "5")))
except ValueError:
    _MAX_RUNNING = 5

try:
    _EXTERNAL_ALIVE_THRESHOLD = max(60, int(os.environ.get("MAGISTRY_EXTERNAL_ALIVE_THRESHOLD", "300")))
except ValueError:
    _EXTERNAL_ALIVE_THRESHOLD = 300


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
    config = dict(scenario_config or {})
    config["seed"] = int(seed)
    if rounds is not None:
        config["ticks"] = int(rounds)

    base_name = _make_run_name(
        scenario=str(config.get("scenario_id") or config.get("id") or config.get("title") or "scenario"),
        governance=governance,
        seed=seed,
        variant=variant or runner_type,
    )
    run_name, out_dir = _reserve_run_dir(base_name)

    scenario_path = out_dir / "_input_scenario.json"
    stdout_handle = None
    stderr_handle = None
    try:
        scenario_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        stdout_path = _RESULTS_DIR / f"{run_name}_stdout.log"
        stderr_path = _RESULTS_DIR / f"{run_name}_stderr.log"
        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "magistry_lc.cli",
            "run",
            "--scenario",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        if parallel_agents is True:
            env["MAGISTRY_PARALLEL_AGENTS"] = "1"
        elif parallel_agents is False:
            env["MAGISTRY_PARALLEL_AGENTS"] = "0"
        if parallel_workers is not None:
            env["MAGISTRY_PARALLEL_WORKERS"] = str(int(parallel_workers))
        if parallel_window is not None:
            env["MAGISTRY_PARALLEL_WINDOW"] = str(float(parallel_window))

        proc = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
        )
    except Exception:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
        _release_reserved_run_name(run_name)
        raise

    threading.Thread(
        target=_close_logs_when_done,
        args=(proc, stdout_handle, stderr_handle),
        daemon=True,
    ).start()

    with _active_lock:
        _reserved_run_names.discard(run_name)
        _active[run_name] = proc
    return {"run_name": run_name, "pid": proc.pid}


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
    from web.backend.routes.scenarios import load_template_config_for_web

    cfg = load_template_config_for_web(scenario, governance=governance)
    payload = cfg.model_dump(mode="json")
    payload["scenario_id"] = scenario
    return launch_simulation_from_config(
        scenario_config=payload,
        governance=governance,
        seed=seed,
        runner_type=runner_type,
        rounds=rounds,
        variant=runner_type,
        personalities_dir=personalities_dir,
        interviews_dir=interviews_dir,
    )


def _make_run_name(
    *,
    scenario: str,
    governance: str,
    seed: int,
    variant: str | None,
) -> str:
    scenario_slug = _safe_run_part(str(scenario or "scenario"))
    governance_slug = _safe_run_part(str(governance or "G0"))
    parts = [scenario_slug, governance_slug, f"seed{int(seed)}"]
    if variant:
        parts.append(_safe_run_part(variant))
    return "_".join(part for part in parts if part)


def _safe_run_part(value: str) -> str:
    slug = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in {"_", "-"}) else "_"
        for ch in value.strip()
    )
    slug = slug.strip("_")
    return slug[:64] or "run"


def _allocate_run_name(base_name: str) -> str:
    candidate = base_name
    suffix = 2
    while _run_name_taken(candidate):
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    return candidate


def _run_name_taken(run_name: str) -> bool:
    if (_RESULTS_DIR / run_name).exists():
        return True
    if (_RESULTS_DIR / f"{run_name}_events.jsonl").exists():
        return True
    with _active_lock:
        if run_name in _reserved_run_names:
            return True
        return run_name in _active and _active[run_name].poll() is None


def _reserve_run_dir(base_name: str) -> tuple[str, Path]:
    with _active_lock:
        running_now = sum(1 for proc in _active.values() if proc.poll() is None)
        if running_now >= _MAX_RUNNING:
            raise TooManyRunsError(
                f"Слишком много одновременных прогонов: {running_now} >= {_MAX_RUNNING}"
            )

        candidate = base_name
        suffix = 2
        while True:
            out_dir = _RESULTS_DIR / candidate
            legacy_events = _RESULTS_DIR / f"{candidate}_events.jsonl"
            if legacy_events.exists():
                candidate = f"{base_name}_{suffix}"
                suffix += 1
                continue
            if candidate in _reserved_run_names:
                candidate = f"{base_name}_{suffix}"
                suffix += 1
                continue
            if candidate in _active and _active[candidate].poll() is None:
                candidate = f"{base_name}_{suffix}"
                suffix += 1
                continue
            try:
                out_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                candidate = f"{base_name}_{suffix}"
                suffix += 1
                continue
            _reserved_run_names.add(candidate)
            return candidate, out_dir


def _release_reserved_run_name(run_name: str) -> None:
    with _active_lock:
        _reserved_run_names.discard(run_name)


def _close_logs_when_done(
    proc: subprocess.Popen,
    stdout_handle,
    stderr_handle,
) -> None:
    try:
        proc.wait()
    except Exception:
        return
    finally:
        try:
            stdout_handle.close()
        except Exception:
            pass
        try:
            stderr_handle.close()
        except Exception:
            pass


def _discover_external_runs() -> list[dict]:
    """Обнаружить внешние (CLI-запущенные) прогоны по файловой системе.

    Сканирует ``_RESULTS_DIR`` на предмет событий в двух форматах:
    ``*_events.jsonl`` и ``{run}/events.jsonl``. Прогон считается внешним,
    если отсутствует summary и events-файл обновлялся недавно.

    Returns:
        Список словарей с информацией о внешних прогонах.
    """
    if not _RESULTS_DIR.is_dir():
        return []

    now = time.time()
    active_names: set[str] = set(_active)
    external: list[dict] = []

    for ref in list_run_artifacts(results_dir=_RESULTS_DIR):
        run_name = ref.name
        if run_name in active_names:
            continue

        summary_paths = run_json_sidecar_candidates(ref, "summary", results_dir=_RESULTS_DIR)
        if any(path.exists() for path in summary_paths):
            continue

        try:
            mtime = ref.events_path.stat().st_mtime
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
                "stop_supported": False,
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
                        "external": False,
                        "stop_supported": True,
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
                        "external": False,
                        "stop_supported": True,
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
