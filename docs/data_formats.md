# Форматы данных

Описание форматов данных, используемых в MAGISTRY: сценарии, типы агентов, архетипы личности, интервью и результаты.

## Сценарии (`scenarios/`)

YAML-файлы с полной конфигурацией сценария. Корневая модель — `ScenarioConfig` (Pydantic).

```yaml
version: 1
title: "LC Minimal"
description: "Минимальный пример сценария."
seed: 42
ticks: 3

llm:
  model: "gpt-4o-mini"
  base_url: null
  provider_order: ["Groq"]
  temperature: 0.1
  use_tool_calls: true

runtime:
  language: "ru"
  max_actions_per_turn: 2
  tick_events_history: 200
  enable_worldgen: false
  enrich_personas: false
  persona_enrich_mode: "full"  # full | core
  spawn_secondary: false
  max_secondary_per_agent: 2
  max_agents: 15
  allow_runtime_spawn: false

governance:
  position_policy: "dao"
  quorum: 0.5
  pass_threshold: 0.5
  vote_duration_ticks: 2
  require_consent: true
  audit:
    enabled: true
    actor_id: "agent:auditor"
    mode: "rules"
    lookback_events: 120
    private_contact_window_ticks: 3
    min_confidence_to_flag: 0.6
    min_confidence_to_freeze: 0.85
    freeze_duration_ticks: 3
    reputation_freeze_enabled: true
    reputation_penalty_delta: null

agents:
  - agent_id: "agent:off_1"
    name: "Козлов И.М."
    internal: true
    persona: "Начальник отдела. Прагматик."
    capabilities: ["message", "work", "dao"]
    initial_title: "специалист"
    wants_promotion: true

  - agent_id: "agent:auditor"
    name: "Аудитор"
    internal: true
    persona: "Независимый аудитор."
    capabilities: ["audit", "dao"]
    initial_title: "специалист"
    wants_promotion: false

world:
  channels:
    - channel_id: "chan:public"
      title: "Публичный канал"
  orgs: []
  work_items:
    - work_id: "work:D-001"
      work_type: "hiring_process"
      title: "Найм в отдел"
      description: "Конкурс на вакансию."
      participants: ["agent:off_1", "agent:auditor"]
```

### Блоки конфигурации

| Блок | Описание |
|---|---|
| `llm` | Модель, API, провайдер, температура |
| `runtime` | Язык, лимит действий за ход, генератор мира, обогащение персон |
| `governance` | Политика должностей, кворум, порог, голосование и runtime-аудит |
| `memory` | Рабочий буфер, долгосрочный индекс, веса retrieval, эмбеддинги |
| `agents` | Список агентов с ID, именем, персоной, полномочиями |
| `world` | Каналы, организации, рабочие элементы |

### Ключевые поля `runtime`

| Поле | Тип | Назначение |
|---|---|---|
| `enrich_personas` | `bool` | Runtime-обогащение summary → biography/interview перед первым тиком |
| `persona_enrich_mode` | `full`/`core` | `full` = summary+biography+interview, `core` = summary+biography |
| `spawn_secondary` | `bool` | Извлекать вторичных агентов из социального графа биографий до первого тика |
| `max_secondary_per_agent` | `int` | Лимит социальных связей, извлекаемых из одной персоны |
| `max_agents` | `int` | Общий потолок на количество агентов в мире |
| `allow_runtime_spawn` | `bool` | Разрешить `spawn_agent` и worldgen-spawn в ходе симуляции |

### Типизированные ID

Все идентификаторы сущностей типизированы по виду:

| Вид | Префикс | Пример |
|---|---|---|
| Агент | `agent:` | `agent:off_1` |
| Канал | `chan:` | `chan:public` |
| Организация | `org:` | `org:admin` |
| Рабочий элемент | `work:` | `work:D-001` |
| Голосование | `vote:` | `vote:1` |

### Полномочия агентов

| Полномочие | Действия |
|---|---|
| `message` | `send_message`, `publish` |
| `work` | `create_work_item`, `add_work_note`, `submit_work_proposal` |
| `dao` | `nominate_position_change`, `cast_vote` |
| `audit` | Право на audit-related действия (`modify_reputation` через `perform`) и роль видимого аудитора в событиях |
| `spawn` | `spawn_agent` — создать нового участника в рантайме |

