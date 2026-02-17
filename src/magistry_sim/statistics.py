"""Статистическое сравнение режимов governance."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from scipy.stats import mannwhitneyu


@dataclass
class ModeStats:
    """Описательная статистика одного режима governance.

    Attributes:
        mean: Среднее значение метрики.
        std: Стандартное отклонение.
        n: Количество наблюдений.
        ci_lower: Нижняя граница доверительного интервала.
        ci_upper: Верхняя граница доверительного интервала.
    """

    mean: float = 0.0
    std: float = 0.0
    n: int = 0
    ci_lower: float = 0.0
    ci_upper: float = 0.0


@dataclass
class PairwiseTest:
    """Результат попарного статистического теста.

    Attributes:
        mode_a: Первый режим.
        mode_b: Второй режим.
        statistic: Значение U-статистики.
        p_value: Исходное p-значение.
        p_value_corrected: p-значение с поправкой Бонферрони.
    """

    mode_a: str = ""
    mode_b: str = ""
    statistic: float = 0.0
    p_value: float = 0.0
    p_value_corrected: float = 0.0


@dataclass
class ComparisonReport:
    """Отчёт сравнения режимов governance.

    Attributes:
        mode_stats: Описательная статистика по каждому режиму.
        pairwise_tests: Результаты попарных тестов.
    """

    mode_stats: dict[str, ModeStats] = field(default_factory=dict)
    pairwise_tests: list[PairwiseTest] = field(default_factory=list)


def bootstrap_ci(
    values: list[float],
    confidence: float = 0.95,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    """Вычислить доверительный интервал методом bootstrap.

    Args:
        values: Набор наблюдений.
        confidence: Уровень доверия (0.0-1.0).
        n_bootstrap: Количество bootstrap-выборок.
        seed: Зерно генератора для воспроизводимости.

    Returns:
        Кортеж (нижняя граница, верхняя граница).
    """
    if len(values) <= 1:
        val = values[0] if values else 0.0
        return val, val

    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []

    for _ in range(n_bootstrap):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    alpha = 1.0 - confidence
    lower_idx = int((alpha / 2) * n_bootstrap)
    upper_idx = int((1.0 - alpha / 2) * n_bootstrap) - 1
    lower_idx = max(0, lower_idx)
    upper_idx = min(n_bootstrap - 1, upper_idx)

    return means[lower_idx], means[upper_idx]


def compare_governance_modes(
    results_by_mode: dict[str, list[float]],
    confidence: float = 0.95,
    n_bootstrap: int = 1000,
) -> ComparisonReport:
    """Сравнить режимы governance по метрике.

    Вычисляет описательную статистику для каждого режима,
    проводит попарные тесты Манна-Уитни и применяет
    поправку Бонферрони для множественных сравнений.

    Args:
        results_by_mode: Словарь {режим: список значений метрики}.
        confidence: Уровень доверия для bootstrap.
        n_bootstrap: Количество bootstrap-выборок.

    Returns:
        Отчёт сравнения.
    """
    import math

    report = ComparisonReport()

    # Описательная статистика
    for mode, values in results_by_mode.items():
        n = len(values)
        mean = sum(values) / n if n > 0 else 0.0
        variance = sum((v - mean) ** 2 for v in values) / n if n > 0 else 0.0
        std = math.sqrt(variance)
        ci_lower, ci_upper = bootstrap_ci(
            values, confidence=confidence, n_bootstrap=n_bootstrap,
        )
        report.mode_stats[mode] = ModeStats(
            mean=mean,
            std=std,
            n=n,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
        )

    # Попарные тесты Манна-Уитни
    modes = sorted(results_by_mode.keys())
    pairs = list(combinations(modes, 2))
    n_comparisons = len(pairs)

    for mode_a, mode_b in pairs:
        values_a = results_by_mode[mode_a]
        values_b = results_by_mode[mode_b]

        stat, p_value = mannwhitneyu(
            values_a, values_b, alternative="two-sided",
        )
        p_corrected = min(p_value * n_comparisons, 1.0)

        report.pairwise_tests.append(PairwiseTest(
            mode_a=mode_a,
            mode_b=mode_b,
            statistic=stat,
            p_value=p_value,
            p_value_corrected=p_corrected,
        ))

    return report
