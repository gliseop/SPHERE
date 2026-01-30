from __future__ import annotations

from dataclasses import dataclass

from magistry_sim.models import GovernanceConfig, RiskReport
from magistry_sim.world import World


@dataclass(slots=True)
class Auditor:
    config: GovernanceConfig

    def assess(self, *, world: World, tick: int, lpr_id: str, contractor_id: str) -> RiskReport:
        reasons: list[str] = []

        edge = world.social_graph.get_edge_data(lpr_id, contractor_id) or {}
        strength = float(edge.get("strength", 0.0))
        if strength >= 0.8:
            reasons.append("Strong social tie (graph)")
        elif strength >= 0.4:
            reasons.append("Moderate social tie (graph)")

        if world.state.get("explicit_bribe", False):
            reasons.append("Explicit collusion signal (shadow)")

        if world.state.get("timing_anomaly", False):
            reasons.append("Timing anomaly (behavior)")

        if world.state.get("mask_language", False):
            reasons.append("Linguistic masking suspected (text)")

        noise_level = float(world.state.get("noise_level", 0.0))
        if noise_level >= 0.4:
            reasons.append("Log noise / missing data (uncertainty)")

        risk = 0.05
        risk += strength * 0.55
        risk += 0.30 if world.state.get("explicit_bribe", False) else 0.0
        risk += 0.20 if world.state.get("timing_anomaly", False) else 0.0
        risk += 0.10 if world.state.get("mask_language", False) else 0.0
        risk += noise_level * 0.20
        risk = max(0.0, min(1.0, risk))

        return RiskReport(
            tick=tick,
            lpr_id=lpr_id,
            contractor_id=contractor_id,
            risk_score=risk,
            reasons=reasons,
            critical=risk >= self.config.critical_threshold,
        )

