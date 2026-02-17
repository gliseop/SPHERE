"""Тесты генерации ситуационных сводок."""

from magistry_sim.cases import Case, Proposal
from magistry_sim.config import AgentProfile, Capability, Need
from magistry_sim.context import build_situation, _build_auditor_section
from magistry_sim.locations import Location, LocationManager
from magistry_sim.state import ReputationRecord, WorldState


class TestBuildSituation:
    def _make_state(self):
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Иванов А.П.",
            position="начальник отдела",
            capabilities=[
                Capability(
                    action="open_case", case_types=["procurement"]
                ),
            ],
        )
        state.reputation["off_1"] = ReputationRecord(score=20.0)
        state.resources.init_agent(
            "off_1", budget_limit=10_000_000
        )
        state.graph.add_agent("off_1")
        return state

    def test_basic_situation(self):
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "Иванов А.П." in text
        assert "раунд 0" in text
        assert "начальник отдела" in text

    def test_situation_with_reputation(self):
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "20.0" in text
        assert "рост активен" in text

    def test_situation_with_resources(self):
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "Бюджетный лимит" in text

    def test_situation_with_cases(self):
        state = self._make_state()
        case = Case(
            id="D-001",
            case_type="procurement",
            title="Закупка серверов",
            description="Описание",
            owner_id="off_1",
            stage="collecting",
        )
        case.proposals.append(
            Proposal(
                id="P-001",
                case_id="D-001",
                author_id="biz_1",
                content="Предложение на 5М",
                submitted_at=0,
            )
        )
        state.cases["D-001"] = case
        text = build_situation("off_1", state)
        assert "D-001" in text
        assert "Закупка серверов" in text

    def test_situation_with_needs(self):
        state = self._make_state()
        state.active_needs.append(
            Need(
                case_type="procurement",
                description="Нужно оборудование",
                target_agent_id="off_1",
                appear_round=0,
            )
        )
        text = build_situation("off_1", state)
        assert "Нужно оборудование" in text

    def test_unknown_agent(self):
        state = self._make_state()
        text = build_situation("unknown", state)
        assert "Ошибка" in text

    def test_situation_shows_current_location(self):
        """Сводка включает текущую локацию агента."""
        state = self._make_state()
        locations = LocationManager()
        locations.add_location(Location(id="office", name="Кабинет", public=False))
        locations.place_agent("off_1", "office")
        state.locations = locations

        text = build_situation("off_1", state)
        assert "Кабинет" in text

    def test_situation_without_locations(self):
        """Сводка работает без системы локаций (обратная совместимость)."""
        state = self._make_state()
        text = build_situation("off_1", state)
        assert "Ошибка" not in text


class TestAuditorSkudLog:
    def _make_auditor_state(self):
        state = WorldState()
        state.agents["auditor"] = AgentProfile(
            id="auditor",
            name="Аудитор",
            position="аудитор",
            capabilities=[
                Capability(action="audit", case_types=[]),
            ],
        )
        state.agents["off_1"] = AgentProfile(
            id="off_1", name="Иванов", position="начальник",
        )
        state.agents["biz_1"] = AgentProfile(
            id="biz_1", name="Петров", position="подрядчик",
        )
        state.reputation["auditor"] = ReputationRecord()
        state.reputation["off_1"] = ReputationRecord()
        state.reputation["biz_1"] = ReputationRecord()
        state.graph.add_agent("auditor")
        state.graph.add_agent("off_1")
        state.graph.add_agent("biz_1")
        return state

    def test_auditor_sees_skud_log(self):
        """Аудитор видит журнал СКУД — кто с кем в непубличных локациях."""
        state = self._make_auditor_state()
        locations = LocationManager()
        locations.add_location(Location(id="office", name="Кабинет", public=False))
        locations.place_agent("off_1", "office")
        locations.place_agent("biz_1", "office")
        state.locations = locations

        section = _build_auditor_section("auditor", state)
        text = "\n".join(section)
        assert "off_1" in text
        assert "biz_1" in text
        assert "Кабинет" in text

    def test_auditor_no_skud_without_locations(self):
        """Без LocationManager секция СКУД отсутствует."""
        state = self._make_auditor_state()
        section = _build_auditor_section("auditor", state)
        text = "\n".join(section)
        assert "СКУД" not in text

    def test_auditor_no_skud_public_only(self):
        """СКУД не показывает совместное пребывание в публичных локациях."""
        state = self._make_auditor_state()
        locations = LocationManager()
        locations.add_location(Location(id="hall", name="Зал", public=True))
        locations.place_agent("off_1", "hall")
        locations.place_agent("biz_1", "hall")
        state.locations = locations

        section = _build_auditor_section("auditor", state)
        text = "\n".join(section)
        assert "СКУД" not in text or "off_1" not in text
