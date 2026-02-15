"""Тесты модуля рефлексии."""

import pytest
from unittest.mock import MagicMock
from magistry_sim.memory import MemoryStream
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
