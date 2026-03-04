# Ревью коммита `6272517` — MAGISTRY-LC (исправления по ревью v1)

Предыдущий коммит (`4a68dcc`) реализовал M0–M3 из плана. Текущий коммит `6272517` закрывает критичные и существенные замечания ревью v1.

Все тесты проходят (548 + 6 новых = 554), регрессий нет.

---

## Что исправлено (из ревью v1)

### P0 (критичные) — исправлены

**1. Память агента — реализован гибридный индекс** (ранее P0, закрыто)

Новый модуль `memory.py` (272 строки) реализует двухслойную память:
- Working buffer (`WorkingEntry[]`) с LLM-суммаризацией через `maybe_summarize_working()` при превышении порога (`working_max_entries=40`, батч `working_summarize_batch=20`).
- Долгосрочный индекс (`MemoryDoc[]`) с гибридным retrieval: cosine similarity по embeddings + BM25 по токенам + recency decay + importance. Веса настраиваемы через `MemoryConfig.weights`.
- Дедуп по cosine threshold (0.92): повторные наблюдения не раздувают индекс, а увеличивают `repeats`.
- Eviction по `(importance, last_seen_tick)` при превышении `long_term_max_docs=800`.

`engine.py:92-127` — при инициализации биография, summary и интервью загружаются в долгосрочную память как `MemoryDoc` с видами `persona`/`interview`.

`engine.py:415-503` (`_update_agent_memory`) — каждое событие тика записывается в working buffer и (при importance >= 5.0) в долгосрочный индекс. После каждого тика вызывается `maybe_summarize_working()`.

`agent.py:114-143` (`_render_memory`) — при формировании промпта выполняется hybrid retrieval по последним наблюдениям как запрос. Агент видит: summary персоны, сводку рабочей памяти, последние записи, релевантные факты из долгосрочной памяти.

`config.py:27-86` — `MemoryConfig` с настройками: пороги, top-k, веса, decay, embeddings provider.

**2. Персона агента — реализована трехслойная генерация** (ранее P0, закрыто)

Новый модуль `persona.py` (241 строка):
- `PersonaArtifact` (summary + biography + interview с 30 QA).
- `PersonaLibrary` — загрузка персон из YAML/JSON на диске.
- `PersonaGenerator` — LLM-генерация полного артефакта (biography 1000–2000 слов, 30 вопросов интервью) из подсказки.
- 30 вопросов (`INTERVIEW_QUESTIONS_V2`) по 8 доменам: повседневная жизнь, работа, финансы, отношения, ценности, конфликты, власть, нарративная идентичность.
- `chunk_text()` — чанкинг биографии для загрузки в долгосрочную память.

`config.py:147` — `persona: PersonaArtifact` вместо `persona: str`. Обратная совместимость: `_coerce_persona` конвертирует строку в `PersonaArtifact(summary=...)`.

`composer.py:173-189` — `WorldComposer.compose()` параллельно обогащает каждого агента через `PersonaGenerator` (asyncio.gather).

### P1 (существенные) — исправлены

**4. Capabilities проверяются** (ранее P1, закрыто)

`arbiter.py:294-298` — `_require(cap)` проверяет capability перед каждым structured-действием. Без `message` нельзя отправлять сообщения, без `work` — работать с делами, без `dao` — голосовать, без `audit` — менять репутацию.

`arbiter.py:585-596` — `_missing_capability_for_op()` проверяет capabilities для ops, сгенерированных LLM в perform.

Тест `test_arbiter_enforces_message_capability` подтверждает отклонение при отсутствии capability.

**7. Арбитраж perform параллелен** (ранее P1, закрыто)

`arbiter.py:217-279` — `arbitrate_tick()` разделяет structured-действия (детерминированные, обрабатываются синхронно) и perform-действия (LLM). Все perform-вызовы собираются и отправляются на LLM параллельно через `asyncio.gather`. Конвертация ops в StateOp происходит детерминированно после.

**8. Утечка всех ID в промпт агента** (ранее P1, закрыто)

`agent.py:75-81` — агенту показываются только релевантные категории ID: agents, work_items, channels, orgs, open votes (и votes только при наличии capability `dao`). Artifact ID, закрытые vote ID и другие внутренние ID не утекают.

**9. Проверка `wants_promotion`** (ранее P1, закрыто)

`arbiter.py:420-421` — при номинации проверяется `target.wants_promotion`. Если `False` — действие отклоняется с причиной `target_declines_promotion`.

`arbiter.py:663-664` — то же для perform-ops `open_vote`.

Тест `test_arbiter_blocks_nomination_when_target_declines_promotion` подтверждает.

**12. Тихий skip ops при ошибке — исправлен на reject** (ранее P1, закрыто)

`arbiter.py:543-551` — если `_op_from_llm` выбрасывает исключение, всё действие отклоняется с причиной `perform_op_invalid:{op_type}:{error}`, вместо тихого пропуска отдельных ops. Тест `test_perform_invalid_op_is_rejected_not_silently_skipped` подтверждает.

**15. Приватные сообщения не утекают в world-gen** (ранее P1, закрыто)

`worldgen.py:49-57` — world-gen фильтрует события: видит только `public`/`internal` аудиторию. Для приватных сообщений, ошибочно помеченных как internal/public, текст редактируется (`text` → `text_redacted: true`).

