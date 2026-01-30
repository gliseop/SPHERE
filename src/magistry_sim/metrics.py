from __future__ import annotations

from dataclasses import dataclass

from magistry_sim.models import RunResult


@dataclass(slots=True)
class Metrics:
    ticks: int
    corruption_rate: float
    audit_flag_rate: float
    tribunal_trigger_rate: float
    tribunal_guilty_rate: float


def compute_metrics(result: RunResult) -> Metrics:
    ticks = len(result.outcomes)
    if ticks == 0:
        return Metrics(
            ticks=0,
            corruption_rate=0.0,
            audit_flag_rate=0.0,
            tribunal_trigger_rate=0.0,
            tribunal_guilty_rate=0.0,
        )

    corruption = sum(1 for o in result.outcomes if o.corruption)
    audit_flag = sum(1 for o in result.outcomes if o.audit_flagged)
    tribunal_trigger = sum(1 for o in result.outcomes if o.tribunal_triggered)
    tribunal_guilty = sum(1 for o in result.outcomes if o.tribunal_guilty is True)

    return Metrics(
        ticks=ticks,
        corruption_rate=corruption / ticks,
        audit_flag_rate=audit_flag / ticks,
        tribunal_trigger_rate=tribunal_trigger / ticks,
        tribunal_guilty_rate=(tribunal_guilty / tribunal_trigger) if tribunal_trigger else 0.0,
    )

