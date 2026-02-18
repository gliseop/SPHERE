"""Валидация метрик симуляции против эмпирических данных."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from scipy.stats import mannwhitneyu as _mannwhitneyu
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False


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


@dataclass
class DirectionalHypothesis:
    """Направленная гипотеза о сравнении двух условий.

    Attributes:
        name: Краткое название гипотезы.
        description: Описание на русском языке.
        condition_a: Ключ первого условия (например "S1/G0").
        condition_b: Ключ второго условия (например "S1/G2").
        metric: Название метрики для сравнения.
        direction: "greater" если A > B ожидается, "less" если A < B.
        source: Теоретическое обоснование.
    """

    name: str
    description: str
    condition_a: str
    condition_b: str
    metric: str
    direction: str
    source: str


@dataclass
class HypothesisTestResult:
    """Результат проверки одной гипотезы.

    Attributes:
        hypothesis: Проверяемая гипотеза.
        value_a: Среднее значение метрики для условия A.
        value_b: Среднее значение метрики для условия B.
        confirmed: Подтверждена ли гипотеза (A > B или A < B согласно direction).
        n_a: Число наблюдений в условии A.
        n_b: Число наблюдений в условии B.
        p_value: p-значение теста Манна-Уитни (None если n < 2 в любом условии).
    """

    hypothesis: DirectionalHypothesis
    value_a: float
    value_b: float
    confirmed: bool
    n_a: int
    n_b: int
    p_value: Optional[float]


STANDARD_HYPOTHESES: list[DirectionalHypothesis] = [
    DirectionalHypothesis(
        name="H1",
        description=(
            "Коррупционный сценарий с аудитором без полномочий (S1/G0) порождает "
            "больше приватных переговоров, чем с аудитором с полномочиями (S1/G2)"
        ),
        condition_a="S1/G0",
        condition_b="S1/G2",
        metric="private_message_ratio",
        direction="greater",
        source=(
            "Теория наблюдаемости: аудитор с полномочиями снижает скрытую "
            "коммуникацию при сговоре"
        ),
    ),
    DirectionalHypothesis(
        name="H2",
        description=(
            "Коррупционный сценарий без аудитора (S1/G0) порождает больше "
            "приватных переговоров, чем чистый сценарий (S0/G0)"
        ),
        condition_a="S1/G0",
        condition_b="S0/G0",
        metric="private_message_ratio",
        direction="greater",
        source=(
            "Коррупционный сценарий порождает больше приватных переговоров, "
            "чем чистый"
        ),
    ),
    DirectionalHypothesis(
        name="H3",
        description=(
            "В коррупционном сценарии без аудитора (S2/G0) нарушений больше, "
            "чем при наличии аудитора без санкций (S2/G1)"
        ),
        condition_a="S2/G0",
        condition_b="S2/G1",
        metric="violations_total",
        direction="greater",
        source=(
            "Эффект сдерживания: присутствие аудитора предотвращает нарушения "
            "даже без санкций"
        ),
    ),
    DirectionalHypothesis(
        name="H4",
        description=(
            "Аудитор с полномочиями (S1/G2) обнаруживает нарушения эффективнее "
            "рекомендательного аудитора (S1/G1)"
        ),
        condition_a="S1/G2",
        condition_b="S1/G1",
        metric="f1",
        direction="greater",
        source=(
            "Аудитор с полномочиями обнаруживает нарушения эффективнее "
            "рекомендательного"
        ),
    ),
]


def test_directional_hypotheses(
    runs_by_condition: dict[str, list[dict]],
) -> list[HypothesisTestResult]:
    """Проверить направленные гипотезы на наборе прогонов симуляции.

    Для каждой гипотезы из STANDARD_HYPOTHESES извлекает значения целевой
    метрики из прогонов условий A и B, вычисляет средние и проверяет
    направление. Если в обоих условиях не менее двух наблюдений —
    дополнительно вычисляет p-значение критерия Манна-Уитни (односторонний).
    При недоступном scipy используется только сравнение средних.

    Args:
        runs_by_condition: Словарь {ключ_условия: список метрик прогона}.
            Ключ условия — строка вида "S1/G0", "S2/G1" и т.д.
            Каждый элемент списка — dict с числовыми метриками прогона
            (private_message_ratio, f1, violations_total, и т.д.)

    Returns:
        Список результатов проверки гипотез.
    """
    results: list[HypothesisTestResult] = []

    for hyp in STANDARD_HYPOTHESES:
        runs_a = runs_by_condition.get(hyp.condition_a, [])
        runs_b = runs_by_condition.get(hyp.condition_b, [])

        values_a = [r[hyp.metric] for r in runs_a if hyp.metric in r]
        values_b = [r[hyp.metric] for r in runs_b if hyp.metric in r]

        mean_a = sum(values_a) / len(values_a) if values_a else 0.0
        mean_b = sum(values_b) / len(values_b) if values_b else 0.0

        if hyp.direction == "greater":
            confirmed = mean_a > mean_b
        else:
            confirmed = mean_a < mean_b

        p_value: Optional[float] = None
        if _SCIPY_AVAILABLE and len(values_a) >= 2 and len(values_b) >= 2:
            alternative = (
                "greater" if hyp.direction == "greater" else "less"
            )
            try:
                stat_result = _mannwhitneyu(
                    values_a, values_b, alternative=alternative
                )
                p_value = float(stat_result.pvalue)
            except ValueError:
                p_value = None

        results.append(
            HypothesisTestResult(
                hypothesis=hyp,
                value_a=mean_a,
                value_b=mean_b,
                confirmed=confirmed,
                n_a=len(values_a),
                n_b=len(values_b),
                p_value=p_value,
            )
        )

    return results


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
