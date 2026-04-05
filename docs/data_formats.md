# Форматы данных

Описание форматов данных, используемых в SPHERE: сценарии, типы агентов, архетипы личности, интервью и результаты.

## Сценарии (`scenarios/`)

YAML-файлы с полной конфигурацией сценария. Корневая модель — `ScenarioConfig` (Pydantic).

```yaml
version: 1
title: "LC Minimal"
description: "Минимальный пример сценария."
ticks: 3

llm:
  model: "nvidia/nemotron-3-super-120b-a12b"
  base_url: null
  provider_order: []  # опциональный override; обычно OpenRouter routing задают через OPENROUTER_PROVIDER_ORDER в .env
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
  micro_reaction_rounds: 0
  micro_reaction_max_agents_per_round: 6
  pending_interaction_horizon_ticks: 2
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
  agent_prompt:
    extra_rules:
      - "Если хочешь написать конкретному участнику, не оформляй этот шаг как публикацию в канале."

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
    actor_id: null
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
    org_id: "org:admin"
    zone_id: "zone:main_office"
    initial_reputation: 4.5
    initial_title: "специалист"
    wants_promotion: true

world:
  channels:
    - channel_id: "chan:public"
      title: "Публичный канал"
  orgs:
    - org_id: "org:admin"
      title: "Администрация района"
  work_items:
    - work_id: "work:D-001"
      work_type: "hiring_process"
      title: "Найм в отдел"
      description: "Конкурс на вакансию."
      participants: ["agent:off_1"]
  artifacts:
    - artifact_id: "art:repair_report"
      artifact_type: "report"
      title: "Отчёт по ремонту"
      summary: "Базовый внутренний отчёт по объекту."
      owner_org_id: "org:admin"
      zone_id: "zone:main_office"
      related_work_id: "work:D-001"
      visibility: "internal"
      status: "active"
      tags: ["repair", "report"]
  environment:
    institution_modes:
      - org_id: "org:admin"
        operating_mode: "strained"
        transparency_mode: "limited"
        access_mode: "restricted"
        security_mode: "heightened"
        capture_risk: "medium"
        linked_zone_ids: ["zone:main_office"]
    zones:
      - zone_id: "zone:main_office"
        title: "Главный корпус администрации"
        zone_type: "office"
        primary_org_id: "org:admin"
        access_mode: "controlled"
        transparency_mode: "internal"
        security_level: "heightened"
    resource_pools:
      - resource_id: "res:roads_budget"
        title: "Бюджет дорожного ремонта"
        owner_org_id: "org:admin"
        quantity: 1250
        unit: "тыс. руб."
        status: "strained"
        pressure: "Сроки поджимают."
    information_climate:
      public_mood: "Раздражение из-за задержек."
      oversight_attention: "Повышенное."
      media_pressure: "Локальные медиа ищут тему."
      narrative_temperature: "Напряжённая повестка."
      active_signals: ["жалобы на ремонт"]
```

### Блоки конфигурации

| Блок | Описание |
|---|---|
| `llm` | Модель, API, провайдер, температура |
| `runtime` | Язык, лимит действий за ход, генератор мира, обогащение персон |
| `governance` | Политика должностей, кворум, порог, голосование и runtime-аудит |
| `memory` | Рабочий буфер, долгосрочный индекс, веса retrieval, эмбеддинги |
| `agents` | Список агентов с ID, именем, персоной, полномочиями |
| `world` | Каналы, организации, рабочие элементы, `artifacts` и стартовый `environment`-слой |

Поле `agents[].initial_reputation` задаёт стартовую репутацию внутреннего агента. Движок применяет её при инициализации `WorldState`, а первый `reputation_snapshot` в `events.jsonl` отражает именно это значение.

Поля `agents[].org_id` и `agents[].zone_id` опционально привязывают агента к организации и/или зоне среды. Если они заданы, движок может:

