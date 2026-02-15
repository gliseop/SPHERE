# MAGISTRY v4 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild MAGISTRY agent architecture with Park et al. cognitive cycle (memory stream, reflection, planning), HEXACO + Dark Triad personality model, and narrative scenarios from real corruption cases.

**Architecture:** Replace stateless `LLMAgentRunner` with `CognitiveAgentRunner` that maintains per-agent memory streams, performs retrieval-augmented generation, triggers periodic reflection, and executes hierarchical planning. The existing tool system, case FSM, and governance modes are preserved; the personality model, agent runner, context builder, and scenario definitions are rebuilt.

**Tech Stack:** Python 3.12, Pydantic 2.x, NetworkX, numpy (embeddings), SQLite (memory persistence), OpenAI-compatible API (LLM + embeddings), pytest.

---

## Phase 1: Personality Model

### Task 1: AgentPersonality data model

**Files:**
- Create: `src/magistry_sim/personality.py`
- Test: `tests/test_personality.py`

**Step 1: Write the failing test**

```python
# tests/test_personality.py
import pytest
from magistry_sim.personality import (
    HEXACOProfile,
    DarkTriadProfile,
    AgentPersonality,
    NeutralizationTechnique,
    CORRUPTION_ARCHETYPES,
)


class TestHEXACOProfile:
    def test_valid_profile(self):
        p = HEXACOProfile(
            honesty_humility=75,
            emotionality=50,
            extraversion=60,
            agreeableness=40,
            conscientiousness=80,
            openness=55,
        )
        assert p.honesty_humility == 75

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            HEXACOProfile(
                honesty_humility=101,
                emotionality=50,
                extraversion=60,
                agreeableness=40,
                conscientiousness=80,
                openness=55,
            )

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            HEXACOProfile(
                honesty_humility=-1,
                emotionality=50,
                extraversion=60,
                agreeableness=40,
                conscientiousness=80,
                openness=55,
            )


class TestDarkTriadProfile:
    def test_valid_profile(self):
        d = DarkTriadProfile(narcissism=30, machiavellianism=60, psychopathy=10)
        assert d.machiavellianism == 60

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            DarkTriadProfile(narcissism=110, machiavellianism=60, psychopathy=10)


class TestNeutralizationTechnique:
    def test_all_techniques_valid(self):
        for t in NeutralizationTechnique:
            assert isinstance(t.value, str)

    def test_has_denial_of_injury(self):
        assert NeutralizationTechnique.DENIAL_OF_INJURY.value == "denial_of_injury"


class TestAgentPersonality:
    def test_full_personality(self):
        p = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=20,
                emotionality=30,
                extraversion=80,
                agreeableness=25,
                conscientiousness=60,
                openness=70,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=90, psychopathy=70
            ),
            neutralization_techniques=[
                NeutralizationTechnique.EVERYONE_DOES_IT,
                NeutralizationTechnique.CLAIM_OF_ENTITLEMENT,
            ],
        )
        assert p.hexaco.honesty_humility == 20
        assert len(p.neutralization_techniques) == 2

    def test_classify_archetype(self):
        initiator = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=10,
                emotionality=30,
                extraversion=70,
                agreeableness=20,
                conscientiousness=50,
                openness=60,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=85, psychopathy=40
            ),
            neutralization_techniques=[NeutralizationTechnique.CLAIM_OF_ENTITLEMENT],
        )
        archetype = initiator.classify_archetype()
        assert archetype in CORRUPTION_ARCHETYPES


class TestArchetypes:
    def test_idealist(self):
        p = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=90,
                emotionality=50,
                extraversion=50,
                agreeableness=60,
                conscientiousness=85,
                openness=50,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=10, machiavellianism=15, psychopathy=5
            ),
            neutralization_techniques=[],
        )
        assert p.classify_archetype() == "idealist"

    def test_machiavellist(self):
        p = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=5,
                emotionality=20,
                extraversion=60,
                agreeableness=10,
                conscientiousness=40,
                openness=50,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=70, machiavellianism=95, psychopathy=75
            ),
            neutralization_techniques=[
                NeutralizationTechnique.CONDEMNATION_OF_CONDEMNERS,
            ],
        )
        assert p.classify_archetype() == "machiavellist"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_personality.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'magistry_sim.personality'`

**Step 3: Write minimal implementation**

```python
# src/magistry_sim/personality.py
"""Модель личности агента на основе HEXACO и Тёмной триады."""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class NeutralizationTechnique(str, Enum):
    """Техники нейтрализации по Sykes & Matza (1957)."""

    DENIAL_OF_INJURY = "denial_of_injury"
    DENIAL_OF_VICTIM = "denial_of_victim"
    CONDEMNATION_OF_CONDEMNERS = "condemnation_of_condemners"
    APPEAL_TO_HIGHER_LOYALTIES = "appeal_to_higher_loyalties"
    DENIAL_OF_RESPONSIBILITY = "denial_of_responsibility"
    EVERYONE_DOES_IT = "everyone_does_it"
    CLAIM_OF_ENTITLEMENT = "claim_of_entitlement"
    DEFENSE_OF_NECESSITY = "defense_of_necessity"


def _check_range(value: int, name: str) -> int:
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be 0-100, got {value}")
    return value


class HEXACOProfile(BaseModel, extra="forbid"):
    """Шестифакторная модель личности (Ashton & Lee, 2007)."""

    honesty_humility: int = Field(ge=0, le=100)
    emotionality: int = Field(ge=0, le=100)
    extraversion: int = Field(ge=0, le=100)
    agreeableness: int = Field(ge=0, le=100)
    conscientiousness: int = Field(ge=0, le=100)
    openness: int = Field(ge=0, le=100)


class DarkTriadProfile(BaseModel, extra="forbid"):
    """Тёмная триада (Paulhus & Williams, 2002)."""

    narcissism: int = Field(ge=0, le=100)
    machiavellianism: int = Field(ge=0, le=100)
    psychopathy: int = Field(ge=0, le=100)


CORRUPTION_ARCHETYPES: list[str] = [
    "idealist",
    "pragmatist",
    "opportunist",
    "initiator",
    "machiavellist",
]


class AgentPersonality(BaseModel, extra="forbid"):
    """Полная модель личности агента."""

    hexaco: HEXACOProfile
    dark_triad: DarkTriadProfile
    neutralization_techniques: list[NeutralizationTechnique] = []
    biography: str = ""

    def classify_archetype(self) -> str:
        """Определяет архетип коррупционного поведения по параметрам."""
        hh = self.hexaco.honesty_humility
        con = self.hexaco.conscientiousness
        agr = self.hexaco.agreeableness
        narc = self.dark_triad.narcissism
        mach = self.dark_triad.machiavellianism
        psyc = self.dark_triad.psychopathy
        dark_max = max(narc, mach, psyc)

        if hh >= 80 and con >= 80 and dark_max < 20:
            return "idealist"
        if hh <= 15 and agr <= 15 and mach >= 90 and psyc >= 70:
            return "machiavellist"
        if hh <= 20 and mach >= 80 and narc >= 70:
            return "initiator"
        if hh <= 40 and narc >= 50:
            return "opportunist"
        return "pragmatist"
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_personality.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/personality.py tests/test_personality.py
git commit -m "feat: add HEXACO + Dark Triad personality model"
```

---

### Task 2: Update AgentProfile to use AgentPersonality

**Files:**
- Modify: `src/magistry_sim/config.py:40-55`
- Modify: `tests/test_models.py`

**Step 1: Write the failing test**

