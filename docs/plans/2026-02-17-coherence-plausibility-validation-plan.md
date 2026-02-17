# Согласованность, правдоподобность, валидация — план реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Цель:** устранить шесть критических пробелов между проектным документом v4 и текущей реализацией: встроить техники нейтрализации в рефлексию, убрать хардкод фокальных точек, перевести интервью на structured output, добавить случайное распределение интервью, реализовать затухание репутации, заменить хардкод событий на LLM-генерацию, дополнить экономическую модель, интегрировать физическое пространство, добавить LLM-генератор сценариев, построить фреймворк пакетного запуска с метриками и нарративной сводкой.

**Архитектура:** минимальные изменения существующих модулей, новый код через расширение протоколов. LLM-провайдер получает метод `generate_structured` для JSON-схем. Существующие модули `event_generator.py`, `locations.py`, `resources.py` переиспользуются и расширяются.

**Стек:** Python 3.11+, Pydantic v2, rank-bm25, sentence-transformers, OpenAI SDK (structured output), scipy (статистика), networkx.

---

## Фаза 1 — Внутренняя согласованность

### Задача 1: Structured output в LLM-провайдере

Добавить метод `generate_structured` в протокол `LLMProvider` для генерации ответов по JSON-схеме. Это основа для задач 3 (интервью), 6 (события), 9 (сценарии), 14 (сводка).

**Файлы:**
- Модифицировать: `src/magistry_sim/llm.py`
- Тест: `tests/test_llm.py`

**Шаг 1: Написать тест на протокол**

```python
# tests/test_llm.py — добавить в конец

class TestStructuredOutput:
    def test_mock_returns_schema_compliant_dict(self):
        """MockLLMProvider.generate_structured возвращает dict по схеме."""
        provider = MockLLMProvider()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "score": {"type": "number"},
            },
            "required": ["name", "score"],
        }
        result = provider.generate_structured(
            system="test", user="test", schema=schema
        )
        assert isinstance(result.data, dict)
        assert "name" in result.data
        assert "score" in result.data

    def test_mock_uses_predefined_structured_responses(self):
        """MockLLMProvider возвращает предопределённый structured-ответ."""
        provider = MockLLMProvider(
            structured_responses={"interview": {"q1": "answer1"}}
        )
        result = provider.generate_structured(
            system="", user="interview question", schema={}
        )
        assert result.data == {"q1": "answer1"}
```

**Шаг 2: Запустить тест, убедиться что падает**

Запуск: `pytest tests/test_llm.py::TestStructuredOutput -v`
Ожидание: FAIL — `generate_structured` не существует.

**Шаг 3: Реализовать**

В `llm.py` добавить:

```python
@dataclass
class StructuredLLMResponse:
    """Ответ LLM со structured output."""
    data: dict
    model: str = "mock"
    usage: dict = field(default_factory=dict)
```

В протокол `LLMProvider` добавить метод:

```python
def generate_structured(
    self,
    system: str,
    user: str,
    schema: dict,
    temperature: float = 0.0,
) -> StructuredLLMResponse:
    ...
```

В `MockLLMProvider.__init__` добавить параметр `structured_responses: dict[str, dict] | None = None`. Реализация `generate_structured`: ищет ключ из `structured_responses` по подстроке в `user`, при отсутствии — генерирует заглушку из схемы (пустые строки, нули).

В `OpenAICompatibleProvider` реализовать через `response_format={"type": "json_schema", "json_schema": {...}}`.

**Шаг 4: Запустить тест, убедиться что проходит**

Запуск: `pytest tests/test_llm.py::TestStructuredOutput -v`
Ожидание: PASS.

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/llm.py tests/test_llm.py
git commit -m "feat: add generate_structured to LLM provider protocol"
```

---

### Задача 2: Техники нейтрализации в рефлексии

Встроить освоенные агентом техники нейтрализации в промпт фазы рефлексии.

**Файлы:**
- Модифицировать: `src/magistry_sim/reflection.py:77-105` (synthesize_insights)
- Модифицировать: `src/magistry_sim/reflection.py:108-157` (run_reflection_cycle)
- Модифицировать: `src/magistry_sim/cognitive_runner.py:268-275` (вызов рефлексии)
- Тест: `tests/test_reflection.py`

**Шаг 1: Написать тест**

```python
# tests/test_reflection.py — добавить

