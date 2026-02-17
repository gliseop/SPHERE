"""Валидация метрик симуляции против эмпирических данных."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BenchmarkRange:
    """Эмпирический диапазон для одной метрики.

    Attributes:
        metric: Название метрики.
        low: Нижняя граница допустимого диапазона.
        high: Верхняя граница допустимого диапазона.
        source: Источник данных (публикация, организация).
        governance: Режим управления, к которому привязан диапазон.
    """

    metric: str
    low: float
    high: float
    source: str
    governance: str = ""

    def contains(self, value: float) -> bool:
        """Проверить, попадает ли значение в диапазон.

        Args:
            value: Проверяемое значение.

        Returns:
            True, если значение в пределах [low, high].
        """
        return self.low <= value <= self.high


@dataclass
class ValidationResult:
    """Результат проверки одной метрики.

    Attributes:
        metric: Название метрики.
        actual: Фактическое значение (None, если метрика отсутствует).
        low: Нижняя граница диапазона.
        high: Верхняя граница диапазона.
        within_range: Попадает ли значение в диапазон.
        source: Источник эмпирических данных.
    """

    metric: str
    actual: float | None
    low: float
    high: float
    within_range: bool
    source: str


@dataclass
class ValidationReport:
    """Отчёт валидации метрик против эмпирических данных.

    Attributes:
        results: Список результатов по каждой метрике.
    """

    results: list[ValidationResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Общее число проверенных метрик."""
        return len(self.results)

    @property
    def passed(self) -> int:
        """Число метрик, попавших в эмпирические диапазоны."""
        return sum(1 for r in self.results if r.within_range)

    @property
    def failed(self) -> int:
        """Число метрик, не попавших в диапазоны."""
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        """Доля метрик, прошедших валидацию."""
        if self.total == 0:
            return 0.0
        return self.passed / self.total


def validate_against_empirical(
    metrics: dict[str, float],
    benchmarks: list[BenchmarkRange],
) -> ValidationReport:
    """Сравнить метрики симуляции с эмпирическими диапазонами.

    Для каждого диапазона из benchmarks ищет соответствующую метрику
    в словаре metrics и проверяет попадание в диапазон. Отсутствующие
    метрики помечаются как не прошедшие валидацию.

    Args:
        metrics: Словарь {название метрики: значение}.
        benchmarks: Список эмпирических диапазонов.

    Returns:
        Отчёт валидации.
    """
    results: list[ValidationResult] = []

    for bench in benchmarks:
        actual = metrics.get(bench.metric)
        if actual is None:
            results.append(ValidationResult(
                metric=bench.metric,
                actual=None,
                low=bench.low,
                high=bench.high,
                within_range=False,
                source=bench.source,
            ))
        else:
            results.append(ValidationResult(
                metric=bench.metric,
                actual=actual,
                low=bench.low,
                high=bench.high,
                within_range=bench.contains(actual),
                source=bench.source,
            ))

    return ValidationReport(results=results)


def load_benchmarks(path: Path) -> list[BenchmarkRange]:
    """Загрузить эмпирические диапазоны из JSON-файла.

    Args:
        path: Путь к файлу с диапазонами.

    Returns:
        Список эмпирических диапазонов.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return [
        BenchmarkRange(
            metric=item["metric"],
            low=item["low"],
            high=item["high"],
            source=item.get("source", ""),
            governance=item.get("governance", ""),
        )
        for item in data
    ]
