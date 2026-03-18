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
  provider_order: []  # опционально: только для OpenRouter provider routing
  temperature: 0.1
  use_tool_calls: true

runtime:
  language: "ru"
  start_date: "2026-01-01"
  tick_granularity: "day"  # hour | half_day | day | week
  tick_duration_days: 1
  parallel_agents: true
  parallel_workers: 4
  parallel_window_seconds: 300
  temporal_past_slack_days: 1
  temporal_future_horizon_days: 120
  max_actions_per_turn: 2
  tick_events_history: 200
  enable_worldgen: false
  worldgen_every_ticks: 1
  worldgen_pre_tick: false
  worldgen_event_budget_per_tick: 3
  agent_context_budget_per_tick: 1
  max_scene_changes_per_tick: 2
  max_new_actors_per_window: 2
  enrich_personas: false
  persona_enrich_mode: "full"  # full | core
  spawn_secondary: false
  max_secondary_per_agent: 2
  max_agents: 15
  allow_runtime_spawn: false
  request_entity_internal_only: true
  ecology_activation_window_ticks: 2
  worldgen_allow_internal_spawns: false
  worldgen_context_scope: "core"
  freeform_truth_enabled: false
  freeform_truth_window_ticks: 5

memory:
  embeddings_mock: false  # default для обычных прогонов; true имеет смысл в тестах
  embeddings_model: null
  embeddings_base_url: null

governance:
  position_policy: "dao"  # v1: единственный поддерживаемый режим
  quorum: 0.5
  pass_threshold: 0.5
  vote_duration_ticks: 2
  require_consent: true
  allow_self_nomination: false
  allow_target_self_vote: false
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
    initial_reputation: 4.5
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

scripted_events:
  - event_id: "fork_deadline"
    tick: 2
    audience: "internal"
    description: "Министерство требует ускорить подготовку документов до конца недели."
    once: true
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
| `scripted_events` | Предопределённые внешние события / развилки сценария |

Поле `agents[].initial_reputation` задаёт стартовую репутацию внутреннего агента. Движок применяет её при инициализации `WorldState`, а первый `reputation_snapshot` в `events.jsonl` отражает именно это значение.

### Ключевые поля `runtime`

| Поле | Тип | Назначение |
|---|---|---|
| `start_date` | `YYYY-MM-DD \| null` | Каноническая дата тика `0`; если не задана, агент и worldgen видят только номер тика |
| `tick_granularity` | `hour` / `half_day` / `day` / `week` | Качественный временной масштаб тика |
| `tick_duration_days` | `int` | Множитель для выбранной гранулярности (`day` = дни, `hour` = часы, `half_day` = полудни, `week` = недели) |
| `parallel_agents` | `bool` | Выполнять `propose_actions` параллельно в пределах тика; при `false` генерация решений идёт последовательно |
| `parallel_workers` | `int \| null` | Опциональный лимит одновременных LLM-вызовов на этапе генерации действий |
| `parallel_window_seconds` | `float \| null` | Совместимый параметр окна батчирования для web launcher; в текущем tick-engine один тик образует один batch |
| `temporal_past_slack_days` | `int` | Сколько дней назад арбитр ещё допускает абсолютную дату в действии |
| `temporal_future_horizon_days` | `int` | Максимальный горизонт будущих абсолютных дат в структурированных действиях |
| `worldgen_every_ticks` | `int` | Положительный интервал запуска worldgen в тиках; `0` и отрицательные значения недопустимы |
| `worldgen_pre_tick` | `bool` | Запускать ли pre-tick worldgen до `propose_actions`, чтобы подать агентам personal contexts |
| `worldgen_event_budget_per_tick` | `int` | Верхняя граница числа `world_event` от worldgen за один запуск |
| `agent_context_budget_per_tick` | `int` | Бюджет `agent_daily_context` на один pre-tick запуск |
| `max_scene_changes_per_tick` | `int` | Лимит scene hooks / scene changes на тик |
| `max_new_actors_per_window` | `int` | Лимит worldgen-spawn suggestions за одно окно |
| `enrich_personas` | `bool` | Runtime-обогащение summary → biography/interview перед первым тиком |
| `persona_enrich_mode` | `full`/`core` | `full` = summary+biography+interview+expert reflection, `core` = summary+biography |
| `spawn_secondary` | `bool` | Извлекать вторичных агентов из социального графа биографий до первого тика |
| `max_secondary_per_agent` | `int` | Лимит социальных связей, извлекаемых из одной персоны |
| `max_agents` | `int` | Общий потолок на количество агентов в мире |
| `allow_runtime_spawn` | `bool` | Разрешить `spawn_agent` и worldgen-spawn в ходе симуляции |
| `request_entity_internal_only` | `bool` | При `true` только внутренние акторы могут создавать `chan:*`/`org:*` через `request_entity` |
| `ecology_activation_window_ticks` | `int` | Если `> 0`, не-core ecology-акторы ходят только при недавней релевантности |
| `worldgen_allow_internal_spawns` | `bool` | Разрешить worldgen порождать внутренних акторов; по умолчанию выключено |
| `worldgen_context_scope` | `core` / `all` | Для каких акторов pre-worldgen строит personal contexts (`core` по умолчанию) |
| `freeform_truth_enabled` | `bool` | Включить post-hoc `truth_freeform.jsonl` |
| `freeform_truth_window_ticks` | `int` | Размер окна событий для `FreeformTruthRecorder` |

