"""Тесты провайдера эмбеддингов."""

from magistry_lc.llm import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)


class TestMockEmbeddingProvider:
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


class TestCreateEmbeddingProvider:
    def test_mock_returns_mock_provider(self):
        provider = create_embedding_provider(mock=True)
        assert isinstance(provider, MockEmbeddingProvider)
        emb = provider.embed("test")
        assert len(emb) == 384

    def test_real_returns_openai_provider(self):
        provider = create_embedding_provider(mock=False)
        assert isinstance(provider, OpenAIEmbeddingProvider)