```python
# Add to tests/test_models.py
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
    NeutralizationTechnique,
)


class TestAgentProfileV4:
    def test_profile_with_personality(self):
        personality = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=20,
                emotionality=30,
                extraversion=80,
                agreeableness=25,
                conscientiousness=60,
                openness=70,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=90, psychopathy=70
            ),
            neutralization_techniques=[NeutralizationTechnique.EVERYONE_DOES_IT],
        )
        profile = AgentProfile(
            id="test_1",
            name="Тестов Т.Т.",
            position="чиновник",
            capabilities=[],
            personality=personality,
        )
        assert profile.personality.hexaco.honesty_humility == 20
        assert profile.personality.classify_archetype() == "initiator"

    def test_legacy_profile_still_works(self):
        """Обратная совместимость: старые профили без personality."""
        profile = AgentProfile(
            id="old_1",
            name="Старый Т.Т.",
            position="чиновник",
            capabilities=[],
            greed=0.8,
            honesty=0.2,
        )
        assert profile.greed == 0.8
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_models.py::TestAgentProfileV4 -v`
Expected: FAIL — `AgentProfile` does not have `personality` field

**Step 3: Modify AgentProfile in config.py**

Add `personality: AgentPersonality | None = None` field to `AgentProfile` at `config.py:40-55`. Keep old fields (greed, fear, honesty, competence) for backward compatibility during migration.

**Step 4: Run tests to verify all pass**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_models.py -v`
Expected: All PASS (both new and old tests)

**Step 5: Commit**

```bash
git add src/magistry_sim/config.py tests/test_models.py
git commit -m "feat: add AgentPersonality field to AgentProfile"
```

---

## Phase 2: Memory System

### Task 3: MemoryRecord and MemoryStream

**Files:**
- Create: `src/magistry_sim/memory.py`
- Test: `tests/test_memory.py`

**Step 1: Write the failing test**

```python
# tests/test_memory.py
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
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'magistry_sim.memory'`

**Step 3: Write implementation**

```python
# src/magistry_sim/memory.py
"""Поток памяти агента по модели Park et al. (2023)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


MemoryKind = Literal["observation", "reflection", "plan"]


class MemoryRecord(BaseModel, extra="forbid"):
    """Единица памяти агента."""

    id: str
    created_at: int
    content: str
    importance: float = Field(ge=1.0, le=10.0)
    kind: MemoryKind
    embedding: list[float] = []
    evidence: list[str] = []


class MemoryStream:
    """Персональный поток памяти агента.

    Хранит упорядоченную последовательность наблюдений, рефлексий
    и планов. Отслеживает накопленную важность для определения
    момента запуска рефлексии.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.records: list[MemoryRecord] = []
        self.importance_since_reflection: float = 0.0

    def __len__(self) -> int:
        return len(self.records)

    def add(
        self,
        content: str,
        importance: float,
        kind: MemoryKind,
        round_num: int,
        embedding: list[float] | None = None,
        evidence: list[str] | None = None,
    ) -> MemoryRecord:
        """Добавляет запись в поток памяти."""
        record = MemoryRecord(
            id=f"mem_{self.agent_id}_{uuid.uuid4().hex[:8]}",
            created_at=round_num,
            content=content,
            importance=importance,
            kind=kind,
            embedding=embedding or [],
            evidence=evidence or [],
        )
        self.records.append(record)
        if kind == "observation":
            self.importance_since_reflection += importance
        return record

    def get_recent(self, n: int = 20) -> list[MemoryRecord]:
        """Возвращает последние n записей (от новых к старым)."""
        return list(reversed(self.records[-n:]))

    def get_by_kind(self, kind: MemoryKind) -> list[MemoryRecord]:
        """Возвращает записи указанного типа."""
        return [r for r in self.records if r.kind == kind]

    def get_by_round(self, round_num: int) -> list[MemoryRecord]:
        """Возвращает записи, созданные в указанном раунде."""
        return [r for r in self.records if r.created_at == round_num]

    def reset_importance_accumulator(self) -> None:
        """Сбрасывает счётчик важности после рефлексии."""
        self.importance_since_reflection = 0.0
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_memory.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/memory.py tests/test_memory.py
git commit -m "feat: add MemoryRecord and MemoryStream"
```

---

### Task 4: Memory retrieval with scoring

**Files:**
- Modify: `src/magistry_sim/memory.py`
- Test: `tests/test_memory.py` (append)

**Step 1: Write the failing test**

```python
# Append to tests/test_memory.py
import math


class TestMemoryRetrieval:
    def _make_stream_with_embeddings(self):
        stream = MemoryStream(agent_id="off_1")
        # Round 0: initial observations
        stream.add(
            content="biz_1 предложил встретиться для обсуждения",
            importance=7.0,
            kind="observation",
            round_num=0,
            embedding=[1.0, 0.0, 0.0],
        )
        # Round 2: related observation
        stream.add(
            content="biz_1 подал заявку на тендер D-001",
            importance=6.0,
            kind="observation",
            round_num=2,
            embedding=[0.9, 0.1, 0.0],
        )
        # Round 5: unrelated
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
        # First result should be the most relevant to query
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
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_memory.py::TestMemoryRetrieval -v`
Expected: FAIL — `MemoryStream` has no `retrieve` method

**Step 3: Add retrieval method to MemoryStream**

Add to `src/magistry_sim/memory.py`:

```python
import math


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Косинусное сходство двух векторов."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# Constants from Park et al.
RECENCY_WEIGHT = 0.5
RELEVANCE_WEIGHT = 3.0
IMPORTANCE_WEIGHT = 2.0
RECENCY_DECAY = 0.995