def test_synthesize_insights_includes_neutralization_techniques():
    """Промпт синтеза включает техники нейтрализации агента."""
    captured_prompts = []

    class CaptureLLM:
        def generate(self, system, user, temperature=0.0):
            captured_prompts.append(user)
            return LLMResponse(text="Инсайт с denial_of_injury")

    from magistry_sim.personality import NeutralizationTechnique
    techniques = [
        NeutralizationTechnique.DENIAL_OF_INJURY,
        NeutralizationTechnique.EVERYONE_DOES_IT,
    ]
    memories = [MemoryRecord(
        id="m1", created_at=0, content="test",
        importance=5.0, kind="observation", embedding=[],
    )]
    synthesize_insights(
        "Стоит ли рисковать?", memories, CaptureLLM(), "agent_1",
        neutralization_techniques=techniques,
    )
    assert "denial_of_injury" in captured_prompts[-1]
    assert "everyone_does_it" in captured_prompts[-1]


def test_synthesize_insights_works_without_techniques():
    """Синтез работает без техник нейтрализации (обратная совместимость)."""
    class SimpleLLM:
        def generate(self, system, user, temperature=0.0):
            return LLMResponse(text="Простой инсайт")

    memories = [MemoryRecord(
        id="m1", created_at=0, content="test",
        importance=5.0, kind="observation", embedding=[],
    )]
    result = synthesize_insights(
        "Что делать?", memories, SimpleLLM(), "agent_1",
    )
    assert result == "Простой инсайт"
```

**Шаг 2: Запустить тест, убедиться что падает**

Запуск: `pytest tests/test_reflection.py::test_synthesize_insights_includes_neutralization_techniques -v`
Ожидание: FAIL — `synthesize_insights` не принимает `neutralization_techniques`.

**Шаг 3: Реализовать**

В `reflection.py:synthesize_insights` добавить необязательный параметр `neutralization_techniques: list[NeutralizationTechnique] | None = None`. Если передан и не пуст, добавить в промпт блок:

```python
techniques_text = ""
if neutralization_techniques:
    tech_list = ", ".join(t.value for t in neutralization_techniques)
    techniques_text = (
        f"\n\nТехники рационализации, доступные агенту: {tech_list}. "
        f"Если вывод связан с рационализацией, укажи использованную "
        f"технику в формате [technique: название]."
    )
```

В `run_reflection_cycle` добавить параметр `neutralization_techniques` и пробросить в `synthesize_insights`.

В `cognitive_runner.py:run_turn` в месте вызова рефлексии (строка ~269) извлечь техники из профиля:

```python
techniques = []
if profile and profile.personality:
    techniques = profile.personality.neutralization_techniques

run_reflection_cycle(
    stream=stream,
    llm=self._llm,
    embedder=self._embedder,
    current_round=current_round,
    neutralization_techniques=techniques,
)
```

**Шаг 4: Запустить тесты**

Запуск: `pytest tests/test_reflection.py -v`
Ожидание: все PASS.

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/reflection.py src/magistry_sim/cognitive_runner.py tests/test_reflection.py
git commit -m "feat: include neutralization techniques in reflection synthesis"
```

---

### Задача 3: Универсальные фокальные точки рефлексии

Убрать хардкод трёх коррупционных фокусов из `generate_focal_points`.

**Файлы:**
- Модифицировать: `src/magistry_sim/reflection.py:35-74` (generate_focal_points)
- Тест: `tests/test_reflection.py`

**Шаг 1: Написать тест**

```python
def test_generate_focal_points_no_corruption_hardcode():
    """Промпт генерации фокальных точек не содержит хардкода о коррупции."""
    captured_prompts = []

    class CaptureLLM:
        def generate(self, system, user, temperature=0.0):
            captured_prompts.append(user)
            return LLMResponse(text='["Вопрос 1", "Вопрос 2", "Вопрос 3"]')

    stream = MemoryStream(agent_id="test")
    stream.add(content="тест", importance=5.0, kind="observation",
               round_num=0, embedding=[0.1]*8)

    generate_focal_points(stream, CaptureLLM())
    prompt = captured_prompts[-1]
    assert "разоблач" not in prompt.lower()
    assert "выгод" not in prompt.lower() or "моральн" not in prompt.lower()
    assert "рационализац" not in prompt.lower()
```

