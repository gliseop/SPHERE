# Дизайн runtime-auditor для SPHERE-LC

## Цель документа

Документ фиксирует технический дизайн отдельного runtime-аудитора для `sphere_lc`. Речь идёт не о post-hoc анализе и не о расширении существующего `ViolationOracle`, а о компоненте, который работает во время симуляции и влияет на мир как часть governance-режима.

Ключевое различие, на котором строится весь дизайн:

- `Arbiter` проверяет допустимость действий и переводит их в `StateOp`;
- `RuntimeAuditor` выявляет уже совершённые или формирующиеся нарушения и генерирует сигналы/вмешательства;
- `ViolationOracle` анализирует прогон после завершения и не влияет на мир.

Иными словами:

- `Arbiter != Auditor`
- `Auditor != Oracle`

## Текущее состояние кодовой базы

Сейчас в проекте уже есть:

- [arbiter.py](../src/sphere_lc/arbiter.py) — процессуальная проверка допустимости действий;
- [dao.py](../src/sphere_lc/dao.py) — механизм голосований;
- [ops.py](../src/sphere_lc/ops.py) — операции вроде `ModifyReputationOp`, `OpenVoteOp`;
- [oracle.py](../src/sphere_lc/oracle.py) — post-hoc анализ нарушений по `events.jsonl`.

Сейчас в проекте нет:

- отдельного runtime-аудитора как модуля или агента, который анализирует события в ходе симуляции и сам генерирует `audit_flagged` / `case_opened` / `risk_signal_emitted`.

Нарративный capability `audit` для обычных агентов больше не нужен; аудитный слой должен существовать отдельно от сюжетных агентов.

## Зачем нужен отдельный runtime-auditor

Если оставить только `Arbiter`, система умеет отвечать на вопросы вида:

- можно ли этому агенту голосовать?
- существует ли такой адресат?
- можно ли развернуть это `perform` в набор `StateOp`?

Но этого недостаточно для вопросов другого класса:

- проголосовал ли агент в пользу аффилированного лица?
- сформировался ли повторяющийся паттерн конфликта интересов?
- была ли передана чувствительная информация вне допустимого контура?
- является ли последовательность действий подозрительной даже при формальной допустимости каждого отдельного шага?

Эти вопросы не относятся к admissibility. Они относятся к detection. Поэтому нужен отдельный слой.

## Рекомендуемая архитектура

### Выбор: модуль, а не свободный агент

Рекомендуется реализовать runtime-аудитор в v1 как отдельный модуль, а не как обычного `AgentRunner`-агента.

### Почему именно так

Плюсы модульного аудитора:

- лучше воспроизводимость;
- проще контролировать входной контекст;
- легче жёстко ограничить схему выходных данных;
- проще тестировать отдельно от генеративного поведения обычных агентов;
- чище разделяются роли `agent behavior`, `admissibility`, `detection`, `evaluation`.

### Что делать с образом аудитора в мире

Сюжетный агент-аудитор не требуется.

Рекомендуемый вариант:

- не заводить обычного narrative-agent для аудита;
- использовать `actor_id=null` или служебный `agent:*`-ID только как метку audit-событий;
- всю detection-логику держать вне `AgentRunner`.

## Ответственность runtime-auditor

### Что он должен делать

- анализировать события текущего тика;
- использовать ограничённое окно предшествующей истории;
- находить сигналы риска и потенциальные нарушения;
- формировать структурированные findings;
- по policy-правилам инициировать вмешательства:
  - флаг;
  - открытие кейса;
  - заморозку репутации;
  - изменение репутации;
  - эскалацию в DAO или review-процесс.

### Чего он не должен делать

- не должен подменять `Arbiter`;
- не должен писать итоговый ground truth;
- не должен использовать `trace.jsonl` как источник истины;
- не должен быть единственным измерителем качества режима;
- не должен видеть больше данных, чем разрешено audit access policy.

## Граница между Arbiter, RuntimeAuditor и Oracle

### Arbiter

Отвечает на вопрос:

- допустимо ли это действие в текущем мире?

