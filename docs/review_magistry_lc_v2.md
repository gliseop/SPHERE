# Ревью коммита `cce22de` — MAGISTRY-LC (embedding, journal, persona fallback)

Предыдущие коммиты: `4a68dcc` (greenfield), `6272517` (память + персона), `5890d01` (исправления по ревью v1). Текущий коммит `cce22de` добавляет инкрементальный YAML-журнал, асинхронные embeddings с кешированием, fallback-генерацию персон, тесты суммаризации и LangGraph с чекпоинтами.

Все тесты проходят (8 LC-тестов), регрессий нет.

---

## Что закрыто из предыдущего ревью

### Из v1 (оставалось 2 частично закрытых)

**3. Инкрементальный YAML-журнал** (ранее P1, частично закрыто → закрыто)

Новый модуль `journal.py` (236 строк) реализует `WorldJournal`:
- Инициализируется из `WorldState` один раз (`from_state`).
- Обновляется инкрементально через `apply_events()` — обрабатывает конкретные типы событий (`entity_created`, `work_item_created`, `vote_opened`, `position_changed` и др.) и обновляет только затронутые записи.
- Кеширует YAML-сериализацию (`_dirty` + `_yaml_cache`) — повторный вызов `to_yaml()` без изменений возвращает кеш.
- `engine.py:66` — журнал создается один раз. `engine.py:244` — обновляется через `apply_events()` после каждого тика. `engine.py:217` — передается в арбитраж как `journal.to_yaml()`.

Журнал арбитра больше не пересоздается на каждый вызов. Внутри тика все perform-действия используют один снимок (корректно: ops применяются после арбитража).

**5. LangGraph с чекпоинтами** (ранее P2, частично закрыто → закрыто)

`graphs.py:60-72` — при наличии `checkpoint_path` создается `SqliteSaver`. Совместимость с разными версиями LangGraph: `from_conn_string` если доступен, иначе прямой конструктор.

Новый тест `test_langgraph_world_graph_supports_checkpoint_path` подтверждает, что граф компилируется и исполняется с checkpoint_path.

### Из замечаний N1–N7 (ревью v1 → v2)

**N1. `_redact_numbers` вынесена в `utils.py`** (закрыто)

`utils.py:6-17` — единая реализация `redact_numbers()`. Используется в `agent.py:22` и `engine.py:28` через `from .utils import redact_numbers`.

**N2. Зависимость от `magistry_sim` — явная через `deps.py`** (закрыто)

`deps.py` — точка интеграции. Импортирует BM25, EmbeddingProvider, LLMProvider, OpenAICompatibleProvider из `magistry_sim`. Все модули `magistry_lc` импортируют через `deps.py`, а не напрямую из `magistry_sim`.

**N3. Embeddings — асинхронные с батчингом и кешированием** (закрыто)

`embeddings.py` (73 строки):
- `embed_texts()` — батчит вызовы `embedder.embed_batch()` через `asyncio.to_thread()`.
- `embed_texts_cached()` — добавляет кеш по точному тексту. Вычисляет только missing.
- `engine.py:82` — `embed_cache: dict[str, list[float]]` переиспользуется по всему прогону.
- `engine.py:126-131` — при инициализации persona-документов embeddings получаются батчами через кеш.
- `engine.py:512-518` — при обновлении памяти embeddings тоже идут через кеш.

**N4. PersonaGenerator — fallback при обрезанном ответе** (закрыто)

`persona.py:376-440` — `generate()` пробует один LLM-вызов (summary + biography + 30 QA). Если ответ обрезан или невалиден:
1. Fallback: `_generate_core()` — отдельный вызов для summary + biography.
2. Fallback: `_generate_interview_answers()` — чанкированная генерация ответов (по 10). При ошибке чанк делится пополам, до single-question fallback.
3. Гарантия: если summary пуст — используется `persona_hint` или `name`.

**N6. Тест суммаризации working buffer** (закрыто)

`test_memory_summarizes_working_buffer` — создает AgentMemory с `working_max_entries=2`, добавляет 4 записи, вызывает `maybe_summarize_working()` с MockLLMProvider, проверяет что summary обновился и working buffer сократился до 2.

**N7. Приватные поля без подчеркивания** (закрыто)

`memory.py:81-84` — `doc_counter`, `bm25_corpus`, `bm25`, `bm25_dirty` без подчеркивания.

---

## Новые замечания к коммиту `cce22de`

### Существенные

**J1. `WorldJournal.apply_events()` не обрабатывает удаление/закрытие work_items**

`journal.py:87-164` — обрабатываются: `entity_created`, `work_item_created`, `work_note_added`, `work_proposal_submitted`, `vote_opened`, `vote_cast`, `vote_target_consented`, `vote_target_declined`, `vote_closed`, `position_changed`. Отсутствуют: `arbiter_rejected` (для статистики), `message_sent` (для графа активности), `reputation_changed`, а также удаление/архивирование work_items. Журнал растет монотонно: `max_work_items=20` и `max_votes=20` ограничивают только сериализацию в YAML, но словари `work_items` и `votes` растут неограниченно.

**J2. Журнал не содержит историю действий**

