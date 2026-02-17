"""Пакетный запуск серий симуляций для экспериментов."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agents import MockAgentRunner
from .config import ScenarioConfig
from .enums import GovernanceMode
from .environment import Environment, SimulationResult
from .metrics import compute_metrics

logger = logging.getLogger(__name__)


class BatchRunner:
    """Запускает серию симуляций по режимам governance с разными seed.

    Для каждого режима выполняется N прогонов. Результаты сохраняются
    в структуру директорий: {output_dir}/{mode}/run_{i}/.

    Args:
        scenario: Конфигурация сценария.
        modes: Список режимов governance для тестирования.
        runs_per_mode: Количество прогонов на каждый режим.
        output_dir: Директория для сохранения результатов.
        base_seed: Базовое зерно для генерации seed каждого прогона.
        runner_factory: Фабрика runner-ов (по умолчанию MockAgentRunner).
    """

    def __init__(
        self,
        scenario: ScenarioConfig,
        modes: list[GovernanceMode],
        runs_per_mode: int = 10,
        output_dir: Path | None = None,
        base_seed: int = 42,
        runner_factory: Any = None,
    ) -> None:
        self._scenario = scenario
        self._modes = modes
        self._runs_per_mode = runs_per_mode
        self._output_dir = output_dir
        self._base_seed = base_seed
        self._runner_factory = runner_factory

    def _make_seed(self, mode_idx: int, run_idx: int) -> int:
        """Сгенерировать уникальный seed для прогона.

        Args:
            mode_idx: Индекс режима governance.
            run_idx: Индекс прогона.

        Returns:
            Уникальный seed.
        """
        return self._base_seed + mode_idx * 1000 + run_idx

    def _create_runner(self) -> Any:
        """Создать экземпляр runner-а.

        Returns:
            Runner для симуляции.
        """
        if self._runner_factory is not None:
            return self._runner_factory()
        return MockAgentRunner()

    def _save_run(
        self,
        mode: GovernanceMode,
        run_idx: int,
        result: SimulationResult,
    ) -> None:
        """Сохранить результаты отдельного прогона.

        Args:
            mode: Режим governance.
            run_idx: Индекс прогона.
            result: Результат симуляции.
        """
        if self._output_dir is None:
            return

        run_dir = self._output_dir / mode.value / f"run_{run_idx}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # events.jsonl
        events_path = run_dir / "events.jsonl"
        with open(events_path, "w", encoding="utf-8") as f:
            for event in result.events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # metrics.json
        metrics = compute_metrics(result)
        metrics_path = run_dir / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(asdict(metrics), f, ensure_ascii=False, indent=2)

    def run(self) -> list[SimulationResult]:
        """Запустить все симуляции.

        Returns:
            Список результатов всех прогонов.
        """
        results: list[SimulationResult] = []

        for mode_idx, mode in enumerate(self._modes):
            logger.info("Режим %s: запуск %d прогонов", mode.value, self._runs_per_mode)

            for run_idx in range(self._runs_per_mode):
                seed = self._make_seed(mode_idx, run_idx)
                runner = self._create_runner()

                env = Environment(
                    scenario=self._scenario,
                    governance=mode,
                    runner=runner,
                    seed=seed,
                )

                result = env.run()
                results.append(result)
                self._save_run(mode, run_idx, result)

                logger.info(
                    "  Прогон %d/%d (seed=%d): %d раундов",
                    run_idx + 1,
                    self._runs_per_mode,
                    seed,
                    result.rounds_completed,
                )

        return results
