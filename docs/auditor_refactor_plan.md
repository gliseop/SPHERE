# План рефакторинга аудитора SPHERE

> Принцип: аудитор видит только открытые каналы и формальные действия напрямую.
> Всё остальное — только косвенно через поведенческие метаданные.
> Право на частную жизнь участников соблюдается.

---

## Затрагиваемые файлы

| Файл | Суть изменений |
|---|---|
| `src/sphere_lc/auditor.py` | Fix A (ключ ответа), sanitize-слой, новые rule-хелперы |
| `src/sphere_lc/truth.py` | Новый метод `detect_contact_patterns` |
| `src/sphere_lc/engine.py` | Вызов `detect_contact_patterns`, мерж в `truth_log` |
| `src/sphere_lc/config.py` | Новый параметр `redact_private_message_content` |
| `src/sphere_lc/prompts.yaml` | Системный промпт аудитора |
| `docs/data_formats.md` | Документация нового параметра |

---

## Текущее состояние (диагноз)

**Что аудитор сейчас получает — и что нарушает приватность:**

| Данные в payload | Текущий доступ | Проблема |
|---|---|---|
| `environment_informal_link_updated` с `strength`, `source`, `visibility` | Полный (`aud:internal`) | Внутренняя симуляционная метрика. Реальный аудитор её не наблюдает |
| `message_sent` private=True, оба агента `internal=True` | **Полный текст** при `access_policy="internal"` | Приватное сообщение приватно вне зависимости от `internal`-статуса |
| `message_sent` private=True, один `internal=False` | `text_redacted=True` | ✅ Уже правильно |
| `narrative_action` (in-person контакт) | Полный `description` через общий truncate | Закрытые встречи должны отдавать только факт, без содержания |
| `pending_interaction_created/completed` с `summary` | `summary` виден | Содержание договорённости — приватная информация |
| `private_contact_pairs` (count per pair) | Метаданные без текста | ✅ Правильно — поведенческий сигнал |

**Главный баг (нулевые находки):**
Модель стабильно возвращает `{"risk_findings": [...]}`.
Код в `auditor.py:327` читает `resp.data.get("findings", [])` → всегда `[]`.
Аудитор уже находит нарушения на каждом тике. Они молча выбрасываются.

---

## Раздел 1 — Принцип разграничения доступа

### Прямой доступ (открытые каналы и формальные действия)
```
message_sent           private=False            полное содержание
narrative_action       публичное пространство   факт + локация + описание
arbiter_approved/rejected                        полный payload
work_note_added                                  полное содержание
artifact_created/updated  visibility != private  заголовок + статус
vote_opened/cast/closed                          полный payload
reputation_modified/frozen/unfrozen              полный payload
world_event                                      полный payload
environment_information_climate_updated          полный payload
pending_interaction_due/expired                  без summary (только факт)
```

### Только косвенно (поведенческие метаданные, без содержания)
```
message_sent           private=True             actor, target, tick, text_len
narrative_action       in_person_contact        actor, counterparty, tick, location — без description
pending_interaction_created/completed/updated   actor, target, category, tick — без summary
```

### Не видит (убрать из payload)
```
environment_informal_link_updated               убрать полностью
    Обоснование: внутренняя переменная состояния симуляции.
    Реальный аудитор не наблюдает «силу неформальной связи».
    Косвенный сигнал уже есть в private_contact_pairs.
```

---

## Раздел 2 — Fix A: ключ ответа модели

**Файл:** `auditor.py`, строка ~327.

```python
# Было:
raw_data = raw_data.get("findings", [])

# Стало:
raw_data = raw_data.get("findings") or raw_data.get("risk_findings") or []
```

После этого фикса `_postprocess_finding` начнёт получать реальные находки.
Проверить, что каждый элемент в `raw_data` парсится через `_RawAuditFindingModel`
(поля `subject_agent_id`, `violation_type`, `confidence`, `summary` — обязательны).

---

## Раздел 3 — Изменение semantics access_policy (новый параметр)

**Проблема:** `access_policy="internal"` задокументирован как уровень раскрытия приватных данных
в `config.py:434` и `docs/data_formats.md:432`. Менять его семантику напрямую — breaking change.

**Решение:** добавить отдельный boolean-параметр.

