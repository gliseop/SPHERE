from __future__ import annotations

from dataclasses import dataclass
from random import Random

from magistry_sim.enums import AgentRole
from magistry_sim.models import GovernanceConfig, RiskReport
from magistry_sim.reputation import unfreeze
from magistry_sim.world import World


@dataclass(slots=True)
class Tribunal:
    config: GovernanceConfig

    def run(self, *, world: World, tick: int, report: RiskReport, rng: Random) -> bool:
        candidates = [
            agent
            for agent in world.agents.values()
            if agent.role == AgentRole.OFFICIAL
            and agent.id not in {report.lpr_id}
            and not agent.immune
        ]
        rng.shuffle(candidates)
        jurors = candidates[: max(1, self.config.jury_size)]

        votes_guilty = 0
        votes_total = 0
        for juror in jurors:
            votes_total += 1
            # Чем честнее присяжный, тем ближе голос к доказательствам.
            base = report.risk_score
            bias = (0.5 - juror.honesty) * 0.25
            p_guilty = max(0.0, min(1.0, base + bias))
            if rng.random() < p_guilty:
                votes_guilty += 1

        guilty = votes_guilty > (votes_total / 2)
        world.log(
            tick,
            "tribunal_vote",
            lpr_id=report.lpr_id,
            contractor_id=report.contractor_id,
            jurors=[j.id for j in jurors],
            votes_guilty=votes_guilty,
            votes_total=votes_total,
            guilty=guilty,
        )

        lpr = world.agents[report.lpr_id]
        contractor = world.agents[report.contractor_id]

        if guilty:
            if not lpr.immune:
                lpr.eligible_for_lpr = False
            if not contractor.immune:
                contractor.eligible_for_contracts = False
        else:
            unfreeze(lpr)
            unfreeze(contractor)

        return guilty
