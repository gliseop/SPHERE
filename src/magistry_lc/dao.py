"""DAO-логика: голосования и политика должностей."""

from __future__ import annotations

from dataclasses import dataclass

from .config import GovernanceConfig
from .ops import ChangePositionOp, CloseVoteOp
from .state import Vote, WorldState


@dataclass(slots=True)
class DaoEngine:
    """Детерминированная обработка DAO голосований."""

    cfg: GovernanceConfig

    def eligible_voters(self, state: WorldState) -> list[str]:
        """Список голосующих (по умолчанию: все внутренние агенты)."""
        if self.cfg.dao_voters:
            return list(self.cfg.dao_voters)
        return state.get_internal_agent_ids()

    def should_close_vote(self, vote: Vote, *, tick: int) -> bool:
        """Нужно ли закрыть голосование на текущем тике."""
        return vote.status == "open" and tick >= vote.closes_tick

    def close_votes(self, state: WorldState) -> list[object]:
        """Закрыть голосования, срок которых истёк.

        Returns:
            Список ops (CloseVoteOp и, если прошло, ChangePositionOp).
        """
        ops: list[object] = []
        for vote_id in sorted(state.votes.keys()):
            vote = state.votes[vote_id]
            if not self.should_close_vote(vote, tick=state.tick):
                continue
            result = self._compute_result(vote)
            ops.append(CloseVoteOp(vote_id=vote_id, result=result))
            if result == "passed":
                ops.append(
                    ChangePositionOp(
                        actor_id=None,
                        target_agent_id=vote.target_agent_id,
                        new_title=vote.new_title,
                        reason=f"dao_vote:{vote_id}",
                    )
                )
        return ops

    def _compute_result(self, vote: Vote) -> str:
        # Consent gate.
        if self.cfg.require_consent and vote.target_consented is not True:
            return "canceled"

        voters_total = max(1, len(vote.voters))
        cast_total = len(vote.votes)
        if (cast_total / voters_total) < self.cfg.quorum:
            return "failed"

        yes = sum(1 for c in vote.votes.values() if c == "yes")
        no = sum(1 for c in vote.votes.values() if c == "no")
        denom = max(1, yes + no)
        if (yes / denom) >= self.cfg.pass_threshold and yes > no:
            return "passed"
        return "failed"