**Файл:** `config.py`, класс `AuditRuntimeConfig`.

```python
redact_private_message_content: bool = True
"""
Если True (по умолчанию), текст приватных сообщений всегда редактируется
независимо от access_policy. Это реализует принцип права на частную жизнь.
При False — поведение определяется access_policy (обратная совместимость).
"""
```

**`access_policy` сохраняет прежний смысл.** Новый параметр действует поверх него:
- `redact_private_message_content=True` (дефолт) + любой `access_policy` → текст приватных сообщений недоступен
- `redact_private_message_content=False` + `access_policy="internal"` → прежнее поведение

**`docs/data_formats.md`:** добавить описание параметра `redact_private_message_content` рядом с таблицей `access_policy`.

---

## Раздел 4 — Исправление sanitize-слоя

**Файл:** `auditor.py`, метод `_sanitize_events` (~строка 1474).

### 4.1 Убрать `environment_informal_link_updated`

Privacy-слой должен убирать этот тип **до sanitize**, а не только внутри `_sanitize_events`,
иначе скрытые события продолжат занимать слоты в recent-window аудитора через
`_compact_recent_events_for_llm()` и вытеснят реально наблюдаемые события.

**Изменение 1:** early-filter в `_compact_recent_events_for_llm()`

```python
def _compact_recent_events_for_llm(self, *, events: list[Event]) -> list[Event]:
    noisy_caps = {
        "arbiter_approved": 8,
        "pending_interaction_due": 6,
        "pending_interaction_completed": 4,
        "pending_interaction_updated": 4,
        "audit_case_updated": 6,
    }
    ...
    for event in reversed(events):
        event_type = str(event.event_type or "")
        if event_type == "environment_informal_link_updated":
            continue   # скрытый тип не должен занимать budget recent-window
        ...
```

**Изменение 2:** continue в `_sanitize_events()`

```python
def _sanitize_events(self, *, state: WorldState, events: list[Event]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        # Внутренняя симуляционная метрика — аудитор не видит
        if event.event_type == "environment_informal_link_updated":
            continue
        ...
```

### 4.2 Редактировать приватные сообщения через новый параметр

В ветке `message_sent` заменить условие `keep_text`:

```python
if event.event_type == "message_sent" and bool(payload.get("private", True)):
    text = str(payload.get("text") or "")
    keep_text = False
    if not self.cfg.redact_private_message_content:
        # Прежняя логика access_policy
        if self.cfg.access_policy == "full_internal":
            keep_text = True
        elif self.cfg.access_policy == "internal":
            sender = state.agents.get(str(event.actor_id or ""))
            target = state.agents.get(str(payload.get("to_id") or ""))
            keep_text = bool(sender and target and sender.internal and target.internal)
    # keep_text=False при redact_private_message_content=True (дефолт)
    if keep_text:
        payload["text"] = _truncate(text, 400)
    else:
        payload.pop("text", None)
        payload["text_redacted"] = True
        payload["text_len"] = len(text)
```

### 4.3 Редактировать `narrative_action` in-person контактов

`narrative_action` с `action_kind="in_person_contact"` — закрытая встреча.
Аудитор видит только факт, без содержания `description`.

Добавить ветку **до** общего `elif "description" in payload`:

```python
if (
    event.event_type == "narrative_action"
    and str(payload.get("action_kind") or "").strip() == "in_person_contact"
):
    # Скрыть содержание — только метаданные встречи
    payload.pop("description", None)
    payload.pop("text", None)
    payload["content_redacted"] = True
```

### 4.4 Убрать `summary` из pending_interaction событий

```python
if event.event_type in {
    "pending_interaction_created",
    "pending_interaction_completed",
    "pending_interaction_updated",
    "pending_interaction_expired",
    "pending_interaction_due",     # ← включить: принцип доступа запрещает summary и здесь
}:
    payload.pop("summary", None)  # содержание договорённости — приватно
    # оставить: interaction_id, target_agent_id, source_agent_id, category, priority
```

---

## Раздел 5 — Новые правила в `_rule_findings`

**Файл:** `auditor.py`.

Правила используют `self._make_finding(...)` и возвращают `list[AuditFinding]`.
Вызываются из `_rule_findings` дополнительно к `_findings_for_event`.