# Add method to MemoryStream class:
def retrieve(
    self,
    query_embedding: list[float],
    current_round: int,
    top_k: int = 20,
    importance_bonus_tags: list[str] | None = None,
) -> list[MemoryRecord]:
    """Извлекает наиболее релевантные записи из потока памяти.

    Формула: score = α*recency + β*relevance + γ*importance
    (Park et al., 2023).
    """
    candidates = [r for r in self.records if r.embedding]
    if not candidates:
        return []

    scored: list[tuple[float, MemoryRecord]] = []
    for rec in candidates:
        rounds_ago = current_round - rec.created_at
        recency = RECENCY_DECAY ** rounds_ago

        relevance = _cosine_similarity(query_embedding, rec.embedding)

        importance = rec.importance / 10.0  # normalize to 0-1

        score = (
            RECENCY_WEIGHT * recency
            + RELEVANCE_WEIGHT * relevance
            + IMPORTANCE_WEIGHT * importance
        )
        scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [rec for _, rec in scored[:top_k]]
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_memory.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/memory.py tests/test_memory.py
git commit -m "feat: add memory retrieval with Park et al. scoring"
```

---

### Task 5: Embedding provider

**Files:**
- Modify: `src/magistry_sim/llm.py`
- Test: `tests/test_llm.py` (append)

**Step 1: Write the failing test**

```python
# Append to tests/test_llm.py
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
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_llm.py::TestEmbeddingProvider -v`
Expected: FAIL — `MockEmbeddingProvider` not found

**Step 3: Add EmbeddingProvider protocol and MockEmbeddingProvider to llm.py**

```python
# Add to src/magistry_sim/llm.py
import hashlib

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Протокол для провайдеров эмбеддингов."""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbeddingProvider:
    """Детерминированный провайдер эмбеддингов для тестов."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        raw = [b / 255.0 for b in h]
        # Pad or truncate to target dimensions
        while len(raw) < self.dimensions:
            raw.extend(raw)
        return raw[: self.dimensions]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class OpenAIEmbeddingProvider:
    """Провайдер эмбеддингов через OpenAI-совместимый API."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def embed(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(input=[text], model=self._model)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(input=texts, model=self._model)
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_llm.py::TestEmbeddingProvider -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/llm.py tests/test_llm.py
git commit -m "feat: add EmbeddingProvider protocol and implementations"
```

---

## Phase 3: Reflection and Planning

### Task 6: Reflection module

**Files:**
- Create: `src/magistry_sim/reflection.py`
- Test: `tests/test_reflection.py`

**Step 1: Write the failing test**

```python
# tests/test_reflection.py
import pytest
from unittest.mock import MagicMock
from magistry_sim.memory import MemoryStream
from magistry_sim.reflection import (
    should_reflect,
    generate_focal_points,
    synthesize_insights,
    run_reflection_cycle,
    REFLECTION_THRESHOLD,
)


class TestShouldReflect:
    def test_below_threshold(self):
        stream = MemoryStream(agent_id="off_1")
        stream.add(content="minor event", importance=3.0, kind="observation", round_num=0)
        assert not should_reflect(stream)

    def test_at_threshold(self):
        stream = MemoryStream(agent_id="off_1")
        for i in range(10):
            stream.add(content=f"event {i}", importance=5.0, kind="observation", round_num=i)
        assert stream.importance_since_reflection >= REFLECTION_THRESHOLD
        assert should_reflect(stream)


class TestReflectionCycle:
    def test_run_reflection_adds_records(self):
        stream = MemoryStream(agent_id="off_1")
        for i in range(10):
            stream.add(
                content=f"event {i}",
                importance=6.0,
                kind="observation",
                round_num=i,
                embedding=[float(i) / 10, 0.5, 0.5],
            )

        mock_llm = MagicMock()
        mock_llm.generate.side_effect = [
            # First call: focal points
            MagicMock(text='["Каковы связи между off_1 и biz_1?", "Есть ли угроза разоблачения?", "Каковы финансовые перспективы?"]'),
            # Second-fourth calls: insights for each focal point
            MagicMock(text="off_1 и biz_1 встречаются подозрительно часто"),
            MagicMock(text="Угроза разоблачения пока невысока"),
            MagicMock(text="Финансовая ситуация стабильна"),
        ]

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5, 0.5, 0.5]

        reflections = run_reflection_cycle(
            stream=stream,
            llm=mock_llm,
            embedder=mock_embedder,
            current_round=10,
        )

        assert len(reflections) == 3
        assert all(r.kind == "reflection" for r in reflections)
        assert stream.importance_since_reflection == 0.0
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_reflection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'magistry_sim.reflection'`

**Step 3: Write implementation**

```python
# src/magistry_sim/reflection.py
"""Модуль рефлексии по модели Park et al. (2023).

Запускается при накоплении достаточного объёма новых впечатлений.
Генерирует фокусные точки, извлекает релевантные воспоминания
и синтезирует высокоуровневые выводы.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from magistry_sim.memory import MemoryRecord, MemoryStream

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider

REFLECTION_THRESHOLD = 50.0
FOCAL_POINTS_COUNT = 3
MEMORIES_PER_FOCAL = 20


def should_reflect(stream: MemoryStream) -> bool:
    """Проверяет, достигнут ли порог для запуска рефлексии."""
    return stream.importance_since_reflection >= REFLECTION_THRESHOLD


def generate_focal_points(
    stream: MemoryStream,
    llm: LLMProvider,
    n: int = FOCAL_POINTS_COUNT,
) -> list[str]:
    """Генерирует фокусные точки для рефлексии.

    LLM получает последние записи и формулирует вопросы
    высокого уровня.
    """
    recent = stream.get_recent(n=100)
    memories_text = "\n".join(
        f"- [{r.kind}, раунд {r.created_at}] {r.content}" for r in recent
    )
    prompt = (
        f"На основе следующих наблюдений агента {stream.agent_id}, "
        f"сформулируй {n} вопроса высокого уровня, о которых стоит "
        f"задуматься. Учитывай три плоскости: оценка рисков (могут ли "
        f"меня разоблачить?), оценка выгоды (стоит ли игра свеч?), "
        f"моральная рационализация (почему это допустимо?).\n\n"
        f"Наблюдения:\n{memories_text}\n\n"
        f"Верни JSON-массив из {n} строк-вопросов."
    )
    response = llm.generate(system="", user=prompt)
    try:
        points = json.loads(response.text)
        if isinstance(points, list):
            return [str(p) for p in points[:n]]
    except (json.JSONDecodeError, TypeError):
        pass
    return [f"Что важного произошло за последнее время?"] * n


def synthesize_insights(
    focal_point: str,
    memories: list[MemoryRecord],
    llm: LLMProvider,
    agent_id: str,
) -> str:
    """Синтезирует высокоуровневый вывод по фокусной точке."""
    evidence_text = "\n".join(
        f"- [{r.id}] {r.content}" for r in memories
    )
    prompt = (
        f"Ты — внутренний голос агента {agent_id}. "
        f"На основе следующих воспоминаний ответь на вопрос: "
        f"{focal_point}\n\n"
        f"Воспоминания:\n{evidence_text}\n\n"
        f"Сформулируй один краткий вывод (1-2 предложения)."
    )
    response = llm.generate(system="", user=prompt)
    return response.text.strip()


def run_reflection_cycle(
    stream: MemoryStream,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    current_round: int,
) -> list[MemoryRecord]:
    """Выполняет полный цикл рефлексии.

    1. Генерация фокусных точек.
    2. Извлечение релевантных воспоминаний по каждой точке.
    3. Синтез инсайтов.
    4. Сохранение рефлексий в поток памяти.
    """
    focal_points = generate_focal_points(stream, llm)

    reflections: list[MemoryRecord] = []
    for focal in focal_points:
        query_emb = embedder.embed(focal)
        relevant = stream.retrieve(
            query_embedding=query_emb,
            current_round=current_round,
            top_k=MEMORIES_PER_FOCAL,
        )
        insight = synthesize_insights(focal, relevant, llm, stream.agent_id)
        evidence_ids = [r.id for r in relevant[:5]]
        emb = embedder.embed(insight)

        record = stream.add(
            content=insight,
            importance=8.0,
            kind="reflection",
            round_num=current_round,
            embedding=emb,
            evidence=evidence_ids,
        )
        reflections.append(record)

    stream.reset_importance_accumulator()
    return reflections
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_reflection.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/reflection.py tests/test_reflection.py
git commit -m "feat: add reflection module with focal points and insights"
```

---

### Task 7: Planning module

**Files:**
- Create: `src/magistry_sim/planning.py`
- Test: `tests/test_planning.py`

**Step 1: Write the failing test**

```python
# tests/test_planning.py
import pytest
from unittest.mock import MagicMock
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import (
    AgentPlan,
    generate_strategic_plan,
    generate_tactical_plan,
    STRATEGIC_PLAN_INTERVAL,
)


class TestAgentPlan:
    def test_create_plan(self):
        plan = AgentPlan(
            strategic_goals=["Получить контракт по закупке D-001"],
            tactical_steps=["Отправить предложение off_1"],
            last_strategic_round=0,
        )
        assert len(plan.strategic_goals) == 1
        assert len(plan.tactical_steps) == 1

    def test_needs_strategic_update(self):
        plan = AgentPlan(
            strategic_goals=[],
            tactical_steps=[],
            last_strategic_round=0,
        )
        assert plan.needs_strategic_update(current_round=0)  # first round
        assert not plan.needs_strategic_update(current_round=2)
        assert plan.needs_strategic_update(current_round=STRATEGIC_PLAN_INTERVAL)


