"""Тесты CLI."""

import subprocess
import sys


class TestCLI:
    def test_list_scenarios(self):
        result = subprocess.run(
            [sys.executable, "-m", "magistry_sim.cli", "--list-scenarios"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "S0" in result.stdout
        assert "S1" in result.stdout

    def test_run_s0_g0_mock(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "magistry_sim.cli",
                "--scenario", "S0",
                "--governance", "G0",
                "--mock",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Результаты" in result.stdout

    def test_run_s1_g3_mock(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "magistry_sim.cli",
                "--scenario", "S1",
                "--governance", "G3",
                "--mock",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_run_s0_runner_mock(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "magistry_sim.cli",
                "--scenario", "S0",
                "--governance", "G0",
                "--runner", "mock",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Результаты" in result.stdout

    def test_run_s0_runner_cognitive_mock(self):
        """cognitive runner с mock-провайдерами (без API-ключей)."""
        result = subprocess.run(
            [
                sys.executable, "-m", "magistry_sim.cli",
                "--scenario", "S0",
                "--governance", "G0",
                "--runner", "cognitive",
                "--rounds", "2",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Результаты" in result.stdout

    def test_unknown_scenario(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "magistry_sim.cli",
                "--scenario", "S99",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