- включать релевантный environment-brief в prompt агента;
- активировать периферийного агента при изменениях связанной организации, зоны или принадлежащего ей ресурсного пула;
- подавать агенту локальные pending-follow-up обязательства, если они адресованы этому агенту.

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
| `micro_reaction_rounds` | `int` | Сколько локальных reaction windows движок запускает внутри одного тика после основного батча действий |
| `micro_reaction_max_agents_per_round` | `int` | Верхняя граница числа агентов в одном локальном окне реакции |
| `pending_interaction_horizon_ticks` | `int` | На сколько тиков вперёд живёт локальное ожидающее взаимодействие (`pending_interaction`) после того, как стало актуальным |
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
| `allow_runtime_spawn` | `bool` | Разрешить legacy/runtime-spawn и worldgen-spawn в ходе симуляции; основной когнитивный интерфейс агента при этом остаётся freeform |
| `request_entity_internal_only` | `bool` | При `true` только внутренние акторы могут создавать `chan:*`/`org:*` через `request_entity` |
| `ecology_activation_window_ticks` | `int` | Если `> 0`, не-core ecology-акторы ходят только при недавней релевантности |
| `worldgen_allow_internal_spawns` | `bool` | Разрешить worldgen порождать внутренних акторов; по умолчанию выключено |
| `worldgen_context_scope` | `core` / `all` | Для каких акторов pre-worldgen строит personal contexts (`core` по умолчанию) |
| `freeform_truth_enabled` | `bool` | Включить post-hoc `truth_freeform.jsonl` |
| `freeform_truth_window_ticks` | `int` | Размер окна событий для `FreeformTruthRecorder` |
| `agent_prompt` | `object` | Декларативные prompt-guardrails для когнитивного агента: дополнительные правила адресации, хорошие/плохие примеры и scenario-specific подсказки |

`ScenarioConfig` больше не содержит `seed` и `scripted_events`. Повторяемость authoring-конфига обеспечивается детерминированным runtime-порядком, а внешний фон моделируется через `worldgen`, `world.artifacts`, `information_climate` и реальные действия агентов.

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

### Runtime pending-follow-up queue

`WorldState.pending_interactions` не задаётся напрямую в сценарии, но является частью runtime-состояния мира.

Это first-class очередь коротких локальных обязательств, которые могут переживать несколько тиков:

- ответ на приватное сообщение;
- follow-up по документу или артефакту;
- реакция на ресурсное давление;
- ответ на audit-запрос.

Каждый элемент такой очереди хранит:

- `interaction_id`;
- `target_agent_id`;
- `source_agent_id`;
- `category`;
- `summary`;
- `created_tick`;
- `earliest_tick`;
- `due_tick`;
- `priority`;
- `status`.

Движок может:

- детерминированно создавать и обновлять `pending_interaction_created` / `pending_interaction_updated`;
- эмитить `pending_interaction_due`, когда локальное обязательство доходит до адресата;
- закрывать его через `pending_interaction_completed` или `pending_interaction_expired`.

Если `runtime.micro_reaction_rounds > 0`, часть локальных категорий (`reply`, `artifact_follow_up`, `resource_pressure`) может переходить в `pending_interaction_due` уже в том же тике и закрываться через same-tick follow-up без ожидания следующего глобального шага.

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
| Зона | `zone:` | `zone:main_office` |
| Ресурсный пул | `res:` | `res:roads_budget` |
| Документ / артефакт | `art:` | `art:repair_report` |

### `world.artifacts`

`WorldConfig.artifacts` задаёт стартовые документы и артефакты мира как first-class сущности.

| Поле | Тип | Назначение |
|---|---|---|
| `artifact_id` | `art:*` | Typed-id артефакта |
| `artifact_type` | `str` | Тип (`report`, `memo`, `request`, `leak`, `publication` и т.п.) |
| `title` | `str` | Заголовок |
| `summary` | `str` | Краткое содержание |
| `owner_org_id` | `org:* \| null` | Какая организация владеет артефактом |
| `zone_id` | `zone:* \| null` | Какая зона связана с артефактом |
| `related_work_id` | `work:* \| null` | Какое дело связано с артефактом |
| `visibility` | `public` / `internal` | Видимость артефакта |
| `status` | `str` | Текущее состояние (`active`, `revised`, `archived`, `new`) |
| `tags` | `list[str]` | Короткие смысловые метки |

Post-worldgen теперь также может возвращать:

- `artifact_creations` — создание новых `art:*` сущностей;
- `artifact_updates` — обновление уже существующих артефактов.

Для обратной совместимости legacy `artifact:*` в post-worldgen нормализуется движком в канонический `art:*`.

### `world.environment.informal_links`

`WorldConfig.environment.informal_links` задаёт стартовые неформальные связи между агентами.

