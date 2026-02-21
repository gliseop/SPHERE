"""Тесты LLM-классификатора нарушений."""

from magistry_sim.classifier import ViolationClassifier, CaseClassification
from magistry_sim.llm import MockLLMProvider


class TestViolationClassifier:
    """Постфактум-классификация нарушений."""

    def test_classify_returns_classification(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": True,
                    "violation_type": "фаворитизм",
                    "confidence": 0.85,
                    "evidence": ["Приватные сообщения с победителем"],
                    "reasoning": "Чиновник общался приватно с подрядчиком.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        result = classifier.classify_case(
            case_data={"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement"},
            events=[],
            messages=[],
        )
        assert isinstance(result, CaseClassification)

    def test_classify_populates_fields(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": True,
                    "violation_type": "взятка",
                    "confidence": 0.92,
                    "evidence": ["Перевод средств", "Приватные встречи"],
                    "reasoning": "Обнаружен скрытый перевод средств.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        result = classifier.classify_case(
            case_data={"id": "D-005", "owner_id": "off_2",
                       "decision": "biz_3", "case_type": "procurement"},
            events=[],
            messages=[],
        )
        assert result.case_id == "D-005"
        assert result.is_violation is True
        assert result.violation_type == "взятка"
        assert result.confidence == 0.92
        assert len(result.evidence) == 2
        assert result.reasoning == "Обнаружен скрытый перевод средств."

    def test_classify_no_violation(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": False,
                    "violation_type": "",
                    "confidence": 0.05,
                    "evidence": [],
                    "reasoning": "Чистая процедура.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        result = classifier.classify_case(
            case_data={"id": "D-010", "owner_id": "off_1",
                       "decision": "biz_2", "case_type": "hiring"},
            events=[],
            messages=[],
        )
        assert result.is_violation is False
        assert result.confidence == 0.05

    def test_classify_filters_events_by_case_id(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": False,
                    "violation_type": "",
                    "confidence": 0.1,
                    "evidence": [],
                    "reasoning": "Нет нарушений.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        events = [
            {"event_type": "case_opened", "agent_id": "off_1",
             "payload": {"case_id": "D-001"}},
            {"event_type": "case_opened", "agent_id": "off_2",
             "payload": {"case_id": "D-999"}},
        ]
        result = classifier.classify_case(
            case_data={"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement"},
            events=events,
            messages=[],
        )
        assert isinstance(result, CaseClassification)

    def test_classify_filters_messages_by_owner(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": False,
                    "violation_type": "",
                    "confidence": 0.1,
                    "evidence": [],
                    "reasoning": "Нет нарушений.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        messages = [
            {"from_id": "off_1", "to_id": "biz_1",
             "content": "Привет", "private": True},
            {"from_id": "off_2", "to_id": "biz_2",
             "content": "Другое", "private": False},
        ]
        result = classifier.classify_case(
            case_data={"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement"},
            events=[],
            messages=messages,
        )
        assert isinstance(result, CaseClassification)

    def test_classify_batch(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": False,
                    "violation_type": "",
                    "confidence": 0.1,
                    "evidence": [],
                    "reasoning": "Чистая сделка.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        cases = {
            "D-001": {"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement",
                       "closed_at": 5},
            "D-002": {"id": "D-002", "owner_id": "off_1",
                       "decision": "biz_2", "case_type": "hiring",
                       "closed_at": 7},
        }
        results = classifier.classify_all(
            cases=cases, events=[], messages=[],
        )
        assert len(results) == 2

    def test_classify_batch_skips_investigation(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": False,
                    "violation_type": "",
                    "confidence": 0.1,
                    "evidence": [],
                    "reasoning": "Чистая сделка.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        cases = {
            "D-001": {"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement",
                       "closed_at": 5},
            "D-002": {"id": "D-002", "owner_id": "off_1",
                       "decision": "biz_2", "case_type": "investigation",
                       "closed_at": 7},
        }
        results = classifier.classify_all(
            cases=cases, events=[], messages=[],
        )
        assert len(results) == 1

    def test_classify_batch_skips_open_cases(self):
        mock = MockLLMProvider(
            structured_responses={
                "classify_violation": {
                    "is_violation": False,
                    "violation_type": "",
                    "confidence": 0.1,
                    "evidence": [],
                    "reasoning": "Чистая сделка.",
                }
            }
        )
        classifier = ViolationClassifier(llm=mock)
        cases = {
            "D-001": {"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement",
                       "closed_at": 5},
            "D-002": {"id": "D-002", "owner_id": "off_1",
                       "decision": "", "case_type": "procurement",
                       "closed_at": None},
        }
        results = classifier.classify_all(
            cases=cases, events=[], messages=[],
        )
        assert len(results) == 1

    def test_classify_case_llm_error_returns_default(self):
        """При ошибке LLM возвращается классификация по умолчанию."""

        class BrokenLLM:
            def generate(self, system, user, temperature=0.0):
                raise RuntimeError("LLM недоступен")

            def generate_structured(self, system, user, schema,
                                    temperature=0.0):
                raise RuntimeError("LLM недоступен")

        classifier = ViolationClassifier(llm=BrokenLLM())
        result = classifier.classify_case(
            case_data={"id": "D-001", "owner_id": "off_1",
                       "decision": "biz_1", "case_type": "procurement"},
            events=[],
            messages=[],
        )
        assert result.case_id == "D-001"
        assert result.is_violation is False
        assert result.confidence == 0.0
