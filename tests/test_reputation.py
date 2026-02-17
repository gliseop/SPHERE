"""Тесты системы репутации."""

import pytest
from magistry_sim.state import ReputationRecord
from magistry_sim.reputation import (
    apply_decay,
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


class TestReputationDecay:
    """Тесты затухания репутации."""

    def test_reputation_decay_over_rounds(self):
        """Репутация затухает каждый раунд без активных действий."""
        record = ReputationRecord(score=100.0)
        for _ in range(10):
            apply_decay(record, decay_factor=0.95)
        # 100 * 0.95^10 ≈ 59.87
        assert record.score < 100.0
        assert record.score == pytest.approx(100.0 * 0.95**10, rel=1e-6)

    def test_no_decay_when_factor_is_one(self):
        """При decay_factor=1.0 затухания нет (обратная совместимость)."""
        record = ReputationRecord(score=50.0)
        apply_decay(record, decay_factor=1.0)
        assert record.score == 50.0

    def test_decay_not_applied_when_frozen(self):
        """Замороженная репутация не затухает."""
        record = ReputationRecord(score=80.0, frozen=True)
        apply_decay(record, decay_factor=0.5)
        assert record.score == 80.0

    def test_decay_with_growth_net_decrease(self):
        """При затухании без активности репутация снижается, несмотря на базовый прирост."""
        record = ReputationRecord(score=100.0)
        for _ in range(10):
            apply_decay(record, decay_factor=0.95)
            growth = compute_round_growth(record, cases_resolved=0)
            apply_growth(record, growth)
        # Затухание 5% от базы сильнее фиксированного прироста +1.0
        assert record.score < 100.0
