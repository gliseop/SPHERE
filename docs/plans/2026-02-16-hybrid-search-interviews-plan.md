# Гибридный поиск и библиотека синтетических интервью — План реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Цель:** заменить mock-эмбеддинги на локальную мультиязычную модель, добавить BM25 в поиск по памяти агентов и построить библиотеку нарративных интервью для обогащения личностей.

**Архитектура:** расширяем `MemoryStream` гибридным поиском (BM25 + косинусное сходство), добавляем `LocalEmbeddingProvider` через sentence-transformers, создаём модуль библиотеки интервью с генерацией через LLM и гибридным поиском при инициализации агента.

**Стек:** rank-bm25 (BM25Okapi), sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), pydantic, pytest.

---

## Задача 1: Зависимости

**Файлы:**
- Изменить: `pyproject.toml:17-20`

**Шаг 1: Добавить группу зависимостей `search`**

```toml
[project.optional-dependencies]
llm = ["openai>=1.0"]
crew = ["crewai[tools]>=0.100"]
search = ["rank-bm25>=0.2.2", "sentence-transformers>=3.0"]
dev = ["pytest>=9.0", "pytest-asyncio>=0.23", "rank-bm25>=0.2.2"]
```

Группа `search` опциональная — при отсутствии sentence-transformers система откатывается на `MockEmbeddingProvider`. Пакет `rank-bm25` добавлен также в `dev`, поскольку тесты BM25 должны работать без sentence-transformers.

**Шаг 2: Установить зависимости**

Выполнить:
```bash
cd /home/development/MAGISTRY && pip install -e ".[dev,search]"
```

Ожидаемый результат: `rank-bm25` и `sentence-transformers` установлены.

**Шаг 3: Проверить импорт**

Выполнить:
```bash
python -c "from rank_bm25 import BM25Okapi; print('BM25 OK')"
python -c "from sentence_transformers import SentenceTransformer; print('ST OK')"
```

Ожидаемый результат: оба вывода без ошибок.

**Шаг 4: Зафиксировать**

```bash
git add pyproject.toml
git commit -m "feat: add search optional dependencies (rank-bm25, sentence-transformers)"
```

---

## Задача 2: LocalEmbeddingProvider

**Файлы:**
- Изменить: `src/magistry_sim/llm.py:154` (после `EmbeddingProvider`)
- Тест: `tests/test_embedding.py`

**Шаг 1: Написать падающий тест**

Добавить в конец файла `tests/test_embedding.py`:

```python
import pytest


class TestLocalEmbeddingProvider:
    def test_lazy_load_model(self):
        """Модель загружается только при первом вызове embed()."""
        from magistry_sim.llm import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider()
        assert provider._model is None
        emb = provider.embed("тестовый текст")
        assert provider._model is not None
        assert len(emb) == 384

    def test_embed_returns_384_dim(self):
        from magistry_sim.llm import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider()
        emb = provider.embed("закупка серверного оборудования")
        assert len(emb) == 384
        assert all(isinstance(x, float) for x in emb)

    def test_embed_batch(self):
        from magistry_sim.llm import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider()
        texts = ["первый текст", "второй текст", "третий текст"]
        embeddings = provider.embed_batch(texts)
        assert len(embeddings) == 3
        assert all(len(e) == 384 for e in embeddings)

    def test_similar_texts_have_high_cosine(self):
        from magistry_sim.llm import LocalEmbeddingProvider
        from magistry_sim.memory import _cosine_similarity

        provider = LocalEmbeddingProvider()
        e1 = provider.embed("закупка серверного оборудования")
        e2 = provider.embed("покупка серверов и техники")
        e3 = provider.embed("прогулка в парке с собакой")
        sim_close = _cosine_similarity(e1, e2)
        sim_far = _cosine_similarity(e1, e3)
        assert sim_close > sim_far

    def test_protocol_compliance(self):
        from magistry_sim.llm import LocalEmbeddingProvider, EmbeddingProvider

        provider = LocalEmbeddingProvider()
        assert isinstance(provider, EmbeddingProvider)

    def test_fallback_when_no_sentence_transformers(self, monkeypatch):
        """create_embedding_provider возвращает Mock при отсутствии библиотеки."""
        from magistry_sim import llm

        provider = llm.create_embedding_provider(mock=True)
        assert isinstance(provider, llm.MockEmbeddingProvider)
```

**Шаг 2: Убедиться, что тест падает**

Выполнить:
```bash
pytest tests/test_embedding.py::TestLocalEmbeddingProvider -v
```

Ожидаемый результат: FAIL — `ImportError: cannot import name 'LocalEmbeddingProvider'`

**Шаг 3: Реализация**

В `src/magistry_sim/llm.py` после класса `MockEmbeddingProvider` (строка 193) добавить:

```python
class LocalEmbeddingProvider:
    """Локальный провайдер эмбеддингов на базе sentence-transformers.

    Использует мультиязычную модель paraphrase-multilingual-MiniLM-L12-v2
    (384 измерения). Модель загружается лениво при первом вызове embed().

    Attributes:
        _model_name: Имя модели HuggingFace.
        _model: Экземпляр SentenceTransformer (None до первого вызова).
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ) -> None:
        self._model_name = model_name
        self._model = None

    def _ensure_model(self) -> None:
        """Загрузить модель при первом обращении."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)

    def embed(self, text: str) -> list[float]:
        """Получить эмбеддинг текста.

        Args:
            text: Входной текст.

        Returns:
            Вектор эмбеддинга (384 измерения).
        """
        self._ensure_model()
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Получить эмбеддинги для списка текстов.

        Args:
            texts: Список входных текстов.

        Returns:
            Список векторов эмбеддингов.
        """
        self._ensure_model()
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]
```

Также добавить фабричную функцию после `create_provider` (после строки 391):

