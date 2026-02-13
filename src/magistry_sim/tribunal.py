"""DAO Трибунал: децентрализованное голосование по подозрительным сделкам."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

from magistry_sim.enums import AgentRole
from magistry_sim.models import GovernanceConfig, JurorVote, RiskReport, TribunalResult
from magistry_sim.reputation import unfreeze
from magistry_sim.world import World


@dataclass(slots=True)
class Tribunal:
    """DAO Трибунал для коллективных решений.

    Args:
        config: Конфигурация управления (содержит jury_size).
    """

    config: GovernanceConfig

    def run(self, *, world: World, tick: int, report: RiskReport, rng: Random) -> TribunalResult:
        """Провести трибунал.

        Args:
            world: Состояние мира.
            tick: Номер тика.
            report: Отчёт аудитора.
            rng: Генератор случайных чисел.

        Returns:
            TribunalResult с вердиктом, списком присяжных и голосами.
        """
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
        juror_details: list[JurorVote] = []
        for juror in jurors:
            votes_total += 1
            # Чем честнее присяжный, тем ближе голос к доказательствам.
            base = report.risk_score
            bias = (0.5 - juror.honesty) * 0.25
            p_guilty = max(0.0, min(1.0, base + bias))
            voted_guilty = rng.random() < p_guilty
            if voted_guilty:
                votes_guilty += 1
            juror_details.append(
                JurorVote(juror_id=juror.id, honesty=juror.honesty, voted_guilty=voted_guilty)
            )

        guilty = votes_guilty > (votes_total / 2)
        juror_ids = [j.id for j in jurors]

        world.log(
            tick,
            "tribunal_vote",
            lpr_id=report.lpr_id,
            contractor_id=report.contractor_id,
            jurors=juror_ids,
            votes_guilty=votes_guilty,
            votes_total=votes_total,
            guilty=guilty,
            juror_details=[jv.model_dump() for jv in juror_details],
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

        return TribunalResult(
            guilty=guilty,
            juror_ids=juror_ids,
            votes_guilty=votes_guilty,
            votes_total=votes_total,
            juror_details=juror_details,
        )
