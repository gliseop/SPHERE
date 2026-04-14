"""LLM-провайдер: протокол, mock, кеш, OpenAI-совместимый."""

from .protocols import LLMProvider, LLMResponse, StructuredLLMResponse
from .embeddings import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)
from .providers import MockLLMProvider, OpenAICompatibleProvider, create_provider
from .cache import LLMCache
from ._utils import LLMCallError
from .caller import LLMCaller, create_llm_provider

__all__ = [
    # protocols
    "LLMProvider",
    "LLMResponse",
    "StructuredLLMResponse",
    # embeddings
    "EmbeddingProvider",
    "MockEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "create_embedding_provider",
    # providers
    "MockLLMProvider",
    "OpenAICompatibleProvider",
    "create_provider",
    # cache
    "LLMCache",
    # utils
    "LLMCallError",
    # caller
    "LLMCaller",
    "create_llm_provider",
]