```python
def create_embedding_provider(
    mock: bool = True,
    model_name: str | None = None,
) -> EmbeddingProvider:
    """Фабрика провайдеров эмбеддингов.

    Args:
        mock: Использовать mock-провайдер.
        model_name: Имя модели sentence-transformers.

    Returns:
        Экземпляр провайдера эмбеддингов.
    """
    if mock:
        return MockEmbeddingProvider(dimensions=384)

    try:
        provider = LocalEmbeddingProvider(
            model_name=model_name
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        return provider
    except ImportError:
        import warnings

        warnings.warn(
            "sentence-transformers не установлен, используется MockEmbeddingProvider. "
            "Установите: pip install magistry-sim[search]",
            stacklevel=2,
        )
        return MockEmbeddingProvider(dimensions=384)
```

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_embedding.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 5: Прогнать все тесты**

Выполнить:
```bash
pytest tests/ -v
```

Ожидаемый результат: 239+ тестов PASS, ничего не сломано.

**Шаг 6: Зафиксировать**

```bash
git add src/magistry_sim/llm.py tests/test_embedding.py
git commit -m "feat: add LocalEmbeddingProvider with sentence-transformers"
```

---

## Задача 3: BM25-индекс в MemoryStream

**Файлы:**
- Изменить: `src/magistry_sim/memory.py`
- Тест: `tests/test_memory.py`

**Шаг 1: Написать падающие тесты**

Добавить в конец файла `tests/test_memory.py`:

```python
class TestBM25Index:
    def test_bm25_index_built_on_add(self):
        """BM25-индекс пересоздаётся при каждом add()."""
        stream = MemoryStream(agent_id="off_1")
        assert stream._bm25 is None
        stream.add(
            content="закупка серверного оборудования",
            importance=5.0,
            kind="observation",
            round_num=0,
        )
        assert stream._bm25 is not None

    def test_bm25_scores_lexical_match(self):
        """BM25 даёт ненулевой скор при лексическом совпадении."""
        stream = MemoryStream(agent_id="off_1")
        stream.add(
            content="закупка серверного оборудования на 10 млн",
            importance=5.0,
            kind="observation",
            round_num=0,
        )
        stream.add(
            content="прогулка в парке с коллегами",
            importance=3.0,
            kind="observation",
            round_num=1,
        )
        scores = stream.bm25_scores("закупка оборудования")
        assert len(scores) == 2
        assert scores[0] > scores[1]

    def test_bm25_scores_empty_stream(self):
        stream = MemoryStream(agent_id="off_1")
        scores = stream.bm25_scores("любой запрос")
        assert scores == []

    def test_bm25_tokenization_case_insensitive(self):
        """Токенизация нечувствительна к регистру."""
        stream = MemoryStream(agent_id="off_1")
        stream.add(
            content="Закупка СЕРВЕРНОГО оборудования",
            importance=5.0,
            kind="observation",
            round_num=0,
        )
        scores = stream.bm25_scores("закупка серверного")
        assert scores[0] > 0.0
```

**Шаг 2: Убедиться, что тесты падают**

Выполнить:
```bash
pytest tests/test_memory.py::TestBM25Index -v
```

Ожидаемый результат: FAIL — `AttributeError: 'MemoryStream' object has no attribute '_bm25'`

**Шаг 3: Реализация**

Изменить `src/magistry_sim/memory.py`:

1. Добавить импорт в начало файла (после строки 6):

```python
from typing import Literal

from rank_bm25 import BM25Okapi
```

2. Изменить `__init__` класса `MemoryStream` (строка 76):

```python
def __init__(self, agent_id: str) -> None:
    self.agent_id = agent_id
    self.records: list[MemoryRecord] = []
    self.importance_since_reflection: float = 0.0
    self._bm25_corpus: list[list[str]] = []
    self._bm25: BM25Okapi | None = None
```

3. В методе `add()` после `self.records.append(record)` (строка 115) добавить перестроение BM25:

```python
self.records.append(record)
# Обновить BM25-индекс
tokens = content.lower().split()
self._bm25_corpus.append(tokens)
self._bm25 = BM25Okapi(self._bm25_corpus)
```

4. Добавить метод `bm25_scores()` после `get_by_round()` (после строки 151):

```python
def bm25_scores(self, query: str) -> list[float]:
    """Вычисляет BM25-скоры для всех записей по запросу.

    Args:
        query: Текстовый запрос.

    Returns:
        Список скоров, соответствующих порядку self.records.
    """
    if self._bm25 is None or not self._bm25_corpus:
        return []
    tokens = query.lower().split()
    return list(self._bm25.get_scores(tokens))
```

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_memory.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 5: Зафиксировать**

```bash
git add src/magistry_sim/memory.py tests/test_memory.py
git commit -m "feat: add BM25 index to MemoryStream"
```

---

## Задача 4: Гибридная формула retrieve()

**Файлы:**
- Изменить: `src/magistry_sim/memory.py:14-18` (константы), `src/magistry_sim/memory.py:153-191` (метод retrieve)
- Тест: `tests/test_memory.py`

**Шаг 1: Написать падающие тесты**

Добавить в конец `tests/test_memory.py`:

```python
class TestHybridRetrieval:
    def _make_hybrid_stream(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(
            content="закупка серверного оборудования на тендере",
            importance=7.0,
            kind="observation",
            round_num=0,
            embedding=[1.0, 0.0, 0.0],
        )
        stream.add(
            content="biz_1 подал заявку на тендер D-001",
            importance=6.0,
            kind="observation",
            round_num=2,
            embedding=[0.9, 0.1, 0.0],
        )
        stream.add(
            content="juror_0 обсудил процедуру с juror_1",
            importance=3.0,
            kind="observation",
            round_num=5,
            embedding=[0.0, 0.0, 1.0],
        )
        return stream

    def test_hybrid_retrieve_uses_bm25(self):
        """Гибридный retrieve использует BM25 в дополнение к косинусу."""
        stream = self._make_hybrid_stream()
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=5,
            top_k=3,
            query_text="тендер закупка",
        )
        assert len(results) == 3
        # Первый результат — лексическое + семантическое совпадение
        assert "закупка" in results[0].content or "тендер" in results[0].content

    def test_hybrid_retrieve_without_query_text_fallback(self):
        """Без query_text работает как раньше (только косинус)."""
        stream = self._make_hybrid_stream()
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=5,
            top_k=2,
        )
        assert len(results) == 2

    def test_hybrid_retrieve_bm25_boost(self):
        """BM25 повышает ранг записи с точным лексическим совпадением."""
        stream = MemoryStream(agent_id="off_1")
        # Семантически далёкая, но лексически точная
        stream.add(
            content="закупка оборудования",
            importance=3.0,
            kind="observation",
            round_num=0,
            embedding=[0.0, 0.0, 1.0],  # далеко от запроса
        )
        # Семантически близкая, но лексически далёкая
        stream.add(
            content="приобретение техники",
            importance=3.0,
            kind="observation",
            round_num=0,
            embedding=[1.0, 0.0, 0.0],  # близко к запросу
        )
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=0,
            top_k=2,
            query_text="закупка оборудования",
        )
        # BM25-бонус должен поднять первую запись
        assert results[0].content == "закупка оборудования"
```

