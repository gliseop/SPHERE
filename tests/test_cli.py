"""Тесты CLI."""

import os
import subprocess
import sys

import pytest


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

    def test_run_requires_api_key(self):
        env = dict(os.environ)
        env["OPENAI_API_KEY"] = ""
        result = subprocess.run(
            [
                sys.executable, "-m", "magistry_sim.cli",
                "--scenario", "S0",
                "--governance", "G0",
                "--runner", "cognitive",
                "--rounds", "1",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        out = (result.stdout or "") + "\n" + (result.stderr or "")
        assert "OPENAI_API_KEY is not set" in out

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
