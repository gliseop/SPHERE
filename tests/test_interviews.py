"""Тесты модели данных и поиска по библиотеке интервью."""

import pytest
from magistry_sim.interviews import (
    Interview,
    InterviewLibrary,
    INTERVIEW_QUESTIONS,
    generate_interview,
)
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
)
from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider


class TestInterviewModel:
    def test_interview_has_required_fields(self):
        interview = Interview(
            id="int_001",
            archetype="opportunist",
            role="чиновник",
            hexaco={"honesty_humility": 30, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 40,
                    "conscientiousness": 45, "openness": 55},
            dark_triad={"narcissism": 60, "machiavellianism": 70,
                        "psychopathy": 30},
            interview={"q1": "answer1", "q2": "answer2"},
            expert_psychologist="анализ психолога",
            expert_economist="анализ экономиста",
        )
        assert interview.id == "int_001"
        assert interview.archetype == "opportunist"

    def test_interview_full_text(self):
        interview = Interview(
            id="int_002",
            archetype="idealist",
            role="аудитор",
            hexaco={"honesty_humility": 90, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 70,
                    "conscientiousness": 85, "openness": 55},
            dark_triad={"narcissism": 10, "machiavellianism": 5,
                        "psychopathy": 5},
            interview={"Как вы принимаете решения?": "Тщательно взвешиваю"},
            expert_psychologist="высокая добросовестность",
            expert_economist="риск-нейтральный",
        )
        text = interview.full_text()
        assert "Как вы принимаете решения?" in text
        assert "Тщательно взвешиваю" in text
        assert "высокая добросовестность" in text

    def test_questions_count(self):
        assert len(INTERVIEW_QUESTIONS) == 10


class TestInterviewLibrary:
    def _make_library(self) -> InterviewLibrary:
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
            interview={"q1": "ответ оппортуниста про закупки"},
            expert_psychologist="склонен к риску",
            expert_economist="ищет выгоду",
        ))
        lib.add(Interview(
            id="int_002",
            archetype="idealist",
            role="аудитор",
            hexaco={"honesty_humility": 90, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 70,
                    "conscientiousness": 85, "openness": 55},
            dark_triad={"narcissism": 10, "machiavellianism": 5,
                        "psychopathy": 5},
            interview={"q1": "ответ идеалиста про справедливость"},
            expert_psychologist="высокая честность",
            expert_economist="нетерпим к коррупции",
        ))
        return lib

    def test_add_and_len(self):
        lib = self._make_library()
        assert len(lib) == 2

    def test_search_by_text(self):
        lib = self._make_library()
        results = lib.search("закупки выгода", top_k=1)
        assert len(results) == 1
        assert results[0].archetype == "opportunist"

    def test_search_empty_library(self):
        lib = InterviewLibrary()
        results = lib.search("любой запрос")
        assert results == []

    def test_load_save_jsonl(self, tmp_path):
        lib = self._make_library()
        path = tmp_path / "interviews.jsonl"
        lib.save_jsonl(path)

        loaded = InterviewLibrary.load_jsonl(path)
        assert len(loaded) == 2
        assert loaded._interviews[0].id == "int_001"

    def test_load_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        lib = InterviewLibrary.load_jsonl(path)
        assert len(lib) == 0


class TestInterviewGeneration:
    def _make_personality(self, hh: int = 30, mach: int = 70) -> AgentPersonality:
        return AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=hh,
                emotionality=50,
                extraversion=60,
                agreeableness=40,
                conscientiousness=45,
                openness=55,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=60,
                machiavellianism=mach,
                psychopathy=30,
            ),
        )

    def test_generate_interview_returns_interview(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)
        personality = self._make_personality()

        result = generate_interview(
            personality=personality,
            role="чиновник",
            archetype="opportunist",
            llm=llm,
            embedder=embedder,
            interview_id="test_001",
        )
        assert result.id == "test_001"
        assert result.archetype == "opportunist"
        assert result.role == "чиновник"
        assert len(result.embedding) == 384

    def test_generate_interview_has_expert_assessments(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)
        personality = self._make_personality()

        result = generate_interview(
            personality=personality,
            role="чиновник",
            archetype="opportunist",
            llm=llm,
            embedder=embedder,
            interview_id="test_002",
        )
        assert result.expert_psychologist != ""
        assert result.expert_economist != ""

    def test_generate_interview_calls_llm(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)
        personality = self._make_personality()

        generate_interview(
            personality=personality,
            role="бизнесмен",
            archetype="initiator",
            llm=llm,
            embedder=embedder,
            interview_id="test_003",
        )
        # 1 вызов на интервью + 1 на психолога + 1 на экономиста = 3
        assert llm.call_count == 3