Тест `test_worldgen_does_not_receive_private_message_text` подтверждает, что `"SECRET"` не попадает в трассу.

### Другие исправления

**3. YAML-журнал строится один раз на тик** (ранее P1, частично закрыто)

`engine.py:352` — `journal_yaml = state.journal_yaml()` вызывается один раз перед `arbiter.arbitrate_tick()`. Все perform-действия тика используют один и тот же снимок. Это снижает нагрузку, но журнал по-прежнему не инкрементальный (пересоздается каждый тик). Для полного закрытия нужна memory арбитра с ConversationSummaryBufferMemory.

**6. Параллелизм через `asyncio.gather`** (ранее P2, закрыто)

`engine.py:337` — `_gather_actions` использует `asyncio.gather` вместо ручного цикла `create_task/await`.

**10. Дублирование `chan:public`** (ранее P2, закрыто)

`engine.py:240-261` — каналы из сценария создаются первыми с проверкой `registry.exists()`. Дефолтный `chan:public` создается только если его еще нет.

**11. `ActionResult.ops` типизирован** (ранее P2, закрыто)

`arbiter.py:62` — `ops: list[StateOp]` вместо `list[object]`.

**13. `ScenarioConfig` мутация** (ранее P3, частично закрыто)

`composer.py:170` — `RuntimeConfig(language=language)` передается в конструктор вместо post-hoc мутации `cfg.runtime.language = language`.

**14. Seed используется для порядка агентов** (ранее P2, закрыто)

`engine.py:227-234` — `_agent_order()` использует `random.Random(seed + tick)` для детерминированного шаффла порядка агентов.

**16. `EventLog.iter_events` — исправлен** (ранее P3, закрыто)

`events.py:84-97` — всегда возвращает `list[Event]`, читает файл целиком через `read_text().splitlines()` и не держит файл открытым.

---

## Что осталось открытым

### Из ревью v1

| # | Проблема | Статус |
|---|---|---|
| 3 | Арбитр пересоздает YAML на каждый тик (не инкрементальный) | Частично: один раз на тик, но не инкрементальный |
| 5 | LangGraph без чекпоинтов | Частично: SqliteSaver подключается при `use_langgraph`, но не тестируется |

### Новые замечания к коммиту `6272517`

**N1. `_redact_numbers` дублируется**

`agent.py:27-34` и `engine.py:423-430` — одна и та же функция `_redact_numbers` определена в двух файлах. Вынести в общий модуль (например, `utils.py`).

**N2. `memory.py` зависит от `magistry_sim.bm25` и `magistry_sim.llm.embeddings`**

`memory.py:16-17` — новый модуль зависит от старого пакета `magistry_sim`. Если greenfield-движок задуман как независимый, зависимость нужно либо сделать явной (re-export), либо вынести BM25/embeddings в общий пакет.

**N3. `embedder.embed()` вызывается синхронно внутри `add_doc`**

`memory.py:112` — `embedder.embed(text)` — синхронный вызов. При добавлении документов в долгосрочную память (например, загрузка 30 interview QA на агента при инициализации × 9 агентов = 270 вызовов) это может быть медленно, если embedder делает HTTP-вызовы. В текущей реализации mock-embedder возвращает мгновенно, но при реальном провайдере станет узким местом.

**N4. `PersonaGenerator.generate()` — один LLM-вызов на summary + biography + 30 QA**

`persona.py:201-240` — всё генерируется одним structured-вызовом. Для моделей с ограниченным output (4K токенов) biography в 1000–2000 слов + 30 ответов по 2–6 предложений может не поместиться в один ответ. Нужен fallback: если ответ обрезан, дозапросить недостающие QA.

**N5. `_importance_for_event` — статические пороги**

`engine.py:460-471` — importance захардкожена по event_type. `arbiter_rejected` = 8.0, `message_sent` = 6.0, и т.д. Из плана: «всё динамическое». На практике пороги могут быть в конфиге, но это P3.

**N6. Нет теста для LLM-суммаризации working buffer**

`memory.py:168-209` (`maybe_summarize_working`) вызывает LLM, но в тестах это не покрыто. MockLLMProvider возвращает пустую строку, и суммаризация фактически не проверяется.

**N7. `memory.py:84-86` — private поля в `slots=True` dataclass**

`_doc_counter`, `_bm25_corpus`, `_bm25`, `_bm25_dirty` — поля с подчеркиванием в dataclass с `slots=True`. Работает в Python 3.12, но подчеркнутые имена в slots — нестандартная практика, которая может удивить при сериализации/дебаге.

---

## Итого

Из 16 замечаний ревью v1:
- **12 закрыты полностью** (включая оба P0).
- **2 закрыты частично** (YAML-журнал, LangGraph чекпоинты).
- **7 новых мелких замечаний** (N1–N7, все P2–P3).

Качество кода выросло существенно. Гибридная память с BM25 + embeddings + recency + importance — ключевая архитектурная деталь, реализованная корректно. Персоны с biography + interview — в соответствии с планом. Capabilities проверяются на уровне арбитра. Приватность world-gen защищена.

Следующий шаг: инкрементальный YAML-журнал арбитра (замечание 3) и тест суммаризации памяти (N6).
