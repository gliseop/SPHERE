"""Тесты системы репутации."""

from magistry_sim.state import ReputationRecord
from magistry_sim.reputation import (
    apply_growth,
    check_promotion,
    compute_round_growth,
    freeze,
    unfreeze,
)


class TestReputation:
    def test_basic_growth(self):
        rec = ReputationRecord(score=10.0)
        growth = compute_round_growth(rec)
        assert growth == 1.0

    def test_growth_with_cases(self):
        rec = ReputationRecord(score=10.0)
        growth = compute_round_growth(rec, cases_resolved=2)
        assert growth == 2.0

    def test_growth_frozen(self):
        rec = ReputationRecord(score=10.0, frozen=True)
        growth = compute_round_growth(rec)
        assert growth == 0.0

    def test_apply_growth(self):
        rec = ReputationRecord(score=10.0)
        apply_growth(rec, 2.0)
        assert rec.score == 12.0

    def test_apply_growth_frozen(self):
        rec = ReputationRecord(score=10.0, frozen=True)
        apply_growth(rec, 2.0)
        assert rec.score == 10.0

    def test_freeze_unfreeze(self):
        rec = ReputationRecord(score=10.0)
        assert not rec.frozen
        freeze(rec)
        assert rec.frozen
        unfreeze(rec)
        assert not rec.frozen

    def test_check_promotion(self):
        rec = ReputationRecord(score=10.0)
        promoted, current, next_title = check_promotion(rec)
        assert current == "специалист"
        assert next_title == "старший специалист"

    def test_promotion_blocked_when_frozen(self):
        rec = ReputationRecord(score=20.0, frozen=True)
        promoted, current, next_title = check_promotion(rec)
        assert not promoted
