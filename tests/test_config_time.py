"""Тесты временных полей ScenarioConfig."""

from datetime import datetime, timedelta, timezone

from magistry_sim.config import ScenarioConfig

MSK = timezone(timedelta(hours=3))


def test_scenario_config_with_time_fields():
    cfg = ScenarioConfig(
        id="S1",
        title="Test",
        description="Test scenario",
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 27, 18, 0, tzinfo=MSK),
    )
    assert cfg.start_time is not None
    assert cfg.end_time is not None
    assert (cfg.end_time - cfg.start_time).days == 11


def test_scenario_config_backward_compat():
    cfg = ScenarioConfig(
        id="S0",
        title="Test",
        description="Old format",
        max_rounds=8,
    )
    assert cfg.max_rounds == 8
    assert cfg.start_time is None
    assert cfg.end_time is None
