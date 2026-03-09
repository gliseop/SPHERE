"""Тесты провайдера эмбеддингов."""

from pathlib import Path

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

    def test_real_returns_openai_provider(self, monkeypatch):
        class _FakeOpenAIEmbeddingProvider:
            def __init__(self, model="text-embedding-3-small", api_key=None, base_url=None):
                self._model = model
                self._client_kwargs = {"api_key": api_key, "base_url": base_url}

        monkeypatch.setattr(
            "magistry_lc.llm.embeddings.OpenAIEmbeddingProvider",
            _FakeOpenAIEmbeddingProvider,
        )
        provider = create_embedding_provider(mock=False)
        assert isinstance(provider, _FakeOpenAIEmbeddingProvider)

    def test_real_loads_dotenv_from_cwd(self, monkeypatch, tmp_path: Path):
        class _FakeOpenAIEmbeddingProvider:
            def __init__(self, model="text-embedding-3-small", api_key=None, base_url=None):
                self._model = model
                self._client_kwargs = {"api_key": api_key, "base_url": base_url}

        monkeypatch.setattr(
            "magistry_lc.llm.embeddings.OpenAIEmbeddingProvider",
            _FakeOpenAIEmbeddingProvider,
        )
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "OPENAI_API_KEY=dotenv-embed-key\n"
            "OPENAI_BASE_URL=https://embed.example/v1\n"
            "EMBEDDING_MODEL=text-embedding-dotenv\n",
            encoding="utf-8",
        )

        provider = create_embedding_provider(mock=False)

        assert isinstance(provider, _FakeOpenAIEmbeddingProvider)
        assert provider._client_kwargs["api_key"] == "dotenv-embed-key"
        assert provider._client_kwargs["base_url"] == "https://embed.example/v1"
        assert provider._model == "text-embedding-dotenv"