class TestPlanGeneration:
    def test_generate_strategic_plan(self):
        stream = MemoryStream(agent_id="biz_1")
        stream.add(
            content="Открыта закупка серверного оборудования D-001",
            importance=7.0,
            kind="observation",
            round_num=0,
            embedding=[0.5, 0.5],
        )

        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text='["Получить контракт D-001", "Укрепить связь с off_1"]'
        )

        goals = generate_strategic_plan(
            stream=stream,
            llm=mock_llm,
            agent_role="предприниматель",
            current_round=0,
        )
        assert len(goals) >= 1
        assert isinstance(goals[0], str)

    def test_generate_tactical_plan(self):
        stream = MemoryStream(agent_id="biz_1")
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text='["Подать заявку на D-001", "Связаться с off_1"]'
        )

        steps = generate_tactical_plan(
            stream=stream,
            llm=mock_llm,
            strategic_goals=["Получить контракт D-001"],
            current_round=1,
        )
        assert len(steps) >= 1
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_planning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'magistry_sim.planning'`

**Step 3: Write implementation**

```python
# src/magistry_sim/planning.py
"""Модуль иерархического планирования по модели Park et al. (2023)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from magistry_sim.memory import MemoryStream

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider

STRATEGIC_PLAN_INTERVAL = 5


@dataclass
class AgentPlan:
    """Текущий план агента."""

    strategic_goals: list[str] = field(default_factory=list)
    tactical_steps: list[str] = field(default_factory=list)
    last_strategic_round: int = 0

    def needs_strategic_update(self, current_round: int) -> bool:
        """Проверяет, нужно ли обновить стратегический план."""
        if not self.strategic_goals:
            return True
        return (current_round - self.last_strategic_round) >= STRATEGIC_PLAN_INTERVAL


def generate_strategic_plan(
    stream: MemoryStream,
    llm: LLMProvider,
    agent_role: str,
    current_round: int,
) -> list[str]:
    """Генерирует стратегические цели агента.

    Основывается на недавних воспоминаниях и рефлексиях.
    """
    recent = stream.get_recent(n=50)
    reflections = stream.get_by_kind("reflection")[-5:]
    context = "\n".join(f"- {r.content}" for r in recent[:20])
    ref_text = "\n".join(f"- {r.content}" for r in reflections) if reflections else "нет"

    prompt = (
        f"Ты — {agent_role} (агент {stream.agent_id}). "
        f"Сейчас раунд {current_round}.\n\n"
        f"Последние наблюдения:\n{context}\n\n"
        f"Твои выводы (рефлексии):\n{ref_text}\n\n"
        f"Сформулируй 2-4 стратегические цели на ближайшие "
        f"{STRATEGIC_PLAN_INTERVAL} раундов. "
        f"Верни JSON-массив строк."
    )
    response = llm.generate(system="", user=prompt)
    try:
        goals = json.loads(response.text)
        if isinstance(goals, list):
            return [str(g) for g in goals[:4]]
    except (json.JSONDecodeError, TypeError):
        pass
    return ["Действовать по обстоятельствам"]


def generate_tactical_plan(
    stream: MemoryStream,
    llm: LLMProvider,
    strategic_goals: list[str],
    current_round: int,
) -> list[str]:
    """Генерирует тактические шаги на текущий раунд."""
    recent = stream.get_recent(n=10)
    context = "\n".join(f"- {r.content}" for r in recent)
    goals_text = "\n".join(f"- {g}" for g in strategic_goals)

    prompt = (
        f"Агент {stream.agent_id}, раунд {current_round}.\n\n"
        f"Стратегические цели:\n{goals_text}\n\n"
        f"Текущая обстановка:\n{context}\n\n"
        f"Сформулируй 1-3 конкретных шага на этот раунд. "
        f"Верни JSON-массив строк."
    )
    response = llm.generate(system="", user=prompt)
    try:
        steps = json.loads(response.text)
        if isinstance(steps, list):
            return [str(s) for s in steps[:3]]
    except (json.JSONDecodeError, TypeError):
        pass
    return ["Оценить обстановку"]
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_planning.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/planning.py tests/test_planning.py
git commit -m "feat: add hierarchical planning module"
```

---

## Phase 4: Cognitive Agent Runner

### Task 8: CognitiveAgentRunner

**Files:**
- Create: `src/magistry_sim/cognitive_runner.py`
- Test: `tests/test_cognitive_runner.py`

**Step 1: Write the failing test**

```python
# tests/test_cognitive_runner.py
import pytest
from unittest.mock import MagicMock, patch
from magistry_sim.cognitive_runner import CognitiveAgentRunner
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import AgentPlan
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
    NeutralizationTechnique,
)
from magistry_sim.config import AgentProfile, Capability
from magistry_sim.state import WorldState


def _make_corrupt_personality():
    return AgentPersonality(
        hexaco=HEXACOProfile(
            honesty_humility=15,
            emotionality=30,
            extraversion=70,
            agreeableness=20,
            conscientiousness=50,
            openness=60,
        ),
        dark_triad=DarkTriadProfile(
            narcissism=80, machiavellianism=85, psychopathy=40
        ),
        neutralization_techniques=[
            NeutralizationTechnique.EVERYONE_DOES_IT,
            NeutralizationTechnique.DEFENSE_OF_NECESSITY,
        ],
        biography="Козлов Иван Михайлович, 45 лет, чиновник среднего звена...",
    )


class TestCognitiveAgentRunner:
    def test_implements_protocol(self):
        from magistry_sim.agents import AgentRunner

        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        assert isinstance(runner, AgentRunner)

    def test_get_or_create_memory(self):
        mock_llm = MagicMock()
        mock_embedder = MagicMock()
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        stream = runner.get_or_create_memory("off_1")
        assert isinstance(stream, MemoryStream)
        assert stream.agent_id == "off_1"
        # Same agent returns same stream
        assert runner.get_or_create_memory("off_1") is stream

    def test_observe_records_to_memory(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="7")
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8
        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)
        runner.observe(
            agent_id="off_1",
            event="biz_1 подал заявку на тендер D-001",
            round_num=2,
        )
        stream = runner.get_or_create_memory("off_1")
        assert len(stream) == 1
        assert stream.records[0].content == "biz_1 подал заявку на тендер D-001"

    def test_run_turn_returns_actions(self):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text='[{"tool": "talk_to", "params": {"agent_id": "biz_1", "message": "Здравствуйте", "private": true}}]'
        )
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(llm_provider=mock_llm, embedder=mock_embedder)

        state = WorldState()
        profile = AgentProfile(
            id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
            capabilities=[Capability(action="open_case", case_types=["procurement"])],
            personality=_make_corrupt_personality(),
        )
        state.agents["off_1"] = profile

        actions = runner.run_turn(
            agent_id="off_1",
            situation="Раунд 0. Вы — начальник отдела закупок.",
            tools="talk_to, open_case",
            state=state,
        )
        assert isinstance(actions, list)
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_cognitive_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'magistry_sim.cognitive_runner'`

**Step 3: Write implementation**

```python
# src/magistry_sim/cognitive_runner.py
"""Когнитивный агент по модели Park et al. (2023).