| Поле | Тип | Назначение |
|---|---|---|
| `agent_a_id` | `agent:*` | Первый участник связи |
| `agent_b_id` | `agent:*` | Второй участник связи |
| `link_type` | `str` | Тип связи (`private_contact`, `coordination`, `favor`, `kinship`, `dependency` и т.п.) |
| `strength` | `float` | Сила связи в диапазоне `[0, 1]` |
| `visibility` | `str` | Насколько связь явная или латентная |
| `pressure` | `str` | Какое неформальное давление или ожидание она несёт |
| `source` | `str` | Источник (`configured`, `worldgen`, `interaction`) |

Кроме конфигурации и worldgen, движок теперь может детерминированно усиливать такие связи по факту взаимодействий, например после приватных сообщений и координации по общему делу.

### `world.environment`

`WorldConfig.environment` задаёт стартовый stateful environment layer поверх обычной оргструктуры.

Поддерживаются следующие блоки:

| Поле | Тип | Назначение |
|---|---|---|
| `institution_modes` | `InstitutionRegimeConfig[]` | Операционные режимы организаций (`org:*`) |
| `zones` | `ZoneConfig[]` | Зоны/территории среды с собственными режимами доступа и прозрачности |
| `resource_pools` | `ResourcePoolConfig[]` | Ресурсные контуры с количеством, владельцем и текущим давлением |
| `information_climate` | `InformationClimateConfig` | Глобальный информационный фон мира |
| `informal_links` | `InformalLinkConfig[]` | Стартовые неформальные связи и зависимости между агентами |
| `population_blueprints` | `PopulationBlueprintConfig[]` | Шаблоны для систематического наращивания периферийной агентности вокруг `org:*` / `zone:*` |

Этот слой на текущем этапе:

- инициализируется в `WorldState.environment`;
- отражается в YAML-журнале мира;
- попадает в `state_snapshot`, который видит worldgen;
- не заменяет собой `orgs`/`work_items`, а существует параллельно им.

Post-worldgen может дополнительно вернуть `environment_updates`, которые движок применяет детерминированно к `org:*`, `zone:*` и `res:*`. На текущем этапе поддерживаются:

- обновление режимов организаций;
- обновление режимов зон;
- обновление ресурсных пулов;
- обновление глобального информационного климата.

### `world.environment.population_blueprints`

`PopulationBlueprintConfig` позволяет не вручную перечислять всех периферийных акторов, а задавать шаблоны плотности среды вокруг организации или зоны.

| Поле | Тип | Назначение |
|---|---|---|
| `blueprint_id` | `str` | Стабильный ID шаблона |
| `role_label` | `str` | Человеко-читаемая роль для новых акторов |
| `persona_hint` | `str` | Базовая подсказка для персоны |
| `desired_count` | `int` | Сколько акторов этого класса мир старается поддерживать |
| `activation` | `bootstrap` / `environment_change` | Когда включается шаблон |
| `internal` | `bool` | Внутренний ли это актор |
| `capabilities` | `list[str]` | Разрешённые capabilities |
| `org_id` | `org:* \| null` | К какой организации привязан актор |
| `zone_id` | `zone:* \| null` | К какой зоне привязан актор |
| `name_pool` | `list[str]` | Опциональный пул имён для новых акторов |

На текущем этапе движок поддерживает два режима:

- `bootstrap` — заполнить population-сегмент сразу при инициализации мира;
- `environment_change` — дорастить population-сегмент после значимых средовых сдвигов.

### Полномочия агентов

| Полномочие | Где реально используется |
|---|---|
| `message` | legacy/internal typed-ops и совместимость старых сценариев; базовая коммуникация больше не должна рассматриваться как отдельный capability-gate |
| `work` | внутренние work-операции арбитра (`create_work_item`, `add_work_note`, `submit_work_proposal`) |
| `dao` | внутренние DAO-операции арбитра (`nominate_position_change`, `cast_vote`) |
| `spawn` | legacy/runtime `spawn_agent` для совместимости и системных сценариев |

Нарративные агенты с capability `audit` больше не поддерживаются. Аудит выполняется отдельным runtime-layer (`RuntimeAuditor`), а не обычным `AgentRunner`.

Когнитивный агент в нормальном режиме больше не выбирает typed action из меню. Он возвращает один свободный `proposal` на тик, а арбитр уже материализует это намерение во внутренние typed operations.

Вторичные и runtime-спавненные агенты проходят фильтрацию capability-набора: движок оставляет только безопасный поднабор `message`/`work`, чтобы новые агенты не получали специальных governance-полномочий или право порождать следующих агентов.

