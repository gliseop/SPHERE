"""Тесты пакетного запуска симуляций."""

import json
import shutil
from pathlib import Path

import pytest
from magistry_sim.batch import BatchRunner
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.scenarios import get_scenario


@pytest.fixture
def output_dir(tmp_path):
    """Временная директория для результатов."""
    return tmp_path / "batch_output"


class TestBatchRunner:
    """Тесты BatchRunner."""

    def test_runs_correct_number_of_simulations(self, output_dir):
        """Количество запусков = N * len(modes)."""
        scenario = get_scenario(ScenarioId.S0)
        modes = [GovernanceMode.G0, GovernanceMode.G2]
        runner = BatchRunner(
            scenario=scenario,
            modes=modes,
            runs_per_mode=3,
            output_dir=output_dir,
        )
        results = runner.run()
        assert len(results) == 6  # 3 * 2

    def test_creates_directory_structure(self, output_dir):
        """Результаты сохраняются в {output_dir}/{mode}/run_{i}/."""
        scenario = get_scenario(ScenarioId.S0)
        modes = [GovernanceMode.G0]
        runner = BatchRunner(
            scenario=scenario,
            modes=modes,
            runs_per_mode=2,
            output_dir=output_dir,
        )
        runner.run()

        assert (output_dir / "G0" / "run_0").is_dir()
        assert (output_dir / "G0" / "run_1").is_dir()

    def test_saves_events_jsonl(self, output_dir):
        """Каждый запуск сохраняет events.jsonl."""
        scenario = get_scenario(ScenarioId.S0)
        runner = BatchRunner(
            scenario=scenario,
            modes=[GovernanceMode.G0],
            runs_per_mode=1,
            output_dir=output_dir,
        )
        runner.run()

        events_path = output_dir / "G0" / "run_0" / "events.jsonl"
        assert events_path.exists()

    def test_saves_metrics_json(self, output_dir):
        """Каждый запуск сохраняет metrics.json."""
        scenario = get_scenario(ScenarioId.S0)
        runner = BatchRunner(
            scenario=scenario,
            modes=[GovernanceMode.G0],
            runs_per_mode=1,
            output_dir=output_dir,
        )
        runner.run()

        metrics_path = output_dir / "G0" / "run_0" / "metrics.json"
        assert metrics_path.exists()
        with open(metrics_path) as f:
            data = json.load(f)
        assert "total_cases" in data
        assert "rounds" in data

    def test_different_seeds_per_run(self, output_dir):
        """Каждый запуск использует уникальный seed."""
        scenario = get_scenario(ScenarioId.S0)
        runner = BatchRunner(
            scenario=scenario,
            modes=[GovernanceMode.G0],
            runs_per_mode=3,
            output_dir=output_dir,
            base_seed=100,
        )
        results = runner.run()
        seeds = [r.seed for r in results]
        assert len(set(seeds)) == 3  # все разные

    def test_base_seed_reproducibility(self, output_dir):
        """Одинаковый base_seed даёт одинаковые результаты."""
        scenario = get_scenario(ScenarioId.S0)
        runner1 = BatchRunner(
            scenario=scenario,
            modes=[GovernanceMode.G0],
            runs_per_mode=1,
            output_dir=output_dir,
            base_seed=42,
        )
        results1 = runner1.run()

        output_dir2 = output_dir.parent / "batch_output2"
        runner2 = BatchRunner(
            scenario=scenario,
            modes=[GovernanceMode.G0],
            runs_per_mode=1,
            output_dir=output_dir2,
            base_seed=42,
        )
        results2 = runner2.run()

        assert results1[0].seed == results2[0].seed
        assert (
            results1[0].rounds_completed == results2[0].rounds_completed
        )

    def test_single_mode_single_run(self, output_dir):
        """Минимальный запуск: 1 режим, 1 прогон."""
        scenario = get_scenario(ScenarioId.S0)
        runner = BatchRunner(
            scenario=scenario,
            modes=[GovernanceMode.G0],
            runs_per_mode=1,
            output_dir=output_dir,
        )
        results = runner.run()
        assert len(results) == 1
        assert results[0].governance == "G0"