**Шаг 2: Запустить, убедиться что падает**

Запуск: `pytest tests/test_reflection.py::test_generate_focal_points_no_corruption_hardcode -v`
Ожидание: FAIL — текущий промпт содержит «разоблачить», «выгода», «рационализация».

**Шаг 3: Реализовать**

Заменить промпт в `generate_focal_points` (строки 58-66):

```python
prompt = (
    f"На основе следующих наблюдений агента {stream.agent_id}, "
    f"сформулируй {n} вопроса высокого уровня, о которых стоит "
    f"задуматься. Вопросы должны затрагивать наиболее значимые "
    f"темы из наблюдений.\n\n"
    f"Наблюдения:\n{memories_text}\n\n"
    f"Верни JSON-массив из {n} строк-вопросов."
)
```

**Шаг 4: Тест проходит**

Запуск: `pytest tests/test_reflection.py -v`
Ожидание: PASS.

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/reflection.py tests/test_reflection.py
git commit -m "feat: make reflection focal points universal instead of corruption-specific"
```

---

### Задача 4: Парсинг интервью через structured output

Перевести `generate_interview` на `generate_structured` вместо парсинга текста.

**Файлы:**
- Модифицировать: `src/magistry_sim/interviews.py:190-293` (generate_interview)
- Тест: `tests/test_interviews.py`

**Шаг 1: Написать тест**

```python
def test_generate_interview_structured_output():
    """Каждый вопрос получает отдельный ответ через structured output."""
    answers_data = {
        f"q{i+1}": f"Ответ на вопрос {i+1}" for i in range(10)
    }
    psych_data = {"analysis": "Психологический анализ"}
    econ_data = {"analysis": "Экономический анализ"}

    class StructuredLLM:
        def __init__(self):
            self._call = 0

        def generate(self, system, user, temperature=0.0):
            return LLMResponse(text="fallback")

        def generate_structured(self, system, user, schema, temperature=0.0):
            self._call += 1
            if self._call == 1:
                return StructuredLLMResponse(data=answers_data)
            elif self._call == 2:
                return StructuredLLMResponse(data=psych_data)
            else:
                return StructuredLLMResponse(data=econ_data)

    personality = _make_test_personality()  # хелпер из существующих тестов
    embedder = MockEmbeddingProvider(dimensions=8)

    interview = generate_interview(
        personality=personality,
        role="чиновник",
        archetype="pragmatist",
        llm=StructuredLLM(),
        embedder=embedder,
        interview_id="test-001",
    )

    # Каждый вопрос должен иметь уникальный ответ
    answers = list(interview.interview.values())
    assert len(set(answers)) == 10  # все 10 ответов различны
    assert answers[0] == "Ответ на вопрос 1"
```

**Шаг 2: Запустить, убедиться что падает**

Запуск: `pytest tests/test_interviews.py::test_generate_interview_structured_output -v`
Ожидание: FAIL — текущий код не использует `generate_structured`.

**Шаг 3: Реализовать**

Заменить тело `generate_interview` (строки 239-248):

```python
# Схема для ответов на 10 вопросов
answer_schema = {
    "type": "object",
    "properties": {
        f"q{i+1}": {"type": "string", "description": q}
        for i, q in enumerate(INTERVIEW_QUESTIONS)
    },
    "required": [f"q{i+1}" for i in range(len(INTERVIEW_QUESTIONS))],
}

interview_response = llm.generate_structured(
    system="Ты участник глубинного интервью о личности и карьере.",
    user=interview_prompt,
    schema=answer_schema,
)

# Маппинг ответов на вопросы
answers: dict[str, str] = {}
for i, q in enumerate(INTERVIEW_QUESTIONS):
    key = f"q{i+1}"
    answers[q] = interview_response.data.get(key, "")
```

Аналогично для экспертных оценок — через `generate_structured` со схемой `{"analysis": "string"}`.

**Шаг 4: Тесты проходят**

Запуск: `pytest tests/test_interviews.py -v`
Ожидание: PASS.

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/interviews.py tests/test_interviews.py
git commit -m "feat: use structured output for interview generation"
```

---

### Задача 5: Случайное распределение интервью