Runtime-аудит реализован отдельным модулем `auditor.py` и не сводится к обычному `AgentRunner`. `governance.audit.actor_id` может быть `null` или служебным `agent:*`-ID для маркировки audit-событий, но такой ID не обязан соответствовать сюжетному агенту.

Для `spawn_agent`, secondary-spawn и worldgen-spawn действует дополнительное правило: новый агент должен иметь человеко-читаемое имя, а не `agent:*`, slug или абстрактную должность. Role-alias ссылки либо переиспользуют уже существующего актора, либо отклоняются.

Если новый агент порождается через runtime/worldgen и для него заданы `org_id` и/или `zone_id`, движок сохраняет эту привязку в `AgentState`. Это позволяет:

- сразу встроить нового актора в локальную среду;
- показывать ему релевантный environment-brief;
- будить его по изменениям связанной организации, зоны, артефактов и ресурсных пулов.

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
| `actor_id` | `agent:* \| null` | Какой служебный agent ID использовать как `actor_id` в audit-событиях; не обязан соответствовать narrative-агенту |
| `mode` | `llm` / `hybrid` / `rules` | Режим detection; `llm` — основной путь, `rules` — fallback/debug |
| `lookback_events` | `int` | Глубина окна recent events |
| `private_contact_window_ticks` | `int` | Окно приватных контактов для conflict-like правил |
| `obligation_window_ticks` | `int` | Окно pressure/obligation-эвристик для omission-like нарушений |
| `response_window_ticks` | `int` | Сколько тиков даётся на объяснение/документы до follow-up escalation |
| `max_findings_per_tick` | `int` | Лимит findings на тик |
| `access_policy` | `metadata_only` / `internal` / `full_internal` | Какой объём private/internal данных раскрывается LLM-аудитору |
| `min_confidence_to_flag` | `float` | Порог эмиссии `audit_flagged` |
| `min_confidence_to_open_case` | `float` | Порог открытия audit-case |
| `min_confidence_to_freeze` | `float` | Порог заморозки репутации |
| `min_confidence_to_review` | `float` | Порог маршрутизации в collegial review |
| `freeze_duration_ticks` | `int` | Длительность заморозки репутации в тиках |
| `case_repeat_escalation_threshold` | `int` | После скольких эпизодов повторяющийся кейс автоматически уходит в review/monitoring |
| `external_subject_confidence_cap` | `float` | Верхняя граница confidence для внешних субъектов finding’ов |
| `reputation_freeze_enabled` | `bool` | Разрешить `SetReputationFreezeOp` |
| `reputation_penalty_delta` | `float \| null` | Legacy/custom поле; built-in `G0–G3` больше не используют штрафную семантику |
| `collegial_review_enabled` | `bool` | Разрешить route в `audit_review` |
| `review_jury_size` | `int` | Размер review-jury для collegial review |

### `runtime.agent_prompt`

`runtime.agent_prompt` позволяет менять prompt-layer без правки `agent.py`.

| Поле | Тип | Назначение |
|---|---|---|
| `addressing_hint` | `str` | Базовый контракт адресации: как различать личное сообщение и публичный пост |
| `public_message_hint` | `str` | Scenario-specific уточнение для публикаций в `chan:*` / `org:*` |
| `private_message_hint` | `str` | Scenario-specific уточнение для приватных сообщений между агентами |
| `extra_rules` | `list[str]` | Дополнительные правила, которые подмешиваются в prompt каждого агента |
| `extra_good_examples` | `list[str]` | Сценарные «хорошие» примеры materializable `proposal` |
| `extra_bad_examples` | `list[str]` | Сценарные «плохие» примеры, которые prompt явно запрещает |

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

