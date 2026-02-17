"""Тесты модуля рефлексии."""

import pytest
from unittest.mock import MagicMock
from magistry_sim.llm import LLMResponse
from magistry_sim.memory import MemoryRecord, MemoryStream
from magistry_sim.reflection import (
    should_reflect,
    generate_focal_points,
    synthesize_insights,
    run_reflection_cycle,
    REFLECTION_THRESHOLD,
)


class TestShouldReflect:
    def test_below_threshold(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="minor event", importance=3.0, kind="observation", round_num=0)
        assert not should_reflect(stream)

    def test_at_threshold(self):
        stream = MemoryStream(agent_id="off_1")
        for i in range(10):
            stream.add(content=f"event {i}", importance=5.0, kind="observation", round_num=i)
        assert stream.importance_since_reflection >= REFLECTION_THRESHOLD
        assert should_reflect(stream)


class TestReflectionCycle:
    def test_run_reflection_adds_records(self):
        stream = MemoryStream(agent_id="off_1")
        for i in range(10):
            stream.add(
                content=f"event {i}",
                importance=6.0,
                kind="observation",
                round_num=i,
                embedding=[float(i) / 10, 0.5, 0.5],
            )

        mock_llm = MagicMock()
        mock_llm.generate.side_effect = [
            MagicMock(text='["Каковы связи между off_1 и biz_1?", "Есть ли угроза разоблачения?", "Каковы финансовые перспективы?"]'),
            MagicMock(text="off_1 и biz_1 встречаются подозрительно часто"),
            MagicMock(text="Угроза разоблачения пока невысока"),
            MagicMock(text="Финансовая ситуация стабильна"),
        ]

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5, 0.5, 0.5]

        reflections = run_reflection_cycle(
            stream=stream,
            llm=mock_llm,
            embedder=mock_embedder,
            current_round=10,
        )

        assert len(reflections) == 3
        assert all(r.kind == "reflection" for r in reflections)
        assert stream.importance_since_reflection == 0.0


class TestNeutralizationTechniques:
    """Тесты передачи техник нейтрализации в рефлексию."""

    def test_synthesize_insights_includes_neutralization_techniques(self):
        """Промпт синтеза включает техники нейтрализации агента."""
        captured_prompts = []

        class CaptureLLM:
            def generate(self, system, user, temperature=0.0):
                captured_prompts.append(user)
                return LLMResponse(text="Инсайт с denial_of_injury")

        from magistry_sim.personality import NeutralizationTechnique

        techniques = [
            NeutralizationTechnique.DENIAL_OF_INJURY,
            NeutralizationTechnique.EVERYONE_DOES_IT,
        ]
        memories = [
            MemoryRecord(
                id="m1",
                created_at=0,
                content="test",
                importance=5.0,
                kind="observation",
                embedding=[],
            ),
        ]
        synthesize_insights(
            "Стоит ли рисковать?",
            memories,
            CaptureLLM(),
            "agent_1",
            neutralization_techniques=techniques,
        )
        assert "denial_of_injury" in captured_prompts[-1]
        assert "everyone_does_it" in captured_prompts[-1]

    def test_synthesize_insights_works_without_techniques(self):
        """Синтез работает без техник нейтрализации (обратная совместимость)."""

        class SimpleLLM:
            def generate(self, system, user, temperature=0.0):
                return LLMResponse(text="Простой инсайт")

        memories = [
            MemoryRecord(
                id="m1",
                created_at=0,
                content="test",
                importance=5.0,
                kind="observation",
                embedding=[],
            ),
        ]
        result = synthesize_insights(
            "Что делать?",
            memories,
            SimpleLLM(),
            "agent_1",
        )
        assert result == "Простой инсайт"

    def test_run_reflection_cycle_passes_techniques(self):
        """run_reflection_cycle пробрасывает техники в synthesize_insights."""
        captured_prompts = []

        class CaptureLLM:
            def __init__(self):
                self._call = 0

            def generate(self, system, user, temperature=0.0):
                self._call += 1
                if self._call == 1:
                    return LLMResponse(
                        text='["Вопрос 1", "Вопрос 2", "Вопрос 3"]'
                    )
                captured_prompts.append(user)
                return LLMResponse(text="Инсайт")

        class MockEmbedder:
            def embed(self, text):
                return [0.5, 0.5, 0.5]

        from magistry_sim.personality import NeutralizationTechnique

        techniques = [NeutralizationTechnique.DENIAL_OF_INJURY]
        stream = MemoryStream(agent_id="test")
        for i in range(10):
            stream.add(
                content=f"event {i}",
                importance=6.0,
                kind="observation",
                round_num=i,
                embedding=[float(i) / 10, 0.5, 0.5],
            )

        run_reflection_cycle(
            stream=stream,
            llm=CaptureLLM(),
            embedder=MockEmbedder(),
            current_round=10,
            neutralization_techniques=techniques,
        )
        assert any("denial_of_injury" in p for p in captured_prompts)