Вторичные и runtime-спавненные агенты проходят фильтрацию capability-набора: движок оставляет только безопасный поднабор `message`/`work`, чтобы новые агенты не получали `audit` или право порождать следующих агентов.

Важно: capability `audit` и `RuntimeAuditor` — не одно и то же. В версии v1 runtime-аудит реализован отдельным rules-first модулем `auditor.py`, который может использовать `actor_id` аудитора из конфигурации, но не сводится к обычному `AgentRunner`.

### Ключевые поля `governance.audit`

| Поле | Тип | Назначение |
|---|---|---|
| `enabled` | `bool` | Включить runtime-аудитор |
| `actor_id` | `agent:* \| null` | Какой agent ID использовать как `actor_id` в audit-событиях |
| `mode` | `rules` / `hybrid` / `llm` | Режим detection; v1 использует rules-first логику |
| `lookback_events` | `int` | Глубина окна recent events |
| `private_contact_window_ticks` | `int` | Окно приватных контактов для conflict-like правил |
| `max_findings_per_tick` | `int` | Лимит findings на тик |
| `min_confidence_to_flag` | `float` | Порог эмиссии `audit_flagged` |
| `min_confidence_to_freeze` | `float` | Порог заморозки репутации |
| `freeze_duration_ticks` | `int` | Длительность заморозки репутации в тиках |
| `reputation_freeze_enabled` | `bool` | Разрешить `SetReputationFreezeOp` |
| `reputation_penalty_delta` | `float \| null` | Опциональный отрицательный штраф к репутации |

### Персона агента

Поле `persona` принимает строку (краткое описание) или объект `PersonaArtifact`:

```yaml
# Короткая форма
persona: "Прагматик. Готов к серым схемам."

# Полная форма
persona:
  summary: "Прагматик. Готов к серым схемам."
  biography: "Родился в 1978 году..."
  interview:
    - question: "Как вы принимаете решения?"
      answer: "Стараюсь взвесить..."
```

### Кэш runtime-обогащения персон (`personas.json`)

Если `runtime.enrich_personas=true`, движок сохраняет рядом с артефактами прогона файл `{out_dir}/personas.json`:

```json
{
  "meta": {
    "version": 1,
    "fingerprint": "sha256...",
    "input": {
      "seed": 42,
      "language": "ru",
      "persona_enrich_mode": "core"
    }
  },
  "personas": {
    "agent:off_1": {
      "summary": "…",
      "biography": "…",
      "interview": []
    }
  }
}
```

Кэш используется только при полном совпадении fingerprint входов.

## Типы агентов (`data/agent_types/`)

JSON-файлы, определяющие шаблоны для веб-интерфейса. Используются CRUD-эндпоинтами для создания сценариев через UI.

```json
{
  "id": "auditor",
  "name": "Аудитор",
  "description": "Проводит проверки и подаёт отчёты.",
  "id_prefix": "aud",
  "capabilities": [
    { "action": "audit", "case_types": [] },
    { "action": "file_report", "case_types": [] }
  ],
  "resources": {
    "budget_limit": 0,
    "staffing_slots": 0,
    "contract_capacity": 0
  }
}
```

Доступные типы: `auditor`, `business_contractor`, `juror`, `official_procurement`.

## Архетипы личности (`data/personalities/`)

JSON-файлы с профилями по HEXACO, Dark Triad и техниками нейтрализации. Используются веб-интерфейсом для назначения персистентных личностей.

```json
{
  "id": "pragmatist",
  "name": "Прагматик-исполнитель",
  "description": "Держит баланс между правилами и реальностью.",
  "prototypes": ["Макс Вебер", "Дуайт Эйзенхауэр"],
  "biography": "Вы цените стабильность и управляемость...",
  "hexaco": {
    "honesty_humility": 62,
    "emotionality": 48,
    "extraversion": 52,
    "agreeableness": 58,
    "conscientiousness": 70,
    "openness": 46
  },
  "dark_triad": {
    "narcissism": 28,
    "machiavellianism": 35,
    "psychopathy": 18
  },
  "neutralization_techniques": ["defense_of_necessity"]
}
```

Доступные архетипы: `machiavellist`, `opportunist`, `pragmatist`, `reformer`, `technocrat`.

## Интервью (`data/interviews/`)

