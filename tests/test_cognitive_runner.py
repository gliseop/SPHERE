"""Тесты когнитивного агента (CognitiveAgentRunner)."""

import pytest
from unittest.mock import MagicMock
from magistry_sim.cognitive_runner import CognitiveAgentRunner
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import AgentPlan
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
    NeutralizationTechnique,
)
from magistry_sim.config import AgentProfile, Capability
from magistry_sim.state import WorldState


def _make_corrupt_personality():
    return AgentPersonality(
        hexaco=HEXACOProfile(
            honesty_humility=15,
            emotionality=30,
            extraversion=70,
            agreeableness=20,
            conscientiousness=50,
            openness=60,
        ),
        dark_triad=DarkTriadProfile(
            narcissism=80, machiavellianism=85, psychopathy=40
        ),
        neutralization_techniques=[
            NeutralizationTechnique.EVERYONE_DOES_IT,
            NeutralizationTechnique.DEFENSE_OF_NECESSITY,
        ],
        biography="Козлов Иван Михайлович, 45 лет, чиновник среднего звена...",
    )


class TestCognitiveAgentRunner:
    def test_implements_protocol(self):
        from magistry_sim.agents import AgentRunner

        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        assert isinstance(runner, AgentRunner)

    def test_get_or_create_memory(self):
        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        stream = runner.get_or_create_memory("off_1")
        assert isinstance(stream, MemoryStream)
        assert stream.agent_id == "off_1"
        # Повторный вызов возвращает тот же объект
        assert runner.get_or_create_memory("off_1") is stream

    def test_get_or_create_memory_different_agents(self):
        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        stream1 = runner.get_or_create_memory("off_1")
        stream2 = runner.get_or_create_memory("biz_1")
        assert stream1 is not stream2
        assert stream1.agent_id == "off_1"
        assert stream2.agent_id == "biz_1"

    def test_observe_records_to_memory(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="7")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        runner.observe(
            agent_id="off_1",
            event="biz_1 подал заявку на тендер D-001",
            round_num=2,
        )
        stream = runner.get_or_create_memory("off_1")
        assert len(stream) == 1
        assert stream.records[0].content == "biz_1 подал заявку на тендер D-001"

    def test_observe_importance_from_llm(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="9")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 4
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        runner.observe(agent_id="off_1", event="test event", round_num=0)
        stream = runner.get_or_create_memory("off_1")
        assert stream.records[0].importance == 9.0

    def test_observe_importance_fallback_on_invalid(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="not a number")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 4
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        runner.observe(agent_id="off_1", event="test event", round_num=0)
        stream = runner.get_or_create_memory("off_1")
        assert stream.records[0].importance == 5.0

    def test_run_turn_returns_actions(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text='[{"tool": "talk_to", "args": {"agent_id": "biz_1", "message": "Здравствуйте", "private": true}}]'
        )
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)

        state = WorldState()
        profile = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
            capabilities=[Capability(action="open_case", case_types=["procurement"])],
            personality=_make_corrupt_personality(),
        )
        state.agents["off_1"] = profile

        actions = runner.run_turn(
            agent_id="off_1",
            situation="Раунд 0. Вы — начальник отдела закупок.",
            tools=["talk_to", "open_case"],
            state=state,
        )
        assert isinstance(actions, list)

    def test_run_turn_without_personality(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="[]")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)

        state = WorldState()
        profile = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
        )
        state.agents["off_1"] = profile

        actions = runner.run_turn(
            agent_id="off_1",
            situation="Раунд 0.",
            tools=["talk_to"],
            state=state,
        )
        assert isinstance(actions, list)

    def test_run_reply_returns_string(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="Спасибо за предложение.")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)

        state = WorldState()
        profile = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
            personality=_make_corrupt_personality(),
        )
        state.agents["off_1"] = profile

        reply = runner.run_reply(
            agent_id="off_1",
            message="Предлагаю обсудить условия.",
            sender_id="biz_1",
            context="Текущая ситуация",
            state=state,
        )
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_run_reply_records_observation(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="Понял, спасибо.")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)

        state = WorldState()
        profile = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник",
        )
        state.agents["off_1"] = profile

        runner.run_reply(
            agent_id="off_1",
            message="Привет!",
            sender_id="biz_1",
            context="",
            state=state,
        )
        stream = runner.get_or_create_memory("off_1")
        assert len(stream) >= 1
        assert "biz_1" in stream.records[0].content

    def test_get_or_create_plan(self):
        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        plan = runner.get_or_create_plan("off_1")
        assert isinstance(plan, AgentPlan)
        assert runner.get_or_create_plan("off_1") is plan

    def test_interview_library_loading(self, tmp_path):
        """CognitiveAgentRunner загружает библиотеку интервью из JSONL."""
        from magistry_sim.interviews import Interview, InterviewLibrary
        from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider

        lib = InterviewLibrary()
        lib.add(Interview(
            id="int_001",
            archetype="opportunist",
            role="чиновник",
            hexaco={"honesty_humility": 30, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 40,
                    "conscientiousness": 45, "openness": 55},
            dark_triad={"narcissism": 60, "machiavellianism": 70,
                        "psychopathy": 30},
            interview={"q1": "Я всегда ищу выгоду в сделках"},
            expert_psychologist="склонен к рискованным решениям",
            expert_economist="высокая склонность к оппортунизму",
        ))
        path = tmp_path / "interviews.jsonl"
        lib.save_jsonl(path)

        runner = CognitiveAgentRunner(
            llm_provider=MockLLMProvider(),
            embedder=MockEmbeddingProvider(dimensions=384),
            verbose=False,
            interview_library_path=path,
        )
        assert runner._interview_library is not None
        assert len(runner._interview_library) == 1

    def test_interview_library_none_by_default(self):
        """Без пути библиотека интервью = None."""
        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        assert runner._interview_library is None

    def test_interview_assignment_uses_sample_not_search(self, tmp_path):
        """Интервью назначаются через sample(), а не search()."""
        from magistry_sim.interviews import Interview, InterviewLibrary
        from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider
        from unittest.mock import patch

        lib = InterviewLibrary()
        for i in range(5):
            lib.add(Interview(
                id=f"itv-{i}",
                archetype="pragmatist",
                role="чиновник",
                hexaco={"honesty_humility": 30, "emotionality": 50,
                        "extraversion": 60, "agreeableness": 40,
                        "conscientiousness": 45, "openness": 55},
                dark_triad={"narcissism": 60, "machiavellianism": 70,
                            "psychopathy": 30},
                interview={"q": f"answer-{i}"},
                expert_psychologist=f"psych-{i}",
                expert_economist=f"econ-{i}",
            ))
        path = tmp_path / "interviews.jsonl"
        lib.save_jsonl(path)

        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=8)
        runner = CognitiveAgentRunner(
            llm_provider=llm,
            embedder=embedder,
            interview_library_path=path,
        )

        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
            personality=_make_corrupt_personality(),
        )

        # Подменяем sample чтобы убедиться что вызывается именно он
        sample_called = False
        original_sample = runner._interview_library.sample

        def track_sample(*args, **kwargs):
            nonlocal sample_called
            sample_called = True
            return original_sample(*args, **kwargs)

        runner._interview_library.sample = track_sample

        system_prompt, _ = runner._build_cognitive_prompt(
            agent_id="off_1",
            situation="Раунд 0.",
            tools=["talk_to"],
            state=state,
        )
        assert sample_called, "sample() должен вызываться вместо search()"
        assert "Нарративное интервью" in system_prompt

    def test_interview_assignment_deterministic_per_agent(self, tmp_path):
        """Один и тот же agent_id всегда получает одно и то же интервью."""
        from magistry_sim.interviews import Interview, InterviewLibrary
        from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider

        lib = InterviewLibrary()
        for i in range(5):
            lib.add(Interview(
                id=f"itv-{i}",
                archetype="pragmatist",
                role="чиновник",
                hexaco={"honesty_humility": 30, "emotionality": 50,
                        "extraversion": 60, "agreeableness": 40,
                        "conscientiousness": 45, "openness": 55},
                dark_triad={"narcissism": 60, "machiavellianism": 70,
                            "psychopathy": 30},
                interview={"q": f"answer-{i}"},
                expert_psychologist=f"psych-{i}",
                expert_economist=f"econ-{i}",
            ))
        path = tmp_path / "interviews.jsonl"
        lib.save_jsonl(path)

        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=8)
        runner = CognitiveAgentRunner(
            llm_provider=llm,
            embedder=embedder,
            interview_library_path=path,
        )

        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
            personality=_make_corrupt_personality(),
        )

        prompt1, _ = runner._build_cognitive_prompt(
            "off_1", "Раунд 0.", ["talk_to"], state,
        )
        prompt2, _ = runner._build_cognitive_prompt(
            "off_1", "Раунд 0.", ["talk_to"], state,
        )
        assert prompt1 == prompt2, "Один agent_id должен получать одинаковое интервью"