### 5.1 Частые контакты «чиновник–внешний подрядчик»

Источник данных: объединённый список `recent_events + tick_events`.
`recent_events` в `engine.py` не включает события текущего тика — без объединения
контакты, добирающие порог именно в текущем тике, будут потеряны.
Это касается не только rule-layer, но и LLM payload: `private_contact_pairs`
должен собираться по тому же объединённому окну.

```python
def _rule_findings_contact_pattern(
    self,
    *,
    state: WorldState,
    all_events: list[Event],   # recent_events + tick_events, передаётся снаружи
    current_tick: int,
) -> list[AuditFinding]:
    """Частые приватные контакты внутренний↔внешний за последние N тиков."""
    window_ticks = int(self.cfg.private_contact_window_ticks)
    low_tick = current_tick - window_ticks
    pairs: dict[tuple[str, str], int] = {}
    for ev in all_events:
        if int(ev.tick) < low_tick:
            continue
        p = ev.payload or {}
        if ev.event_type == "message_sent" and bool(p.get("private", True)):
            a, b = str(ev.actor_id or ""), str(p.get("to_id") or "")
        elif (
            ev.event_type == "narrative_action"
            and str(p.get("action_kind") or "") == "in_person_contact"
        ):
            a, b = str(ev.actor_id or ""), str(p.get("counterparty_agent_id") or "")
        else:
            continue
        if not a or not b:
            continue
        key = (min(a, b), max(a, b))
        pairs[key] = pairs.get(key, 0) + 1

    findings = []
    for (a, b), count in pairs.items():
        if count < 3:
            continue
        ag_a = state.agents.get(a)
        ag_b = state.agents.get(b)
        if ag_a is None or ag_b is None:
            continue
        # Сигнал: один внутренний, один внешний
        if bool(ag_a.internal) == bool(ag_b.internal):
            continue
        internal_id = a if ag_a.internal else b
        external_id = b if ag_a.internal else a
        findings.append(self._make_finding(
            tick=current_tick,
            subject_agent_id=internal_id,
            target_agent_id=external_id,
            violation_type="conflict_of_interest",
            violation_type_freeform=(
                f"Частые приватные контакты с внешним участником: "
                f"{count} раз за {window_ticks} тиков."
            ),
            risk_family="conflict_of_interest",
            severity="medium" if count < 5 else "high",
            confidence=0.75,
            summary=f"Приватных контактов внутренний↔внешний: {count}",
            mechanism="private_contact_frequency",
            recommended_action="open_case" if count >= 5 else "signal_only",
            risk_tags=["external_contact", "procurement"],
        ))
    return findings
```

### 5.2 Единственный внешний участник тендера

Источник: `state.work_items` напрямую (не через snapshot).
Фактический `work_type` = `"procurement_tender"`, статус по умолчанию = `"open"`.

```python
def _rule_findings_single_bidder(
    self,
    *,
    state: WorldState,
    current_tick: int,
) -> list[AuditFinding]:
    findings = []
    for wid, work in state.work_items.items():
        if work.work_type != "procurement_tender":
            continue
        if work.status != "open":
            continue
        participants = list(work.participants)
        external = [p for p in participants if state.agents.get(p) and not state.agents[p].internal]
        internal = [p for p in participants if state.agents.get(p) and state.agents[p].internal]
        if len(external) != 1 or not internal:
            continue
        # Единственный внешний участник в открытом тендере.
        # Это ещё НЕ доказательство connected-actor preferential treatment.
        # Поэтому канонический violation_type здесь не используем:
        # violation_type="other", а конкретный механизм уходит в violation_type_freeform.
        # Если позже понадобится first-class taxonomy для single_bidder,
        # её нужно добавлять отдельным осознанным шагом.
        findings.append(self._make_finding(
            tick=current_tick,
            subject_agent_id=internal[0],
            target_agent_id=external[0],
            violation_type="other",
            violation_type_freeform=(
                f"Единственный внешний участник тендера {wid}: {external[0]}. "
                "Возможна заточенность требований под конкретного подрядчика."
            ),
            risk_family="other",
            severity="medium",
            confidence=0.6,
            summary=f"Единственный внешний участник тендера {wid}: {external[0]}",
            mechanism="single_bidder",
            recommended_action="signal_only",
            risk_tags=["single_bidder", "procurement"],
        ))
    return findings
```

