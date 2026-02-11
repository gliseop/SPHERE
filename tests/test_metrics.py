"""Тесты confusion matrix и метрик (Stage 7)."""
from __future__ import annotations

import pytest

from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.llm import MockLLMProvider
from magistry_sim.metrics import compute_metrics
from magistry_sim.models import ConfusionMatrix, GovernanceConfig


def _make_engine(mode: GovernanceMode = GovernanceMode.G3_FULL) -> SimulationEngine:
    return SimulationEngine(
        governance=GovernanceConfig(mode=mode),
        llm=MockLLMProvider(seed=42),
    )


def test_confusion_matrix_properties() -> None:
    """Математическая корректность precision/recall/F1."""
    # TP=3, FP=1, TN=5, FN=1
    cm = ConfusionMatrix(tp=3, fp=1, tn=5, fn=1)
    assert cm.precision == pytest.approx(3 / 4)  # 0.75
    assert cm.recall == pytest.approx(3 / 4)  # 0.75
    assert cm.f1 == pytest.approx(0.75)  # 2*0.75*0.75/(0.75+0.75)
    assert cm.accuracy == pytest.approx(8 / 10)  # 0.8


def test_confusion_matrix_edge_cases() -> None:
    """Edge cases: нет TP, нет предсказаний."""
    # Нет предсказаний
    cm_empty = ConfusionMatrix()
    assert cm_empty.precision == 0.0
    assert cm_empty.recall == 0.0
    assert cm_empty.f1 == 0.0
    assert cm_empty.accuracy == 0.0

    # Только TN (идеальный чистый сценарий)
    cm_all_tn = ConfusionMatrix(tn=10)
    assert cm_all_tn.precision == 0.0
    assert cm_all_tn.recall == 0.0
    assert cm_all_tn.f1 == 0.0
    assert cm_all_tn.accuracy == 1.0


@pytest.mark.asyncio
async def test_confusion_matrix_clean_scenario() -> None:
    """S0 (чистая сделка): ожидаем TN, возможно FP при шуме."""
    engine = _make_engine()
    result = await engine.run(scenario_id=ScenarioId.S0_CLEAN, seed=100, ticks=1)
    metrics = compute_metrics(result)

    # В чистом сценарии не должно быть коррупции
    assert metrics.confusion.tp == 0
    assert metrics.confusion.fn == 0


@pytest.mark.asyncio
async def test_metrics_have_confusion_fields() -> None:
    """Метрики содержат все поля confusion matrix."""
    engine = _make_engine()
    result = await engine.run(scenario_id=ScenarioId.S1_KICKBACK, seed=42, ticks=1)
    metrics = compute_metrics(result)

    assert hasattr(metrics, 'confusion')
    assert hasattr(metrics, 'precision')
    assert hasattr(metrics, 'recall')
    assert hasattr(metrics, 'f1')
    assert isinstance(metrics.confusion, ConfusionMatrix)

    # Сумма confusion matrix == число тиков
    cm = metrics.confusion
    assert cm.tp + cm.fp + cm.tn + cm.fn == metrics.ticks


@pytest.mark.asyncio
async def test_batch_all_scenarios() -> None:
    """Пакетный запуск S0-S9 × G0-G3 без ошибок."""
    llm = MockLLMProvider(seed=42)
    results = []
    for scenario_id in ScenarioId:
        for mode in GovernanceMode:
            config = GovernanceConfig(mode=mode)
            engine = SimulationEngine(governance=config, llm=llm)
            result = await engine.run(scenario_id=scenario_id)
            metrics = compute_metrics(result)
            results.append((scenario_id, mode, metrics))

    assert len(results) == 40  # 10 сценариев × 4 режима

    for sid, mode, m in results:
        assert m.ticks > 0
        assert 0.0 <= m.corruption_rate <= 1.0
        assert 0.0 <= m.precision <= 1.0
        assert 0.0 <= m.recall <= 1.0
        assert 0.0 <= m.f1 <= 1.0