Кэш используется только при полном совпадении fingerprint входов. Кроме локального `{out_dir}/personas.json`, движок теперь пишет и persistent fingerprint-cache рядом с директориями прогонов, чтобы одинаковые repeated runs могли переиспользовать enrichment между разными `out_dir`.

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
{"tick": 2, "round": 2, "event_type": "message_sent", "actor_id": "agent:off_1", "agent_id": "agent:off_1", "payload": {"to_id": "agent:off_2", "text": "..."}, "audience": ["agent:off_1", "agent:off_2"], "timestamp": "..."}
{"tick": 2, "round": 2, "event_type": "audit_flagged", "actor_id": null, "agent_id": "", "payload": {"finding_id": "finding:abc", "case_id": "audit_case:abc", "subject_agent_id": "agent:off_1", "target_agent_id": "agent:off_2", "counterparty_agent_id": "agent:off_2", "related_target_agent_id": "agent:off_2", "violation_type": "support_vote_after_private_contact", "violation_type_freeform": "координация перед голосованием после приватного контакта", "evidence_refs": [{"tick": 2, "event_type": "vote_cast", "target_agent_id": "agent:off_2"}]}, "audience": ["aud:internal"], "timestamp": "..."}
{"tick": 2, "round": 2, "event_type": "audit_case_updated", "actor_id": null, "agent_id": "", "payload": {"case_id": "audit_case:abc", "subject_agent_id": "agent:off_1", "target_agent_id": "agent:off_2", "violation_type": "support_vote_after_private_contact", "episode_count": 2, "response_due_tick": 4, "monitoring": true}, "audience": ["aud:internal"], "timestamp": "..."}
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
| `audit_case_updated` | Повторный эпизод или policy-state обновили существующий audit-case |
| `audit_escalated` | Кейc эскалирован в санкционный/процедурный слой |
| `audit_explanation_requested` | Аудит запросил объяснение по открытому кейсу |
| `audit_documents_requested` | Аудит запросил документы/артефакты по кейсу |
| `audit_monitoring_enabled` | Для кейса включено усиленное наблюдение |
| `audit_case_closed` | Audit-case закрыт |
| `review_case_opened` | Открыто collegial review по audit-case |
| `review_case_closed` | Collegial review закрыто |
| `audit_runtime_error` | Ошибка runtime-аудитора на тике |
| `arbiter_approved` | Арбитр одобрил действие |
| `arbiter_rejected` | Арбитр отклонил действие |
| `arbiter_op_failed` | Операция не удалась (ошибка apply) |
| `world_event` | Внешнее событие от WorldGenerator |

Для `audit_flagged` и связанных audit-case событий актуален двухслойный контракт:

- `violation_type` — внутренний канонический тип, который нужен для строгого matching, case aggregation и policy-layer;
- `violation_type_freeform` — свободная формулировка LLM-сигнала, если runtime-аудитор сначала увидел риск содержательно, а не через готовый taxonomy label.

### Трассировка LLM (trace.jsonl)

Отдельный журнал всех LLM-вызовов: промпт, ответ, длительность. Хранится в отдельном файле, чтобы не утекать в контекст симуляции.

### Sidecar-файлы directory-run

Для `results/{run_name}/` современный LC-движок пишет дополнительные JSON-файлы:

- `scenario.json` — сериализованный `ScenarioConfig` конкретного прогона.
- `names.json` — отображение `agent_id -> display name`, используемое web UI и WebSocket `meta`.
- `status.json` — heartbeat-статус прогона (`running` / `finished` / `failed`) с `updated_at`, `pid` и последним tick.
- `summary.json` — итоговая агрегированная сводка (`governance` + `fidelity`).
- `perf_summary.json` — агрегированные performance-метрики: суммарные токены, LLM-duration, overlap, `p50/p95/max`, slowest calls, timeout/error counters, разрез по фазам, по локальным embedding-фазам и по тикам.
- `environment_summary.json` — финальный компактный срез среды и pending-interactions.
- `environment_timeline.jsonl` — покадровая телеметрия среды по тикам.

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
  "agent:off_1": "Козлов И.М."
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

```json
// environment_summary.json
{
  "tick": 2,
  "environment": {
    "counts": {"institutions": 1, "zones": 1, "resource_pools": 1, "informal_links": 2}
  },
  "pending_interactions": {"open": 1, "completed": 3, "expired": 0},
  "spawn_sources": {"scenario": 2, "population_blueprint": 2}
}
```

```json
// perf_summary.json
{
  "overall": {
    "prompt_tokens": 262040,
    "completion_tokens": 51438,
    "total_tokens": 313478,
    "sum_llm_duration_s": 683.948,
    "trace_span_s": 305.975,
    "overlap_ratio": 2.235,
    "retries_used_total": 0,
    "timeout_count": 0
  },
  "by_phase": {
    "agent": {"calls": 14, "total_tokens": 123930, "p95_duration_ms": 15177.2},
    "memory": {"calls": 6, "total_tokens": 22217, "p95_duration_ms": 26103.9},
    "auditor": {"calls": 3, "total_tokens": 72249, "p95_duration_ms": 47366.8}
  },
  "by_local_phase": {
    "embeddings_query": {"calls": 14, "duration_s": 15.2},
    "embeddings_memory": {"calls": 6, "duration_s": 3.7}
  },
  "by_tick": {
    "0": {"calls": 21, "total_tokens": 81686},
    "1": {"calls": 10, "total_tokens": 108234}
  },
  "slowest_calls": [
    {"phase": "auditor", "tick": 7, "duration_ms": 58882.0}
  ]
}
```