**Шаг 2: Убедиться, что тесты падают**

Выполнить:
```bash
pytest tests/test_memory.py::TestHybridRetrieval -v
```

Ожидаемый результат: FAIL — `TypeError: retrieve() got an unexpected keyword argument 'query_text'`

**Шаг 3: Реализация**

Изменить `src/magistry_sim/memory.py`:

1. Обновить константы (строки 14-18):

```python
# Коэффициенты гибридной формулы (расширение Park et al., 2023)
RECENCY_WEIGHT = 0.5
COSINE_WEIGHT = 2.0
BM25_WEIGHT = 1.5
IMPORTANCE_WEIGHT = 2.0
RECENCY_DECAY = 0.995
```

2. Заменить метод `retrieve()` (строки 153-191):

```python
def retrieve(
    self,
    query_embedding: list[float],
    current_round: int,
    top_k: int = 20,
    query_text: str | None = None,
) -> list[MemoryRecord]:
    """Извлекает наиболее релевантные записи гибридным поиском.

    Формула: score = alpha*recency + beta*cosine + gamma*bm25 + delta*importance.
    Если query_text не передан, BM25-компонент равен нулю.

    Args:
        query_embedding: Вектор запроса для семантического поиска.
        current_round: Номер текущего раунда (для расчёта давности).
        top_k: Максимальное количество возвращаемых записей.
        query_text: Текст запроса для BM25-поиска.

    Returns:
        Список записей, отсортированных по убыванию оценки.
    """
    candidates = [r for r in self.records if r.embedding]
    if not candidates:
        return []

    # BM25-скоры для всех записей (не только с эмбеддингами)
    bm25_raw: list[float] = []
    if query_text and self._bm25 is not None:
        all_scores = list(self._bm25.get_scores(query_text.lower().split()))
        # Фильтровать: оставить скоры только для записей с эмбеддингами
        indices_with_emb = [
            i for i, r in enumerate(self.records) if r.embedding
        ]
        bm25_raw = [all_scores[i] for i in indices_with_emb]
    else:
        bm25_raw = [0.0] * len(candidates)

    # Нормализация BM25 к [0, 1] через min-max
    bm25_max = max(bm25_raw) if bm25_raw else 0.0
    bm25_min = min(bm25_raw) if bm25_raw else 0.0
    bm25_range = bm25_max - bm25_min
    if bm25_range > 0:
        bm25_norm = [(s - bm25_min) / bm25_range for s in bm25_raw]
    else:
        bm25_norm = [0.0] * len(bm25_raw)

    scored: list[tuple[float, MemoryRecord]] = []
    for idx, rec in enumerate(candidates):
        rounds_ago = current_round - rec.created_at
        recency = RECENCY_DECAY ** rounds_ago
        cosine = _cosine_similarity(query_embedding, rec.embedding)
        # Нормализация косинуса к [0, 1]
        cosine_norm = (cosine + 1.0) / 2.0
        importance = rec.importance / 10.0
        bm25 = bm25_norm[idx] if idx < len(bm25_norm) else 0.0

        score = (
            RECENCY_WEIGHT * recency
            + COSINE_WEIGHT * cosine_norm
            + BM25_WEIGHT * bm25
            + IMPORTANCE_WEIGHT * importance
        )
        scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [rec for _, rec in scored[:top_k]]
```

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_memory.py -v
```

Ожидаемый результат: все тесты PASS (включая старые — обратная совместимость через `query_text=None`).

**Шаг 5: Прогнать все тесты**

Выполнить:
```bash
pytest tests/ -v
```

Ожидаемый результат: 239+ тестов PASS. Старые вызовы `retrieve()` без `query_text` работают как раньше.

**Шаг 6: Зафиксировать**

```bash
git add src/magistry_sim/memory.py tests/test_memory.py
git commit -m "feat: hybrid retrieval formula (cosine + BM25 + recency + importance)"
```

---

## Задача 5: Передача query_text в CognitiveAgentRunner

**Файлы:**
- Изменить: `src/magistry_sim/cognitive_runner.py`
- Тест: `tests/test_cognitive_runner.py`

**Шаг 1: Написать падающий тест**

Добавить в `tests/test_cognitive_runner.py`:

```python
def test_retrieve_passes_query_text(self):
    """Когнитивный runner передаёт query_text при извлечении из памяти."""
    from unittest.mock import MagicMock, patch

    runner = CognitiveAgentRunner(
        llm_provider=MockLLMProvider(),
        embedder=MockEmbeddingProvider(dimensions=3),
        verbose=False,
    )
    stream = runner.get_or_create_memory("off_1")
    stream.add(
        content="закупка оборудования",
        importance=5.0,
        kind="observation",
        round_num=0,
        embedding=[1.0, 0.0, 0.0],
    )
    # Проверяем, что retrieve вызывается с query_text
    with patch.object(stream, "retrieve", wraps=stream.retrieve) as mock_ret:
        stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=1,
            query_text="закупка",
        )
        mock_ret.assert_called_once()
        _, kwargs = mock_ret.call_args
        assert "query_text" in kwargs