JSON-файлы с результатами нарративных интервью. Содержат ответы на 30 вопросов по 8 доменам, сгенерированные LLM на основе профиля личности.

```json
{
  "id": "pragmatist",
  "archetype": "pragmatist",
  "role": "чиновник",
  "hexaco": { "..." : "..." },
  "dark_triad": { "..." : "..." },
  "interview": {
    "Опишите обычный день из вашей жизни...": "Мой обычный день начинается...",
    "Как вы принимаете решения под давлением...": "Когда на работе возникает давление..."
  }
}
```

Восемь доменов вопросов: повседневная жизнь, мотивация, финансы, лояльность, правила и принципы, межличностные отношения, давление и манипуляции, самооценка.

Файлы интервью используются `PersonaGenerator` и `InterviewFragmentIndex` для фрагментного поиска при принятии решений. Подробнее — в [persona_interview_design.md](./persona_interview_design.md).

## Результаты (`results/` и `lc_results/`)

### Журнал событий (events.jsonl)

Каждая строка — JSON-объект с событием:

```json
{"tick": 1, "round": 1, "event_type": "entity_created", "actor_id": null, "agent_id": "", "payload": {"entity_id": "agent:off_1", "kind": "agent"}, "audience": ["aud:internal"], "timestamp": "2026-03-05T10:30:00+00:00"}
{"tick": 2, "round": 2, "event_type": "message_sent", "actor_id": "agent:off_1", "agent_id": "agent:off_1", "payload": {"to": "agent:auditor", "text": "..."}, "audience": ["agent:off_1", "agent:auditor"], "timestamp": "..."}
{"tick": 2, "round": 2, "event_type": "audit_flagged", "actor_id": "agent:auditor", "agent_id": "agent:auditor", "payload": {"finding_id": "finding:abc", "target_agent_id": "agent:off_1", "violation_type": "support_vote_after_private_contact"}, "audience": ["aud:internal"], "timestamp": "..."}
{"tick": 2, "round": 2, "event_type": "arbiter_approved", "actor_id": "agent:off_1", "agent_id": "agent:off_1", "payload": {"action_type": "send_message"}, "audience": ["aud:internal"], "timestamp": "..."}
```

Поля `tick` и `actor_id` остаются каноническими для движка. Поля `round` и `agent_id` сериализуются как compatibility-aliases для текущего web/UI слоя и legacy-клиентов.

Типы событий:

| Тип | Описание |
|---|---|
| `entity_created` | Регистрация сущности (агент, канал, организация, work item) |
| `message_sent` | Отправка сообщения |
| `work_item_created` | Создание рабочего элемента |
| `work_note_added` | Добавление заметки |
| `work_proposal_submitted` | Подача предложения |
| `reputation_modified` | Изменение репутации |
| `reputation_frozen` / `reputation_unfrozen` | Заморозка или снятие заморозки репутации |
| `reputation_gain_blocked` | Попытка повысить репутацию замороженному агенту |
| `vote_opened` | Открытие голосования |
| `vote_cast` | Подача голоса |
| `vote_closed` | Закрытие голосования |
| `vote_target_consented` / `vote_target_declined` | Ответ кандидата |
| `position_changed` | Смена должности |
| `audit_flagged` | Runtime-аудитор зафиксировал finding |
| `audit_case_opened` | По finding открыт audit-case |
| `audit_escalated` | Кейc эскалирован в санкционный/процедурный слой |
| `audit_case_closed` | Audit-case закрыт |
| `audit_runtime_error` | Ошибка runtime-аудитора на тике |
| `arbiter_approved` | Арбитр одобрил действие |
| `arbiter_rejected` | Арбитр отклонил действие |
| `arbiter_op_failed` | Операция не удалась (ошибка apply) |
| `world_event` | Внешнее событие от WorldGenerator |

### Трассировка LLM (trace.jsonl)

Отдельный журнал всех LLM-вызовов: промпт, ответ, длительность. Хранится в отдельном файле, чтобы не утекать в контекст симуляции.

### Нарушения (violations.json)

Результат работы оракула — JSON-массив выявленных нарушений:

```json
[
  {
    "tick_range": [1, 5],
    "description": "Агент off_1 передал информацию о тендере бизнесу до публичного объявления.",
    "severity": "high",
    "agents_involved": ["agent:off_1", "agent:biz_1"]
  }
]
```
