"""Тесты для web.backend.runner — обнаружение внешних прогонов."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from web.backend import runner
from web.backend.run_artifacts import parse_run_name


@pytest.fixture()
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Подготовить временную директорию results и подменить _RESULTS_DIR."""
    monkeypatch.setattr(runner, "_RESULTS_DIR", tmp_path)
    # Очистить глобальное состояние между тестами
    runner._active.clear()
    runner._active_started_at.clear()
    runner._reserved_run_names.clear()
    return tmp_path


def _write_status(path: Path, *, state: str = "running", pid: int = 0, updated_at: float | None = None) -> None:
    ts = time.time() if updated_at is None else updated_at
    path.write_text(
        json.dumps(
            {
                "state": state,
                "pid": pid,
                "tick": 0,
                "updated_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "error": None,
            }
        ),
        encoding="utf-8",
    )


# ---------- _discover_external_runs ----------


class TestDiscoverExternalRuns:
    """Тесты для _discover_external_runs()."""

    def test_empty_dir(self, results_dir: Path):
        """Пустая директория — нет внешних прогонов."""
        assert runner._discover_external_runs() == []

    def test_fresh_events_with_running_status_detected(self, results_dir: Path):
        """Свежий status-sidecar делает legacy-run видимым как внешний running."""
        events = results_dir / "S2_G1_seed1_cognitive_events.jsonl"
        events.write_text('{"type": "action"}\n')
        _write_status(results_dir / "S2_G1_seed1_cognitive_status.json")

        runs = runner._discover_external_runs()
        assert len(runs) == 1
        assert runs[0]["run_name"] == "S2_G1_seed1_cognitive"
        assert runs[0]["status"] == "running"
        assert runs[0]["external"] is True
        assert runs[0]["pid"] == 0

    def test_fresh_directory_events_with_running_status_detected(self, results_dir: Path):
        """Свежий directory-based status-sidecar делает прогон видимым как внешний running."""
        run_dir = results_dir / "lc_run"
        run_dir.mkdir()
        events = run_dir / "events.jsonl"
        events.write_text('{"type": "action"}\n')
        _write_status(run_dir / "status.json")

        runs = runner._discover_external_runs()
        assert len(runs) == 1
        assert runs[0]["run_name"] == "lc_run"
        assert runs[0]["status"] == "running"
        assert runs[0]["external"] is True
        assert runs[0]["pid"] == 0

    def test_launcher_directory_run_recovered_after_restart(self, results_dir: Path):
        """Directory-run с _input_scenario.json должен переобнаруживаться после рестарта backend."""
        run_dir = results_dir / "web_run"
        run_dir.mkdir()
        (run_dir / "events.jsonl").write_text('{"type": "action"}\n', encoding="utf-8")
        (run_dir / "_input_scenario.json").write_text('{"title": "web run"}\n', encoding="utf-8")
        _write_status(run_dir / "status.json", pid=4242)

        runs = runner._discover_external_runs()
        assert len(runs) == 1
        assert runs[0]["run_name"] == "web_run"
        assert runs[0]["pid"] == 4242
        assert runs[0]["status"] == "running"
        assert runs[0]["external"] is True
        assert runs[0]["stop_supported"] is False
        assert runs[0]["activity_at"] > 0.0

    def test_events_without_status_are_not_treated_as_running(self, results_dir: Path):
        """Одного свежего events.jsonl больше недостаточно для статуса running."""
        events = results_dir / "no_status_events.jsonl"
        events.write_text('{"type": "action"}\n')

        assert runner._discover_external_runs() == []

    def test_events_with_summary_ignored(self, results_dir: Path):
        """Если summary-файл существует — прогон считается завершённым."""
        events = results_dir / "run1_events.jsonl"
        events.write_text('{"type": "action"}\n')
        _write_status(results_dir / "run1_status.json")
        summary = results_dir / "run1_summary.json"
        summary.write_text("{}")

        assert runner._discover_external_runs() == []

    def test_stale_status_ignored(self, results_dir: Path):
        """Если heartbeat в status-sidecar устарел — не считать прогон живым."""
        events = results_dir / "old_run_events.jsonl"
        events.write_text('{"type": "action"}\n')
        old_time = time.time() - (runner._EXTERNAL_ALIVE_THRESHOLD + 5)
        _write_status(results_dir / "old_run_status.json", updated_at=old_time)

        assert runner._discover_external_runs() == []

    def test_api_run_not_duplicated(self, results_dir: Path):
        """Прогон, зарегистрированный в _active, не дублируется."""
        events = results_dir / "api_run_events.jsonl"
        events.write_text('{"type": "action"}\n')
        _write_status(results_dir / "api_run_status.json")

        # Имитировать присутствие в _active
        runner._active["api_run"] = object()  # type: ignore[assignment]

        try:
            assert runner._discover_external_runs() == []
        finally:
            runner._active.clear()

    def test_multiple_external_runs(self, results_dir: Path):
        """Несколько внешних прогонов обнаруживаются одновременно."""
        for name in ("run_a", "run_b", "run_c"):
            (results_dir / f"{name}_events.jsonl").write_text("{}\n")
            _write_status(results_dir / f"{name}_status.json")

        runs = runner._discover_external_runs()
        found = {r["run_name"] for r in runs}
        assert found == {"run_a", "run_b", "run_c"}

    def test_nonexistent_results_dir(self, monkeypatch: pytest.MonkeyPatch):
        """Если директория results не существует — пустой список."""
        monkeypatch.setattr(runner, "_RESULTS_DIR", Path("/nonexistent_dir_abc"))
        assert runner._discover_external_runs() == []