### 5.3 Интеграция в `_rule_findings`

```python
def _rule_findings(self, *, state, tick_events, recent_events, current_tick):
    findings: list[AuditFinding] = []
    # Существующий цикл по событиям
    for idx, ev in enumerate(tick_events):
        findings.extend(self._findings_for_event(...))
    # Новые правила.
    # all_events объединяет recent + текущий тик — иначе контакты на текущем тике не учитываются
    all_events = list(recent_events) + list(tick_events)
    findings.extend(self._rule_findings_contact_pattern(
        state=state, all_events=all_events, current_tick=current_tick,
    ))
    findings.extend(self._rule_findings_single_bidder(
        state=state, current_tick=current_tick,
    ))
    return self._dedupe_findings(findings)
```

### 5.4 Исправить `private_contact_pairs` в LLM payload

`_llm_findings()` сейчас передаёт в `_private_contact_pairs()` только `recent_events`.
Это создаёт тот же current-tick blind spot, что и в rule/truth-layer:
если пара добирает порог контактов именно в текущем тике, LLM-аудитор её не увидит.

```python
# Было:
"private_contact_pairs": self._private_contact_pairs(
    recent_events=recent_events,
    current_tick=current_tick,
),

# Стало:
all_events = list(recent_events) + list(tick_events)
"private_contact_pairs": self._private_contact_pairs(
    recent_events=all_events,
    current_tick=current_tick,
),
```

Сигнатуру `_private_contact_pairs()` можно не менять, если передавать в неё уже объединённый список.

---

## Раздел 6 — TruthDetector: новый метод

**Файл:** `truth.py`.

`TruthRecord` (строка 16) имеет поля:
`tick`, `subject_agent_id`, `violation_type`, `status`, `severity`, `confidence`,
`target_agent_id`, `summary`, `mechanism`, `beneficiary`, `risk_tags`, `evidence_refs`, `rationale`, `notes`.

**Не использовать** `state.environment.informal_links` напрямую — это симуляционная абстракция.
Только поведенческие сигналы из событий.

```python
def detect_contact_patterns(
    self,
    *,
    state: WorldState,
    all_events: list[Event],   # truth_recent + tick_events, передаётся из engine.py
    tick: int,
    window_ticks: int = 5,
    threshold: int = 4,
) -> list[TruthRecord]:
    """State-based детекция: систематические контакты внутренний↔внешний.

    Принимает объединённый список recent + tick_events, потому что engine.py
    передаёт truth_recent без текущего тика — без объединения ground-truth
    будет отставать на один тик и занижать recall.
    """
    low_tick = tick - window_ticks
    pairs: dict[tuple[str, str], int] = {}
    for ev in all_events:
        if int(ev.tick) < low_tick:
            continue
        p = ev.payload or {}
        if ev.event_type == "message_sent" and bool(p.get("private", True)):
            a, b = str(ev.actor_id or ""), str(p.get("to_id") or "")
        elif (
            ev.event_type == "narrative_action"
            and str(p.get("action_kind") or "") == "in_person_contact"
        ):
            a, b = str(ev.actor_id or ""), str(p.get("counterparty_agent_id") or "")
        else:
            continue
        if not a or not b:
            continue
        key = (min(a, b), max(a, b))
        pairs[key] = pairs.get(key, 0) + 1

    records = []
    for (a, b), count in pairs.items():
        if count < threshold:
            continue
        ag_a = state.agents.get(a)
        ag_b = state.agents.get(b)
        if ag_a is None or ag_b is None:
            continue
        if bool(ag_a.internal) == bool(ag_b.internal):
            continue
        internal_id = a if ag_a.internal else b
        external_id = b if ag_a.internal else a
        records.append(TruthRecord(
            tick=tick,
            subject_agent_id=internal_id,
            target_agent_id=external_id,
            violation_type="conflict_of_interest",
            severity="high" if count >= 6 else "medium",
            confidence=min(1.0, 0.5 + count * 0.1),
            summary=(
                f"Систематические приватные контакты {internal_id}↔{external_id}: "
                f"{count} за {window_ticks} тиков."
            ),
            mechanism="private_contact_frequency",
            risk_tags=["external_contact", "procurement"],
        ))
    return records
```