Заменить поиск по роли на случайную выборку из библиотеки.

**Файлы:**
- Модифицировать: `src/magistry_sim/cognitive_runner.py:216-225` (раздел интервью в промпте)
- Тест: `tests/test_cognitive_runner.py`

**Шаг 1: Написать тест**

```python
def test_interview_assignment_is_random():
    """Интервью назначаются случайно, а не по роли."""
    from magistry_sim.interviews import Interview, InterviewLibrary

    library = InterviewLibrary()
    for i in range(5):
        library.add(Interview(
            id=f"itv-{i}", archetype="pragmatist", role="чиновник",
            hexaco={...}, dark_triad={...},
            interview={f"q": f"answer-{i}"},
            expert_psychologist=f"psych-{i}",
            expert_economist=f"econ-{i}",
            embedding=[float(i)] * 8,
        ))

    # Два агента с разными ролями должны получить случайные интервью
    # (а не одинаковые по роли "чиновник")
    # Проверяем, что метод sample вызывается вместо search
    assert hasattr(library, "sample")
    result = library.sample(n=1, seed=42)
    assert len(result) == 1
    result2 = library.sample(n=1, seed=43)
    # С разными seed могут быть разные результаты (но не обязательно)
    assert len(result2) == 1
```

**Шаг 2: Запустить, убедиться что падает**

Запуск: `pytest tests/test_cognitive_runner.py::test_interview_assignment_is_random -v`
Ожидание: FAIL — `InterviewLibrary.sample` не существует.

**Шаг 3: Реализовать**

В `InterviewLibrary` добавить метод:

```python
def sample(self, n: int = 1, seed: int | None = None) -> list[Interview]:
    """Случайная выборка из библиотеки.

    Args:
        n: Количество интервью.
        seed: Зерно для воспроизводимости.

    Returns:
        Список случайных интервью.
    """
    if not self._interviews:
        return []
    rng = random.Random(seed)
    k = min(n, len(self._interviews))
    return rng.sample(self._interviews, k)
```

В `cognitive_runner.py:_build_cognitive_prompt` заменить строки 217-225:

```python
interview_text = ""
if self._interview_library and len(self._interview_library) > 0:
    # Случайное распределение: seed = hash(agent_id) для воспроизводимости
    seed = hash(agent_id) % (2**31)
    results = self._interview_library.sample(n=1, seed=seed)
    if results:
        interview_text = (
            f"\n## Нарративное интервью (пример личности)\n"
            f"{results[0].full_text()}\n"
        )
```

**Шаг 4: Тесты проходят**

Запуск: `pytest tests/test_cognitive_runner.py tests/test_interviews.py -v`
Ожидание: PASS.

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/interviews.py src/magistry_sim/cognitive_runner.py tests/test_cognitive_runner.py tests/test_interviews.py
git commit -m "feat: random interview assignment instead of role-based search"
```

---

## Фаза 2 — Социальная правдоподобность

### Задача 6: Затухание репутации

**Файлы:**
- Модифицировать: `src/magistry_sim/reputation.py:17-35`
- Модифицировать: `src/magistry_sim/config.py` (добавить `reputation_decay` в `GovernanceConfig`)
- Модифицировать: `src/magistry_sim/environment.py:260-277`
- Тест: `tests/test_reputation.py`

**Шаг 1: Написать тест**

```python
def test_reputation_decay_over_rounds():
    """Репутация затухает каждый раунд без активных действий."""
    record = ReputationRecord(score=100.0)
    for _ in range(10):
        apply_decay(record, decay_factor=0.95)
        growth = compute_round_growth(record, cases_resolved=0)
        apply_growth(record, growth)
    # 100 * 0.95^10 + сумма приростов (каждый раунд +1.0 минимум, но на уменьшающуюся базу)
    # Без активности репутация должна снижаться
    assert record.score < 100.0


def test_no_decay_when_factor_is_one():
    """При decay_factor=1.0 затухания нет (обратная совместимость)."""
    record = ReputationRecord(score=50.0)
    apply_decay(record, decay_factor=1.0)
    assert record.score == 50.0
