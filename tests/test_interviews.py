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
from magistry_sim.llm import (
    LLMResponse,
    MockLLMProvider,
    MockEmbeddingProvider,
    StructuredLLMResponse,
)


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

    def test_sample_returns_requested_count(self):
        """sample() возвращает запрошенное количество интервью."""
        lib = self._make_library()
        result = lib.sample(n=1, seed=42)
        assert len(result) == 1
        assert isinstance(result[0], Interview)

    def test_sample_with_different_seeds(self):
        """sample() с разными seed может возвращать разные результаты."""
        lib = InterviewLibrary()
        for i in range(10):
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
        results_seed_42 = [itv.id for itv in lib.sample(n=3, seed=42)]
        results_seed_99 = [itv.id for itv in lib.sample(n=3, seed=99)]
        # С разными seed хотя бы один элемент должен различаться
        # (вероятность совпадения при 10 элементах крайне мала)
        assert results_seed_42 != results_seed_99

    def test_sample_deterministic_with_same_seed(self):
        """sample() с одинаковым seed возвращает одинаковый результат."""
        lib = self._make_library()
        result1 = [itv.id for itv in lib.sample(n=2, seed=42)]
        result2 = [itv.id for itv in lib.sample(n=2, seed=42)]
        assert result1 == result2

    def test_sample_empty_library(self):
        """sample() из пустой библиотеки возвращает пустой список."""
        lib = InterviewLibrary()
        result = lib.sample(n=1, seed=42)
        assert result == []

    def test_sample_n_exceeds_library_size(self):
        """sample() при n > размера библиотеки возвращает все элементы."""
        lib = self._make_library()
        result = lib.sample(n=100, seed=42)
        assert len(result) == 2  # библиотека содержит 2 интервью


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
        llm = MockLLMProvider(
            structured_responses={
                "Проанализируй": {"analysis": "Экспертный анализ"},
            }
        )
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
        assert result.expert_psychologist == "Экспертный анализ"
        assert result.expert_economist == "Экспертный анализ"

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


class TestStructuredInterviewGeneration:
    """Тесты генерации интервью через structured output."""

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

    def test_generate_interview_uses_structured_output(self):
        """generate_interview вызывает generate_structured для ответов."""
        answers_data = {
            f"q{i+1}": f"Ответ на вопрос {i+1}" for i in range(10)
        }
        psych_data = {"analysis": "Психологический анализ"}
        econ_data = {"analysis": "Экономический анализ"}

        class StructuredLLM:
            def __init__(self):
                self._call = 0

            def generate(self, system, user, temperature=0.0):
                return LLMResponse(text="fallback")

            def generate_structured(self, system, user, schema, temperature=0.0):
                self._call += 1
                if self._call == 1:
                    return StructuredLLMResponse(data=answers_data)
                elif self._call == 2:
                    return StructuredLLMResponse(data=psych_data)
                else:
                    return StructuredLLMResponse(data=econ_data)

        personality = self._make_personality()
        embedder = MockEmbeddingProvider(dimensions=8)

        interview = generate_interview(
            personality=personality,
            role="чиновник",
            archetype="pragmatist",
            llm=StructuredLLM(),
            embedder=embedder,
            interview_id="test-structured-001",
        )

        # Каждый вопрос получает свой уникальный ответ
        answers = list(interview.interview.values())
        assert len(answers) == 10
        assert len(set(answers)) == 10
        assert answers[0] == "Ответ на вопрос 1"

    def test_generate_interview_structured_expert_assessments(self):
        """Экспертные оценки получены через structured output."""
        answers_data = {f"q{i+1}": f"ответ {i+1}" for i in range(10)}
        psych_data = {"analysis": "Склонен к риску, низкая эмпатия"}
        econ_data = {"analysis": "Ищет краткосрочную выгоду"}

        class StructuredLLM:
            def __init__(self):
                self._call = 0

            def generate(self, system, user, temperature=0.0):
                return LLMResponse(text="fallback")

            def generate_structured(self, system, user, schema, temperature=0.0):
                self._call += 1
                if self._call == 1:
                    return StructuredLLMResponse(data=answers_data)
                elif self._call == 2:
                    return StructuredLLMResponse(data=psych_data)
                else:
                    return StructuredLLMResponse(data=econ_data)

        personality = self._make_personality()
        embedder = MockEmbeddingProvider(dimensions=8)

        interview = generate_interview(
            personality=personality,
            role="чиновник",
            archetype="pragmatist",
            llm=StructuredLLM(),
            embedder=embedder,
            interview_id="test-structured-002",
        )

        assert interview.expert_psychologist == "Склонен к риску, низкая эмпатия"
        assert interview.expert_economist == "Ищет краткосрочную выгоду"

    def test_generate_interview_structured_three_calls(self):
        """generate_interview делает ровно 3 вызова generate_structured."""
        call_count = 0

        class CountingLLM:
            def generate(self, system, user, temperature=0.0):
                return LLMResponse(text="fallback")

            def generate_structured(self, system, user, schema, temperature=0.0):
                nonlocal call_count
                call_count += 1
                if "q1" in schema.get("properties", {}):
                    return StructuredLLMResponse(
                        data={f"q{i+1}": f"a{i+1}" for i in range(10)}
                    )
                return StructuredLLMResponse(data={"analysis": "text"})

        personality = self._make_personality()
        embedder = MockEmbeddingProvider(dimensions=8)

        generate_interview(
            personality=personality,
            role="чиновник",
            archetype="pragmatist",
            llm=CountingLLM(),
            embedder=embedder,
            interview_id="test-structured-003",
        )

        assert call_count == 3
