# Ревью коммита `d4f81ec` — MAGISTRY-LC (journal history, persona retry cap, embeddings logging)

Предыдущие коммиты: `4a68dcc` (greenfield), `6272517` (память + персона), `5890d01` (фиксы по v1), `cce22de` (journal + embeddings + persona fallback). Текущий коммит `d4f81ec` закрывает замечания J1–J10 из ревью v2.

Все тесты проходят (9 LC-тестов), регрессий нет.

---

## Что закрыто из ревью v2

### P1

**J2. Журнал без истории действий** (закрыто)

`journal.py:55` — добавлено поле `history: list[dict[str, Any]]` с ограничением `history_max_entries=60`.

`journal.py:297-398` — `_history_entry()` конвертирует 13 типов событий в компактные записи с truncation: `arbiter_approved`, `arbiter_rejected`, `arbiter_op_failed`, `message_sent`, `work_note_added`, `work_proposal_submitted`, `vote_opened`, `vote_cast`, `vote_closed`, `vote_target_consented`, `vote_target_declined`, `position_changed`, `reputation_modified`, `entity_created`.

`journal.py:114-118` — каждое событие проходит через `_history_entry()` при `apply_events()`.

`journal.py:224` — `to_dict()` включает `"history": list(self.history)`.

Арбитр теперь видит последние 60 действий (одобренных и отклоненных) при оценке perform.

Тест `test_world_journal_tracks_history_and_caps` подтверждает: после двух событий `len(d["history"]) == 2`.

**J4. PersonaGenerator retry без ограничений** (закрыто)

`persona.py:275` — добавлен параметр `max_structured_calls: int = 12`.

`persona.py:338-340` — при достижении лимита structured-вызовов оставшиеся вопросы обрабатываются через `_answer_one()` (один `generate` вызов на вопрос, без structured). Это ограничивает structured-вызовы до 12, а single-question fallback не использует structured parsing (дешевле, не может зациклиться).

`persona.py:273-304` — `_answer_one()` вынесена как отдельная async-функция (ранее inline-код в блоке `if len(qs) <= 1`), используется и при single-question fallback, и при превышении лимита.

### P2

**J1. Журнал растет неограниченно — eviction** (закрыто)

`journal.py:39-41` — `store_max_work_items=200`, `store_max_votes=200` — ограничения на хранение в памяти (не путать с `max_work_items=20` — лимит на сериализацию в YAML).

`journal.py:248-288` — `_enforce_caps()` вызывается после каждого `apply_events()` и при `from_state()`. Eviction по принципу LRU с приоритетом закрытых: `_evict_one_work_item()` и `_evict_one_vote()` ищут последний (наименее актуальный) элемент со статусом != "open", а если все open — удаляют последний.

`journal.py:240-246` — `_touch()` перемещает entity_id в начало order-списка при обновлении, обеспечивая LRU-порядок.

Тест `test_world_journal_tracks_history_and_caps` подтверждает: после создания 5 work_items при `store_max_work_items=3` → `len(journal.work_items) <= 3`.

**J3. `embed_texts` тихо глотает ошибки** (закрыто)

`embeddings.py:19-28` — `_warn_embeddings_once()` логирует первые 3 предупреждения, затем подавляет (anti-spam).

`embeddings.py:54-61` — при exception: логируется тип ошибки, количество текстов и суммарный объём символов.

`embeddings.py:64-69` — при несовпадении размера батча: логируется expected vs got.

**J6. Composer: agent_id от LLM без нормализации** (закрыто)

`composer.py:116-123` — `_normalize_typed_id()`: если ID без двоеточия — добавляет `kind:`, если с двоеточием — проверяет `ensure_kind()`.

`composer.py:176-182` — все agent_id нормализуются перед использованием.

`composer.py:224-250` — все channel_id, org_id, work_id, participants нормализуются через `_normalize_typed_id()` с соответствующим `EntityKind`.

**J7. Журнал без reputation** (закрыто)

`journal.py:408` — `_agent_entry()` включает `"reputation": round(float(a.reputation), 3)` для internal-агентов.

**J10. `arbiter_approved` не попадает в долгосрочную память** (закрыто)

`config.py:65` — `"arbiter_approved": 6.0` добавлено в `importance_by_event`. Также добавлены: `"entity_created": 5.0`, `"vote_cast": 5.0`, `"reputation_modified": 6.0`. Все выше `importance_threshold=5.0` — попадают в долгосрочную память.

### P3

**J5. `import yaml` внутри метода** (закрыто)

`journal.py:12-15` — `import yaml` перенесен на уровень модуля в `try/except ImportError`.