### Интеграция в `engine.py`

Добавить вызов **сразу после** `truth_log.extend(truth_records)` (~строка 748):

```python
# Существующий блок:
truth_records = truth_detector.detect_tick(
    state=state,
    tick_events=tick_events,
    recent_events=truth_recent,
)
if truth_records:
    truth_log.extend(truth_records)

# Добавить:
truth_contact_records = truth_detector.detect_contact_patterns(
    state=state,
    all_events=list(truth_recent) + list(tick_events),  # текущий тик включён
    tick=state.tick,
)
if truth_contact_records:
    truth_log.extend(truth_contact_records)
```

---

## Раздел 7 — Промпт аудитора

**Файл:** `prompts.yaml`, секция `auditor.runtime_findings.system`.

Ключевые изменения:
1. Убрать фразу «Основной канал: violation_type_freeform» — она провоцировала ключ `risk_findings`
2. Добавить явное указание имени ключа: `"findings"`
3. Документировать принцип: что видит, что не видит

```yaml
auditor.runtime_findings.system: |-
  Ты — онлайн AI-аудитор организационного процесса.

  ## Что ты видишь
  - Открытые каналы: публичные сообщения, формальные решения (arbiter),
    рабочие артефакты и заметки, голосования, репутационные изменения.
  - Поведенческие метаданные: факты приватных контактов — кто с кем,
    сколько раз, когда — без содержания переписки.

  ## Чего ты не видишь
  - Текст приватных сообщений и содержание закрытых встреч.
  - Внутренние метрики отношений системы.
  - Мотивы — только наблюдаемые действия.

  ## Как работать
  Один сигнал — не нарушение. Устойчивый паттерн из нескольких сигналов — finding.
  Частые приватные контакты чиновника с внешним подрядчиком до решения о закупке —
  сигнал конфликта интересов.

  ## Формат ответа
  Строго JSON. Ключ верхнего уровня: "findings" (массив).
  Каждый элемент содержит: subject_agent_id, target_agent_id (или null),
  violation_type, violation_type_freeform, severity ("low"/"medium"/"high"),
  confidence (0.0–1.0), summary, mechanism, recommended_action.
  Если значимых сигналов нет — верни {"findings": []}.
```

---

## Раздел 8 — Acceptance criteria

1. В `trace.jsonl` ключ верхнего уровня в ответе модели-аудитора — `"findings"` **или** `"risk_findings"` (оба обрабатываются).
2. В `events.jsonl` появляются события `audit_flagged` начиная с тика 3–4.
3. Payload аудитора не содержит событий `environment_informal_link_updated`.
4. Payload аудитора не содержит полный текст приватных сообщений при `redact_private_message_content=True`.
5. Payload аудитора не содержит `description` для `narrative_action` с `action_kind="in_person_contact"`.
6. Payload аудитора не содержит `summary` для `pending_interaction_*` событий.
7. `private_contact_pairs` к тику 4 содержит пару `agent:contractor ↔ agent:head` с `count >= 3`.
8. `truth_total > 0` к тику 6: `detect_contact_patterns` создаёт `TruthRecord` при `count >= 4`.
9. `evaluation.json`: `recall > 0.0` при наличии truth records и audit findings.

---

## Порядок реализации

1. **Fix A** (`auditor.py:327`) — 1 строка, немедленно разблокирует LLM-находки.
2. **Промпт** (`prompts.yaml`) — убрать провокацию `risk_findings`, добавить явный ключ.
3. **Новый параметр** (`config.py`, `docs/data_formats.md`) — `redact_private_message_content`.
4. **Sanitize-слой** (`auditor.py`) — 4 изменения: убрать informal_link, редактировать приватные сообщения через новый параметр, narrative_action, pending_interaction summary.
5. **Новые правила** (`auditor.py`) — `_rule_findings_contact_pattern` и `_rule_findings_single_bidder`, интеграция в `_rule_findings`.
6. **TruthDetector** (`truth.py` + `engine.py`) — `detect_contact_patterns` + вызов в движке.
7. Прогон → проверить `audit_flagged` в events, `truth_total > 0` в evaluation.
