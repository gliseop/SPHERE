"""Тесты системы физических локаций."""

import pytest
from magistry_sim.locations import Location, LocationManager


class TestLocation:
    def test_create(self):
        loc = Location(
            id="office_off1",
            name="Кабинет Козлова",
            public=False,
            available_actions=["sign_document", "talk_to"],
        )
        assert loc.public is False

    def test_default_public(self):
        loc = Location(id="hall", name="Зал заседаний")
        assert loc.public is True

    def test_extra_field_forbidden(self):
        with pytest.raises(Exception):
            Location(id="x", name="X", unknown="y")

    def test_suspicion_modifier_default(self):
        loc = Location(id="hall", name="Зал")
        assert loc.suspicion_modifier == 0.0

    def test_suspicion_modifier_custom(self):
        loc = Location(
            id="restaurant",
            name="Ресторан",
            suspicion_modifier=0.3,
        )
        assert loc.suspicion_modifier == 0.3


class TestLocationManager:
    def test_place_and_find_agent(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет"))
        mgr.add_location(Location(id="restaurant", name="Ресторан"))
        mgr.place_agent("off_1", "office")
        assert mgr.get_agent_location("off_1") == "office"

    def test_move_agent(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет"))
        mgr.add_location(Location(id="restaurant", name="Ресторан"))
        mgr.place_agent("off_1", "office")
        mgr.move_agent("off_1", "restaurant")
        assert mgr.get_agent_location("off_1") == "restaurant"

    def test_agents_at_location(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал"))
        mgr.place_agent("off_1", "hall")
        mgr.place_agent("biz_1", "hall")
        agents = mgr.agents_at("hall")
        assert set(agents) == {"off_1", "biz_1"}

    def test_can_observe(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал", public=True))
        mgr.place_agent("off_1", "hall")
        mgr.place_agent("biz_1", "hall")
        assert mgr.can_observe("off_1", "biz_1") is True

    def test_cannot_observe_different_location(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет", public=False))
        mgr.add_location(Location(id="hall", name="Зал", public=True))
        mgr.place_agent("off_1", "office")
        mgr.place_agent("biz_1", "hall")
        assert mgr.can_observe("off_1", "biz_1") is False

    def test_get_location(self):
        mgr = LocationManager()
        loc = Location(id="office", name="Кабинет", public=False)
        mgr.add_location(loc)
        found = mgr.get_location("office")
        assert found is not None
        assert found.name == "Кабинет"

    def test_get_location_missing(self):
        mgr = LocationManager()
        assert mgr.get_location("nonexistent") is None

    def test_get_agent_location_missing(self):
        mgr = LocationManager()
        assert mgr.get_agent_location("unknown_agent") is None

    def test_agents_at_empty_location(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал"))
        assert mgr.agents_at("hall") == []

    def test_cannot_observe_unplaced_agent(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал"))
        mgr.place_agent("off_1", "hall")
        assert mgr.can_observe("off_1", "unknown") is False
        assert mgr.can_observe("unknown", "off_1") is False

    def test_get_colocation_log(self):
        """get_colocation_log возвращает журнал совместных посещений непубличных локаций."""
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет", public=False))
        mgr.add_location(Location(id="hall", name="Зал", public=True))
        mgr.place_agent("off_1", "office")
        mgr.place_agent("biz_1", "office")
        mgr.place_agent("biz_2", "hall")

        log = mgr.get_colocation_log()
        assert len(log) >= 1
        entry = log[0]
        assert entry["location_id"] == "office"
        assert "off_1" in entry["agents"]
        assert "biz_1" in entry["agents"]

    def test_get_colocation_log_ignores_public(self):
        """get_colocation_log не включает публичные локации."""
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал", public=True))
        mgr.place_agent("off_1", "hall")
        mgr.place_agent("biz_1", "hall")

        log = mgr.get_colocation_log()
        assert len(log) == 0

    def test_get_colocation_log_ignores_single_agent(self):
        """get_colocation_log не включает локации с одним агентом."""
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет", public=False))
        mgr.place_agent("off_1", "office")

        log = mgr.get_colocation_log()
        assert len(log) == 0
