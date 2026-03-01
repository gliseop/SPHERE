"""LLM-провайдер: протокол, mock, кеш, OpenAI-совместимый."""

from magistry_sim.llm.protocols import (
    LLMProvider,
    LLMResponse,
    StructuredLLMResponse,
)
from magistry_sim.llm.cache import LLMCache
from magistry_sim.llm.embeddings import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)
from magistry_sim.llm.providers import (
    MockLLMProvider,
    OpenAICompatibleProvider,
    create_provider,
)
from magistry_sim.llm._utils import (
    LLMCallError,
    _extract_json,
    _strip_think_tags,
)

__all__ = [
    # protocols
    "LLMProvider",
    "LLMResponse",
    "StructuredLLMResponse",
    # cache
    "LLMCache",
    # embeddings
    "EmbeddingProvider",
    "MockEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "create_embedding_provider",
    # providers
    "MockLLMProvider",
    "OpenAICompatibleProvider",
    "create_provider",
    # utils (public-facing)
    "LLMCallError",
    "_extract_json",
    "_strip_think_tags",
]