### RuntimeAuditor

Отвечает на вопрос:

- не сформировалось ли нарушение или значимый сигнал риска по уже совершённым действиям?

### Oracle

Отвечает на вопрос:

- что можно заключить о нарушениях по завершённому прогону при внешнем post-hoc анализе?

## Предлагаемый интерфейс модуля

### Новый модуль

Рекомендуемый файл:

- [auditor.py](../src/sphere_lc/auditor.py)

### Основной класс

```python
@dataclass(slots=True)
class RuntimeAuditor:
    llm: LLMCaller | None
    cfg: AuditRuntimeConfig

    async def inspect_tick(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
    ) -> AuditOutcome:
        ...
```

### Структура результата

```python
class AuditOutcome(BaseModel):
    findings: list[AuditFinding]
    events: list[Event]
    ops: list[StateOp]
```

### Finding-модель

```python
class AuditFinding(BaseModel):
    finding_id: str
    tick: int
    subject_agent_id: str
    violation_type: str
    violation_type_freeform: str = ""
    severity: str
    confidence: float
    evidence_refs: list[dict[str, object]]
    summary: str
    recommended_action: str
```

Ключевая идея:

- rules-layer сразу может выдавать канонические findings;
- LLM-layer должен прежде всего выдавать свободный риск-сигнал (`violation_type_freeform`, `summary`, `mechanism`), не будучи обязанным подбирать точный `violation_type`;
- преобразование freeform-findings в канонический `violation_type` и затем в реальные `Event` / `StateOp` делается детерминированно по policy-правилам.

## Предлагаемые конфигурации

### Новый конфиг

Рекомендуется добавить отдельную конфигурацию audit-слоя:

```python
class AuditRuntimeConfig(BaseModel):
    enabled: bool = False
    actor_id: str | None = None
    mode: Literal["rules", "hybrid", "llm"] = "hybrid"
    lookback_events: int = 120
    access_policy: Literal["metadata_only", "internal", "full_internal"] = "internal"
    min_confidence_to_flag: float = 0.6
    min_confidence_to_freeze: float = 0.8
    reputation_freeze_enabled: bool = True
    reputation_penalty_enabled: bool = False
    dao_escalation_enabled: bool = False
```

### Куда её положить

Два варианта:

1. `ScenarioConfig.audit`
2. `ScenarioConfig.governance.audit`

Рекомендуемый вариант:

- `governance.audit`

Причина:

- runtime-auditor является частью механизма управления, а не общей runtime-инфраструктуры.

## Источники данных для аудитора

### Что подавать на вход

Аудитор должен получать:

- `tick_events` текущего тика;
- ограниченное окно `recent_events`;
- компактный срез `WorldState` по внутренним агентам, open votes, work items и оргструктуре;
- опционально агрегированные признаки риска.

### Что не подавать на вход

- `trace.jsonl`;
- сырые prompts/responses других LLM-компонентов;
- служебную LLM-трассу арбитра;
- полный dump всего состояния без ограничения.

## Политика доступа аудитора

У аудитора не должно быть по умолчанию тотального знания обо всех приватных сообщениях.

### Рекомендуемые режимы

#### `metadata_only`

Аудитор видит:

- факт наличия приватной коммуникации;
- участников;
- время;
- канал;
- длину/тип, но не текст.

#### `internal`

Аудитор видит:

- публичные события;
- внутренние события;
- ограниченный доступ к частной информации по policy-правилам.

#### `full_internal`

Аудитор видит:

- весь internal слой, включая те private payloads, которые policy разрешает раскрывать для аудита.

### Рекомендуемое поведение для v1

Для стартовой версии лучше использовать:

- `metadata_only` или `internal`

Даже при `audit.mode="llm"` рекомендуется оставлять deterministic baseline rules, но только для структурных паттернов (`reputation_modified`, `vote_opened`, `vote_cast`, `pending_interaction_due/expired`). Text-based omission/corruption inference не должна жить в rules-layer и должна оставаться в LLM-first контуре.

## Рекомендуемые event types

### Detection layer

