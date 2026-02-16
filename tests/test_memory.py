"""Тесты потока памяти агента."""

import math

import pytest
from magistry_sim.memory import MemoryRecord, MemoryStream


class TestMemoryRecord:
    def test_create_observation(self):
        rec = MemoryRecord(
            id="m_001",
            created_at=0,
            content="off_1 отправил приватное сообщение biz_1",
            importance=7.0,
            kind="observation",
        )
        assert rec.kind == "observation"
        assert rec.importance == 7.0
        assert rec.embedding == []
        assert rec.evidence == []

    def test_create_reflection(self):
        rec = MemoryRecord(
            id="m_002",
            created_at=3,
            content="off_1 и biz_1 поддерживают подозрительно тесные контакты",
            importance=9.0,
            kind="reflection",
            evidence=["m_001"],
        )
        assert rec.kind == "reflection"
        assert rec.evidence == ["m_001"]

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError):
            MemoryRecord(
                id="m_003",
                created_at=0,
                content="test",
                importance=5.0,
                kind="invalid",
            )

    def test_importance_range(self):
        with pytest.raises(ValueError):
            MemoryRecord(
                id="m_004",
                created_at=0,
                content="test",
                importance=11.0,
                kind="observation",
            )


class TestMemoryStream:
    def test_add_and_retrieve(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(
            content="Получено сообщение от biz_1",
            importance=6.0,
            kind="observation",
            round_num=0,
        )
        assert len(stream) == 1
        assert stream.records[0].content == "Получено сообщение от biz_1"

    def test_auto_id_generation(self):
        stream = MemoryStream(agent_id="off_1")
        r1 = stream.add(content="first", importance=5.0, kind="observation", round_num=0)
        r2 = stream.add(content="second", importance=5.0, kind="observation", round_num=0)
        assert r1.id != r2.id

    def test_get_recent(self):
        stream = MemoryStream(agent_id="off_1")
        for i in range(10):
            stream.add(
                content=f"event {i}",
                importance=5.0,
                kind="observation",
                round_num=i,
            )
        recent = stream.get_recent(n=3)
        assert len(recent) == 3
        assert recent[0].content == "event 9"

    def test_importance_accumulator(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="a", importance=8.0, kind="observation", round_num=0)
        stream.add(content="b", importance=7.0, kind="observation", round_num=0)
        assert stream.importance_since_reflection == 15.0

    def test_reset_importance_accumulator(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="a", importance=8.0, kind="observation", round_num=0)
        stream.reset_importance_accumulator()
        assert stream.importance_since_reflection == 0.0

    def test_filter_by_kind(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="obs1", importance=5.0, kind="observation", round_num=0)
        stream.add(content="ref1", importance=8.0, kind="reflection", round_num=1)
        stream.add(content="plan1", importance=6.0, kind="plan", round_num=1)
        obs = stream.get_by_kind("observation")
        assert len(obs) == 1
        assert obs[0].content == "obs1"

    def test_get_by_round(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="r0", importance=5.0, kind="observation", round_num=0)
        stream.add(content="r1", importance=5.0, kind="observation", round_num=1)
        stream.add(content="r1b", importance=5.0, kind="observation", round_num=1)
        round1 = stream.get_by_round(1)
        assert len(round1) == 2


class TestMemoryRetrieval:
    def _make_stream_with_embeddings(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(
            content="biz_1 предложил встретиться для обсуждения",
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

    def test_retrieve_returns_scored_results(self):
        stream = self._make_stream_with_embeddings()
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=5,
            top_k=2,
        )
        assert len(results) == 2
        assert "biz_1" in results[0].content

    def test_retrieve_respects_top_k(self):
        stream = self._make_stream_with_embeddings()
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=5,
            top_k=1,
        )
        assert len(results) == 1

    def test_retrieve_empty_stream(self):
        stream = MemoryStream(agent_id="off_1")
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=0,
            top_k=5,
        )
        assert results == []

    def test_retrieve_skips_records_without_embedding(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="no embedding", importance=5.0, kind="observation", round_num=0)
        stream.add(
            content="with embedding",
            importance=5.0,
            kind="observation",
            round_num=0,
            embedding=[1.0, 0.0],
        )
        results = stream.retrieve(
            query_embedding=[1.0, 0.0],
            current_round=0,
            top_k=5,
        )
        assert len(results) == 1
        assert results[0].content == "with embedding"


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
            embedding=[0.0, 0.0, 1.0],
        )
        # Семантически близкая, но лексически далёкая
        stream.add(
            content="приобретение техники",
            importance=3.0,
            kind="observation",
            round_num=0,
            embedding=[1.0, 0.0, 0.0],
        )
        results = stream.retrieve(
            query_embedding=[1.0, 0.0, 0.0],
            current_round=0,
            top_k=2,
            query_text="закупка оборудования",
        )
        # BM25-бонус должен поднять первую запись
        assert results[0].content == "закупка оборудования"