```

**Шаг 2: Запустить, убедиться что падает**

**Шаг 3: Реализовать**

В `reputation.py` добавить функцию `apply_decay`:

```python
def apply_decay(record: ReputationRecord, decay_factor: float = 0.95) -> None:
    """Применить затухание репутации.

    Args:
        record: Запись репутации.
        decay_factor: Коэффициент затухания (0.0-1.0).
    """
    if not record.frozen:
        record.score *= decay_factor
```

В `GovernanceConfig` добавить `reputation_decay: float = Field(default=1.0, ge=0.0, le=1.0)`.

В `environment.py:_apply_round_end_effects` перед вызовом `compute_round_growth` добавить вызов `apply_decay(rep, decay_factor=self._scenario.governance.reputation_decay)`.

**Шаг 4: Тесты**

Запуск: `pytest tests/test_reputation.py -v`

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/reputation.py src/magistry_sim/config.py src/magistry_sim/environment.py tests/test_reputation.py
git commit -m "feat: add reputation decay mechanism"
```

---

### Задача 7: LLM-генератор событий

Заменить хардкод типов стохастических событий на LLM-генерацию по контексту мира.

**Файлы:**
- Модифицировать: `src/magistry_sim/event_generator.py`
- Модифицировать: `src/magistry_sim/environment.py` (интеграция генератора в цикл)
- Тест: `tests/test_event_generator.py`

**Шаг 1: Написать тест**

```python
def test_llm_event_generator_uses_world_context():
    """LLM-генератор использует контекст мира для генерации событий."""
    captured = []

    class CaptureLLM:
        def generate_structured(self, system, user, schema, temperature=0.0):
            captured.append(user)
            return StructuredLLMResponse(data={
                "has_event": True,
                "event_type": "journalist_investigation",
                "description": "Журналист обнаружил подозрительные закупки",
                "affected_agents": ["off_1"],
            })

    gen = LLMEventGenerator(llm=CaptureLLM())
    events = gen.generate(round_num=5, world_context="Бюджет исчерпан на 90%")

    assert len(events) >= 1
    assert "Бюджет исчерпан" in captured[0]
    assert events[0]["description"] == "Журналист обнаружил подозрительные закупки"


def test_llm_event_generator_can_return_no_events():
    """LLM может решить, что в этом раунде ничего не происходит."""
    class NoEventLLM:
        def generate_structured(self, system, user, schema, temperature=0.0):
            return StructuredLLMResponse(data={"has_event": False})

    gen = LLMEventGenerator(llm=NoEventLLM())
    events = gen.generate(round_num=1, world_context="Всё спокойно")
    assert events == []
```

**Шаг 2: Запустить, убедиться что падает**

**Шаг 3: Реализовать**

Добавить `LLMEventGenerator` в `event_generator.py`:

```python
class LLMEventGenerator:
    """Генератор событий через LLM на основе контекста мира."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def generate(
        self, round_num: int, world_context: str,
    ) -> list[dict[str, Any]]:
        schema = {
            "type": "object",
            "properties": {
                "has_event": {"type": "boolean"},
                "event_type": {"type": "string"},
                "description": {"type": "string"},
                "affected_agents": {
                    "type": "array", "items": {"type": "string"}
                },
            },
            "required": ["has_event"],
        }
        result = self._llm.generate_structured(
            system=(
                "Ты — генератор мировых событий для симуляции организационных "
                "процессов. На основе текущего состояния мира реши, происходит "
                "ли внешнее событие в этом раунде. Событие должно логически "
                "следовать из контекста."
            ),
            user=(
                f"Раунд {round_num}.\n\n"
                f"Состояние мира:\n{world_context}\n\n"
                f"Произойдёт ли внешнее событие в этом раунде?"
            ),
            schema=schema,
        )
        if not result.data.get("has_event", False):
            return []
        return [{
            "event_type": result.data.get("event_type", "unknown"),
            "description": result.data.get("description", ""),
            "affected_agents": result.data.get("affected_agents", []),
            "source": "llm",
        }]
```

В `environment.py:run` перед циклом ходов агентов вызывать `LLMEventGenerator.generate()` и логировать результаты в `event_log`. Передавать `world_context` из `build_situation` для общего обзора.

**Шаг 4: Тесты**