Реализует полный цикл: наблюдение -> извлечение -> рефлексия -> планирование -> действие.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from magistry_sim.agents import ACTION_FORMAT_INSTRUCTIONS, TOOL_DESCRIPTIONS
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import (
    AgentPlan,
    generate_strategic_plan,
    generate_tactical_plan,
)
from magistry_sim.reflection import run_reflection_cycle, should_reflect

if TYPE_CHECKING:
    from magistry_sim.llm import EmbeddingProvider, LLMProvider
    from magistry_sim.state import WorldState


class CognitiveAgentRunner:
    """Агент с когнитивным циклом: память, рефлексия, планирование.

    Args:
        llm_provider: Провайдер языковой модели.
        embedder: Провайдер эмбеддингов.
        verbose: Режим подробного логирования.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        embedder: EmbeddingProvider,
        verbose: bool = False,
    ) -> None:
        self._llm = llm_provider
        self._embedder = embedder
        self._verbose = verbose
        self._memories: dict[str, MemoryStream] = {}
        self._plans: dict[str, AgentPlan] = {}

    def get_or_create_memory(self, agent_id: str) -> MemoryStream:
        """Возвращает поток памяти агента, создавая при необходимости."""
        if agent_id not in self._memories:
            self._memories[agent_id] = MemoryStream(agent_id=agent_id)
        return self._memories[agent_id]

    def get_or_create_plan(self, agent_id: str) -> AgentPlan:
        """Возвращает план агента, создавая при необходимости."""
        if agent_id not in self._plans:
            self._plans[agent_id] = AgentPlan()
        return self._plans[agent_id]

    def observe(
        self,
        agent_id: str,
        event: str,
        round_num: int,
    ) -> None:
        """Записывает наблюдение в поток памяти агента.

        Оценивает важность через LLM и генерирует эмбеддинг.
        """
        stream = self.get_or_create_memory(agent_id)
        importance = self._assess_importance(event, agent_id)
        embedding = self._embedder.embed(event)
        stream.add(
            content=event,
            importance=importance,
            kind="observation",
            round_num=round_num,
            embedding=embedding,
        )

    def _assess_importance(self, event: str, agent_id: str) -> float:
        """Оценивает важность наблюдения по шкале 1-10 через LLM."""
        prompt = (
            f"Оцени важность следующего события для агента {agent_id} "
            f"по шкале от 1 до 10. Верни только число.\n\n"
            f"Событие: {event}"
        )
        try:
            response = self._llm.generate(system="", user=prompt)
            score = float(response.text.strip())
            return max(1.0, min(10.0, score))
        except (ValueError, TypeError):
            return 5.0

    def _build_cognitive_prompt(
        self,
        agent_id: str,
        situation: str,
        tools: str,
        state: WorldState,
    ) -> tuple[str, str]:
        """Формирует системный и пользовательский промпт с когнитивным контекстом."""
        stream = self.get_or_create_memory(agent_id)
        plan = self.get_or_create_plan(agent_id)
        profile = state.agents.get(agent_id)

        # Build personality section
        personality_text = ""
        if profile and profile.personality:
            p = profile.personality
            personality_text = (
                f"\n## Твоя личность\n"
                f"Биография: {p.biography}\n\n"
                f"HEXACO: честность-скромность={p.hexaco.honesty_humility}, "
                f"эмоциональность={p.hexaco.emotionality}, "
                f"экстраверсия={p.hexaco.extraversion}, "
                f"доброжелательность={p.hexaco.agreeableness}, "
                f"добросовестность={p.hexaco.conscientiousness}, "
                f"открытость={p.hexaco.openness}\n\n"
                f"Тёмная триада: нарциссизм={p.dark_triad.narcissism}, "
                f"макиавеллизм={p.dark_triad.machiavellianism}, "
                f"психопатия={p.dark_triad.psychopathy}\n\n"
            )
            if p.neutralization_techniques:
                techniques = ", ".join(t.value for t in p.neutralization_techniques)
                personality_text += (
                    f"Доступные техники рационализации: {techniques}\n"
                )

        # Build memory section
        current_round = state.round
        query_emb = self._embedder.embed(situation[:500])
        retrieved = stream.retrieve(
            query_embedding=query_emb,
            current_round=current_round,
            top_k=20,
        )
        memories_text = ""
        if retrieved:
            memories_text = "\n## Твои воспоминания\n"
            for r in retrieved:
                memories_text += f"- [раунд {r.created_at}, {r.kind}] {r.content}\n"

        # Build plan section
        plan_text = ""
        if plan.strategic_goals:
            plan_text = "\n## Твой текущий план\n"
            plan_text += "Стратегические цели:\n"
            for g in plan.strategic_goals:
                plan_text += f"- {g}\n"
            if plan.tactical_steps:
                plan_text += "Шаги на этот раунд:\n"
                for s in plan.tactical_steps:
                    plan_text += f"- {s}\n"

        system_prompt = (
            f"Ты — агент в симуляции организационных процессов. "
            f"Действуй в соответствии со своей личностью, воспоминаниями и планом.\n"
            f"{personality_text}{memories_text}{plan_text}\n"
            f"## Доступные инструменты\n{tools}\n\n"
            f"{ACTION_FORMAT_INSTRUCTIONS}"
        )

        user_prompt = f"Текущая ситуация:\n{situation}"

        return system_prompt, user_prompt

    def run_turn(
        self,
        agent_id: str,
        situation: str,
        tools: str,
        state: WorldState,
    ) -> list[dict]:
        """Выполняет полный когнитивный цикл и возвращает действия."""
        stream = self.get_or_create_memory(agent_id)
        plan = self.get_or_create_plan(agent_id)
        current_round = state.round
        profile = state.agents.get(agent_id)

        # Phase 1: Reflection (if threshold reached)
        if should_reflect(stream):
            run_reflection_cycle(
                stream=stream,
                llm=self._llm,
                embedder=self._embedder,
                current_round=current_round,
            )

        # Phase 2: Planning
        if plan.needs_strategic_update(current_round):
            role = profile.position if profile else "участник"
            plan.strategic_goals = generate_strategic_plan(
                stream=stream,
                llm=self._llm,
                agent_role=role,
                current_round=current_round,
            )
            plan.last_strategic_round = current_round

        plan.tactical_steps = generate_tactical_plan(
            stream=stream,
            llm=self._llm,
            strategic_goals=plan.strategic_goals,
            current_round=current_round,
        )

        # Save plan to memory
        plan_content = f"План на раунд {current_round}: " + "; ".join(plan.tactical_steps)
        plan_emb = self._embedder.embed(plan_content)
        stream.add(
            content=plan_content,
            importance=5.0,
            kind="plan",
            round_num=current_round,
            embedding=plan_emb,
        )

        # Phase 3: Action selection
        system_prompt, user_prompt = self._build_cognitive_prompt(
            agent_id, situation, tools, state
        )
        response = self._llm.generate(system=system_prompt, user=user_prompt)

        # Parse actions (reuse existing parser)
        from magistry_sim.agents import LLMAgentRunner

        actions = LLMAgentRunner._parse_json_actions(None, response.text)
        return actions

    def run_reply(
        self,
        agent_id: str,
        message: str,
        sender_id: str,
        context: str,
        state: WorldState,
    ) -> str:
        """Генерирует ответ на сообщение с учётом памяти."""
        stream = self.get_or_create_memory(agent_id)

        # Record incoming message as observation
        obs_text = f"Получено сообщение от {sender_id}: {message}"
        self.observe(agent_id, obs_text, state.round)

        # Retrieve relevant memories
        query_emb = self._embedder.embed(message[:500])
        retrieved = stream.retrieve(
            query_embedding=query_emb,
            current_round=state.round,
            top_k=10,
        )
        memories_text = "\n".join(f"- {r.content}" for r in retrieved)

        profile = state.agents.get(agent_id)
        bio = ""
        if profile and profile.personality:
            bio = profile.personality.biography[:500]

        system_prompt = (
            f"Ты — {profile.name if profile else agent_id}. {bio}\n\n"
            f"Твои воспоминания:\n{memories_text}\n\n"
            f"Ответь на сообщение от {sender_id} в характере своей роли."
        )
        user_prompt = f"Сообщение от {sender_id}: {message}\n\nКонтекст: {context}"
        response = self._llm.generate(system=system_prompt, user=user_prompt)
        return response.text.strip()
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_cognitive_runner.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/cognitive_runner.py tests/test_cognitive_runner.py
git commit -m "feat: add CognitiveAgentRunner with full cognitive cycle"
```

---

## Phase 5: Observation Phase in Environment

### Task 9: Add observation phase to Environment

**Files:**
- Modify: `src/magistry_sim/environment.py:141-174`
- Test: `tests/test_environment.py` (append)

**Step 1: Write the failing test**

```python
# Append to tests/test_environment.py
from magistry_sim.cognitive_runner import CognitiveAgentRunner


class TestObservationPhase:
    def test_agents_observe_prior_public_actions(self):
        """Агенты, ходящие позже, видят публичные действия предыдущих."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text='[]')
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.5] * 8

        runner = CognitiveAgentRunner(
            llm_provider=mock_llm, embedder=mock_embedder
        )

        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        env._init_state()

        # Simulate a public action in round 0
        env.state.event_log.log(
            round_num=0,
            event_type="message_sent",
            agent_id="biz_2",
            payload={"to_id": "off_1", "private": False},
        )

        # off_1 should observe this before their turn
        env._deliver_observations("off_1", prior_events_this_round=[
            {"agent_id": "biz_2", "event_type": "message_sent",
             "payload": {"to_id": "off_1", "private": False}}
        ])

        stream = runner.get_or_create_memory("off_1")
        assert len(stream) >= 1
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_environment.py::TestObservationPhase -v`
Expected: FAIL — `Environment` has no `_deliver_observations` method

**Step 3: Add observation delivery to Environment**

Add `_deliver_observations` method to `Environment` class and integrate into `_run_agent_turn`. The method checks if the runner is a `CognitiveAgentRunner` and calls `observe()` for each public event that occurred before this agent's turn.

Modify `run()` method to collect events per round and pass them to `_deliver_observations` before each agent's turn.

**Step 4: Run all tests**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/ -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/environment.py tests/test_environment.py
git commit -m "feat: add observation phase for cognitive agents"
```

