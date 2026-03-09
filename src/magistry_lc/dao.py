"""DAO-логика: голосования и политика должностей."""

from __future__ import annotations

from dataclasses import dataclass

from .config import GovernanceConfig
from .ops import ChangePositionOp, CloseVoteOp, StateOp
from .state import Vote, WorldState


@dataclass(slots=True, frozen=True)
class VoteDecision:
    """Детерминированный исход DAO-голосования."""

    result: str
    reason: str


@dataclass(slots=True)
class DaoEngine:
    """Детерминированная обработка DAO голосований."""

    cfg: GovernanceConfig

    def eligible_voters(self, state: WorldState, *, exclude_agent_ids: set[str] | None = None) -> list[str]:
        """Список голосующих (внутренние агенты с capability ``dao``)."""
        if self.cfg.dao_voters:
            source = [aid for aid in self.cfg.dao_voters if aid in state.agents]
        else:
            source = state.get_internal_agent_ids()

        excluded = set(exclude_agent_ids or set())
        voters: list[str] = []
        seen: set[str] = set()
        for aid in source:
            if aid in seen:
                continue
            seen.add(aid)
            if aid in excluded:
                continue
            agent = state.agents.get(aid)
            if agent is None or not agent.internal:
                continue
            if "dao" not in agent.capabilities:
                continue
            voters.append(aid)
        return voters

    def should_close_vote(self, vote: Vote, *, tick: int) -> bool:
        """Нужно ли закрыть голосование на текущем тике."""
        return vote.status == "open" and tick >= vote.closes_tick

    def close_votes(self, state: WorldState) -> list[StateOp]:
        """Закрыть голосования, срок которых истёк.

        Returns:
            Список ops (CloseVoteOp и, если прошло, ChangePositionOp).
        """
        ops: list[StateOp] = []
        for vote_id in sorted(state.votes.keys()):
            vote = state.votes[vote_id]
            if not self.should_close_vote(vote, tick=state.tick):
                continue
            decision = self._compute_result(state, vote)
            ops.append(CloseVoteOp(vote_id=vote_id, result=decision.result, reason=decision.reason))
            if decision.result == "passed":
                ops.append(
                    ChangePositionOp(
                        actor_id=None,
                        target_agent_id=vote.target_agent_id,
                        new_title=vote.new_title,
                        reason=f"dao_vote:{vote_id}",
                    )
                )
        return ops

    def _compute_result(self, state: WorldState, vote: Vote) -> VoteDecision:
        target = state.agents.get(vote.target_agent_id)
        if target is None:
            return VoteDecision(result="canceled", reason="target_missing")
        if target.reputation_frozen:
            return VoteDecision(result="canceled", reason="reputation_frozen")
        if not target.wants_promotion:
            return VoteDecision(result="canceled", reason="target_declines_promotion")

        # Consent gate.
        if self.cfg.require_consent and vote.target_consented is not True:
            return VoteDecision(result="canceled", reason="consent_missing")

        voters_total = max(1, len(vote.voters))
        cast_total = len(vote.votes)
        if (cast_total / voters_total) < self.cfg.quorum:
            return VoteDecision(result="failed", reason="quorum_not_reached")

        yes = sum(1 for c in vote.votes.values() if c == "yes")
        no = sum(1 for c in vote.votes.values() if c == "no")
        denom = max(1, yes + no)
        if (yes / denom) >= self.cfg.pass_threshold and yes > no:
            return VoteDecision(result="passed", reason="threshold_passed")
        return VoteDecision(result="failed", reason="threshold_failed")
