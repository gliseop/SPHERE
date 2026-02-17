"""Тесты валидации метрик симуляции против эмпирических данных."""

import json
from pathlib import Path

import pytest
from magistry_sim.validation import (
    BenchmarkRange,
    ValidationReport,
    ValidationResult,
    load_benchmarks,
    validate_against_empirical,
)


class TestBenchmarkRange:
    """Тесты модели диапазона."""

    def test_value_within_range(self):
        r = BenchmarkRange(metric="corruption_rate", low=0.3, high=0.7, source="TI")
        assert r.contains(0.5)

    def test_value_below_range(self):
        r = BenchmarkRange(metric="corruption_rate", low=0.3, high=0.7, source="TI")
        assert not r.contains(0.1)

    def test_value_above_range(self):
        r = BenchmarkRange(metric="corruption_rate", low=0.3, high=0.7, source="TI")
        assert not r.contains(0.9)

    def test_boundary_values_included(self):
        r = BenchmarkRange(metric="test", low=0.0, high=1.0, source="test")
        assert r.contains(0.0)
        assert r.contains(1.0)


class TestValidateAgainstEmpirical:
    """Тесты validate_against_empirical."""

    def test_all_metrics_within_range(self):
        """Все метрики попадают в эмпирические диапазоны."""
        metrics = {
            "corruption_rate": 0.5,
            "detection_rate": 0.3,
        }
        benchmarks = [
            BenchmarkRange(
                metric="corruption_rate", low=0.3, high=0.7,
                source="Transparency International",
            ),
            BenchmarkRange(
                metric="detection_rate", low=0.1, high=0.5,
                source="Criminology research",
            ),
        ]
        report = validate_against_empirical(metrics, benchmarks)
        assert isinstance(report, ValidationReport)
        assert report.total == 2
        assert report.passed == 2
        assert report.failed == 0
        assert all(r.within_range for r in report.results)

    def test_metric_outside_range(self):
        """Метрика вне эмпирического диапазона."""
        metrics = {
            "corruption_rate": 0.95,
        }
        benchmarks = [
            BenchmarkRange(
                metric="corruption_rate", low=0.3, high=0.7,
                source="TI",
            ),
        ]
        report = validate_against_empirical(metrics, benchmarks)
        assert report.passed == 0
        assert report.failed == 1
        result = report.results[0]
        assert not result.within_range
        assert result.actual == 0.95
        assert result.metric == "corruption_rate"

    def test_missing_metric_marked_as_missing(self):
        """Отсутствующая метрика помечается как пропущенная."""
        metrics = {}
        benchmarks = [
            BenchmarkRange(
                metric="corruption_rate", low=0.3, high=0.7,
                source="TI",
            ),
        ]
        report = validate_against_empirical(metrics, benchmarks)
        assert report.failed == 1
        assert report.results[0].actual is None
        assert not report.results[0].within_range

    def test_mixed_results(self):
        """Часть метрик попадает, часть нет."""
        metrics = {
            "corruption_rate": 0.5,
            "detection_rate": 0.9,
            "false_positive_rate": 0.05,
        }
        benchmarks = [
            BenchmarkRange(metric="corruption_rate", low=0.3, high=0.7, source="TI"),
            BenchmarkRange(metric="detection_rate", low=0.1, high=0.5, source="Crim"),
            BenchmarkRange(metric="false_positive_rate", low=0.0, high=0.15, source="Crim"),
        ]
        report = validate_against_empirical(metrics, benchmarks)
        assert report.total == 3
        assert report.passed == 2
        assert report.failed == 1

    def test_report_pass_rate(self):
        """Отчёт содержит корректную долю прохождения."""
        metrics = {"a": 0.5, "b": 0.5}
        benchmarks = [
            BenchmarkRange(metric="a", low=0.0, high=1.0, source="test"),
            BenchmarkRange(metric="b", low=0.8, high=1.0, source="test"),
        ]
        report = validate_against_empirical(metrics, benchmarks)
        assert report.pass_rate == 0.5

    def test_governance_specific_benchmarks(self):
        """Валидация с привязкой к режиму управления."""
        metrics = {
            "corruption_rate_G0": 0.6,
            "corruption_rate_G3": 0.2,
        }
        benchmarks = [
            BenchmarkRange(
                metric="corruption_rate_G0", low=0.4, high=0.8,
                source="TI", governance="G0",
            ),
            BenchmarkRange(
                metric="corruption_rate_G3", low=0.05, high=0.3,
                source="TI", governance="G3",
            ),
        ]
        report = validate_against_empirical(metrics, benchmarks)
        assert report.passed == 2


class TestLoadBenchmarks:
    """Тесты загрузки эмпирических данных из файла."""

    def test_load_benchmarks_from_json(self, tmp_path):
        """Загрузка из JSON-файла."""
        data = [
            {
                "metric": "corruption_rate",
                "low": 0.3,
                "high": 0.7,
                "source": "Transparency International",
            },
        ]
        path = tmp_path / "benchmarks.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        benchmarks = load_benchmarks(path)
        assert len(benchmarks) == 1
        assert benchmarks[0].metric == "corruption_rate"
        assert benchmarks[0].source == "Transparency International"

    def test_load_default_benchmarks(self):
        """Загрузка дефолтных эмпирических данных."""
        path = Path("data/empirical_benchmarks.json")
        if not path.exists():
            pytest.skip("empirical_benchmarks.json not yet created")
        benchmarks = load_benchmarks(path)
        assert len(benchmarks) >= 1
