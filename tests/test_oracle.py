"""Тесты LLM-оракула нарушений."""

from magistry_sim.llm import MockLLMProvider
from magistry_sim.oracle import OracleVerdict, ViolationOracle


class TestOracleVerdict:
    """Проверка структуры датакласса OracleVerdict."""

    def test_default_fields(self):
        verdict = OracleVerdict(case_id="D-001", violation_type="взятка")
        assert verdict.case_id == "D-001"
        assert verdict.violation_type == "взятка"
        assert verdict.agents_involved == []
        assert verdict.confidence == 0.0
        assert verdict.evidence == []
        assert verdict.reasoning == ""

    def test_all_fields(self):
        verdict = OracleVerdict(
            case_id="D-005",
            violation_type="фаворитизм",
            agents_involved=["off_1", "biz_2"],
            confidence=0.92,
            evidence=["Приватные сообщения", "Связь в графе"],
            reasoning="Обнаружена скрытая связь.",
        )
        assert verdict.case_id == "D-005"
        assert verdict.violation_type == "фаворитизм"
        assert len(verdict.agents_involved) == 2
        assert verdict.confidence == 0.92
        assert len(verdict.evidence) == 2
        assert verdict.reasoning == "Обнаружена скрытая связь."


class TestViolationOracle:
    """Проверка оракула нарушений."""

    def test_empty_violations(self):
        """Оракул возвращает пустой список при отсутствии нарушений."""
        mock = MockLLMProvider(
            structured_responses={
                "Определи все нарушения": {
                    "violations": [],
                }
            }
        )
        oracle = ViolationOracle(llm=mock)
        verdicts = oracle.analyze(events=[], messages=[], cases={})
        assert verdicts == []

    def test_finds_violations(self):
        """Оракул обнаруживает нарушения из ответа LLM."""
        mock = MockLLMProvider(
            structured_responses={
                "Определи все нарушения": {
                    "violations": [
                        {
                            "case_id": "D-001",
                            "violation_type": "взятка",
                            "agents_involved": ["off_1", "biz_1"],
                            "confidence": 0.95,
                            "evidence": [
                                "Приватные сообщения",
                                "Перевод средств",
                            ],
                            "reasoning": "Обнаружен скрытый перевод.",
                        },
                        {
                            "case_id": "D-003",
                            "violation_type": "фаворитизм",
                            "agents_involved": ["off_2"],
                            "confidence": 0.7,
                            "evidence": ["Связь в графе"],
                            "reasoning": "Победитель --- друг владельца.",
                        },
                    ],
                }
            }
        )
        oracle = ViolationOracle(llm=mock)
        verdicts = oracle.analyze(
            events=[
                {"round": 1, "agent_id": "off_1",
                 "event_type": "case_opened", "payload": {}},
            ],
            messages=[
                {"round": 1, "from_id": "off_1", "to_id": "biz_1",
                 "content": "Обсудим условия", "private": True},
            ],
            cases={
                "D-001": {
                    "case_type": "procurement",
                    "title": "Закупка серверов",
                    "owner_id": "off_1",
                    "decision": "biz_1",
                },
                "D-003": {
                    "case_type": "hiring",
                    "title": "Найм сотрудника",
                    "owner_id": "off_2",
                    "decision": "biz_3",
                },
            },
        )
        assert len(verdicts) == 2
        assert verdicts[0].case_id == "D-001"
        assert verdicts[0].violation_type == "взятка"
        assert verdicts[0].confidence == 0.95
        assert len(verdicts[0].agents_involved) == 2
        assert len(verdicts[0].evidence) == 2
        assert verdicts[1].case_id == "D-003"
        assert verdicts[1].violation_type == "фаворитизм"

    def test_llm_error_returns_empty(self):
        """При ошибке LLM оракул возвращает пустой список."""

        class BrokenLLM:
            def generate(self, system, user, temperature=0.0):
                raise RuntimeError("LLM недоступен")

            def generate_structured(self, system, user, schema,
                                    temperature=0.0):
                raise RuntimeError("LLM недоступен")

        oracle = ViolationOracle(llm=BrokenLLM())
        verdicts = oracle.analyze(events=[], messages=[], cases={})
        assert verdicts == []

    def test_mock_default_returns_empty(self):
        """MockLLMProvider без настроенных ответов возвращает пустой список."""
        mock = MockLLMProvider()
        oracle = ViolationOracle(llm=mock)
        verdicts = oracle.analyze(events=[], messages=[], cases={})
        assert verdicts == []