### `scripted_events`

`ScenarioConfig.scripted_events` позволяет задать предопределённые события мира без изменения кода движка.

| Поле | Тип | Назначение |
|---|---|---|
| `event_id` | `str` | Стабильный ID scripted-события; если пуст, движок сгенерирует `scripted:{index}` |
| `tick` | `int \| null` | Если задан, событие эмитится на конкретном тике |
| `if_event_types` | `list[str]` | Необязательные условия: какие типы событий должны уже встретиться в истории |
| `if_work_ids_open` | `list[str]` | Необязательные условия: какие work items должны быть в статусе `open` |
| `audience` | `public` / `internal` | Аудитория `world_event` |
| `description` | `str` | Текст внешнего события |
| `once` | `bool` | При `true` scripted event срабатывает только один раз |

### `agent_daily_context` (prompt-layer)

`agent_daily_context` не сериализуется прямо в сценарии, но создаётся pre-tick worldgen и/или fallback-логикой движка.

Минимальные поля:

- `where_day_starts`;
- `personal_pressure`;
- `social_encounter`;
- `ambient_signal`;
- `today_hook`.

Расширенные поля для risky-pressure режима:

- `private_pressure`;
- `opportunity`;
- `exposure_risk`.

### Ключевые поля `memory`

| Поле | Тип | Назначение |
|---|---|---|
| `embeddings_mock` | `bool` | При `false` движок пытается использовать реальные embeddings; при `true` берёт детерминированный mock-провайдер |
| `embeddings_model` | `str \| null` | Явное имя embedding-модели; если не задано, используется `EMBEDDING_MODEL` или `text-embedding-3-small` |
| `embeddings_base_url` | `str \| null` | Отдельный OpenAI-compatible endpoint для embeddings; при `null` используется `OPENAI_BASE_URL` или `llm.base_url` |

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

Для `spawn_agent`, secondary-spawn и worldgen-spawn действует дополнительное правило: новый агент должен иметь человеко-читаемое имя, а не `agent:*`, slug или абстрактную должность. Role-alias ссылки либо переиспользуют уже существующего актора, либо отклоняются.

При `runtime.request_entity_internal_only=true` действие `request_entity` также ограничено внутренними акторами: external/ecology-персонажи не могут бесконтрольно развернуть публичную инфраструктуру мира (`chan:*`, `org:*`) через обычный агентный ход.

Во внутреннем runtime-состоянии (`AgentState`) движок дополнительно поддерживает `story_state: str` — короткую персональную линию агента на текущий момент. Это не отдельный сериализуемый блок сценария, а runtime-sidecar, который обновляется движком по persona и наблюдаемым событиям.

### Ключевые поля `governance`

| Поле | Тип | Назначение |
|---|---|---|
| `require_consent` | `bool` | Требовать явное согласие цели номинации через `respond_nomination` |
| `allow_self_nomination` | `bool` | Разрешить self-nomination; для исследовательского режима обычно `false` |
| `allow_target_self_vote` | `bool` | Разрешить цели голосования голосовать за себя; для коллегиального режима обычно `false` |

### Ключевые поля `governance.audit`