### Truth-layer (`truth.jsonl`)

Отдельный deterministic sidecar с каноническими truth-records для post-hoc оценки runtime-аудита.

Пример записи:

```json
{
  "tick": 2,
  "subject_agent_id": "agent:off_1",
  "violation_type": "self_reputation_award",
  "status": "committed",
  "severity": "high",
  "confidence": 1.0,
  "target_agent_id": "agent:off_1",
  "evidence_refs": [{"tick": 2, "event_type": "reputation_modified", "timestamp": "2026-03-05T10:31:12.345678+00:00"}],
  "rationale": "Агент повысил собственную репутацию."
}
```

Ключевая особенность:

- `truth.jsonl` не является продолжением `events.jsonl`;
- он пишется отдельно и не подаётся агентам;
- он используется для формального post-hoc сравнения governance-treatment и truth-layer.
- при дедупликации учитываются не только `tick`/`subject_agent_id`/`violation_type`, но и цель/контекст (`target_agent_id`, `evidence_refs`, включая `timestamp` исходного события), чтобы несколько однотипных нарушений в один тик не схлопывались в один кейс.

Deterministic truth-layer после удаления lexical/keyword-эвристик фиксирует только структурные паттерны:

- `self_reputation_award`;
- `reputation_reward_after_private_contact`;
- `self_nomination`;
- `nomination_after_private_contact`;
- `support_vote_after_private_contact`;

`truth.jsonl` постепенно смещается в сторону unified finding contract:

- `summary`;
- `mechanism`;
- `beneficiary`;
- `risk_tags`;
- `evidence_refs`.

### Freeform truth (`truth_freeform.jsonl`)

Опциональный LLM-sidecar, который пишет richer truth в свободной форме по схеме.

Он включается через `runtime.freeform_truth_enabled=true` и не заменяет deterministic `truth.jsonl`.

Если `truth_freeform.jsonl` присутствует, post-hoc evaluation использует его как источник semantic/case truth для `evaluation.json`, не подменяя strict baseline на `truth.jsonl`.

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
  "risk_tags": ["pressure_not_to_escalate", "non_disclosure"],
  "severity": "medium",
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
  "freeform_truth_total": 5,
  "runtime_flagged_total": 4,
  "true_positive": 2,
  "false_positive": 2,
  "false_negative": 1,
  "precision": 0.5,
  "recall": 0.6667,
  "f1": 0.5714,
  "semantic_truth_source": "truth_freeform",
  "semantic_truth_total": 5,
  "semantic_true_positive": 3,
  "semantic_false_positive": 1,
  "semantic_false_negative": 0,
  "semantic_precision": 0.75,
  "semantic_recall": 1.0,
  "semantic_f1": 0.8571,
  "case_truth_source": "truth_freeform",
  "case_truth_total": 3,
  "case_true_positive": 2,
  "case_false_positive": 0,
  "case_false_negative": 1,
  "case_precision": 1.0,
  "case_recall": 0.6667,
  "case_f1": 0.8,
  "by_violation_type": {
    "self_reputation_award": {"truth": 1, "signals": 1, "tp": 1, "fp": 0, "fn": 0}
  }
}
```

При сопоставлении runtime-сигналов с truth-layer strict baseline всегда считается по deterministic `truth.jsonl`: он нормализует `evidence_refs` до core-signature и меньше зависит от шумовых полей внутри evidence. Если рядом присутствует `truth_freeform.jsonl`, semantic/case метрики берут truth именно оттуда; иначе они fallback’ятся на deterministic truth. Поверх strict baseline дополнительно считаются:

- semantic matching:

- совпадение subject;
- temporal proximity;
- overlap по `target_agent_id` / `beneficiary`;
- overlap по `evidence_refs`;
- overlap по `risk_tags`;
- similarity `summary + mechanism`.

- case-level matching:

- схлопывание повторяющихся episode-level finding’ов в кейс по `subject + violation_type + counterparty`;
- отдельные `case_*` метрики для более устойчивой governance-оценки, когда один и тот же кейс даёт несколько близких runtime/truth-эпизодов.

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
