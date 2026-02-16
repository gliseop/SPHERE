"""Тесты провайдера эмбеддингов."""

import math

from magistry_sim.llm import (
    EmbeddingProvider,
    LocalEmbeddingProvider,
    MockEmbeddingProvider,
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


class TestLocalEmbeddingProvider:
    def test_lazy_loading(self):
        provider = LocalEmbeddingProvider()
        assert provider._model is None

    def test_embed_returns_384_dimensions(self):
        provider = LocalEmbeddingProvider()
        emb = provider.embed("тестовый текст")
        assert len(emb) == 384
        assert all(isinstance(v, float) for v in emb)

    def test_embed_batch(self):
        provider = LocalEmbeddingProvider()
        texts = ["первый текст", "второй текст"]
        embeddings = provider.embed_batch(texts)
        assert len(embeddings) == 2
        assert len(embeddings[0]) == 384
        assert len(embeddings[1]) == 384

    def test_normalized_vectors(self):
        provider = LocalEmbeddingProvider()
        emb = provider.embed("нормализованный вектор")
        norm = math.sqrt(sum(v * v for v in emb))
        assert abs(norm - 1.0) < 0.01

    def test_similar_texts_close_embeddings(self):
        provider = LocalEmbeddingProvider()
        e1 = provider.embed("коррупция и взятки")
        e2 = provider.embed("взяточничество и подкуп")
        e3 = provider.embed("программирование на Python")
        cosine_similar = sum(a * b for a, b in zip(e1, e2))
        cosine_different = sum(a * b for a, b in zip(e1, e3))
        assert cosine_similar > cosine_different

    def test_protocol_compliance(self):
        provider = LocalEmbeddingProvider()
        assert isinstance(provider, EmbeddingProvider)


class TestCreateEmbeddingProvider:
    def test_mock_returns_mock_provider(self):
        provider = create_embedding_provider(mock=True)
        assert isinstance(provider, MockEmbeddingProvider)
        emb = provider.embed("test")
        assert len(emb) == 384

    def test_real_returns_local_provider(self):
        provider = create_embedding_provider(mock=False)
        assert isinstance(provider, LocalEmbeddingProvider)
