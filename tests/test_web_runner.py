"""Тесты для web.backend.runner — обнаружение внешних прогонов."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from web.backend import runner


@pytest.fixture()
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Подготовить временную директорию results и подменить _RESULTS_DIR."""
    monkeypatch.setattr(runner, "_RESULTS_DIR", tmp_path)
    # Очистить глобальное состояние между тестами
    runner._active.clear()
    return tmp_path


# ---------- _discover_external_runs ----------


class TestDiscoverExternalRuns:
    """Тесты для _discover_external_runs()."""

    def test_empty_dir(self, results_dir: Path):
        """Пустая директория — нет внешних прогонов."""
        assert runner._discover_external_runs() == []

    def test_fresh_events_no_summary(self, results_dir: Path):
        """Свежий events-файл без summary — внешний прогон обнаружен."""
        events = results_dir / "S2_G1_seed1_cognitive_events.jsonl"
        events.write_text('{"type": "action"}\n')

        runs = runner._discover_external_runs()
        assert len(runs) == 1
        assert runs[0]["run_name"] == "S2_G1_seed1_cognitive"
        assert runs[0]["status"] == "running"
        assert runs[0]["external"] is True
        assert runs[0]["pid"] == 0

    def test_fresh_directory_events_no_summary(self, results_dir: Path):
        """Свежий directory-based events-файл без summary — прогон обнаружен."""
        run_dir = results_dir / "lc_run"
        run_dir.mkdir()
        events = run_dir / "events.jsonl"
        events.write_text('{"type": "action"}\n')

        runs = runner._discover_external_runs()
        assert len(runs) == 1
        assert runs[0]["run_name"] == "lc_run"
        assert runs[0]["status"] == "running"
        assert runs[0]["external"] is True
        assert runs[0]["pid"] == 0

    def test_events_with_summary_ignored(self, results_dir: Path):
        """Если summary-файл существует — прогон считается завершённым."""
        events = results_dir / "run1_events.jsonl"
        events.write_text('{"type": "action"}\n')
        summary = results_dir / "run1_summary.json"
        summary.write_text("{}")

        assert runner._discover_external_runs() == []

    def test_stale_events_ignored(self, results_dir: Path):
        """Если файл не обновлялся дольше порога — не считать живым."""
        events = results_dir / "old_run_events.jsonl"
        events.write_text('{"type": "action"}\n')
        # Сделать файл «старым»: mtime < now - _EXTERNAL_ALIVE_THRESHOLD
        import os

        old_time = time.time() - (runner._EXTERNAL_ALIVE_THRESHOLD + 5)
        os.utime(events, (old_time, old_time))

        assert runner._discover_external_runs() == []

    def test_api_run_not_duplicated(self, results_dir: Path):
        """Прогон, зарегистрированный в _active, не дублируется."""
        events = results_dir / "api_run_events.jsonl"
        events.write_text('{"type": "action"}\n')

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

        result = runner.list_active()
        external = [r for r in result if r.get("external")]
        assert len(external) == 1
        assert external[0]["run_name"] == "ext_run"

    def test_list_active_empty_when_no_runs(self, results_dir: Path):
        """Без прогонов — пустой список."""
        assert runner.list_active() == []


# ---------- is_external_run ----------


class TestIsExternalRun:
    """Тесты для is_external_run()."""

    def test_true_for_external(self, results_dir: Path):
        """Возвращает True для обнаруженного внешнего прогона."""
        events = results_dir / "ext_events.jsonl"
        events.write_text("{}\n")

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

        with pytest.raises(RuntimeError, match="Невозможно остановить внешний прогон"):
            runner.stop_simulation("ext_stop")

    def test_stop_unknown_returns_none(self, results_dir: Path):
        """Остановка несуществующего прогона возвращает None."""
        assert runner.stop_simulation("ghost") is None


class TestLaunchSimulationDisabled:
    """Тесты для отключённых legacy-launch функций."""

    def test_launch_simulation_from_config_disabled(self):
        """launch_simulation_from_config возвращает явную ошибку миграции."""
        with pytest.raises(RuntimeError, match="legacy launcher magistry_sim удалён"):
            runner.launch_simulation_from_config(
                scenario_config={"id": "S1", "agents": []},
                governance="G1",
            )

    def test_launch_simulation_disabled(self):
        """launch_simulation возвращает явную ошибку миграции."""
        with pytest.raises(RuntimeError, match="legacy launcher magistry_sim удалён"):
            runner.launch_simulation(
                scenario="S1",
                governance="G1",
            )
