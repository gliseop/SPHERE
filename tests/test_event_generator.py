"""Тесты генератора мировых событий."""

import pytest
from magistry_sim.event_generator import (
    ScheduledEvent,
    StochasticConfig,
    EventGenerator,
    LLMEventGenerator,
)
from magistry_sim.llm import StructuredLLMResponse


class TestScheduledEvent:
    def test_create(self):
        e = ScheduledEvent(
            round=5,
            event_type="audit_inspection",
            params={"target": "off_1"},
        )
        assert e.round == 5

    def test_extra_field_forbidden(self):
        with pytest.raises(Exception):
            ScheduledEvent(
                round=5,
                event_type="audit_inspection",
                params={},
                unknown_field="x",
            )


class TestStochasticConfig:
    def test_defaults(self):
        c = StochasticConfig()
        assert 0 <= c.journalist_investigation <= 1.0

    def test_out_of_range_raises(self):
        with pytest.raises(Exception):
            StochasticConfig(journalist_investigation=1.5)

    def test_negative_raises(self):
        with pytest.raises(Exception):
            StochasticConfig(citizen_complaint=-0.1)


class TestEventGenerator:
    def test_scheduled_fires_on_round(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(round=3, event_type="budget_review", params={}),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=3, world_state=None)
        assert len(events) == 1
        assert events[0]["event_type"] == "budget_review"

    def test_scheduled_does_not_fire_on_wrong_round(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(round=3, event_type="budget_review", params={}),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=1, world_state=None)
        assert len(events) == 0

    def test_stochastic_with_probability_1(self):
        gen = EventGenerator(
            scheduled=[],
            stochastic=StochasticConfig(
                journalist_investigation=1.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=0, world_state=None)
        types = [e["event_type"] for e in events]
        assert "journalist_investigation" in types

    def test_stochastic_with_probability_0(self):
        gen = EventGenerator(
            scheduled=[],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=0, world_state=None)
        assert len(events) == 0

    def test_scheduled_event_has_source_field(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(round=0, event_type="test_event", params={}),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=0, world_state=None)
        assert events[0]["source"] == "scheduled"

    def test_stochastic_event_has_source_field(self):
        gen = EventGenerator(
            scheduled=[],
            stochastic=StochasticConfig(
                journalist_investigation=1.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=0, world_state=None)
        stochastic_events = [e for e in events if e["source"] == "stochastic"]
        assert len(stochastic_events) >= 1

    def test_multiple_scheduled_same_round(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(round=2, event_type="event_a", params={}),
                ScheduledEvent(round=2, event_type="event_b", params={"x": 1}),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=2, world_state=None)
        assert len(events) == 2
        types = {e["event_type"] for e in events}
        assert types == {"event_a", "event_b"}

    def test_params_passed_through(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(
                    round=1,
                    event_type="inspection",
                    params={"target": "off_1", "severity": "high"},
                ),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=1, world_state=None)
        assert events[0]["params"]["target"] == "off_1"
        assert events[0]["params"]["severity"] == "high"


class TestLLMEventGenerator:
    """Тесты LLM-генератора мировых событий."""

    def test_uses_world_context_in_prompt(self):
        """LLM-генератор передаёт контекст мира в промпт."""
        captured = []

        class CaptureLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(data={
                    "has_event": True,
                    "event_type": "journalist_investigation",
                    "description": "Журналист обнаружил подозрительные закупки",
                    "affected_agents": ["off_1"],
                })

        gen = LLMEventGenerator(llm=CaptureLLM())
        events = gen.generate(round_num=5, world_context="Бюджет исчерпан на 90%")

        assert len(events) >= 1
        assert "Бюджет исчерпан" in captured[0]
        assert events[0]["description"] == "Журналист обнаружил подозрительные закупки"

    def test_returns_no_events_when_llm_decides(self):
        """LLM может решить, что в этом раунде ничего не происходит."""
        class NoEventLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data={"has_event": False})

        gen = LLMEventGenerator(llm=NoEventLLM())
        events = gen.generate(round_num=1, world_context="Всё спокойно")
        assert events == []

    def test_event_has_source_llm(self):
        """События от LLM-генератора помечены source=llm."""
        class EventLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data={
                    "has_event": True,
                    "event_type": "citizen_complaint",
                    "description": "Жалоба граждан",
                    "affected_agents": [],
                })

        gen = LLMEventGenerator(llm=EventLLM())
        events = gen.generate(round_num=3, world_context="Контекст")
        assert len(events) == 1
        assert events[0]["source"] == "llm"

    def test_event_contains_all_fields(self):
        """Событие содержит event_type, description, affected_agents, source."""
        class FullEventLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data={
                    "has_event": True,
                    "event_type": "external_audit",
                    "description": "Внешняя проверка",
                    "affected_agents": ["off_1", "biz_1"],
                })

        gen = LLMEventGenerator(llm=FullEventLLM())
        events = gen.generate(round_num=2, world_context="Контекст")
        event = events[0]
        assert event["event_type"] == "external_audit"
        assert event["description"] == "Внешняя проверка"
        assert event["affected_agents"] == ["off_1", "biz_1"]
        assert event["source"] == "llm"

    def test_round_num_in_prompt(self):
        """Номер раунда передаётся в промпт LLM."""
        captured = []

        class CaptureLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(data={"has_event": False})

        gen = LLMEventGenerator(llm=CaptureLLM())
        gen.generate(round_num=7, world_context="Контекст")
        assert "7" in captured[0]
