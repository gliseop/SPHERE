# Ревью коммита `eed101f` — MAGISTRY-LC (private redaction, world_event history, robust ID normalization)

Предыдущие коммиты: `4a68dcc`→`6272517`→`5890d01`→`cce22de`→`d4f81ec`. Текущий коммит `eed101f` закрывает замечания K2, K3, K7 из ревью v3.

Все тесты проходят (11 LC-тестов), регрессий нет.

---

## Что закрыто из ревью v3

### P2

**K2. Приватные сообщения в истории журнала** (закрыто)

`journal.py:328-346` — приватные сообщения (`private=True`) редактируются в истории: текст заменяется на `<redacted>`, сохраняется только `text_len`. Публичные сообщения сохраняются с truncation до 240 символов. Арбитр видит факт приватного общения (кто → кому), но не содержание.

Тест `test_world_journal_redacts_private_messages_and_tracks_world_events` подтверждает: `"SECRET" not in yaml_text`.

**K3. Пустой agent_id от LLM** (закрыто)

Полная переработка ID-нормализации в `composer.py`:

`composer.py:122-164` — три уровня защиты:
1. `_try_normalize_typed_id()` — возвращает `None` вместо exception при невалидном ID.
2. `_normalize_or_fallback()` — при `None` генерирует `make_id(kind, fallback)`.
3. `_unique_id()` — проверяет уникальность, при коллизии добавляет суффикс `_2`, `_3`, ...

`composer.py:217-231` — агенты: каждый получает гарантированно уникальный typed ID. `agent_id_map` маппит исходные строки LLM → финальные ID для participants.

`composer.py:295-326` — work_items: participants проходят через `agent_id_map`, проверяются на принадлежность `used_agents`, дедуплицируются.

`_compose_schema()` — добавлены `"minLength": 1` для всех ID-полей, что дает первую линию защиты на уровне JSON Schema.

Тест `test_composer_normalizes_or_falls_back_on_invalid_ids`: три агента (пустой ID, без префикса, с дубликатом `agent:off_1`), пустой org_id, пустой work_id, participants с пустыми строками и дубликатами. Все ID уникальны, все имеют правильный префикс, participants ∈ known_agents.

### P3

**K7. `world_event` не в истории журнала** (закрыто)

`journal.py:348-355` — `world_event` добавлен в `_history_entry()` с truncation описания до 280 символов.

Тест подтверждает: `any(e.get("type") == "world_event" for e in d["history"])`.

---

## Новые замечания к коммиту `eed101f`

### Мелкие

**L1. `_unique_id` — бесконечный цикл при патологическом вводе**

`composer.py:159-164` — `while True` ищет свободный суффикс. Теоретически бесконечный, если `used` содержит все варианты. На практике при < 100 агентах не проблема, но `for n in range(2, 1000)` с raise был бы безопаснее.

**L2. `agent_id_map` — неочевидная семантика**

`composer.py:219-230` — `agent_id_map` маппит raw → final и normalized → final. Если LLM вернет `off_1` и `agent:off_1` как разных агентов, первый маппинг `off_1 → agent:off_1` запишется, а при обработке второго `agent:off_1` уже в map — маппинг не обновится. Но `_unique_id` сделает второго `agent:off_1_2`. В итоге participant `off_1` будет маппиться на первого, а `agent:off_1` тоже на первого (из map), хотя второй агент — `agent:off_1_2`. Это корректно для данного конкретного теста, но может удивить при других паттернах дубликатов.

**L3. `text_len` в redacted history — можно извлечь длину секрета**

`journal.py:343` — `"text_len": len(text)` сохраняется для приватных сообщений. Это метаданные, а не содержание, но в теории длина текста может помочь арбитру делать предположения. Не критично, но стоит знать.

**L4. `_compose_schema` — `"required": []` для world**

`composer.py:115` — `"required": []` означает что LLM может вернуть world без channels, orgs, work_items. Это корректно (defaults пустые), но `_ComposeWorld` имеет `Field(default_factory=list)`, а JSON Schema не указывает defaults. При модели, которая строго следует required — world может быть просто `{}`.

---

## Итого по приоритетам

| # | Проблема | Приоритет |
|---|---|---|
| L1 | `_unique_id` without upper bound | P3 |
| L2 | `agent_id_map` edge case at duplicates | P3 |
| L3 | `text_len` leaks message length | P3 |
| L4 | `"required": []` for world schema | P3 |

---

## Сводка по всем ревью

| Ревью | P0 | P1 | P2 | P3 | Тесты |
|---|---|---|---|---|---|
| v1 (4a68dcc) | 2 | 6 | 4 | 4 | 2 |
| v2 (6272517 + 5890d01) | 0 | 0 | 2 part. | 7 | 8 |
| v3 (cce22de) | 0 | 2 | 5 | 3 | 8 |
| v4 (d4f81ec) | 0 | 0 | 2 | 5 | 9 |
| **v5 (eed101f)** | **0** | **0** | **0** | **4** | **11** |

Все P0, P1 и P2 закрыты. Остаются только 4 мелких P3, ни одно из которых не блокирует production-прогон.