Новый audit-слой должен использовать собственные события, не смешанные с `arbiter_*`.

#### `audit_flagged`

Минимальный finding без немедленного санкционного действия.

Payload:

- `finding_id`
- `subject_agent_id`
- `violation_type`
- `severity`
- `confidence`
- `evidence_refs`
- `summary`

#### `audit_case_opened`

Формальное открытие стабильного кейса по finding. Кейс должен быть keyed не по `finding_id`, а по episode-key (`subject + violation_type + target + beneficiary`), чтобы повторные finding’и агрегировались.

Payload:

- `case_id`
- `finding_id`
- `subject_agent_id`
- `target_agent_id` / `counterparty_agent_id`
- `violation_type`
- `recommended_action`
- `episode_count`
- `response_due_tick`

#### `audit_case_updated`

Повторный episode или policy-state обновили существующий кейс.

Payload:

- `case_id`
- `finding_id`
- `subject_agent_id`
- `target_agent_id`
- `violation_type`
- `episode_count`
- `response_due_tick`
- `monitoring`
- `review_vote_id`

#### `audit_escalated`

Передача кейса в следующий слой рассмотрения.

Payload:

- `finding_id`
- `route`
- `target_agent_id`
- `reason`

#### `audit_case_closed`

Закрытие кейса.

Payload:

- `case_id`
- `result`
- `reason`

### Intervention layer

Тут рекомендуется переиспользовать существующие или почти существующие механизмы.

#### Уже существующее

- `reputation_modified`
- `vote_opened`
- `vote_closed`
- `position_changed`

#### Чего не хватает

Для согласования с теоретической моделью полезно добавить отдельную freeze-операцию:

- `SetReputationFreezeOp`

которая будет эмитить:

- `reputation_frozen`
- и, желательно, `reputation_unfrozen`

Сейчас в коде `graph_state` уже ожидает `reputation_frozen`, но отдельного op для этого нет.

## Где запускать аудитора в тиковом цикле

### Текущий порядок в `engine.py`

Сейчас укрупнённый порядок такой:

1. сбор действий агентов;
2. арбитраж и применение ops;
3. регистрация новых агентов;
4. закрытие DAO-голосований;
5. обновление памяти;
6. worldgen;
7. `journal.apply_events`;

### Рекомендуемый целевой порядок

Для аудитора лучше следующий порядок:

1. сбор действий агентов;
2. арбитраж и применение ops;
3. регистрация новых агентов;
4. закрытие DAO-голосований;
5. worldgen;
6. runtime-auditor;
7. `journal.apply_events`;
8. обновление памяти.

### Почему именно так

- аудитор должен видеть уже совершённые факты тика;
- он должен учитывать результаты DAO и worldgen того же тика;
- его сигналы и санкции должны попадать в журнал как часть итоговой истории тика;
- если память обновляется после аудитора, агенты получают единый feedback-loop по всем последствиям тика.

### Минимально инвазивный вариант

Если менять порядок цикла сейчас нежелательно, допустим переходный вариант:

1. оставить memory update как есть;
2. встроить аудитора после worldgen;
3. позволить audit-событиям попадать в память только со следующего тика.

## Как runtime-auditor взаимодействует с DAO

### Ограничение текущей реализации

Текущий DAO-слой заточен под `position_change` через `OpenVoteOp`.

Это означает:

- runtime-auditor не должен в v1 делать вид, что в системе уже есть полноценный `audit_case_review` vote type.

### Рекомендуемый v1

В первой версии:

- аудитор умеет flag/freeze/penalize/escalate;
- `dao_escalation_enabled=false` по умолчанию;
- DAO-эскалация включается только после расширения модели голосований.

## Как runtime-auditor взаимодействует с репутацией

### v1 policy

На старте лучше быть консервативным:

- finding средней уверенности даёт `audit_flagged`;
- finding высокой уверенности даёт `reputation_frozen`;
- прямой negative delta по репутации включать только если теоретическая модель это явно допускает.

### Почему freeze лучше penalty для старта