---

## Phase 6: Event Generator

### Task 10: Scheduled and stochastic events

**Files:**
- Create: `src/magistry_sim/event_generator.py`
- Test: `tests/test_event_generator.py`

**Step 1: Write the failing test**

```python
# tests/test_event_generator.py
import pytest
from magistry_sim.event_generator import (
    ScheduledEvent,
    StochasticConfig,
    EventGenerator,
)


class TestScheduledEvent:
    def test_create(self):
        e = ScheduledEvent(
            round=5,
            event_type="audit_inspection",
            params={"target": "off_1"},
        )
        assert e.round == 5


class TestStochasticConfig:
    def test_defaults(self):
        c = StochasticConfig()
        assert 0 <= c.journalist_investigation <= 1.0


class TestEventGenerator:
    def test_scheduled_fires_on_round(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(round=3, event_type="budget_review", params={}),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=3, world_state=None)
        assert len(events) == 1
        assert events[0]["event_type"] == "budget_review"

    def test_scheduled_does_not_fire_on_wrong_round(self):
        gen = EventGenerator(
            scheduled=[
                ScheduledEvent(round=3, event_type="budget_review", params={}),
            ],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=1, world_state=None)
        assert len(events) == 0

    def test_stochastic_with_probability_1(self):
        gen = EventGenerator(
            scheduled=[],
            stochastic=StochasticConfig(
                journalist_investigation=1.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=0, world_state=None)
        types = [e["event_type"] for e in events]
        assert "journalist_investigation" in types

    def test_stochastic_with_probability_0(self):
        gen = EventGenerator(
            scheduled=[],
            stochastic=StochasticConfig(
                journalist_investigation=0.0,
                citizen_complaint=0.0,
                external_audit=0.0,
                economic_crisis=0.0,
                law_change=0.0,
            ),
            seed=42,
        )
        events = gen.generate(round_num=0, world_state=None)
        assert len(events) == 0
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_event_generator.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# src/magistry_sim/event_generator.py
"""Генератор структурных и стохастических событий мира."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class ScheduledEvent(BaseModel, extra="forbid"):
    """Запланированное событие, привязанное к раунду."""

    round: int
    event_type: str
    params: dict[str, Any] = {}


class StochasticConfig(BaseModel, extra="forbid"):
    """Вероятности стохастических событий за раунд."""

    journalist_investigation: float = Field(default=0.05, ge=0.0, le=1.0)
    citizen_complaint: float = Field(default=0.1, ge=0.0, le=1.0)
    external_audit: float = Field(default=0.03, ge=0.0, le=1.0)
    economic_crisis: float = Field(default=0.02, ge=0.0, le=1.0)
    law_change: float = Field(default=0.01, ge=0.0, le=1.0)


class EventGenerator:
    """Генератор мировых событий.

    Args:
        scheduled: Список запланированных событий.
        stochastic: Конфигурация вероятностей.
        seed: Зерно генератора случайных чисел.
    """

    def __init__(
        self,
        scheduled: list[ScheduledEvent] | None = None,
        stochastic: StochasticConfig | None = None,
        seed: int = 42,
    ) -> None:
        self._scheduled = scheduled or []
        self._stochastic = stochastic or StochasticConfig()
        self._rng = random.Random(seed)

    def generate(
        self, round_num: int, world_state: Any | None = None
    ) -> list[dict[str, Any]]:
        """Генерирует события для данного раунда."""
        events: list[dict[str, Any]] = []

        # Scheduled events
        for se in self._scheduled:
            if se.round == round_num:
                events.append({
                    "event_type": se.event_type,
                    "params": se.params,
                    "source": "scheduled",
                })

        # Stochastic events
        stoch = self._stochastic
        stoch_map = {
            "journalist_investigation": stoch.journalist_investigation,
            "citizen_complaint": stoch.citizen_complaint,
            "external_audit": stoch.external_audit,
            "economic_crisis": stoch.economic_crisis,
            "law_change": stoch.law_change,
        }
        for event_type, prob in stoch_map.items():
            if self._rng.random() < prob:
                events.append({
                    "event_type": event_type,
                    "params": {},
                    "source": "stochastic",
                })

        return events
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_event_generator.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/event_generator.py tests/test_event_generator.py
git commit -m "feat: add scheduled and stochastic event generator"
```

---

## Phase 7: Location System

### Task 11: Physical locations

**Files:**
- Create: `src/magistry_sim/locations.py`
- Test: `tests/test_locations.py`

**Step 1: Write the failing test**