| Поле | Тип | Назначение |
|---|---|---|
| `enabled` | `bool` | Включить runtime-аудитор |
| `actor_id` | `agent:* \| null` | Какой agent ID использовать как `actor_id` в audit-событиях |
| `mode` | `rules` | Режим detection; в `RuntimeAuditor` v1 поддерживается только rules-first режим |
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
  reflections:
    - expert: "psychologist"
      summary: "Избегает прямого конфликта и предпочитает обходные траектории."
      evidence_indices: [0, 4]
    - expert: "economist"
      summary: "Сильно реагирует на карьерные стимулы и управляемый риск."
      evidence_indices: [2, 8]
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
      "interview": [],
      "reflections": []
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
{"tick": 1, "round": 1, "event_type": "entity_created", "actor_id": null, "agent_id": "", "payload": {"entity_id": "agent:off_1", "kind": "agent", "meta": {"name": "Козлов И.М.", "internal": true, "capabilities": ["message", "work", "dao"]}}, "audience": ["aud:internal"], "timestamp": "2026-03-05T10:30:00+00:00"}
{"tick": 1, "round": 1, "event_type": "reputation_snapshot", "actor_id": null, "agent_id": "agent:off_1", "payload": {"target_agent_id": "agent:off_1", "score": 0.0, "internal": true, "frozen": false, "title": "специалист"}, "audience": ["aud:internal"], "timestamp": "2026-03-05T10:30:00+00:00"}
{"tick": 2, "round": 2, "event_type": "message_sent", "actor_id": "agent:off_1", "agent_id": "agent:off_1", "payload": {"to_id": "agent:auditor", "text": "..."}, "audience": ["agent:off_1", "agent:auditor"], "timestamp": "..."}
{"tick": 2, "round": 2, "event_type": "audit_flagged", "actor_id": "agent:auditor", "agent_id": "agent:auditor", "payload": {"finding_id": "finding:abc", "subject_agent_id": "agent:off_1", "target_agent_id": "agent:off_1", "related_target_agent_id": "agent:off_2", "violation_type": "support_vote_after_private_contact"}, "audience": ["aud:internal"], "timestamp": "..."}
{"tick": 2, "round": 2, "event_type": "arbiter_approved", "actor_id": "agent:off_1", "agent_id": "agent:off_1", "payload": {"action_type": "send_message"}, "audience": ["aud:internal"], "timestamp": "..."}
```

Поля `tick` и `actor_id` остаются каноническими для движка. Поля `round` и `agent_id` сериализуются как compatibility-aliases для текущего web/UI слоя и legacy-клиентов.
Для событий об агентах движок дополнительно пишет `payload.meta.internal` (`entity_created`) и `payload.internal` (`reputation_snapshot`), чтобы web UI мог отличать внутренних участников от внешних и не показывать репутацию там, где она не применяется.

Типы событий:

| Тип | Описание |
|---|---|
| `entity_created` | Регистрация сущности (агент, канал, организация, work item) |
| `reputation_snapshot` | Снимок стартовой репутации/статуса агента для UI и sidecar-аналитики |
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

### Sidecar-файлы directory-run

Для `results/{run_name}/` современный LC-движок пишет дополнительные JSON-файлы:

- `scenario.json` — сериализованный `ScenarioConfig` конкретного прогона.
- `names.json` — отображение `agent_id -> display name`, используемое web UI и WebSocket `meta`.
- `status.json` — heartbeat-статус прогона (`running` / `finished` / `failed`) с `updated_at`, `pid` и последним tick.
- `summary.json` — итоговая агрегированная сводка (`governance` + `fidelity`).

Примеры:

```json
// scenario.json
{
  "version": 1,
  "title": "LC Minimal",
  "ticks": 3,
  "runtime": {
    "parallel_agents": true,
    "parallel_workers": 4,
    "parallel_window_seconds": 300.0
  },
  "agents": [
    {"agent_id": "agent:off_1", "name": "Козлов И.М.", "internal": true, "initial_reputation": 4.5}
  ],
  "world": {"channels": [{"channel_id": "chan:public", "title": "Публичный канал"}]}
}
```

```json
// names.json
{
  "agent:off_1": "Козлов И.М.",
  "agent:auditor": "Аудитор"
}
```

```json
// status.json
{
  "state": "running",
  "pid": 18432,
  "tick": 2,
  "updated_at": "2026-03-11T18:42:10.123456+00:00",
  "error": null
}
```

### Truth-layer (`truth.jsonl`)

Отдельный deterministic sidecar с каноническими truth-records для post-hoc оценки runtime-аудита.

Пример записи:

```json
{
  "tick": 2,
  "subject_agent_id": "agent:auditor",
  "violation_type": "self_reputation_award",
  "status": "committed",
  "severity": "high",
  "confidence": 1.0,
  "target_agent_id": "agent:auditor",
  "evidence_refs": [{"tick": 2, "event_type": "reputation_modified", "timestamp": "2026-03-05T10:31:12.345678+00:00"}],
  "rationale": "Агент повысил собственную репутацию."
}
```

Ключевая особенность:

- `truth.jsonl` не является продолжением `events.jsonl`;
- он пишется отдельно и не подаётся агентам;
- он используется для формального post-hoc сравнения governance-treatment и truth-layer.
- при дедупликации учитываются не только `tick`/`subject_agent_id`/`violation_type`, но и цель/контекст (`target_agent_id`, `evidence_refs`, включая `timestamp` исходного события), чтобы несколько однотипных нарушений в один тик не схлопывались в один кейс.

Кроме исходных governance-эвристик (`self_nomination`, `support_vote_after_private_contact` и т.п.), deterministic truth теперь может фиксировать некоторые tender-domain нарушения:

- `preferential_treatment_for_connected_actor`;
- `non_escalation_under_pressure`;
- `partial_disclosure_under_deadline_pressure`.

### Freeform truth (`truth_freeform.jsonl`)

Опциональный LLM-sidecar, который пишет richer truth в свободной форме по схеме.

Он включается через `runtime.freeform_truth_enabled=true` и не заменяет deterministic `truth.jsonl`.

Пример записи:

```json
{
  "tick": 7,
  "subject_agent_id": "agent:head",
  "target_agent_id": "agent:spec",
  "violation_type_freeform": "pressure_not_to_escalate",
  "summary": "Руководитель давит на специалиста, чтобы та не эскалировала подозрительное совпадение в документах.",
  "mechanism": "private_pressure",
  "beneficiary": "agent:head",
  "confidence": 0.74,
  "evidence_refs": [{"tick": 7, "event_type": "message_sent"}],
  "notes": "Контекст указывает на страх карьерных последствий."
}
```

Назначение:

- фиксировать нарушения, которые сложно выразить rules-first детектором;
- расширять предметную истину без ломки воспроизводимого baseline;
- поддерживать отдельный исследовательский слой поверх deterministic truth.

### Post-hoc evaluation (`evaluation.json`)

Итоговая сводка сравнения runtime-аудита и truth-layer.

Пример:

```json
{
  "truth_total": 3,
  "runtime_flagged_total": 4,
  "true_positive": 2,
  "false_positive": 2,
  "false_negative": 1,
  "precision": 0.5,
  "recall": 0.6667,
  "f1": 0.5714,
  "by_violation_type": {
    "self_reputation_award": {"truth": 1, "signals": 1, "tp": 1, "fp": 0, "fn": 0}
  }
}
```

При сопоставлении runtime-сигналов с truth-layer учитываются `related_target_agent_id` и `evidence_refs` из `audit_flagged`, поэтому два однотипных finding'а одного субъекта в один и тот же тик считаются двумя отдельными случаями, если у них разный контекст или разное исходное событие (например, разные `vote_id` или разные `timestamp` в `evidence_refs`).

### Fidelity sidecar (`fidelity.json`)

Отдельная сводка по правдоподобию и структурной дисциплине мира:

```json
{
  "temporal_violations_total": 0,
  "identity_machine_name_total": 0,
  "identity_role_alias_total": 0,
  "phantom_rejection_total": 3,
  "bureaucratic_loop_total": 5,
  "world_event_total": 12,
  "narrating_leakage_total": 1,
  "perform_approved_total": 4,
  "reputation_event_total": 7
}
```

### Сводный отчёт (`summary.json`)

`summary.json` не смешивает governance-оценку и fidelity-оценку, а хранит два независимых блока:

```json
{
  "governance": { "truth_total": 3, "true_positive": 2 },
  "fidelity": { "temporal_violations_total": 0, "bureaucratic_loop_total": 5 },
  "freeform_truth_total": 6
}
```

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
