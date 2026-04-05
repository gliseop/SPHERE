"""Обёртка для запуска симуляций через subprocess."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .run_artifacts import (
    list_run_artifacts,
    resolve_run_artifact,
    run_json_sidecar_candidates,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_DIR = _PROJECT_ROOT / "results"
_RUN_METADATA_FILENAME = "run.json"

# Хранилище активных процессов: run_name -> subprocess.Popen
_active: dict[str, subprocess.Popen] = {}
_active_started_at: dict[str, float] = {}
_reserved_run_names: set[str] = set()
_active_lock = threading.Lock()

_logger = logging.getLogger(__name__)

try:
    _MAX_RUNNING = max(1, int(os.environ.get("SPHERE_MAX_RUNNING", "5")))
except ValueError:
    _MAX_RUNNING = 5

try:
    _EXTERNAL_ALIVE_THRESHOLD = max(60, int(os.environ.get("SPHERE_EXTERNAL_ALIVE_THRESHOLD", "300")))
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
    if not _env_truthy("SPHERE_PARALLEL_AGENTS"):
        return
    cmd.append("--parallel-agents")

    raw_workers = os.environ.get("SPHERE_PARALLEL_WORKERS")
    if raw_workers:
        try:
            workers = int(raw_workers)
        except ValueError:
            workers = 0
        if workers > 0:
            cmd.extend(["--parallel-workers", str(workers)])

    raw_window = os.environ.get("SPHERE_PARALLEL_WINDOW")
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
        run_name: Имя прогона (например, S1_G1_web).
        scenario_id: Идентификатор сценария (S0, S1, S2).
        governance: Идентификатор режима управления (G0-G3).
    """
    _logger.warning(
        "Cannot write names JSON for run %s: legacy names export path removed", run_name
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


def _apply_parallel_runtime_overrides(
    scenario_config: dict,
    *,
    parallel_agents: bool | None,
    parallel_workers: int | None,
    parallel_window: float | None,
) -> tuple[bool | None, int | None, float | None]:
    runtime = scenario_config.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
        scenario_config["runtime"] = runtime

    if parallel_agents is not None:
        runtime["parallel_agents"] = bool(parallel_agents)
    if parallel_workers is not None:
        runtime["parallel_workers"] = int(parallel_workers)
    if parallel_window is not None:
        runtime["parallel_window_seconds"] = float(parallel_window)

    resolved_parallel_agents = runtime.get("parallel_agents")
    if not isinstance(resolved_parallel_agents, bool):
        resolved_parallel_agents = None

    resolved_parallel_workers = runtime.get("parallel_workers")
    if not isinstance(resolved_parallel_workers, int) or resolved_parallel_workers <= 0:
        resolved_parallel_workers = None

    resolved_parallel_window = runtime.get("parallel_window_seconds")
    if isinstance(resolved_parallel_window, int):
        resolved_parallel_window = float(resolved_parallel_window)
    if not isinstance(resolved_parallel_window, float) or resolved_parallel_window < 0.0:
        resolved_parallel_window = None

    return (
        resolved_parallel_agents,
        resolved_parallel_workers,
        resolved_parallel_window,
    )


def _run_metadata_path(out_dir: Path) -> Path:
    return out_dir / _RUN_METADATA_FILENAME


def _build_run_metadata(
    *,
    run_name: str,
    scenario_config: dict,
    governance: str,
    runner_type: str,
    variant: str | None,
    started_at: datetime,
) -> dict[str, object]:
    """Собрать sidecar-метаданные прогона для web UI."""
    title = str(
        scenario_config.get("title")
        or scenario_config.get("name")
        or scenario_config.get("scenario_id")
        or scenario_config.get("id")
        or run_name
    ).strip() or run_name
    runtime_raw = scenario_config.get("runtime")
    runtime = runtime_raw if isinstance(runtime_raw, dict) else {}
    ticks_total = max(0, int(scenario_config.get("ticks") or 0))
    start_date_raw = runtime.get("start_date")
    tick_granularity = str(runtime.get("tick_granularity") or "day")
    tick_duration_days = max(1, int(runtime.get("tick_duration_days") or 1))
    simulated_start_date = None
    simulated_end_date = None

    try:
        from sphere_lc.config import RuntimeConfig

        runtime_cfg = RuntimeConfig.model_validate(
            {
                "start_date": start_date_raw,
                "tick_granularity": tick_granularity,
                "tick_duration_days": tick_duration_days,
            }
        )
        if runtime_cfg.start_date is not None:
            simulated_start_date = runtime_cfg.start_date.isoformat()
            last_tick = max(0, ticks_total - 1)
            end_date = runtime_cfg.simulated_date(last_tick)
            simulated_end_date = end_date.isoformat() if end_date is not None else simulated_start_date
    except Exception:
        simulated_start_date = str(start_date_raw).strip() or None
        simulated_end_date = simulated_start_date

    return {
        "run_name": run_name,
        "display_name": title,
        "scenario_id": str(scenario_config.get("scenario_id") or "").strip() or None,
        "scenario_title": title,
        "governance": str(governance or "").strip(),
        "runner_type": runner_type,
        "variant": variant or runner_type,
        "ticks_total": ticks_total,
        "runtime": {
            "start_date": str(start_date_raw).strip() or None,
            "tick_granularity": tick_granularity,
            "tick_duration_days": tick_duration_days,
        },
        "simulated_start_date": simulated_start_date,
        "simulated_end_date": simulated_end_date,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "status": "running",
        "returncode": None,
    }


def _write_run_metadata(out_dir: Path, metadata: dict[str, object]) -> None:
    _run_metadata_path(out_dir).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _update_run_metadata(
    out_dir: Path,
    *,
    status: str,
    finished_at: datetime | None = None,
    returncode: int | None = None,
) -> None:
    path = _run_metadata_path(out_dir)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    payload["status"] = status
    payload["finished_at"] = finished_at.isoformat() if finished_at is not None else payload.get("finished_at")
    payload["returncode"] = int(returncode) if returncode is not None else payload.get("returncode")
    try:
        _write_run_metadata(out_dir, payload)
    except OSError:
        _logger.exception("Failed to update run metadata for %s", out_dir)


def _read_run_metadata_for_name(run_name: str) -> dict[str, object]:
    ref = resolve_run_artifact(run_name, results_dir=_RESULTS_DIR)
    if ref is None:
        return {}
    candidates = [
        ref.events_path.parent / _RUN_METADATA_FILENAME,
        _RESULTS_DIR / f"{run_name}_run.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def launch_simulation_from_config(
    scenario_config: dict,
    governance: str,
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
    if rounds is not None:
        config["ticks"] = int(rounds)
    resolved_parallel_agents, resolved_parallel_workers, resolved_parallel_window = (
        _apply_parallel_runtime_overrides(
            config,
            parallel_agents=parallel_agents,
            parallel_workers=parallel_workers,
            parallel_window=parallel_window,
        )
    )

    base_name = _make_run_name(
        scenario=str(config.get("scenario_id") or config.get("id") or config.get("title") or "scenario"),
        governance=governance,
        variant=variant or runner_type,
    )
    run_name, out_dir = _reserve_run_dir(base_name)

    scenario_path = out_dir / "_input_scenario.json"
    stdout_path = _RESULTS_DIR / f"{run_name}_stdout.log"
    stderr_path = _RESULTS_DIR / f"{run_name}_stderr.log"
    stdout_handle = None
    stderr_handle = None
    started_at = datetime.now(timezone.utc)
    try:
        scenario_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_run_metadata(
            out_dir,
            _build_run_metadata(
                run_name=run_name,
                scenario_config=config,
                governance=governance,
                runner_type=runner_type,
                variant=variant or runner_type,
                started_at=started_at,
            ),
        )

        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "sphere_lc.cli",
            "run",
            "--scenario",
            str(scenario_path),
            "--out",
            str(out_dir),
        ]

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        if resolved_parallel_agents is True:
            env["SPHERE_PARALLEL_AGENTS"] = "1"
        elif resolved_parallel_agents is False:
            env["SPHERE_PARALLEL_AGENTS"] = "0"
        if resolved_parallel_workers is not None:
            env["SPHERE_PARALLEL_WORKERS"] = str(int(resolved_parallel_workers))
        if resolved_parallel_window is not None:
            env["SPHERE_PARALLEL_WINDOW"] = str(float(resolved_parallel_window))

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
        for path in (stdout_path, stderr_path):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                pass
        try:
            shutil.rmtree(out_dir)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        _release_reserved_run_name(run_name)
        raise

    threading.Thread(
        target=_close_logs_when_done,
        args=(proc, stdout_handle, stderr_handle, out_dir),
        daemon=True,
    ).start()

    with _active_lock:
        _reserved_run_names.discard(run_name)
        _active[run_name] = proc
        _active_started_at[run_name] = started_at.timestamp()
    return {"run_name": run_name, "pid": proc.pid}


def launch_simulation(
    scenario: str,
    governance: str,
    runner_type: str = "cognitive",
    rounds: int = 10,
    *,
    personalities_dir: Path | None = None,
    interviews_dir: Path | None = None,
    parallel_agents: bool | None = None,
    parallel_workers: int | None = None,
    parallel_window: float | None = None,
) -> dict:
    """Запустить симуляцию как subprocess.

    Args:
        scenario: Идентификатор сценария (S0, S1, S2).
        governance: Идентификатор режима управления (G0-G3).
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
        runner_type=runner_type,
        rounds=rounds,
        variant=runner_type,
        personalities_dir=personalities_dir,
        interviews_dir=interviews_dir,
        parallel_agents=parallel_agents,
        parallel_workers=parallel_workers,
        parallel_window=parallel_window,
    )


def _make_run_name(
    *,
    scenario: str,
    governance: str,
    variant: str | None,
) -> str:
    scenario_slug = _safe_run_part(str(scenario or "scenario"))
    governance_slug = _safe_run_part(str(governance or "G0"))
    parts = [scenario_slug, governance_slug]
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
    out_dir: Path,
) -> None:
    returncode: int | None = None
    try:
        returncode = proc.wait()
    except Exception:
        return
    finally:
        _update_run_metadata(
            out_dir,
            status="finished",
            finished_at=datetime.now(timezone.utc),
            returncode=returncode,
        )
        try:
            stdout_handle.close()
        except Exception:
            pass
        try:
            stderr_handle.close()
        except Exception:
            pass


def _read_run_status(ref) -> tuple[dict[str, object], Path] | None:
    """Прочитать status-sidecar прогона."""
    for path in run_json_sidecar_candidates(ref, "status", results_dir=_RESULTS_DIR):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            return data, path
    return None


def _status_activity_at(status: dict[str, object], path: Path) -> float:
    raw = str(status.get("updated_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _status_pid(status: dict[str, object]) -> int:
    try:
        pid = int(status.get("pid") or 0)
    except (TypeError, ValueError):
        return 0
    return pid if pid >= 0 else 0


def _discover_external_runs(*, exclude_names: set[str] | None = None) -> list[dict]:
    """Обнаружить внешние (CLI-запущенные) прогоны по файловой системе.

    Сканирует ``_RESULTS_DIR`` на предмет событий в двух форматах:
    ``*_events.jsonl`` и ``{run}/events.jsonl``. Внешний прогон считается
    живым только если рядом есть ``status.json``/``*_status.json`` со
    статусом ``running`` и свежим heartbeat.

    Returns:
        Список словарей с информацией о внешних прогонах.
    """
    if not _RESULTS_DIR.is_dir():
        return []

    now = time.time()
    active_names: set[str] = set(_active)
    if exclude_names:
        active_names.update(exclude_names)
    external: list[dict] = []

    for ref in list_run_artifacts(results_dir=_RESULTS_DIR):
        run_name = ref.name
        if run_name in active_names:
            continue

        summary_paths = run_json_sidecar_candidates(ref, "summary", results_dir=_RESULTS_DIR)
        if any(path.exists() for path in summary_paths):
            continue

        status_record = _read_run_status(ref)
        if status_record is None:
            continue
        status, status_path = status_record
        if str(status.get("state") or "").strip().lower() != "running":
            continue

        activity_at = _status_activity_at(status, status_path)
        if activity_at <= 0.0:
            continue

        if (now - activity_at) > _EXTERNAL_ALIVE_THRESHOLD:
            continue

        external.append(
            {
                "run_name": run_name,
                "pid": _status_pid(status),
                "status": "running",
                "external": True,
                "stop_supported": False,
                "activity_at": activity_at,
            }
        )
        meta = _read_run_metadata_for_name(run_name)
        if meta.get("display_name"):
            external[-1]["display_name"] = meta.get("display_name")

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
        known_names = set(_active)
        for name, proc in _active.items():
            poll = proc.poll()
            activity_at = _run_activity_at(name=name, fallback=_active_started_at.get(name, 0.0))
            meta = _read_run_metadata_for_name(name)
            if poll is None:
                item = {
                    "run_name": name,
                    "pid": proc.pid,
                    "status": "running",
                    "external": False,
                    "stop_supported": True,
                    "stdout_log": f"{name}_stdout.log",
                    "stderr_log": f"{name}_stderr.log",
                    "activity_at": activity_at,
                }
                if meta.get("display_name"):
                    item["display_name"] = meta.get("display_name")
                result.append(item)
            else:
                item = {
                    "run_name": name,
                    "pid": proc.pid,
                    "status": "finished",
                    "external": False,
                    "stop_supported": True,
                    "returncode": poll,
                    "stdout_log": f"{name}_stdout.log",
                    "stderr_log": f"{name}_stderr.log",
                    "activity_at": activity_at,
                }
                if meta.get("display_name"):
                    item["display_name"] = meta.get("display_name")
                result.append(item)
                finished.append(name)
        # Очистить завершённые
        for name in finished:
            del _active[name]
            _active_started_at.pop(name, None)

        # Обнаружить внешние (CLI-запущенные) прогоны
        result.extend(_discover_external_runs(exclude_names=known_names))

        result.sort(
            key=lambda item: (
                0 if item.get("status") == "running" else 1,
                float(item.get("activity_at") or 0.0),
                str(item.get("run_name") or ""),
            )
        )

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
            _active_started_at.pop(run_name, None)
    return {"run_name": run_name, "status": "stopped"}


def _run_activity_at(*, name: str, fallback: float) -> float:
    """Время последней активности/старта прогона для сортировки live-списка."""
    ref = resolve_run_artifact(name, results_dir=_RESULTS_DIR)
    if ref is None:
        return fallback
    try:
        return max(float(fallback), float(ref.events_path.stat().st_mtime))
    except OSError:
        return fallback