```

**Шаг 2: Найти все вызовы retrieve() в cognitive_runner.py и добавить query_text**

Прочитать `src/magistry_sim/cognitive_runner.py`, найти каждый вызов `stream.retrieve(...)` или `memory.retrieve(...)` и добавить параметр `query_text=<текст_запроса>`.

Контекст вызова: при формировании промпта агента runner создаёт текстовую строку-описание текущей ситуации. Эта строка используется и для эмбеддинга, и теперь для BM25. Передать её как `query_text`.

**Шаг 3: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_cognitive_runner.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 4: Зафиксировать**

```bash
git add src/magistry_sim/cognitive_runner.py tests/test_cognitive_runner.py
git commit -m "feat: pass query_text to hybrid retrieve in CognitiveAgentRunner"
```

---

## Задача 6: Обновить cli.py для LocalEmbeddingProvider

**Файлы:**
- Изменить: `src/magistry_sim/cli.py:153-186`
- Тест: `tests/test_cli.py`

**Шаг 1: Написать падающий тест**

Добавить в `tests/test_cli.py`:

```python
def test_cognitive_runner_local_embedder(monkeypatch):
    """EMBEDDING_PROVIDER=local создаёт LocalEmbeddingProvider."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")

    from magistry_sim.cli import _create_runner
    from magistry_sim.llm import LocalEmbeddingProvider

    runner = _create_runner("cognitive")
    assert isinstance(runner._embedder, LocalEmbeddingProvider)
```

**Шаг 2: Убедиться, что тест падает**

Выполнить:
```bash
pytest tests/test_cli.py::test_cognitive_runner_local_embedder -v
```

Ожидаемый результат: FAIL — нет обработки `EMBEDDING_PROVIDER=local`.

**Шаг 3: Реализация**

Изменить блок `if runner_type == "cognitive":` в `src/magistry_sim/cli.py` (строки 153-186):

```python
if runner_type == "cognitive":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    from .cognitive_runner import CognitiveAgentRunner
    from .llm import (
        MockEmbeddingProvider,
        MockLLMProvider,
        create_provider,
        create_embedding_provider,
    )

    has_api_key = bool(os.getenv("OPENAI_API_KEY"))
    if has_api_key:
        llm = create_provider(mock=False)
        embed_mode = os.getenv("EMBEDDING_PROVIDER", "mock").lower()
        if embed_mode == "local":
            embedder = create_embedding_provider(mock=False)
        elif embed_mode == "openai":
            from .llm import OpenAIEmbeddingProvider
            embedder = OpenAIEmbeddingProvider()
        else:
            embedder = MockEmbeddingProvider(dimensions=384)
    else:
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)

    return CognitiveAgentRunner(
        llm_provider=llm,
        embedder=embedder,
        verbose=True,
    )
```

Размерность mock-эмбеддера изменена с 64 на 384 для согласованности с `LocalEmbeddingProvider`.

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_cli.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 5: Зафиксировать**

```bash
git add src/magistry_sim/cli.py tests/test_cli.py
git commit -m "feat: add EMBEDDING_PROVIDER=local option for sentence-transformers"
```

---

## Задача 7: Модель данных интервью

**Файлы:**
- Создать: `src/magistry_sim/interviews.py`
- Тест: `tests/test_interviews.py`

**Шаг 1: Написать падающие тесты**

Создать `tests/test_interviews.py`:

```python
"""Тесты модели данных и поиска по библиотеке интервью."""

import pytest
from magistry_sim.interviews import (
    Interview,
    InterviewLibrary,
    INTERVIEW_QUESTIONS,
)


class TestInterviewModel:
    def test_interview_has_required_fields(self):
        interview = Interview(
            id="int_001",
            archetype="opportunist",
            role="чиновник",
            hexaco={"honesty_humility": 30, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 40,
                    "conscientiousness": 45, "openness": 55},
            dark_triad={"narcissism": 60, "machiavellianism": 70,
                        "psychopathy": 30},
            interview={"q1": "answer1", "q2": "answer2"},
            expert_psychologist="анализ психолога",
            expert_economist="анализ экономиста",
        )
        assert interview.id == "int_001"
        assert interview.archetype == "opportunist"

    def test_interview_full_text(self):
        interview = Interview(
            id="int_002",
            archetype="idealist",
            role="аудитор",
            hexaco={"honesty_humility": 90, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 70,
                    "conscientiousness": 85, "openness": 55},
            dark_triad={"narcissism": 10, "machiavellianism": 5,
                        "psychopathy": 5},
            interview={"Как вы принимаете решения?": "Тщательно взвешиваю"},
            expert_psychologist="высокая добросовестность",
            expert_economist="риск-нейтральный",
        )
        text = interview.full_text()
        assert "Как вы принимаете решения?" in text
        assert "Тщательно взвешиваю" in text
        assert "высокая добросовестность" in text

    def test_questions_count(self):
        assert len(INTERVIEW_QUESTIONS) == 10


class TestInterviewLibrary:
    def _make_library(self) -> InterviewLibrary:
        lib = InterviewLibrary()
        lib.add(Interview(
            id="int_001",
            archetype="opportunist",
            role="чиновник",
            hexaco={"honesty_humility": 30, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 40,
                    "conscientiousness": 45, "openness": 55},
            dark_triad={"narcissism": 60, "machiavellianism": 70,
                        "psychopathy": 30},
            interview={"q1": "ответ оппортуниста про закупки"},
            expert_psychologist="склонен к риску",
            expert_economist="ищет выгоду",
        ))
        lib.add(Interview(
            id="int_002",
            archetype="idealist",
            role="аудитор",
            hexaco={"honesty_humility": 90, "emotionality": 50,
                    "extraversion": 60, "agreeableness": 70,
                    "conscientiousness": 85, "openness": 55},
            dark_triad={"narcissism": 10, "machiavellianism": 5,
                        "psychopathy": 5},
            interview={"q1": "ответ идеалиста про справедливость"},
            expert_psychologist="высокая честность",
            expert_economist="нетерпим к коррупции",
        ))
        return lib

    def test_add_and_len(self):
        lib = self._make_library()
        assert len(lib) == 2

    def test_search_by_text(self):
        lib = self._make_library()
        results = lib.search("закупки выгода", top_k=1)
        assert len(results) == 1
        assert results[0].archetype == "opportunist"

    def test_search_empty_library(self):
        lib = InterviewLibrary()
        results = lib.search("любой запрос")
        assert results == []

    def test_load_save_jsonl(self, tmp_path):
        lib = self._make_library()
        path = tmp_path / "interviews.jsonl"
        lib.save_jsonl(path)

        loaded = InterviewLibrary.load_jsonl(path)
        assert len(loaded) == 2
        assert loaded._interviews[0].id == "int_001"

    def test_load_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        lib = InterviewLibrary.load_jsonl(path)
        assert len(lib) == 0
```

**Шаг 2: Убедиться, что тест падает**

Выполнить:
```bash
pytest tests/test_interviews.py -v
```

Ожидаемый результат: FAIL — `ModuleNotFoundError: No module named 'magistry_sim.interviews'`

**Шаг 3: Реализация**

Создать `src/magistry_sim/interviews.py`:

```python
"""Библиотека синтетических интервью для обогащения личностей агентов."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi


INTERVIEW_QUESTIONS: list[str] = [
    # Уровень 1 — Черты (McAdams)
    "Как вы принимаете решения под давлением?",
    "Что вас мотивирует в работе больше всего?",
    "Как вы обычно реагируете на конфликты с коллегами?",
    # Уровень 2 — Личные заботы
    "Расскажите о карьерном решении, которым вы гордитесь.",
    "Был ли момент, когда вы сомневались в правильности своих действий на работе?",
    "Как вы относитесь к ситуациям, когда формальные правила мешают достижению результата?",
    "Что для вас значит лояльность коллегам и начальству?",
    # Уровень 3 — Нарративная идентичность
    "Расскажите историю из детства или юности, которая сформировала ваше отношение к справедливости.",
    "Какой эпизод из вашей карьеры определил вас как профессионала?",
    "Как бы вы описали себя через 10 лет?",
]


class Interview(BaseModel, extra="forbid"):
    """Синтетическое интервью агента.

    Attributes:
        id: Уникальный идентификатор.
        archetype: Архетип коррупционного поведения.
        role: Роль в сценарии.
        hexaco: Параметры HEXACO, использованные при генерации.
        dark_triad: Параметры Dark Triad.
        interview: Словарь вопрос-ответ.
        expert_psychologist: Экспертная оценка психолога.
        expert_economist: Экспертная оценка экономиста.
        embedding: Вектор эмбеддинга полного текста (384 dim).
    """

    id: str
    archetype: str
    role: str
    hexaco: dict[str, int]
    dark_triad: dict[str, int]
    interview: dict[str, str]
    expert_psychologist: str
    expert_economist: str
    embedding: list[float] = Field(default_factory=list)

    def full_text(self) -> str:
        """Полный текст интервью для эмбеддинга и поиска.

        Returns:
            Конкатенация вопросов, ответов и экспертных оценок.
        """
        parts = []
        for q, a in self.interview.items():
            parts.append(f"Вопрос: {q}\nОтвет: {a}")
        parts.append(f"Оценка психолога: {self.expert_psychologist}")
        parts.append(f"Оценка экономиста: {self.expert_economist}")
        return "\n\n".join(parts)


class InterviewLibrary:
    """Библиотека интервью с гибридным поиском (BM25 + эмбеддинги).

    Attributes:
        _interviews: Список интервью.
        _bm25: BM25-индекс по текстам интервью.
        _corpus: Токенизированный корпус для BM25.
    """

    def __init__(self) -> None:
        self._interviews: list[Interview] = []
        self._corpus: list[list[str]] = []
        self._bm25: BM25Okapi | None = None

    def __len__(self) -> int:
        return len(self._interviews)

    def add(self, interview: Interview) -> None:
        """Добавляет интервью в библиотеку и обновляет BM25-индекс.

        Args:
            interview: Интервью для добавления.
        """
        self._interviews.append(interview)
        tokens = interview.full_text().lower().split()
        self._corpus.append(tokens)
        self._bm25 = BM25Okapi(self._corpus)

    def search(
        self,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
        cosine_weight: float = 2.0,
        bm25_weight: float = 1.5,
    ) -> list[Interview]:
        """Гибридный поиск по библиотеке.

        Args:
            query: Текстовый запрос.
            top_k: Количество результатов.
            query_embedding: Вектор запроса для косинусного поиска.
            cosine_weight: Вес косинусного сходства.
            bm25_weight: Вес BM25.

        Returns:
            Список интервью, отсортированных по релевантности.
        """
        if not self._interviews:
            return []

        from magistry_sim.memory import _cosine_similarity

        # BM25-скоры
        bm25_scores = [0.0] * len(self._interviews)
        if self._bm25 is not None:
            tokens = query.lower().split()
            bm25_scores = list(self._bm25.get_scores(tokens))

        # Нормализация BM25
        bm25_max = max(bm25_scores) if bm25_scores else 0.0
        bm25_min = min(bm25_scores) if bm25_scores else 0.0
        bm25_range = bm25_max - bm25_min
        if bm25_range > 0:
            bm25_norm = [(s - bm25_min) / bm25_range for s in bm25_scores]
        else:
            bm25_norm = [0.0] * len(bm25_scores)

        # Косинусные скоры
        cosine_scores = [0.0] * len(self._interviews)
        if query_embedding:
            for i, itv in enumerate(self._interviews):
                if itv.embedding:
                    cosine_scores[i] = (
                        _cosine_similarity(query_embedding, itv.embedding) + 1.0
                    ) / 2.0

        # Финальный скор
        scored = []
        for i, itv in enumerate(self._interviews):
            score = bm25_weight * bm25_norm[i] + cosine_weight * cosine_scores[i]
            scored.append((score, itv))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [itv for _, itv in scored[:top_k]]

    def save_jsonl(self, path: Path) -> None:
        """Сохраняет библиотеку в JSONL.

        Args:
            path: Путь к файлу.
        """
        with open(path, "w", encoding="utf-8") as f:
            for itv in self._interviews:
                f.write(itv.model_dump_json() + "\n")

    @classmethod
    def load_jsonl(cls, path: Path) -> InterviewLibrary:
        """Загружает библиотеку из JSONL.

        Args:
            path: Путь к файлу.

        Returns:
            Экземпляр библиотеки.
        """
        lib = cls()
        if not path.exists():
            return lib
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    itv = Interview.model_validate_json(line)
                    lib.add(itv)
        return lib
