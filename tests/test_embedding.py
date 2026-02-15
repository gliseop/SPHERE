"""Тесты провайдера эмбеддингов."""

from magistry_sim.llm import MockEmbeddingProvider, EmbeddingProvider


class TestEmbeddingProvider:
    def test_mock_returns_fixed_length(self):
        provider = MockEmbeddingProvider(dimensions=8)
        emb = provider.embed("test text")
        assert len(emb) == 8

    def test_mock_deterministic(self):
        provider = MockEmbeddingProvider(dimensions=8)
        e1 = provider.embed("same text")
        e2 = provider.embed("same text")
        assert e1 == e2

    def test_mock_different_for_different_text(self):
        provider = MockEmbeddingProvider(dimensions=8)
        e1 = provider.embed("alpha")
        e2 = provider.embed("beta")
        assert e1 != e2

    def test_protocol_compliance(self):
        provider = MockEmbeddingProvider(dimensions=8)
        assert isinstance(provider, EmbeddingProvider)
