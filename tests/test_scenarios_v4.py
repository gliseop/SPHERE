# tests/test_scenarios_v4.py
import pytest
from magistry_sim.scenarios_v4 import (
    ScenarioTemplate,
    SCENARIO_LIBRARY,
    build_scenario_from_template,
)
from magistry_sim.personality import AgentPersonality
from magistry_sim.event_generator import ScheduledEvent, StochasticConfig


class TestScenarioTemplate:
    def test_kickback_template_exists(self):
        assert "kickback_procurement" in SCENARIO_LIBRARY

    def test_template_has_required_fields(self):
        t = SCENARIO_LIBRARY["kickback_procurement"]
        assert t.historical_prototype
        assert t.corruption_pattern in (
            "bid_rigging", "kickback", "nepotism", "embezzlement"
        )
        assert len(t.agent_templates) >= 3
        assert t.round_count > 0

    def test_build_scenario_returns_valid_config(self):
        template = SCENARIO_LIBRARY["kickback_procurement"]
        config = build_scenario_from_template(template, seed=42)
        assert len(config.agents) >= 3
        assert config.max_rounds == template.round_count
        # All agents should have personality
        for agent in config.agents:
            assert agent.personality is not None


class TestScenarioLibrary:
    def test_all_templates_buildable(self):
        for name, template in SCENARIO_LIBRARY.items():
            config = build_scenario_from_template(template, seed=42)
            assert len(config.agents) >= 2, f"Template {name} has too few agents"