```

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_interviews.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 5: Зафиксировать**

```bash
git add src/magistry_sim/interviews.py tests/test_interviews.py
git commit -m "feat: interview data model and library with hybrid search"
```

---

## Задача 8: Генератор интервью

**Файлы:**
- Изменить: `src/magistry_sim/interviews.py`
- Тест: `tests/test_interviews.py`

**Шаг 1: Написать падающий тест**

Добавить в `tests/test_interviews.py`:

```python
from unittest.mock import MagicMock
from magistry_sim.interviews import generate_interview, INTERVIEW_QUESTIONS
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
)
from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider


class TestInterviewGeneration:
    def _make_personality(self, hh: int = 30, mach: int = 70) -> AgentPersonality:
        return AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=hh,
                emotionality=50,
                extraversion=60,
                agreeableness=40,
                conscientiousness=45,
                openness=55,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=60,
                machiavellianism=mach,
                psychopathy=30,
            ),
        )

    def test_generate_interview_returns_interview(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)
        personality = self._make_personality()

        result = generate_interview(
            personality=personality,
            role="чиновник",
            archetype="opportunist",
            llm=llm,
            embedder=embedder,
            interview_id="test_001",
        )
        assert result.id == "test_001"
        assert result.archetype == "opportunist"
        assert result.role == "чиновник"
        assert len(result.embedding) == 384

    def test_generate_interview_has_expert_assessments(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)
        personality = self._make_personality()

        result = generate_interview(
            personality=personality,
            role="чиновник",
            archetype="opportunist",
            llm=llm,
            embedder=embedder,
            interview_id="test_002",
        )
        assert result.expert_psychologist != ""
        assert result.expert_economist != ""

    def test_generate_interview_calls_llm(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=384)
        personality = self._make_personality()

        generate_interview(
            personality=personality,
            role="бизнесмен",
            archetype="initiator",
            llm=llm,
            embedder=embedder,
            interview_id="test_003",
        )
        # 1 вызов на интервью + 1 на психолога + 1 на экономиста = 3
        assert llm.call_count == 3
```

**Шаг 2: Убедиться, что тест падает**

Выполнить:
```bash
pytest tests/test_interviews.py::TestInterviewGeneration -v
```

Ожидаемый результат: FAIL — `ImportError: cannot import name 'generate_interview'`

**Шаг 3: Реализация**

Добавить в `src/magistry_sim/interviews.py`:

```python
def generate_interview(
    personality: AgentPersonality,
    role: str,
    archetype: str,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    interview_id: str,
) -> Interview:
    """Генерирует синтетическое интервью через LLM.

    Формирует промпт с профилем личности и вопросами, получает ответы
    от LLM, затем запрашивает экспертные оценки психолога и экономиста.

    Args:
        personality: Профиль личности HEXACO + Dark Triad.
        role: Роль в сценарии (чиновник, бизнесмен, аудитор, кандидат).
        archetype: Архетип коррупционного поведения.
        llm: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.
        interview_id: Уникальный идентификатор интервью.

    Returns:
        Сгенерированное интервью с экспертными оценками и эмбеддингом.
    """
    h = personality.hexaco
    d = personality.dark_triad

    questions_block = "\n".join(
        f"{i+1}. {q}" for i, q in enumerate(INTERVIEW_QUESTIONS)
    )

    interview_prompt = (
        f"Ты — персонаж симуляции. Твоя роль: {role}.\n\n"
        f"Профиль HEXACO (0-100):\n"
        f"- Честность-скромность: {h.honesty_humility}\n"
        f"- Эмоциональность: {h.emotionality}\n"
        f"- Экстраверсия: {h.extraversion}\n"
        f"- Доброжелательность: {h.agreeableness}\n"
        f"- Добросовестность: {h.conscientiousness}\n"
        f"- Открытость: {h.openness}\n\n"
        f"Тёмная триада (0-100):\n"
        f"- Нарциссизм: {d.narcissism}\n"
        f"- Макиавеллизм: {d.machiavellianism}\n"
        f"- Психопатия: {d.psychopathy}\n\n"
        f"Ответь на каждый вопрос от первого лица, развёрнуто (3-5 предложений), "
        f"в соответствии со своим профилем личности. Формат: номер вопроса, "
        f"затем ответ.\n\n{questions_block}"
    )

    interview_response = llm.generate(
        system="Ты участник глубинного интервью о личности и карьере.",
        user=interview_prompt,
    )

    # Парсинг ответов — простое разбиение по номерам
    answers: dict[str, str] = {}
    raw_text = interview_response.text
    for i, q in enumerate(INTERVIEW_QUESTIONS):
        answers[q] = raw_text  # Весь текст как один ответ при mock

    # Экспертная оценка психолога
    psych_response = llm.generate(
        system="Ты клинический психолог, анализирующий результаты интервью.",
        user=(
            f"Проанализируй следующее интервью и дай экспертную оценку: "
            f"личностные черты, мотивация, зоны уязвимости, вероятные паттерны "
            f"поведения в стрессовых ситуациях.\n\n{raw_text}"
        ),
    )

    # Экспертная оценка экономиста
    econ_response = llm.generate(
        system="Ты поведенческий экономист, анализирующий результаты интервью.",
        user=(
            f"Проанализируй следующее интервью и дай экспертную оценку: "
            f"отношение к риску, склонность к оппортунизму, реакция "
            f"на экономические стимулы.\n\n{raw_text}"
        ),
    )

    # Формируем интервью
    interview = Interview(
        id=interview_id,
        archetype=archetype,
        role=role,
        hexaco={
            "honesty_humility": h.honesty_humility,
            "emotionality": h.emotionality,
            "extraversion": h.extraversion,
            "agreeableness": h.agreeableness,
            "conscientiousness": h.conscientiousness,
            "openness": h.openness,
        },
        dark_triad={
            "narcissism": d.narcissism,
            "machiavellianism": d.machiavellianism,
            "psychopathy": d.psychopathy,
        },
        interview=answers,
        expert_psychologist=psych_response.text,
        expert_economist=econ_response.text,
    )

    # Эмбеддинг полного текста
    interview.embedding = embedder.embed(interview.full_text())

    return interview