**J8. `embeddings_mock=True` — warning при наличии API-ключа** (закрыто)

`engine.py:76-80` — если `embeddings_mock=false` но API-ключ не задан: warning + embeddings disabled.

`engine.py:82-86` — если `embeddings_mock=true` но API-ключ задан: warning с рекомендацией `set embeddings_mock=false`.

**J9. `import random` внутри метода** (закрыто)

`engine.py:8` — `import random` перенесен на уровень модуля.

---

## Новые замечания к коммиту `d4f81ec`

### Существенные

**K1. `_touch()` — O(N) на каждый вызов**

`journal.py:240-246` — `order.remove(entity_id)` + `order.insert(0, entity_id)` — оба O(N). При 200 work_items и частых обновлениях это может стать заметным. Для MVP допустимо, но при масштабировании стоит перейти на `OrderedDict` или linked list.

**K2. `_history_entry` содержит текст приватных сообщений**

`journal.py:330-338` — `message_sent` с `"private": true` включает `"text"` (обрезанный до 240 символов). Журнал передается арбитру при perform — арбитр видит содержимое всех приватных сообщений. Из плана: приватные сообщения не должны утекать за пределы участников. World-gen уже защищен (коммит `5890d01`), но арбитр — нет. Вопрос: должен ли арбитр видеть приватную переписку? Если да — это design decision, если нет — нужна фильтрация.

**K3. `_normalize_typed_id` — пустой slug от LLM не обрабатывается**

`composer.py:116-123` — если LLM вернет `agent_id: ""`, то `raw_id.strip()` будет пустой и `raise ValueError("id must be non-empty")`. Это корректно для валидации, но `WorldComposer.compose()` не перехватывает это исключение — оно пролетит до вызывающего кода. В `_compose_schema` нет `minLength` для `agent_id`.

### Мелкие

**K4. `_evict_one_work_item` и `_evict_one_vote` — дублирование логики**

`journal.py:260-288` — две функции идентичны по структуре, отличаются только именами полей (`vote_order`/`work_item_order`, `votes`/`work_items`). Можно обобщить в одну `_evict_one(order, store)`.

**K5. `history_max_entries=60` — история передается целиком в YAML**

`journal.py:224` — `"history": list(self.history)` включается в `to_yaml()`. При 60 записях с текстом до 280 символов это ~17K символов сверху. Вместе с agents, work_items и votes YAML-журнал может вырасти до 30–40K символов. Это в рамках контекста (131K), но стоит мониторить.

**K6. `_warn_embeddings_once` — глобальный счетчик**

`embeddings.py:19-28` — `_embed_warn_count` — глобальная переменная модуля. При параллельных прогонах в одном процессе (тесты, мультипроцессинг) счетчик будет общим. Для MVP допустимо.

**K7. `world_event` не попадает в историю журнала**

`journal.py:297-398` — `_history_entry` возвращает `None` для `world_event`. Арбитр не видит внешние события мира при оценке perform. Возможно, стоит добавить.

---

## Итого по приоритетам

| # | Проблема | Приоритет |
|---|---|---|
| K2 | Приватные сообщения в истории журнала → арбитр видит переписку | P2 (design decision) |
| K3 | Пустой agent_id от LLM — необработанный ValueError | P2 |
| K1 | `_touch()` O(N) — производительность при масштабировании | P3 |
| K4 | Дублирование eviction-логики | P3 |
| K5 | История 60 записей целиком в YAML — мониторить размер | P3 |
| K7 | `world_event` не в истории журнала | P3 |

---

## Что хорошо

- Все P1 из ревью v2 закрыты. Журнал теперь полноценный: состояние + история + eviction + reputation.
- PersonaGenerator retry ограничен и предсказуем.
- Embeddings-логирование — anti-spam с лимитом.
- `_normalize_typed_id` — защита от кривых ID на всех уровнях (agents, channels, orgs, work_items, participants).
- Embeddings mock/real mode — явные warnings при конфликте настроек.

---

## Сводка по всем ревью

| Ревью | P0 | P1 | P2 | P3 | Тесты |
|---|---|---|---|---|---|
| v1 (4a68dcc) | 2 | 6 | 4 | 4 | 2 |
| v2 (6272517 + 5890d01) | 0 | 0 | 2 part. | 7 new | 8 |
| v3 (cce22de) | 0 | 2 | 5 | 3 | 8 |
| v4 (d4f81ec) | 0 | 0 | 2 | 5 | 9 |

Все P0 и P1 закрыты. Остаются 2 P2 (design decision по приватным сообщениям + обработка пустого ID) и 5 мелких P3.
