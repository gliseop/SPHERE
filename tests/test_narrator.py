"""Тесты нарративной сводки мира."""

from magistry_sim.narrator import WorldNarrator, RoundSummary
from magistry_sim.llm import StructuredLLMResponse


class TestWorldNarrator:
    """Тесты WorldNarrator."""

    def _make_round_llm(self, summary_data: dict | None = None):
        """Вспомогательный LLM для тестов сводки раунда."""
        data = summary_data or {
            "events_summary": "Чиновник открыл дело о закупках.",
            "key_decisions": ["Открытие дела D-001"],
            "tensions": ["Бизнесмен недоволен проверкой"],
            "agent_motivations": {
                "off_1": "Стремится закрыть дело быстрее",
                "biz_1": "Ищет способ избежать проверки",
            },
        }

        class RoundLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                return StructuredLLMResponse(data=data)

        return RoundLLM()

    def test_summarize_round_returns_round_summary(self):
        """summarize_round возвращает RoundSummary с корректными полями."""
        narrator = WorldNarrator()
        llm = self._make_round_llm()

        result = narrator.summarize_round(
            round_num=0,
            events=[{"event_type": "case_opened", "agent_id": "off_1"}],
            agent_ids=["off_1", "biz_1"],
            llm=llm,
        )

        assert isinstance(result, RoundSummary)
        assert result.round_num == 0
        assert "закупк" in result.events_summary
        assert len(result.key_decisions) == 1
        assert len(result.tensions) == 1
        assert "off_1" in result.agent_motivations

    def test_summarize_round_passes_events_to_prompt(self):
        """События раунда передаются в промпт LLM."""
        captured = []

        class CaptureLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(data={
                    "events_summary": "",
                    "key_decisions": [],
                    "tensions": [],
                    "agent_motivations": {},
                })

        narrator = WorldNarrator()
        narrator.summarize_round(
            round_num=3,
            events=[{"event_type": "proposal_submitted", "agent_id": "biz_1"}],
            agent_ids=["off_1", "biz_1"],
            llm=CaptureLLM(),
        )

        assert "3" in captured[0]
        assert "proposal_submitted" in captured[0]

    def test_summarize_round_passes_agent_ids_to_prompt(self):
        """Идентификаторы агентов передаются в промпт LLM."""
        captured = []

        class CaptureLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(data={
                    "events_summary": "",
                    "key_decisions": [],
                    "tensions": [],
                    "agent_motivations": {},
                })

        narrator = WorldNarrator()
        narrator.summarize_round(
            round_num=0,
            events=[],
            agent_ids=["off_1", "biz_1", "auditor"],
            llm=CaptureLLM(),
        )

        assert "off_1" in captured[0]
        assert "auditor" in captured[0]

    def test_summarize_simulation_uses_round_summaries(self):
        """summarize_simulation объединяет сводки раундов в итоговый нарратив."""
        captured = []

        class SimLLM:
            def generate_structured(self, system, user, schema, temperature=0.0):
                captured.append(user)
                return StructuredLLMResponse(data={
                    "narrative": "Итоговая сводка симуляции.",
                    "key_findings": ["Коррупция обнаружена"],
                    "outcome": "Трибунал вынес обвинительный вердикт",
                })

        narrator = WorldNarrator()
        summaries = [
            RoundSummary(
                round_num=0,
                events_summary="Открытие дела",
                key_decisions=["Дело D-001"],
                tensions=[],
                agent_motivations={},
            ),
            RoundSummary(
                round_num=1,
                events_summary="Расследование",
                key_decisions=["Аудит"],
                tensions=["Напряжение"],
                agent_motivations={},
            ),
        ]

        result = narrator.summarize_simulation(
            summaries=summaries,
            final_reputation={"off_1": 8.5, "biz_1": 3.2},
            llm=SimLLM(),
        )

        assert "Итоговая сводка" in result["narrative"]
        assert len(result["key_findings"]) == 1
        assert "Открытие дела" in captured[0]
        assert "Расследование" in captured[0]

    def test_narrator_accumulates_summaries(self):
        """WorldNarrator накапливает сводки раундов."""
        narrator = WorldNarrator()
        llm = self._make_round_llm()

        narrator.summarize_round(
            round_num=0, events=[], agent_ids=["off_1"], llm=llm,
        )
        narrator.summarize_round(
            round_num=1, events=[], agent_ids=["off_1"], llm=llm,
        )

        assert len(narrator.round_summaries) == 2
        assert narrator.round_summaries[0].round_num == 0
        assert narrator.round_summaries[1].round_num == 1

    def test_summarize_round_with_empty_events(self):
        """summarize_round работает с пустым списком событий."""
        narrator = WorldNarrator()
        llm = self._make_round_llm({
            "events_summary": "Спокойный раунд без событий.",
            "key_decisions": [],
            "tensions": [],
            "agent_motivations": {},
        })

        result = narrator.summarize_round(
            round_num=5, events=[], agent_ids=["off_1"], llm=llm,
        )

        assert result.events_summary == "Спокойный раунд без событий."
        assert result.key_decisions == []
