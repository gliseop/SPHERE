"""Метрики симуляции: confusion matrix, precision, recall, F1."""

from __future__ import annotations

from dataclasses import dataclass

from magistry_sim.models import ConfusionMatrix, RunResult


@dataclass(slots=True)
class Metrics:
    """Агрегированные метрики прогона симуляции."""

    ticks: int
    corruption_rate: float
    audit_flag_rate: float
    tribunal_trigger_rate: float
    tribunal_guilty_rate: float
    confusion: ConfusionMatrix
    precision: float
    recall: float
    f1: float


def compute_metrics(result: RunResult) -> Metrics:
    """Вычислить метрики из результата симуляции.

    Args:
        result: Результат прогона (RunResult).

    Returns:
        Metrics с confusion matrix и F1.
    """
    ticks = len(result.outcomes)
    if ticks == 0:
        cm = ConfusionMatrix()
        return Metrics(
            ticks=0,
            corruption_rate=0.0,
            audit_flag_rate=0.0,
            tribunal_trigger_rate=0.0,
            tribunal_guilty_rate=0.0,
            confusion=cm,
            precision=0.0,
            recall=0.0,
            f1=0.0,
        )

    corruption = sum(1 for o in result.outcomes if o.corruption)
    audit_flag = sum(1 for o in result.outcomes if o.audit_flagged)
    tribunal_trigger = sum(1 for o in result.outcomes if o.tribunal_triggered)
    tribunal_guilty = sum(1 for o in result.outcomes if o.tribunal_guilty is True)

    # Confusion matrix
    cm = ConfusionMatrix()
    for o in result.outcomes:
        if o.corruption and o.audit_flagged:
            cm.tp += 1
        elif not o.corruption and o.audit_flagged:
            cm.fp += 1
        elif not o.corruption and not o.audit_flagged:
            cm.tn += 1
        elif o.corruption and not o.audit_flagged:
            cm.fn += 1

    return Metrics(
        ticks=ticks,
        corruption_rate=corruption / ticks,
        audit_flag_rate=audit_flag / ticks,
        tribunal_trigger_rate=tribunal_trigger / ticks,
        tribunal_guilty_rate=(tribunal_guilty / tribunal_trigger) if tribunal_trigger else 0.0,
        confusion=cm,
        precision=cm.precision,
        recall=cm.recall,
        f1=cm.f1,
    )