`journal.py:166-180` — `to_dict()` выдает текущее состояние (агенты, work items, votes), но не историю: какие действия были одобрены/отклонены, кто с кем общался, какие предложения подавались. Арбитру для `perform` это критично: без истории он не может оценить, не зацикливается ли агент или не делает ли он взаимоисключающих действий.

**J3. `embed_texts` тихо глотает ошибки**

`embeddings.py:38-39`:
```python
except Exception:
    vecs = []
```
Если embeddings-провайдер падает — весь батч молча заменяется пустыми векторами. Нет логирования, нет метрики. При реальном API (rate limit, timeout) это может привести к деградации памяти без видимых сигналов.

**J4. `PersonaGenerator._generate_interview_answers` — бесконечный retry через деление**

`persona.py:312-343` — если чанк из 10 вопросов не парсится, он делится пополам (5+5), каждая половина снова может не спарситься и делиться дальше, до single-question. В теории это O(N * log(N)) LLM-вызовов. При 30 вопросах и полном fallback — до 90 вызовов. Нет ограничения на максимальное число retry.

**J5. `journal.py:187-188` — `import yaml` внутри метода**

`to_yaml()` делает `import yaml` при каждом вызове (пусть и кешированном по `_dirty`). Лучше один раз при инициализации или на уровне модуля.

### Мелкие

**J6. `_ComposeAgent.agent_id` — произвольная строка от LLM**

`composer.py:19` — LLM генерирует `agent_id` без валидации формата `agent:*`. Если LLM вернет `"off_1"` вместо `"agent:off_1"`, EntityRegistry отклонит при регистрации. Нет explicit нормализации: `ensure_kind(EntityKind.AGENT, raw_id)`.

**J7. `WorldJournal._agent_entry` не включает reputation**

`journal.py:198-206` — entry содержит id, name, internal, title, capabilities. Нет reputation. Арбитр при `perform` не видит репутацию агентов.

**J8. `config.py:79` — `embeddings_mock: bool = True` по умолчанию**

В production-прогоне нужно явно ставить `embeddings_mock: false`. Это легко забыть, и память будет работать без реальных embeddings (BM25 only). Возможно, стоит сделать default `False` и добавить warning при отсутствии API-ключа.

**J9. `engine.py:254-260` — `_agent_order` импортирует `random` внутри метода**

`import random` на каждый тик. Не критично для производительности, но нестандартно.

**J10. `MemoryConfig.importance_by_event` не содержит `arbiter_approved`**

`config.py:63-76` — события `arbiter_approved`, `entity_created`, `vote_cast` не указаны в importance_by_event. Попадают под `importance_default=3.0`, что ниже `importance_threshold=5.0` — и не сохраняются в долгосрочную память. `arbiter_approved` несет информацию о том, что агент успешно сделал — это важно для обратной связи.

**J11. `dao.py:53-56` — `wants_promotion` проверяется при закрытии, но не при номинации**

`dao.py:53-56` — `_compute_result` проверяет `wants_promotion` и возвращает `"canceled"`. Но `arbiter.py:420-421` уже блокирует номинацию при `wants_promotion=False`. Дублирование проверки — не ошибка, но вторая проверка в `dao.py` никогда не сработает (если код арбитра корректен).

---

## Итого по приоритетам

| # | Проблема | Приоритет |
|---|---|---|
| J1 | Журнал не обрабатывает удаление/закрытие work_items, растет неограниченно | P2 |
| J2 | Журнал без истории действий — арбитр не видит контекст решений | P1 |
| J3 | `embed_texts` тихо глотает ошибки | P2 |
| J4 | PersonaGenerator retry без ограничений — до 90 LLM-вызовов | P1 |
| J6 | Composer: agent_id от LLM без нормализации формата | P2 |
| J7 | Журнал без reputation | P2 |
| J8 | `embeddings_mock=True` по умолчанию — легко забыть | P3 |
| J10 | `arbiter_approved` не попадает в долгосрочную память | P2 |

---

## Что хорошо в этом коммите

- `WorldJournal` — компактный, event-driven, с кешированием YAML. Хорошо декомпозирован от WorldState.
- `embeddings.py` — батчинг + кеш + async. Корректно обрабатывает несовпадение размеров.
- PersonaGenerator с трехуровневым fallback (full → core + chunked interview → single question) — надежный подход.
- Тесты покрывают суммаризацию памяти и LangGraph с чекпоинтами.
- `deps.py` — явная точка зависимости, легко заменяемая.

---

## Сводка по всем ревью

| Ревью | Критичных (P0) | Существенных (P1) | Средних (P2) | Мелких (P3) |
|---|---|---|---|---|
| v1 (4a68dcc) | 2 | 6 | 4 | 4 |
| v1 → v2 (6272517 + 5890d01) | 0 (закрыты) | 0 (закрыты) | 2 частично | 7 новых |
| v2 → v3 (cce22de) | 0 | 2 (J2, J4) | 5 (J1, J3, J6, J7, J10) | 3 (J5, J8, J9) |

Все P0 закрыты. Два P1 остаются: журнал без истории действий (J2) и retry без ограничений в PersonaGenerator (J4).
