"""Тесты LLM-провайдера."""

from magistry_sim.llm import (
    LLMProvider,
    LLMResponse,
    MockLLMProvider,
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


class TestCreateProvider:
    def test_create_mock(self):
        provider = create_provider(mock=True)
        assert isinstance(provider, MockLLMProvider)
