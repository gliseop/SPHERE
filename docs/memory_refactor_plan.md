# Рефакторинг памяти агентов SPHERE — реализовано

> Целевая модель: Nemotron 120B, контекст 256k токенов.  
> Принцип: при большом контекстном окне лимиты памяти определяются не размером модели,  
> а смысловой ценностью каждого блока контекста.

---

## Изменённые файлы

| Файл | Суть изменений |
|---|---|
| `src/sphere_lc/agent.py` | read-path: лимиты рендеринга, режим интервью, query для retrieval, ветка для informal_link |
| `src/sphere_lc/config.py` | новые параметры памяти, бюджет summary, режим интервью |
| `src/sphere_lc/memory.py` | обновлены шумовые префиксы |
| `src/sphere_lc/engine.py` | `_event_to_text` для `environment_informal_link_updated` |
| `src/sphere_lc/prompts.yaml` | шаблон `interview_full` |
| `tests/test_persona_enrichment.py` | обновлены и добавлены тесты |

---

## Фаза 1 — Prompt read-path

### 1.1 Статические блоки без обрезки
`biography` и `story_state` рендерятся целиком — детерминированные тексты, не растут в ходе симуляции.  
Прежние лимиты 420 / 320 символов сняты. Шаблоны `biography_excerpt` / `story_state` не переименовывались.

### 1.2 Рабочая память — весь буфер
Вместо `[-6:]` — весь буфер с двумя ограничениями из конфига:
- `working_render_entry_max_chars = 3 000` — лимит одной записи
- `working_render_max_chars = 40 000` — суммарный hard cap (~10k токенов)

Обход с хвоста сохраняет самые свежие события; хронология восстанавливается перед рендерингом.

### 1.3 Суммарий — 30% контекстного окна
`_truncate(mem.summary, 520)` заменён на `_truncate(mem.summary, self.memory.summary_max_chars)`.  
`summary_max_chars` = `256 000 × 0.30 × 4.0` = 307 200 символов.

### 1.4 Интервью — режим `full` / `retrieval`
Добавлен конфиг `interview_render_mode: Literal["retrieval", "full"] = "full"`.

- `"full"` — `persona.interview_as_text()` целиком через шаблон `interview_full`
- `"retrieval"` — прежнее поведение с новыми параметрами `interview_retrieval_top_k = 6`, `interview_retrieval_max_chars = 500`

Bootstrap Q/A-фрагментов в LTM (`engine.py:1723`) не тронут — они остаются как источник для observation-retrieval.

### 1.5 Рефлексии — убрать обрезку
`_truncate(item.text, 180)` → `_truncate(item.text, 600)`.  
Дублирование через `reflections_as_text()` не введено — рефлексии уже в LTM и retrieving-ся.

### 1.6 Неформальные связи — снят cap `[:6]`
`_format_informal_links()` (уже существовавший метод) теперь отдаёт до 12 связей.

### 1.7 Query для retrieval — через `_event_fact_line`
Заменён `f"{ev.event_type} {redact_numbers(ev.payload)}"` на вывод `_event_fact_line()` —
человекочитаемый текст с сохранёнными числами. `redact_numbers` в остальных местах не тронут.

Добавлена ветка в `_event_fact_line` для `environment_informal_link_updated`:
возвращает `"Неформальная связь: A — B [тип] (сила N.NN)"` с `_label_for_id`.

---

## Фаза 2 — Memory write-path

### 2.1 `_event_to_text` для `environment_informal_link_updated`
Было: `return ""` — событие отбрасывалось до проверки importance.  
Стало: возвращает `"Неформальная связь обновлена: agent:X — agent:Y [тип] (сила N.NN)"`.  
Typed ID без `_label_for_id` — `engine.py` не зависит от `agent.py`.

### 2.2 Пороги важности
| Событие | Было | Стало |
|---|---|---|
| `pending_interaction_created` | 4.0 | 5.5 |
| `pending_interaction_completed` | 4.0 | 5.5 |
| `pending_interaction_updated` | 4.0 | 5.0 |
| `environment_informal_link_updated` | — (default 3.0) | 5.0 |

### 2.3 Шумовые префиксы
После §2.1 строка `"environment_informal_link_updated:"` больше не совпадает с новым текстом события.  
Заменена на русскоязычный префикс `"неформальная связь обновлена:"`.  
Аудит-события (`"audit_flagged:"`, `"audit_case_updated:"`) удалены из списка — уникальны, коллапс уничтожал детали.

---

## Фаза 3 — Конфигурация

### Новые параметры `MemoryConfig`
```python
working_max_entries: int = 100                  # было 40
working_render_max_chars: int = 40_000          # новый
working_render_entry_max_chars: int = 3_000     # новый
summary_context_fraction: float = 0.30          # новый
interview_render_mode: Literal["retrieval", "full"] = "full"  # новый
interview_retrieval_top_k: int = 6              # новый
interview_retrieval_max_chars: int = 500        # новый
```

### Бюджет контекста (оценка худшего случая)
| Блок | Символов | Токенов |
|---|---|---|
| Биография + story_state | ~3 000 | ~750 |
| Интервью (full) | ~8 000 | ~2 000 |
| Рефлексии | ~1 500 | ~375 |
| Рабочая память | до 40 000 | до 10 000 |
| Суммарий | до 307 200 | до 76 800 |
| Остальные блоки промпта | ~10 000 | ~2 500 |
| **Итого** | **~370 000** | **~92 500** |

Укладывается в 36% контекста Nemotron (256k). Глобальный budget manager — технический долг, актуален при симуляциях > 50 тиков.

### Технический долг
`summary_max_chars` использует hardcoded `256_000` вместо значения из `LLMConfig`.  
При смене модели обновлять синхронно или передавать `model_context_tokens` в `MemoryConfig` при инициализации `SimEngine`.

---

## Acceptance criteria

1. Промпт агента на тике 3+ содержит полный текст интервью (все Q/A) при `interview_render_mode="full"`.
2. Промпт агента содержит суммарий > 1 000 символов, если рабочая память переполнялась.
3. Промпт агента содержит все записи рабочего буфера (не только 6); суммарный объём ≤ `working_render_max_chars`.
4. Query для retrieval содержит числа: суммы и силу связей (не `<num>`).
5. В LTM к тику 6 есть документы (`result` для actor, `observation` для остальных) о `pending_interaction_completed`.
6. В LTM к тику 6 есть непустые документы о `environment_informal_link_updated`.
7. События `audit_flagged` не схлопываются при суммаризации рабочего буфера.
8. `_format_informal_links` отображает все связи агента (не ограничено 6).
