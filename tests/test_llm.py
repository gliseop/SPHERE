"""Тесты LLM-провайдера."""

from magistry_sim.llm import (
    LLMProvider,
    LLMResponse,
    MockLLMProvider,
    StructuredLLMResponse,
    create_provider,
)


class TestMockLLMProvider:
    def test_default_response(self):
        provider = MockLLMProvider()
        resp = provider.generate(system="sys", user="hello")
        assert isinstance(resp, LLMResponse)
        assert resp.model == "mock"
        assert "Mock-ответ" in resp.text

    def test_keyed_response(self):
        provider = MockLLMProvider(
            responses={"привет": "Здравствуйте!"}
        )
        resp = provider.generate(system="", user="привет, как дела")
        assert resp.text == "Здравствуйте!"

    def test_call_count(self):
        provider = MockLLMProvider()
        assert provider.call_count == 0
        provider.generate(system="", user="1")
        provider.generate(system="", user="2")
        assert provider.call_count == 2

    def test_protocol_compliance(self):
        provider = MockLLMProvider()
        assert isinstance(provider, LLMProvider)


class TestStructuredOutput:
    """Тесты generate_structured для LLM-провайдеров."""

    def test_mock_returns_schema_compliant_dict(self):
        """MockLLMProvider.generate_structured возвращает dict по схеме."""
        provider = MockLLMProvider()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "score": {"type": "number"},
            },
            "required": ["name", "score"],
        }
        result = provider.generate_structured(
            system="test", user="test", schema=schema
        )
        assert isinstance(result, StructuredLLMResponse)
        assert isinstance(result.data, dict)
        assert "name" in result.data
        assert "score" in result.data

    def test_mock_uses_predefined_structured_responses(self):
        """MockLLMProvider возвращает предопределённый structured-ответ."""
        provider = MockLLMProvider(
            structured_responses={"interview": {"q1": "answer1"}}
        )
        result = provider.generate_structured(
            system="", user="interview question", schema={}
        )
        assert result.data == {"q1": "answer1"}

    def test_mock_stub_generates_defaults_for_schema(self):
        """Заглушка генерирует значения по умолчанию из типов схемы."""
        provider = MockLLMProvider()
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "count": {"type": "number"},
                "active": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "count", "active", "tags"],
        }
        result = provider.generate_structured(
            system="test", user="test", schema=schema
        )
        assert isinstance(result.data["title"], str)
        assert isinstance(result.data["count"], (int, float))
        assert isinstance(result.data["active"], bool)
        assert isinstance(result.data["tags"], list)

    def test_structured_response_has_model_and_usage(self):
        """StructuredLLMResponse содержит model и usage."""
        resp = StructuredLLMResponse(data={"key": "val"})
        assert resp.model == "mock"
        assert resp.usage == {}

    def test_mock_structured_falls_back_to_stub(self):
        """Если нет совпадения в structured_responses, возвращает заглушку."""
        provider = MockLLMProvider(
            structured_responses={"specific": {"found": True}}
        )
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = provider.generate_structured(
            system="", user="no match here", schema=schema
        )
        assert "name" in result.data
        assert isinstance(result.data["name"], str)


class TestCreateProvider:
    def test_create_mock(self):
        provider = create_provider(mock=True)
        assert isinstance(provider, MockLLMProvider)