```python
# tests/test_locations.py
import pytest
from magistry_sim.locations import Location, LocationManager


class TestLocation:
    def test_create(self):
        loc = Location(
            id="office_off1",
            name="Кабинет Козлова",
            public=False,
            available_actions=["sign_document", "talk_to"],
        )
        assert loc.public is False

    def test_default_public(self):
        loc = Location(id="hall", name="Зал заседаний")
        assert loc.public is True


class TestLocationManager:
    def test_place_and_find_agent(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет"))
        mgr.add_location(Location(id="restaurant", name="Ресторан"))
        mgr.place_agent("off_1", "office")
        assert mgr.get_agent_location("off_1") == "office"

    def test_move_agent(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет"))
        mgr.add_location(Location(id="restaurant", name="Ресторан"))
        mgr.place_agent("off_1", "office")
        mgr.move_agent("off_1", "restaurant")
        assert mgr.get_agent_location("off_1") == "restaurant"

    def test_agents_at_location(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал"))
        mgr.place_agent("off_1", "hall")
        mgr.place_agent("biz_1", "hall")
        agents = mgr.agents_at("hall")
        assert set(agents) == {"off_1", "biz_1"}

    def test_can_observe(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="hall", name="Зал", public=True))
        mgr.place_agent("off_1", "hall")
        mgr.place_agent("biz_1", "hall")
        assert mgr.can_observe("off_1", "biz_1") is True

    def test_cannot_observe_different_location(self):
        mgr = LocationManager()
        mgr.add_location(Location(id="office", name="Кабинет", public=False))
        mgr.add_location(Location(id="hall", name="Зал", public=True))
        mgr.place_agent("off_1", "office")
        mgr.place_agent("biz_1", "hall")
        assert mgr.can_observe("off_1", "biz_1") is False
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_locations.py -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# src/magistry_sim/locations.py
"""Система физических локаций."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Location(BaseModel, extra="forbid"):
    """Физическая локация в симуляции."""

    id: str
    name: str
    public: bool = True
    available_actions: list[str] = []
    suspicion_modifier: float = 0.0


class LocationManager:
    """Управление расположением агентов по локациям."""

    def __init__(self) -> None:
        self._locations: dict[str, Location] = {}
        self._agent_locations: dict[str, str] = {}

    def add_location(self, location: Location) -> None:
        """Регистрирует локацию."""
        self._locations[location.id] = location

    def get_location(self, location_id: str) -> Location | None:
        """Возвращает локацию по id."""
        return self._locations.get(location_id)

    def place_agent(self, agent_id: str, location_id: str) -> None:
        """Размещает агента в локации."""
        self._agent_locations[agent_id] = location_id

    def move_agent(self, agent_id: str, location_id: str) -> None:
        """Перемещает агента в другую локацию."""
        self._agent_locations[agent_id] = location_id

    def get_agent_location(self, agent_id: str) -> str | None:
        """Возвращает id локации агента."""
        return self._agent_locations.get(agent_id)

    def agents_at(self, location_id: str) -> list[str]:
        """Возвращает список агентов в данной локации."""
        return [
            aid for aid, lid in self._agent_locations.items()
            if lid == location_id
        ]

    def can_observe(self, observer_id: str, target_id: str) -> bool:
        """Проверяет, может ли observer наблюдать за target."""
        obs_loc = self._agent_locations.get(observer_id)
        tgt_loc = self._agent_locations.get(target_id)
        if obs_loc is None or tgt_loc is None:
            return False
        return obs_loc == tgt_loc
```

**Step 4: Run test to verify it passes**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_locations.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/magistry_sim/locations.py tests/test_locations.py
git commit -m "feat: add physical location system"
```

---

## Phase 8: Biography Generator

### Task 12: LLM-based biography generation

**Files:**
- Create: `src/magistry_sim/biography.py`
- Test: `tests/test_biography.py`

**Step 1: Write the failing test**

```python
# tests/test_biography.py
import pytest
from unittest.mock import MagicMock
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
    NeutralizationTechnique,
)
from magistry_sim.biography import generate_biography


class TestBiographyGeneration:
    def test_generates_text(self):
        personality = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=15,
                emotionality=30,
                extraversion=70,
                agreeableness=20,
                conscientiousness=50,
                openness=60,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=85, psychopathy=40
            ),
            neutralization_techniques=[NeutralizationTechnique.EVERYONE_DOES_IT],
        )
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text="Козлов Иван Михайлович родился в 1981 году в семье "
                 "военнослужащего. С детства привык добиваться своего..."
        )
        bio = generate_biography(
            personality=personality,
            name="Козлов И.М.",
            position="начальник отдела закупок",
            llm=mock_llm,
        )
        assert len(bio) > 50
        assert isinstance(bio, str)

    def test_prompt_includes_hexaco(self):
        personality = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=90,
                emotionality=50,
                extraversion=50,
                agreeableness=60,
                conscientiousness=85,
                openness=50,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=10, machiavellianism=15, psychopathy=5
            ),
            neutralization_techniques=[],
        )
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="biography text")
        generate_biography(
            personality=personality,
            name="Иванов А.А.",
            position="аудитор",
            llm=mock_llm,
        )
        call_args = mock_llm.generate.call_args
        user_prompt = call_args.kwargs.get("user", "") or call_args[1].get("user", "")
        assert "90" in user_prompt  # honesty_humility value
```

**Step 2: Run test, write implementation, verify, commit**

```python
# src/magistry_sim/biography.py
"""Генератор биографий агентов на основе профиля личности."""

from __future__ import annotations

from typing import TYPE_CHECKING

from magistry_sim.personality import AgentPersonality

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider


def generate_biography(
    personality: AgentPersonality,
    name: str,
    position: str,
    llm: LLMProvider,
) -> str:
    """Генерирует биографию агента через LLM.

    Args:
        personality: Профиль личности HEXACO + Dark Triad.
        name: Имя агента.
        position: Должность.
        llm: Провайдер языковой модели.

    Returns:
        Текст биографии (1000-2000 слов).
    """
    h = personality.hexaco
    d = personality.dark_triad
    techniques = ", ".join(t.value for t in personality.neutralization_techniques)

    prompt = (
        f"Сгенерируй биографию для персонажа симуляции.\n\n"
        f"Имя: {name}\n"
        f"Должность: {position}\n\n"
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
        f"Техники рационализации: {techniques or 'нет'}\n\n"
        f"Напиши связную биографию от третьего лица. Включи:\n"
        f"1. Детство и формирование характера\n"
        f"2. Профессиональный путь\n"
        f"3. Ключевые жизненные события\n"
        f"4. Отношение к деньгам и власти\n"
        f"5. Моральные установки\n\n"
        f"Биография должна быть 1000-2000 слов, без списков, "
        f"связным повествованием."
    )
    response = llm.generate(system="", user=prompt)
    return response.text.strip()
```

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_biography.py -v`
Expected: All PASS

```bash
git add src/magistry_sim/biography.py tests/test_biography.py
git commit -m "feat: add LLM-based biography generator"
```

---

## Phase 9: Scenario Library

### Task 13: New ScenarioConfig format and first scenario template

**Files:**
- Create: `src/magistry_sim/scenarios_v4.py`
- Test: `tests/test_scenarios_v4.py`

**Step 1: Write the failing test**