- он ближе к тексту ВКР;
- он мягче как интервенция;
- он проще интерпретируется;
- он меньше рискует разрушить динамику симуляции из-за шумной детекции.

## Как не дублировать Oracle

### RuntimeAuditor и Oracle должны различаться по четырём осям

#### 1. Время работы

- `RuntimeAuditor`: во время симуляции
- `Oracle`: после завершения прогона

#### 2. Цель

- `RuntimeAuditor`: вмешаться вовремя
- `Oracle`: измерить и проанализировать

#### 3. Горизонт наблюдения

- `RuntimeAuditor`: текущий тик + короткое окно истории
- `Oracle`: длинные окна или весь прогон

#### 4. Оптимизационный приоритет

- `RuntimeAuditor`: precision и low-latency
- `Oracle`: recall, coverage, interpretive analysis

### Практическое правило

`RuntimeAuditor` не должен пытаться быть полнотой истины. Он должен быть:

- быстрым;
- узким по таксономии;
- консервативным;
- пригодным для реального runtime-вмешательства.

`Oracle` не должен вмешиваться в мир. Он должен быть:

- внешним аналитиком;
- более широким по охвату;
- пригодным для post-hoc оценки и narrative analysis.

## Рекомендуемый режим работы аудитора

### `rules`

Используются только детерминированные правила.

### `hybrid`

Рекомендуемый основной режим:

- rules-layer формирует candidate findings;
- LLM подтверждает или уточняет findings;
- policy-layer детерминированно решает, какие events/ops выпускать.

### `llm`

Чистый LLM-режим возможен, но как основной для исследовательского стенда он хуже:

- больше drift;
- хуже воспроизводимость;
- сложнее защищать в ВКР.

## Поэтапный план внедрения

### Этап 1. Терминология и документация

Сделать явно:

- `Arbiter != RuntimeAuditor != Oracle`

Обновить:

- [simulation_engine.md](./simulation_engine.md)
- [architecture_guide.md](./architecture_guide.md)
- [data_formats.md](./data_formats.md)

### Этап 2. Ввести конфиг и модуль

Добавить:

- `AuditRuntimeConfig`
- `RuntimeAuditor`
- базовые модели findings/outcome

### Этап 3. Реализовать v1 event taxonomy

Минимум:

- `audit_flagged`
- `audit_case_opened`
- `audit_escalated`
- `audit_case_closed`

### Этап 4. Добавить freeze-op

Добавить:

- `SetReputationFreezeOp`

События:

- `reputation_frozen`
- `reputation_unfrozen`

### Этап 5. Встроить аудитора в engine tick

Предпочтительно:

- после worldgen;
- до `journal.apply_events`;
- до обновления памяти, если цикл будет слегка перестроен.

### Этап 6. Развести runtime metrics и post-hoc evaluation

Runtime-аудитор пишет:

- signals;
- interventions.

Post-hoc evaluator считает:

- true positives;
- false positives;
- false negatives;
- prevented violations;
- detection delay.

## Минимальный MVP

Если делать самый короткий маршрут без чрезмерной переделки ядра, то MVP выглядит так:

1. добавить `RuntimeAuditor` как модуль;
2. добавить `AuditRuntimeConfig`;
3. запускать аудитора после worldgen;
4. поддержать только `audit_flagged` и `reputation_frozen`;
5. не трогать DAO-эскалацию до расширения vote model;
6. oracle оставить как внешний post-hoc слой.

Это уже даст системе реальный runtime-audit слой без смешения с `Arbiter`.

## Итоговое решение

Для SPHERE-LC рекомендуется следующая схема:

- `Arbiter` остаётся процессуальным слоем допустимости действий;
- `RuntimeAuditor` добавляется как отдельный модуль online-detection;
- `DAO` остаётся слоем коллективного рассмотрения;
- `Oracle` остаётся внешним post-hoc анализатором;
- итоговые метрики качества режима считаются вне мира.

Это решение лучше всего соответствует:

- текущей архитектуре кода;
- теоретической логике ВКР;
- требованию не смешивать intervention и measurement;
- и задаче построить действительно исследовательский, а не самозамкнутый симулятор.
