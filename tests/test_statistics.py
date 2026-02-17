"""Тесты статистического сравнения режимов governance."""

import pytest
from magistry_sim.statistics import (
    compare_governance_modes,
    bootstrap_ci,
    ComparisonReport,
    ModeStats,
    PairwiseTest,
)


class TestBootstrapCI:
    """Тесты доверительных интервалов bootstrap."""

    def test_bootstrap_ci_returns_tuple(self):
        """bootstrap_ci возвращает кортеж (lower, upper)."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        lo, hi = bootstrap_ci(values, confidence=0.95, n_bootstrap=500)
        assert lo < hi

    def test_bootstrap_ci_single_value(self):
        """При одном значении CI вырожден."""
        lo, hi = bootstrap_ci([5.0], confidence=0.95, n_bootstrap=100)
        assert lo == hi == 5.0

    def test_bootstrap_ci_contains_mean(self):
        """Доверительный интервал содержит среднее."""
        values = list(range(1, 101))
        lo, hi = bootstrap_ci(
            [float(v) for v in values], confidence=0.95, n_bootstrap=1000,
        )
        mean = sum(values) / len(values)
        assert lo <= mean <= hi


class TestCompareGovernanceModes:
    """Тесты сравнения режимов governance."""

    def test_compare_two_modes(self):
        """Сравнение двух режимов возвращает ComparisonReport."""
        results_by_mode = {
            "G0": [0.8, 0.7, 0.9, 0.6, 0.85, 0.75, 0.65, 0.95, 0.7, 0.8],
            "G3": [0.3, 0.2, 0.4, 0.1, 0.35, 0.25, 0.15, 0.45, 0.2, 0.3],
        }
        report = compare_governance_modes(results_by_mode)
        assert isinstance(report, ComparisonReport)
        assert len(report.mode_stats) == 2
        assert len(report.pairwise_tests) == 1

    def test_mode_stats_computed(self):
        """Для каждого режима вычисляется среднее и доверительный интервал."""
        results_by_mode = {
            "G0": [0.5, 0.6, 0.7, 0.8, 0.9],
            "G2": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
        report = compare_governance_modes(results_by_mode)
        g0_stats = report.mode_stats["G0"]
        assert isinstance(g0_stats, ModeStats)
        assert g0_stats.mean == pytest.approx(0.7, rel=1e-6)
        assert g0_stats.ci_lower < g0_stats.ci_upper

    def test_pairwise_test_structure(self):
        """Попарный тест содержит p-value и скорректированный p-value."""
        results_by_mode = {
            "G0": [0.8, 0.7, 0.9, 0.85, 0.75],
            "G3": [0.2, 0.3, 0.1, 0.25, 0.15],
        }
        report = compare_governance_modes(results_by_mode)
        test = report.pairwise_tests[0]
        assert isinstance(test, PairwiseTest)
        assert 0.0 <= test.p_value <= 1.0
        assert 0.0 <= test.p_value_corrected <= 1.0

    def test_bonferroni_correction_applied(self):
        """Поправка Бонферрони увеличивает p-value при нескольких сравнениях."""
        results_by_mode = {
            "G0": [0.8, 0.7, 0.9, 0.85, 0.75],
            "G2": [0.5, 0.4, 0.6, 0.55, 0.45],
            "G3": [0.2, 0.3, 0.1, 0.25, 0.15],
        }
        report = compare_governance_modes(results_by_mode)
        # 3 режима → 3 пары → поправка × 3
        assert len(report.pairwise_tests) == 3
        for test in report.pairwise_tests:
            assert test.p_value_corrected >= test.p_value

    def test_three_modes_all_pairs(self):
        """Три режима дают 3 попарных сравнения."""
        results_by_mode = {
            "G0": [1.0, 2.0, 3.0, 4.0, 5.0],
            "G2": [2.0, 3.0, 4.0, 5.0, 6.0],
            "G3": [3.0, 4.0, 5.0, 6.0, 7.0],
        }
        report = compare_governance_modes(results_by_mode)
        assert len(report.pairwise_tests) == 3
        pairs = {(t.mode_a, t.mode_b) for t in report.pairwise_tests}
        assert ("G0", "G2") in pairs
        assert ("G0", "G3") in pairs
        assert ("G2", "G3") in pairs

    def test_single_mode_no_tests(self):
        """Один режим — нет попарных тестов."""
        results_by_mode = {"G0": [1.0, 2.0, 3.0]}
        report = compare_governance_modes(results_by_mode)
        assert len(report.pairwise_tests) == 0
        assert len(report.mode_stats) == 1

    def test_significant_difference_detected(self):
        """Значимое различие обнаруживается при явно разных распределениях."""
        results_by_mode = {
            "G0": [0.9, 0.85, 0.95, 0.88, 0.92, 0.87, 0.91, 0.93, 0.89, 0.86],
            "G3": [0.1, 0.15, 0.05, 0.12, 0.08, 0.13, 0.09, 0.07, 0.11, 0.14],
        }
        report = compare_governance_modes(results_by_mode)
        assert report.pairwise_tests[0].p_value < 0.05