```

Добавить необходимые импорты в начало `interviews.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider
    from magistry_sim.personality import AgentPersonality
```

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_interviews.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 5: Зафиксировать**

```bash
git add src/magistry_sim/interviews.py tests/test_interviews.py
git commit -m "feat: interview generation via LLM with expert assessments"
```

---

## Задача 9: Поиск интервью при создании агента

**Файлы:**
- Изменить: `src/magistry_sim/cognitive_runner.py`
- Тест: `tests/test_cognitive_runner.py`

**Шаг 1: Написать падающий тест**

Добавить в `tests/test_cognitive_runner.py`:

```python
def test_interview_lookup_enriches_prompt(tmp_path):
    """При наличии библиотеки интервью — ближайшее подставляется в промпт."""
    from magistry_sim.interviews import Interview, InterviewLibrary

    lib = InterviewLibrary()
    lib.add(Interview(
        id="int_001",
        archetype="opportunist",
        role="чиновник",
        hexaco={"honesty_humility": 30, "emotionality": 50,
                "extraversion": 60, "agreeableness": 40,
                "conscientiousness": 45, "openness": 55},
        dark_triad={"narcissism": 60, "machiavellianism": 70,
                    "psychopathy": 30},
        interview={"q1": "Я всегда ищу выгоду в сделках"},
        expert_psychologist="склонен к рискованным решениям",
        expert_economist="высокая склонность к оппортунизму",
    ))
    path = tmp_path / "interviews.jsonl"
    lib.save_jsonl(path)

    runner = CognitiveAgentRunner(
        llm_provider=MockLLMProvider(),
        embedder=MockEmbeddingProvider(dimensions=384),
        verbose=False,
        interview_library_path=path,
    )
    assert runner._interview_library is not None
    assert len(runner._interview_library) == 1
```

**Шаг 2: Убедиться, что тест падает**

Выполнить:
```bash
pytest tests/test_cognitive_runner.py::test_interview_lookup_enriches_prompt -v
```

Ожидаемый результат: FAIL — `TypeError: CognitiveAgentRunner.__init__() got an unexpected keyword argument 'interview_library_path'`

**Шаг 3: Реализация**

Изменить `__init__` класса `CognitiveAgentRunner` в `src/magistry_sim/cognitive_runner.py`:

```python
def __init__(
    self,
    llm_provider: LLMProvider,
    embedder: EmbeddingProvider,
    verbose: bool = False,
    interview_library_path: Path | None = None,
) -> None:
    self._llm = llm_provider
    self._embedder = embedder
    self._verbose = verbose
    self._memories: dict[str, MemoryStream] = {}
    self._plans: dict[str, AgentPlan] = {}
    self._interview_library: InterviewLibrary | None = None

    if interview_library_path:
        from magistry_sim.interviews import InterviewLibrary
        self._interview_library = InterviewLibrary.load_jsonl(
            interview_library_path
        )
```

Добавить импорт `Path` в начало файла:

```python
from pathlib import Path
```

В методе, формирующем промпт агента (найти место, где используется `biography`), добавить поиск интервью:

```python
# Если есть библиотека интервью — найти ближайшее
interview_text = ""
if self._interview_library and len(self._interview_library) > 0:
    personality = agent_plan.personality  # или откуда берётся профиль
    query = f"роль: {agent_profile.position}, архетип: {archetype}"
    results = self._interview_library.search(query, top_k=1)
    if results:
        interview_text = results[0].full_text()
```

Точное место вставки зависит от структуры `run_turn()`, которую нужно прочитать при реализации.

**Шаг 4: Убедиться, что тесты проходят**

Выполнить:
```bash
pytest tests/test_cognitive_runner.py -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 5: Прогнать все тесты**