Запуск: `pytest tests/test_event_generator.py -v`

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/event_generator.py src/magistry_sim/environment.py tests/test_event_generator.py
git commit -m "feat: replace hardcoded stochastic events with LLM generator"
```

---

### Задача 8: Экономическая модель — доход бизнеса и фонд взяток

**Файлы:**
- Модифицировать: `src/magistry_sim/resources.py` (добавить `revenue`, `maintenance_cost`, `bribe_fund`)
- Модифицировать: `src/magistry_sim/environment.py` (расчёт доходов/расходов в конце раунда)
- Модифицировать: `src/magistry_sim/context.py` (текстовое описание экономики)
- Тест: `tests/test_resources.py`

**Шаг 1: Написать тест**

```python
def test_business_loses_revenue_without_contracts():
    """Бизнесмен без контрактов теряет ресурсы каждый раунд."""
    res = AgentResources(
        budget_limit=1000.0, maintenance_cost=50.0
    )
    apply_maintenance(res)
    assert res.budget_spent == 50.0


def test_bribe_fund_accumulation():
    """Фонд взяток увеличивается при коррупционных сделках."""
    res = AgentResources()
    res.add_to_bribe_fund(100.0)
    assert res.bribe_fund == 100.0


def test_bribe_fund_discovery():
    """Обнаружение фонда взяток при проверке."""
    res = AgentResources()
    res.add_to_bribe_fund(500.0)
    assert res.bribe_fund > 0
    assert res.has_bribe_fund()
```

**Шаг 2: Запустить, убедиться что падает**

**Шаг 3: Реализовать**

В `AgentResources` добавить поля:

```python
maintenance_cost: float = 0.0
bribe_fund: float = 0.0
revenue_per_contract: float = 0.0
```

Добавить методы `add_to_bribe_fund`, `has_bribe_fund`, `clear_bribe_fund`.

Добавить функцию `apply_maintenance(res)` — списывает `maintenance_cost` из бюджета каждый раунд.

В `environment.py:_apply_round_end_effects` добавить цикл по бизнес-агентам для применения `maintenance_cost` и начисления дохода за активные контракты.

В `context.py:build_situation` добавить описание фонда взяток (только для владельца): «У вас есть скрытые средства: {bribe_fund}. При обнаружении — трибунал.»

**Шаг 4: Тесты**

Запуск: `pytest tests/test_resources.py -v`

**Шаг 5: Коммит**

```bash
git add src/magistry_sim/resources.py src/magistry_sim/environment.py src/magistry_sim/context.py tests/test_resources.py
git commit -m "feat: add business revenue, maintenance cost and bribe fund"
```

---

### Задача 9: Интеграция физического пространства

**Файлы:**
- Модифицировать: `src/magistry_sim/state.py` (добавить `LocationManager` в `WorldState`)
- Модифицировать: `src/magistry_sim/environment.py` (инициализация локаций, фильтрация по видимости)
- Модифицировать: `src/magistry_sim/context.py` (локация агента, СКУД для аудитора)
- Создать: инструмент `move_to` в `src/magistry_sim/tools/actions.py`
- Тест: `tests/test_locations.py` (дополнить), `tests/test_tools.py`

**Шаг 1: Написать тест**

```python
def test_auditor_sees_skud_log():
    """Аудитор видит журнал СКУД — кто с кем встречался в кабинетах."""
    state = WorldState()
    state.locations = LocationManager()
    office = Location(id="office", name="Кабинет", public=False)
    state.locations.add_location(office)
    state.locations.place_agent("off_1", "office")
    state.locations.place_agent("biz_1", "office")

    section = _build_auditor_section("auditor", state)
    text = "\n".join(section)
    assert "off_1" in text
    assert "biz_1" in text
    assert "Кабинет" in text


def test_move_to_changes_location():
    """Инструмент move_to перемещает агента."""
    # ... setup state with locations ...
    move_to(location_id="restaurant")
    loc = state.locations.get_agent_location("off_1")
    assert loc == "restaurant"