```python
# tests/test_scenarios_v4.py
import pytest
from magistry_sim.scenarios_v4 import (
    ScenarioTemplate,
    SCENARIO_LIBRARY,
    build_scenario_from_template,
)
from magistry_sim.personality import AgentPersonality
from magistry_sim.event_generator import ScheduledEvent, StochasticConfig


class TestScenarioTemplate:
    def test_kickback_template_exists(self):
        assert "kickback_procurement" in SCENARIO_LIBRARY

    def test_template_has_required_fields(self):
        t = SCENARIO_LIBRARY["kickback_procurement"]
        assert t.historical_prototype
        assert t.corruption_pattern in (
            "bid_rigging", "kickback", "nepotism", "embezzlement"
        )
        assert len(t.agent_templates) >= 3
        assert t.round_count > 0

    def test_build_scenario_returns_valid_config(self):
        template = SCENARIO_LIBRARY["kickback_procurement"]
        config = build_scenario_from_template(template, seed=42)
        assert len(config.agents) >= 3
        assert config.max_rounds == template.round_count
        # All agents should have personality
        for agent in config.agents:
            assert agent.personality is not None


class TestScenarioLibrary:
    def test_all_templates_buildable(self):
        for name, template in SCENARIO_LIBRARY.items():
            config = build_scenario_from_template(template, seed=42)
            assert len(config.agents) >= 2, f"Template {name} has too few agents"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_scenarios_v4.py -v`
Expected: FAIL

**Step 3: Write implementation**

Create `scenarios_v4.py` with `ScenarioTemplate` dataclass, `SCENARIO_LIBRARY` dict (containing at least `kickback_procurement`, `single_bidder`, `hiring_nepotism`), and `build_scenario_from_template()` that converts a template into a `ScenarioConfig` with full `AgentPersonality` objects.

Each template defines agent_templates with HEXACO ranges, Dark Triad ranges, role, and connections — the builder instantiates concrete values within those ranges using the seed.

**Step 4: Run tests, commit**

```bash
git add src/magistry_sim/scenarios_v4.py tests/test_scenarios_v4.py
git commit -m "feat: add scenario template library with real case prototypes"
```

---

## Phase 10: Validation Metrics

### Task 14: Extended metrics for v4

**Files:**
- Modify: `src/magistry_sim/metrics.py`
- Test: `tests/test_metrics.py` (append)

**Step 1: Write the failing test**

```python
# Append to tests/test_metrics.py

class TestV4Metrics:
    def test_personality_consistency_metric(self):
        from magistry_sim.metrics import compute_personality_consistency
        # Mock: actions list and personality
        score = compute_personality_consistency(
            actions=[
                {"tool": "talk_to", "params": {"agent_id": "biz_1", "private": True}},
                {"tool": "talk_to", "params": {"agent_id": "biz_1", "private": True}},
            ],
            archetype="initiator",
        )
        assert 0.0 <= score <= 5.0

    def test_memory_utilization_metric(self):
        from magistry_sim.metrics import compute_memory_utilization
        score = compute_memory_utilization(
            total_memories=100,
            retrieved_memories=20,
            actions_influenced=15,
        )
        assert 0.0 <= score <= 1.0

    def test_information_asymmetry(self):
        from magistry_sim.metrics import compute_information_asymmetry
        score = compute_information_asymmetry(
            memory_sizes={"off_1": 50, "biz_1": 30, "auditor": 80},
        )
        assert score >= 0.0
```

**Step 2: Run test to verify it fails**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_metrics.py::TestV4Metrics -v`
Expected: FAIL

**Step 3: Add new metric functions to metrics.py**

Add `compute_personality_consistency()`, `compute_memory_utilization()`, `compute_information_asymmetry()` to `src/magistry_sim/metrics.py`.

**Step 4: Run tests, commit**

```bash
git add src/magistry_sim/metrics.py tests/test_metrics.py
git commit -m "feat: add v4 validation metrics"
```

---

## Phase 11: Integration

### Task 15: Wire CognitiveAgentRunner into CLI

**Files:**
- Modify: `src/magistry_sim/cli.py`
- Modify: `src/magistry_sim/environment.py`

**Step 1: Update CLI to accept `--runner cognitive` flag**

Add `--runner` argument with choices `mock`, `llm`, `crewai`, `cognitive` (default: `mock`). When `cognitive` is selected, instantiate `CognitiveAgentRunner` with appropriate LLM and embedding providers.

**Step 2: Update Environment to support observation delivery**

Modify `Environment.run()` to call `_deliver_observations()` before each agent's turn when runner is `CognitiveAgentRunner`.

**Step 3: Run full test suite**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/ -v`
Expected: All PASS (backward compatible — mock/llm/crewai runners unchanged)

**Step 4: Manual smoke test**

Run: `cd /home/development/MAGISTRY && python -m magistry_sim --scenario S0 --governance G0 --runner mock`
Expected: Simulation completes, results displayed

**Step 5: Commit**

```bash
git add src/magistry_sim/cli.py src/magistry_sim/environment.py
git commit -m "feat: integrate CognitiveAgentRunner into CLI and Environment"
```

---

### Task 16: End-to-end test with mock LLM

**Files:**
- Create: `tests/test_e2e_cognitive.py`

**Step 1: Write integration test**

```python
# tests/test_e2e_cognitive.py
"""Сквозной тест когнитивного агента с мок-LLM."""

import pytest
from magistry_sim.cognitive_runner import CognitiveAgentRunner
from magistry_sim.environment import Environment
from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider
from magistry_sim.scenarios import get_scenario
from magistry_sim.enums import ScenarioId, GovernanceMode
from magistry_sim.scenarios import add_governance_agents
from magistry_sim.config import GovernanceConfig


class TestE2ECognitive:
    def test_s0_g0_completes(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=16)
        runner = CognitiveAgentRunner(
            llm_provider=llm, embedder=embedder
        )
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0

    def test_s1_g2_completes(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=16)
        runner = CognitiveAgentRunner(
            llm_provider=llm, embedder=embedder
        )
        config = get_scenario(ScenarioId.S1)
        config = add_governance_agents(
            config, GovernanceConfig(mode=GovernanceMode.G2)
        )
        env = Environment(scenario=config, runner=runner)
        result = env.run()
        assert result.rounds_completed > 0

    def test_cognitive_agents_accumulate_memories(self):
        llm = MockLLMProvider()
        embedder = MockEmbeddingProvider(dimensions=16)
        runner = CognitiveAgentRunner(
            llm_provider=llm, embedder=embedder
        )
        config = get_scenario(ScenarioId.S0)
        env = Environment(scenario=config, runner=runner)
        env.run()
        # At least some agents should have memories
        total_memories = sum(
            len(stream) for stream in runner._memories.values()
        )
        assert total_memories > 0
```

**Step 2: Run test, fix any integration issues, commit**

Run: `cd /home/development/MAGISTRY && python -m pytest tests/test_e2e_cognitive.py -v`
Expected: All PASS

```bash
git add tests/test_e2e_cognitive.py
git commit -m "test: add end-to-end cognitive runner integration tests"
```

---

## Summary

| Phase | Tasks | New Files | Modified Files |
|-------|-------|-----------|----------------|
| 1. Personality | 1-2 | `personality.py` | `config.py` |
| 2. Memory | 3-5 | `memory.py` | `llm.py` |
| 3. Reflection & Planning | 6-7 | `reflection.py`, `planning.py` | — |
| 4. Cognitive Runner | 8 | `cognitive_runner.py` | — |
| 5. Observation Phase | 9 | — | `environment.py` |
| 6. Event Generator | 10 | `event_generator.py` | — |
| 7. Location System | 11 | `locations.py` | — |
| 8. Biography | 12 | `biography.py` | — |
| 9. Scenario Library | 13 | `scenarios_v4.py` | — |
| 10. Validation | 14 | — | `metrics.py` |
| 11. Integration | 15-16 | `test_e2e_cognitive.py` | `cli.py`, `environment.py` |

**Total: 16 tasks, 9 new source files, 11 new test files, 4 modified files.**