# ---------- list_active ----------


class TestListActiveWithExternal:
    """Тесты для list_active() с учётом внешних прогонов."""

    def test_list_active_includes_external(self, results_dir: Path):
        """list_active() возвращает внешние прогоны вместе с API-запущенными."""
        events = results_dir / "ext_run_events.jsonl"
        events.write_text('{"type": "action"}\n')
        _write_status(results_dir / "ext_run_status.json")

        result = runner.list_active()
        external = [r for r in result if r.get("external")]
        assert len(external) == 1
        assert external[0]["run_name"] == "ext_run"

    def test_list_active_empty_when_no_runs(self, results_dir: Path):
        """Без прогонов — пустой список."""
        assert runner.list_active() == []

    def test_list_active_sorts_running_runs_by_activity(self, results_dir: Path):
        """list_active() отдаёт running-прогоны в порядке старения, чтобы последний был самым свежим."""
        class _Proc:
            def __init__(self, pid: int):
                self.pid = pid

            def poll(self):
                return None

        now = time.time()
        runner._active["api_old"] = _Proc(1001)  # type: ignore[assignment]
        runner._active_started_at["api_old"] = now - 30

        runner._active["api_new"] = _Proc(1002)  # type: ignore[assignment]
        runner._active_started_at["api_new"] = now - 5

        old_ext = results_dir / "ext_old_events.jsonl"
        old_ext.write_text("{}\n")
        new_ext = results_dir / "ext_new_events.jsonl"
        new_ext.write_text("{}\n")
        _write_status(results_dir / "ext_old_status.json", updated_at=now - 20)
        _write_status(results_dir / "ext_new_status.json", updated_at=now - 10)

        running = [item["run_name"] for item in runner.list_active() if item["status"] == "running"]
        assert running == ["api_old", "ext_old", "ext_new", "api_new"]

    def test_list_active_does_not_reclassify_finished_api_run_as_external(self, results_dir: Path):
        """Упавший launcher-run не должен возвращаться как внешний «running»."""

        class _FinishedProc:
            pid = 2001

            def poll(self):
                return 1

        run_name = "failed_run"
        run_dir = results_dir / run_name
        run_dir.mkdir()
        (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (run_dir / "_input_scenario.json").write_text("{}", encoding="utf-8")
        runner._active[run_name] = _FinishedProc()  # type: ignore[assignment]
        runner._active_started_at[run_name] = time.time()

        first = runner.list_active()
        assert first == [
            {
                "run_name": run_name,
                "pid": 2001,
                "status": "finished",
                "external": False,
                "stop_supported": True,
                "returncode": 1,
                "stdout_log": f"{run_name}_stdout.log",
                "stderr_log": f"{run_name}_stderr.log",
                "activity_at": pytest.approx((run_dir / "events.jsonl").stat().st_mtime),
            }
        ]
        assert runner.list_active() == []


# ---------- is_external_run ----------


class TestIsExternalRun:
    """Тесты для is_external_run()."""

    def test_true_for_external(self, results_dir: Path):
        """Возвращает True для обнаруженного внешнего прогона."""
        events = results_dir / "ext_events.jsonl"
        events.write_text("{}\n")
        _write_status(results_dir / "ext_status.json")

        assert runner.is_external_run("ext") is True

    def test_false_for_unknown(self, results_dir: Path):
        """Возвращает False для несуществующего прогона."""
        assert runner.is_external_run("nonexistent") is False


# ---------- stop_simulation ----------


class TestStopExternalRun:
    """Тесты для stop_simulation() с внешними прогонами."""

    def test_stop_external_raises(self, results_dir: Path):
        """Попытка остановить внешний прогон вызывает RuntimeError."""
        events = results_dir / "ext_stop_events.jsonl"
        events.write_text("{}\n")
        _write_status(results_dir / "ext_stop_status.json")

        with pytest.raises(RuntimeError, match="Невозможно остановить внешний прогон"):
            runner.stop_simulation("ext_stop")

    def test_stop_unknown_returns_none(self, results_dir: Path):
        """Остановка несуществующего прогона возвращает None."""
        assert runner.stop_simulation("ghost") is None


class _FakePopen:
    def __init__(self, cmd, cwd=None, env=None, stdout=None, stderr=None, text=None):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.stdout = stdout
        self.stderr = stderr
        self.text = text
        self.pid = 4321
        self._returncode = None

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self._returncode = 0

    def kill(self):
        self._returncode = -9


class TestLaunchSimulation:
    """Тесты для запуска SPHERE-LC через subprocess."""

    def test_launch_simulation_from_config_writes_input_and_registers_process(self, results_dir: Path):
        """launch_simulation_from_config создаёт input-config и регистрирует процесс."""
        with patch("web.backend.runner.subprocess.Popen", side_effect=_FakePopen):
            result = runner.launch_simulation_from_config(
                scenario_config={"id": "S1", "agents": []},
                governance="G1",
                seed=7,
                rounds=5,
            )

        run_name = result["run_name"]
        assert result["pid"] == 4321
        run_dir = results_dir / run_name
        assert run_dir.exists()
        assert (run_dir / "_input_scenario.json").exists()
        assert run_name in runner._active
        proc = runner._active[run_name]
        assert proc.cmd[:3] == [runner.sys.executable, "-m", "sphere_lc.cli"]

    def test_launch_simulation_from_config_applies_parallel_runtime_overrides(self, results_dir: Path):
        """Параллельные параметры попадают и в input-config, и в env subprocess."""
        with patch("web.backend.runner.subprocess.Popen", side_effect=_FakePopen):
            result = runner.launch_simulation_from_config(
                scenario_config={"id": "S1", "agents": []},
                governance="G1",
                seed=7,
                rounds=5,
                parallel_agents=False,
                parallel_workers=3,
                parallel_window=90.0,
            )

        run_name = result["run_name"]
        run_dir = results_dir / run_name
        payload = json.loads((run_dir / "_input_scenario.json").read_text(encoding="utf-8"))
        proc = runner._active[run_name]
        assert payload["runtime"]["parallel_agents"] is False
        assert payload["runtime"]["parallel_workers"] == 3
        assert payload["runtime"]["parallel_window_seconds"] == 90.0
        assert proc.env["SPHERE_PARALLEL_AGENTS"] == "0"
        assert proc.env["SPHERE_PARALLEL_WORKERS"] == "3"
        assert proc.env["SPHERE_PARALLEL_WINDOW"] == "90.0"

    def test_launch_simulation_uses_template_loader(self, results_dir: Path):
        """launch_simulation загружает template-конфиг и делегирует в config-launcher."""
        cfg = {
            "title": "S1 template",
            "ticks": 4,
            "seed": 42,
            "agents": [],
            "world": {},
            "llm": {},
            "memory": {},
            "runtime": {},
            "governance": {},
        }

        with patch("web.backend.runner.subprocess.Popen", side_effect=_FakePopen):
            with patch(
                "web.backend.routes.scenarios.load_template_config_for_web",
                return_value=type("Cfg", (), {"model_dump": lambda self, mode="json": cfg})(),
            ):
                result = runner.launch_simulation(
                    scenario="S1",
                    governance="G1",
                    seed=11,
                    rounds=6,
                )

        assert result["pid"] == 4321
        assert result["run_name"].startswith("S1_G1_seed11")

    def test_launch_simulation_respects_limit(self, results_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """При превышении лимита concurrent-runs выбрасывается TooManyRunsError."""
        monkeypatch.setattr(runner, "_MAX_RUNNING", 1)
        busy = _FakePopen([])
        runner._active["busy"] = busy  # type: ignore[assignment]

        with pytest.raises(runner.TooManyRunsError):
            runner.launch_simulation_from_config(
                scenario_config={"id": "S1", "agents": []},
                governance="G1",
            )

    def test_launch_simulation_skips_reserved_run_name(self, results_dir: Path):
        """Зарезервированное имя не должно переиспользоваться вторым запуском."""
        runner._reserved_run_names.add("S1_G1_seed7_cognitive")

        with patch("web.backend.runner.subprocess.Popen", side_effect=_FakePopen):
            result = runner.launch_simulation_from_config(
                scenario_config={"id": "S1", "agents": []},
                governance="G1",
                seed=7,
                rounds=5,
            )

        assert result["run_name"] == "S1_G1_seed7_cognitive_2"

    def test_launch_simulation_releases_reservation_on_popen_failure(self, results_dir: Path):
        """При ошибке старта резервирование и временные артефакты удаляются."""
        with patch("web.backend.runner.subprocess.Popen", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                runner.launch_simulation_from_config(
                    scenario_config={"id": "S1", "agents": []},
                    governance="G1",
                    seed=7,
                )

        assert runner._reserved_run_names == set()
        assert not (results_dir / "S1_G1_seed7_cognitive").exists()
        assert not (results_dir / "S1_G1_seed7_cognitive_stdout.log").exists()
        assert not (results_dir / "S1_G1_seed7_cognitive_stderr.log").exists()


def test_parse_run_name_uses_rightmost_governance_suffix():
    """Сценарная часть имени может содержать G<n>-фрагменты."""
    meta = parse_run_name("My_G2_experiment_G1_seed42_web")
    assert meta == {
        "scenario": "My_G2_experiment",
        "governance": "G1",
        "seed": 42,
        "variant": "web",
    }