```

**Шаг 2-5:** Аналогично предыдущим задачам.

```bash
git commit -m "feat: integrate physical space with SKUD and move_to tool"
```

---

### Задача 10: LLM-генератор сценариев

**Файлы:**
- Создать: `src/magistry_sim/scenario_generator.py`
- Тест: `tests/test_scenario_generator.py`

**Шаг 1: Написать тест**

```python
def test_generate_scenario_returns_valid_config():
    """LLM-генератор создаёт валидную ScenarioConfig."""
    class ScenarioLLM:
        def generate_structured(self, system, user, schema, temperature=0.0):
            return StructuredLLMResponse(data={
                "title": "Тестовый сценарий",
                "description": "Описание",
                "agents": [
                    {"id": "off_1", "name": "Иванов", "position": "чиновник",
                     "hexaco": {"honesty_humility": 30, ...},
                     "dark_triad": {"narcissism": 60, ...}},
                ],
                "max_rounds": 10,
            })

    config = generate_scenario(
        llm=ScenarioLLM(),
        corruption_type="kickback",
        agent_count=4,
        economic_pressure=0.7,
        governance=GovernanceMode.G2,
    )
    assert isinstance(config, ScenarioConfig)
    assert len(config.agents) >= 1
```

**Шаг 2-5:** Стандартный TDD-цикл.

```bash
git commit -m "feat: add LLM-based scenario generator"
```

---

## Фаза 3 — Эмпирическая валидация

### Задача 11: Фреймворк пакетного запуска

**Файлы:**
- Создать: `src/magistry_sim/batch.py`
- Модифицировать: `src/magistry_sim/cli.py` (команда `batch`)
- Тест: `tests/test_batch.py`

Реализовать `BatchRunner` — принимает параметры генерации сценария, количество прогонов, список режимов governance. Запускает `N × len(modes)` симуляций с разными seed. Сохраняет результаты в директорию: `{output_dir}/{mode}/run_{i}/events.jsonl`, `metrics.json`, `graph.json`.

```bash
git commit -m "feat: add batch runner for experiment series"
```

---

### Задача 12: Метрики качества симуляции

**Файлы:**
- Модифицировать: `src/magistry_sim/metrics.py`
- Тест: `tests/test_metrics.py`

Расширить `metrics.py`:
- `corruption_rate(result)` — доля коррупционных сделок.
- `detection_rate(result)` — доля обнаруженных нарушений.
- `false_positive_rate(result)` — ложные обвинения.
- `personality_consistency(result, llm)` — LLM-судья оценивает согласованность.
- `network_evolution(result)` — изменение графа.
- `neutralization_usage(result)` — частота техник в рефлексиях.

```bash
git commit -m "feat: add comprehensive simulation quality metrics"
```

---

### Задача 13: Статистическое сравнение режимов governance

**Файлы:**
- Создать: `src/magistry_sim/statistics.py`
- Тест: `tests/test_statistics.py`

Реализовать `compare_governance_modes(results_by_mode)`:
- Mann-Whitney U test между парами режимов.
- Поправка Bonferroni.
- Доверительные интервалы (bootstrap).
- Генерация отчёта: таблица метрик по режимам, p-values, графики.

Зависимость: `scipy` (добавить в `[stats]` группу `pyproject.toml`).

```bash
git commit -m "feat: add statistical comparison of governance modes"
```

---

### Задача 14: Нарративная сводка мира

**Файлы:**
- Создать: `src/magistry_sim/narrator.py`
- Модифицировать: `src/magistry_sim/environment.py` (вызов после каждого раунда)
- Тест: `tests/test_narrator.py`

Реализовать `WorldNarrator`:
- `summarize_round(round_num, events, state, llm)` — сводка раунда.
- `summarize_simulation(all_summaries, final_state, llm)` — итоговый нарратив.

Через `generate_structured` с JSON-схемой:
```json
{
  "events_summary": "string",
  "key_decisions": ["string"],
  "tensions": ["string"],
  "agent_motivations": {"agent_id": "string"}
}
```

```bash
git commit -m "feat: add world narrator for simulation summaries"
```

---

### Задача 15: Валидация против эмпирических данных

**Файлы:**
- Создать: `src/magistry_sim/validation.py`
- Создать: `data/empirical_benchmarks.json` (целевые диапазоны из литературы)
- Тест: `tests/test_validation.py`

Реализовать `validate_against_empirical(metrics, benchmarks)`:
- Сравнить corruption_rate с данными Transparency International (диапазоны по режимам).
- Сравнить deterrence_effect с криминологическими исследованиями.
- Вернуть отчёт: какие метрики попадают в эмпирические диапазоны, а какие нет.

```bash
git commit -m "feat: add empirical validation against published benchmarks"
```