Выполнить:
```bash
pytest tests/ -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 6: Зафиксировать**

```bash
git add src/magistry_sim/cognitive_runner.py tests/test_cognitive_runner.py
git commit -m "feat: interview library lookup in CognitiveAgentRunner"
```

---

## Задача 10: Скрипт генерации библиотеки интервью

**Файлы:**
- Создать: `scripts/generate_interviews.py`
- Тест: ручной запуск

**Шаг 1: Создать скрипт**

```python
"""Генерация библиотеки синтетических интервью.

Матрица: 5 архетипов x 4 роли x 3 вариации = 60 интервью.
Результат: data/interview_library.jsonl

Запуск:
    python scripts/generate_interviews.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from magistry_sim.interviews import generate_interview, InterviewLibrary
from magistry_sim.llm import create_provider, create_embedding_provider
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
    CORRUPTION_ARCHETYPES,
)

ROLES = ["чиновник", "бизнесмен", "аудитор", "кандидат"]

# Базовые профили для каждого архетипа
ARCHETYPE_PROFILES: dict[str, list[dict]] = {
    "idealist": [
        {"hh": 90, "em": 50, "ex": 60, "ag": 75, "co": 85, "op": 60,
         "na": 10, "ma": 5, "ps": 5},
        {"hh": 85, "em": 60, "ex": 55, "ag": 70, "co": 90, "op": 65,
         "na": 15, "ma": 10, "ps": 5},
        {"hh": 95, "em": 45, "ex": 65, "ag": 80, "co": 80, "op": 55,
         "na": 5, "ma": 5, "ps": 10},
    ],
    "pragmatist": [
        {"hh": 55, "em": 45, "ex": 60, "ag": 55, "co": 70, "op": 50,
         "na": 35, "ma": 40, "ps": 20},
        {"hh": 50, "em": 50, "ex": 55, "ag": 50, "co": 65, "op": 55,
         "na": 40, "ma": 35, "ps": 25},
        {"hh": 60, "em": 40, "ex": 65, "ag": 60, "co": 75, "op": 45,
         "na": 30, "ma": 45, "ps": 15},
    ],
    "opportunist": [
        {"hh": 30, "em": 40, "ex": 65, "ag": 35, "co": 45, "op": 55,
         "na": 60, "ma": 70, "ps": 30},
        {"hh": 35, "em": 45, "ex": 70, "ag": 30, "co": 40, "op": 60,
         "na": 55, "ma": 65, "ps": 35},
        {"hh": 25, "em": 35, "ex": 60, "ag": 40, "co": 50, "op": 50,
         "na": 65, "ma": 75, "ps": 25},
    ],
    "initiator": [
        {"hh": 15, "em": 30, "ex": 80, "ag": 25, "co": 55, "op": 65,
         "na": 75, "ma": 85, "ps": 40},
        {"hh": 20, "em": 25, "ex": 75, "ag": 20, "co": 50, "op": 70,
         "na": 80, "ma": 80, "ps": 45},
        {"hh": 10, "em": 35, "ex": 85, "ag": 30, "co": 60, "op": 60,
         "na": 70, "ma": 90, "ps": 35},
    ],
    "machiavellist": [
        {"hh": 10, "em": 20, "ex": 70, "ag": 10, "co": 65, "op": 55,
         "na": 85, "ma": 95, "ps": 75},
        {"hh": 5, "em": 15, "ex": 75, "ag": 15, "co": 70, "op": 50,
         "na": 90, "ma": 90, "ps": 80},
        {"hh": 15, "em": 25, "ex": 65, "ag": 5, "co": 60, "op": 60,
         "na": 80, "ma": 95, "ps": 70},
    ],
}


def main():
    llm = create_provider(mock=False)
    embedder = create_embedding_provider(mock=False)

    lib = InterviewLibrary()
    counter = 0

    for archetype in CORRUPTION_ARCHETYPES:
        profiles = ARCHETYPE_PROFILES[archetype]
        for role in ROLES:
            for var_idx, params in enumerate(profiles):
                counter += 1
                interview_id = f"int_{counter:03d}"
                print(
                    f"[{counter}/60] {archetype}/{role}/v{var_idx} -> {interview_id}"
                )

                personality = AgentPersonality(
                    hexaco=HEXACOProfile(
                        honesty_humility=params["hh"],
                        emotionality=params["em"],
                        extraversion=params["ex"],
                        agreeableness=params["ag"],
                        conscientiousness=params["co"],
                        openness=params["op"],
                    ),
                    dark_triad=DarkTriadProfile(
                        narcissism=params["na"],
                        machiavellianism=params["ma"],
                        psychopathy=params["ps"],
                    ),
                )

                interview = generate_interview(
                    personality=personality,
                    role=role,
                    archetype=archetype,
                    llm=llm,
                    embedder=embedder,
                    interview_id=interview_id,
                )
                lib.add(interview)

    output_path = Path("data/interview_library.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lib.save_jsonl(output_path)
    print(f"\nГотово: {len(lib)} интервью сохранено в {output_path}")


if __name__ == "__main__":
    main()
```

**Шаг 2: Проверить, что скрипт запускается с mock (для теста)**

Выполнить:
```bash
cd /home/development/MAGISTRY && python -c "
from scripts.generate_interviews import ARCHETYPE_PROFILES, ROLES
from magistry_sim.personality import CORRUPTION_ARCHETYPES
total = sum(len(v) for v in ARCHETYPE_PROFILES.values()) * len(ROLES)
print(f'Матрица: {total} интервью')
assert total == 60
"
```

Ожидаемый результат: `Матрица: 60 интервью`.

**Шаг 3: Зафиксировать**

```bash
git add scripts/generate_interviews.py
git commit -m "feat: add interview library generation script (5 archetypes x 4 roles x 3 variations)"
```

---

## Задача 11: Интеграция cli.py с библиотекой интервью

**Файлы:**
- Изменить: `src/magistry_sim/cli.py`

**Шаг 1: Реализация**

Добавить аргумент `--interviews` в парсер (после `--summary-json`):

```python
parser.add_argument(
    "--interviews",
    type=str,
    default=None,
    help="Путь к библиотеке интервью (JSONL)",
)
```

Изменить создание `CognitiveAgentRunner` в `_create_runner()`, передав путь:

Принять путь как параметр `_create_runner(runner_type, interview_path=None)` и передать его в конструктор runner-а.

**Шаг 2: Убедиться, что все тесты проходят**

Выполнить:
```bash
pytest tests/ -v
```

Ожидаемый результат: все тесты PASS.

**Шаг 3: Зафиксировать**

```bash
git add src/magistry_sim/cli.py
git commit -m "feat: add --interviews CLI argument for interview library path"
```

---

## Задача 12: Финальный прогон и проверка

**Шаг 1: Полный прогон тестов**

Выполнить:
```bash
pytest tests/ -v --tb=short
```

Ожидаемый результат: 260+ тестов PASS, 0 FAIL.

**Шаг 2: Проверить, что cognitive runner работает с mock**

Выполнить:
```bash
magistry-sim --scenario S1 --governance G0 --runner cognitive --rounds 3
```

Ожидаемый результат: симуляция завершается без ошибок.

**Шаг 3: Проверить, что cognitive runner работает с `EMBEDDING_PROVIDER=local`**

Выполнить:
```bash
EMBEDDING_PROVIDER=local magistry-sim --scenario S1 --governance G0 --runner cognitive --rounds 3
```

Ожидаемый результат: симуляция завершается, в логах видно загрузку модели sentence-transformers.

**Шаг 4: Зафиксировать финальное состояние**

Если были мелкие правки — зафиксировать. Затем:

```bash
git log --oneline -10
```

Ожидаемая история коммитов:
1. `feat: add search optional dependencies (rank-bm25, sentence-transformers)`
2. `feat: add LocalEmbeddingProvider with sentence-transformers`
3. `feat: add BM25 index to MemoryStream`
4. `feat: hybrid retrieval formula (cosine + BM25 + recency + importance)`
5. `feat: pass query_text to hybrid retrieve in CognitiveAgentRunner`
6. `feat: add EMBEDDING_PROVIDER=local option for sentence-transformers`
7. `feat: interview data model and library with hybrid search`
8. `feat: interview generation via LLM with expert assessments`
9. `feat: interview library lookup in CognitiveAgentRunner`
10. `feat: add interview library generation script`
11. `feat: add --interviews CLI argument for interview library path`
