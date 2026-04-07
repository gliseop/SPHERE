# Движок симуляции

Техническая документация движка `sphere_lc`: жизненный цикл, агент, арбитр, память и подсистема LLM.

## Жизненный цикл симуляции

```mermaid
flowchart TD
    CFG[ScenarioConfig YAML/JSON] --> INIT[Инициализация WorldState]
    INIT --> REG[EntityRegistry: регистрация агентов, каналов, организаций, work items]
    REG --> ENRICH{runtime.enrich_personas?}
    ENRICH -->|да| ENRICHRUN[PersonaGenerator: runtime enrichment + personas.json cache]
    ENRICHRUN --> SOCIAL{runtime.spawn_secondary?}
    ENRICH -->|нет| SOCIAL
    SOCIAL -->|да| SG[SocialGraphExtractor + secondary agents]
    SOCIAL -->|нет| BOOT
    SG --> BOOT[Bootstrap persona/interview в долгосрочную память + создание runners]
    BOOT --> TICK[Тик N]

    TICK --> THAW[Снять истёкшие заморозки репутации]
    THAW --> PREW{pre-tick worldgen включён?}
    PREW -->|да| PRECTX[WorldGenerator pre: global events + agent_contexts + scene_hooks]
    PREW -->|нет| SHUFFLE
    PRECTX --> SHUFFLE[Перемешать порядок агентов]
    SHUFFLE --> DECIDE[AgentRunner.decide: мотивационный промпт + context-layer → Action JSON]
    DECIDE --> ARBITER[Arbiter: валидация + перевод в StateOp]
    ARBITER --> APPLY[ops.apply: StateOp → Event]
    APPLY --> REGNEW[Регистрация новых runners]

    REGNEW --> DAO{Есть открытые голосования?}
    DAO -->|да| CLOSE[DaoEngine: проверка кворума, закрытие]
    DAO -->|нет| WGEN
    CLOSE --> WGEN

    WGEN{Генератор мира включён?}
    WGEN -->|да| WGEVT[WorldGenerator: события + spawn suggestions]
    WGEN -->|нет| AUDIT

    WGEVT --> TRUTH[TruthDetector: truth.jsonl]
    TRUTH --> AUDIT[RuntimeAuditor: detection + audit events + ops]
    AUDIT --> LOG[EventLog: запись в JSONL]
    LOG --> MEM[Обновление памяти агента]

    MEM --> NEXT{Ещё тики?}
    NEXT -->|да| TICK
    NEXT -->|нет| EVAL[Governance eval + fidelity sidecars]
    EVAL --> RESULT[Финал: WorldState + events.jsonl + truth.jsonl + trace.jsonl + status.json + evaluation.json + fidelity.json + summary.json + perf_summary.json + environment_summary.json + environment_timeline.jsonl]
```

Симуляция начинается с конфигурации сценария (`ScenarioConfig`), определяющей агентов, полномочия, каналы, организации, рабочие элементы, стартовый `environment`-слой и параметры управления. `WorldEngine` инициализирует `WorldState`, регистрирует все сущности в `EntityRegistry`, материализует `world.environment` как отдельный слой состояния среды и запускает цикл тиков. По завершении прогона он пишет не только JSON/JSONL sidecars, но и `world_history.md` — человекочитаемую историю мира с полной хроникой событий и всем LLM trace.

## Агент (AgentRunner)

`AgentRunner` реализует модель «один LLM-вызов на ход». Тексты всех system/user prompt templates и вспомогательных wording-блоков теперь хранятся централизованно в `src/sphere_lc/prompts.yaml` и подгружаются через `sphere_lc.prompts`.

На каждом тике агент получает:
- системный промпт с diegetic framing: он живёт внутри обычного рабочего дня, не видит мета-фрейм «ты в симуляции» и не должен говорить как внешний аналитик;
- пользовательский промпт с текущей ситуацией: сегодняшняя дата/время (если заданы через `runtime.start_date`), мотивационный блок (`цели/страхи/обязательства/выгоды/угрозы`), личность, должность, служебные обозначения людей/дел/каналов/организаций, открытые процедуры, релевантные воспоминания и последние события.

Если у агента есть полноценная `PersonaArtifact`, краткий мотивационный блок теперь собирается не из случайной смеси summary/биографии/story-state, а из отдельного interview-grounded `motivation`-digest (`goal`, `fear`, `obligation`, `gain`, `pressure`, `threat`). Этот digest строится runtime-enrichment'ом из интервью и expert reflections и служит только компактным prompt-layer представлением уже извлечённой личности, а не её заменой.

Если у агента заданы `org_id` и/или `zone_id`, движок дополнительно подаёт краткий релевантный environment-brief:

- режим организации;
- режим зоны;
- связанные ресурсные пулы организации;
- текущий информационный климат.

Отдельно агент получает spatial-brief: каких участников обычно можно найти в каких зонах прямо сейчас, и какие площадки вообще существуют в мире. Это нужно, чтобы агент мог строить правдоподобные многошаговые ходы вида «сначала уточню, где сидит начальник, потом приду в нужный кабинет и уже там поговорю лично», а не только слепо пытаться отправить private contact через несовпадающие зоны.

Если в мире есть релевантные `art:*`-артефакты (по `org_id`, `zone_id` или связанному `work item`), агент дополнительно видит краткий список документов и следов, относящихся к его локальной среде.

Если в `environment.informal_links` есть связи, в которых участвует агент, движок также подаёт краткий informal-links brief: тип связи, силу, видимость, источник и возможное давление.

Если агенту адресованы открытые `pending_interactions`, он дополнительно видит краткий блок локальных обязательств и ожидающих follow-up: кто инициировал ожидание, какого оно типа, в каком временном окне живёт и к какому `artifact` / `work` / `org` оно привязано.

Если мир порождает периферийных акторов через `population_blueprints`, они сразу получают привязку к `org_id` / `zone_id` и потому начинают видеть релевантный environment-brief и документарную поверхность своей локальной среды.

Помимо списка ID, агент видит краткий shortlist существующих дел (`work_id + title + status`) и блок недавних отклонённых действий/ID. Это уменьшает вероятность phantom-ссылок на несуществующие `work_id` и повторного создания уже существующих задач.

Если включён `runtime.worldgen_pre_tick`, агент дополнительно получает prompt-layer контекст начала дня:
- `agent_daily_context` с личным давлением, социальной пересечкой и `today_hook`;
- `scene_hooks` как необязательные поводы к встречам и разговорам;
- `story_state` — короткую внутреннюю линию агента, которую движок обновляет детерминированно по persona + наблюдаемым событиям.

В более риск-ориентированном режиме `agent_daily_context` может дополнительно включать:
- `private_pressure` — что тянет агента к удобному, но спорному закрытому решению;
- `opportunity` — какую практическую выгоду даёт серый shortcut;
- `exposure_risk` — чем это грозит при раскрытии.

Когнитивный агент возвращает один свободный `reply` на ход: краткое естественное описание ближайшего намерения изнутри своей роли. Typed actions сохраняются внутри runtime как внутренний слой арбитра и `StateOp`, но не как меню, из которого агент должен выбирать. Agent prompt дополнительно содержит contrastive examples: какой ход звучит по-человечески и materializable, а какой остаётся пустой декларацией без наблюдаемого шага мира. Эти examples также condition-ятся по доступным полномочиям: агент без рабочего доступа не получает шаблоны self-work-операций, а цель открытой номинации без права голоса получает шаблон ответа на `vote`, а не открытия нового голосования.

Этап `propose_actions` может идти как последовательно, так и параллельно. Это задаётся через `runtime.parallel_agents`; при включённом режиме `runtime.parallel_workers` ограничивает число одновременных LLM-вызовов. Применение результатов к `WorldState` всё равно остаётся последовательным и детерминированным.

Поверх основного батча действий движок теперь может запускать локальные reaction windows внутри того же тика (`runtime.micro_reaction_rounds`). Они выбирают ограниченный набор агентов, затронутых событиями текущего тика (например, приватным сообщением, `scene_occurred`, `environment_*_updated`) и дают им короткую реакцию до перехода к следующему глобальному тику. Это не отменяет общий tick-engine, но делает мир менее жёстко синхронным.

Дополнительно движок поддерживает first-class очередь `pending_interactions`: короткие локальные обязательства, переживающие тик и доходящие до адресата как `pending_interaction_due`. Эта очередь пополняется детерминированно из самих событий мира (например, private message, документарный follow-up, ресурсное давление, audit-запрос), помогает будить периферию уже после выпадения исходного события из обычного activation-window и частично ослабляет жёсткость глобального тика без отказа от детерминированного apply.

Если включены `runtime.micro_reaction_rounds`, часть локальных категорий `pending_interactions` теперь может доезжать до `pending_interaction_due` уже в том же тике. После основного apply движок делает same-tick follow-up sweep и даёт адресатам короткое окно закрыть reply / queue / artifact-follow-up без обязательного ожидания следующего глобального шага.

Материальный слой среды в актуальной модели больше не включает отдельный queue-layer и scripted external events. Остаются режимы организаций, зоны, ресурсные пулы, информационный климат и неформальные связи.

Внешняя агентность теперь наращивается только через `population_blueprints`, secondary-spawn и обычный worldgen, без отдельного queue-driven контура.

Дополнительно после применения действий движок может детерминированно обновлять `environment.informal_links`: частные сообщения усиливают связи типа `private_contact`, а совместная работа по одному делу — связи типа `coordination`. Это даёт среде накапливаемый латентный слой зависимостей даже без отдельной worldgen-подсказки.

Помимо worldgen-spawn, движок теперь поддерживает `population_blueprints`: конфигурационные шаблоны, позволяющие систематически насыщать мир периферийными акторами вокруг конкретной организации или зоны. В режиме `bootstrap` такие акторы материализуются при инициализации мира, в режиме `environment_change` — после значимых сдвигов среды.

Если `runtime.ecology_activation_window_ticks > 0`, не-core акторы (secondary/worldgen/runtime-spawned) не ходят автоматически каждый тик. Движок активирует их только если они недавно были затронуты событиями, hook-ами, созданием, прямым взаимодействием или открытым `pending_interaction`. Это уменьшает public-process capture со стороны ecology без отключения самой среды.

Перед первым тиком, если `runtime.enrich_personas=true`, движок выполняет runtime-обогащение персон (`summary + biography`, а в режиме `full` ещё и интервью + expert reflection + interview-grounded motivation digest). Результат сохраняется в `{out_dir}/personas.json` и дополнительно пишется в persistent fingerprint-cache рядом с директориями прогонов. За счёт этого одинаковые repeated runs могут переиспользовать enrichment между разными `out_dir` при совпадении fingerprint входов (язык, модель, режим, описание сценария и базовые данные агентов).

Если `runtime.spawn_secondary=true`, после enrichment запускается `SocialGraphExtractor`: он извлекает из биографий и интервью значимых людей, создаёт вторичных агентов до первого тика, обогащает их персоны в том же режиме, что и основной сценарий (`core` или `full`), и связывает первичные/вторичные пары через память. Role-only ссылки и alias-дубли существующих должностей не материализуются в новых агентов.

### Действия (Action)

На уровне когнитивного интерфейса агент больше не собирает `Action[]` сам. Он формулирует один свободный `proposal`, а арбитр уже материализует его в формальные последствия мира.

Внутри runtime `actions.py` по-прежнему хранит расширенный набор typed actions (`send_message`, `create_work_item`, `cast_vote`, `request_entity` и т.д.), но они рассматриваются как внутренний исполнительный словарь арбитра и совместимый слой тестов/runtime, а не как пользовательский интерфейс когнитивного агента.

Prompt-layer агента теперь частично декларативен: `runtime.agent_prompt` позволяет сценарию или governance-template подмешивать дополнительные правила адресации, scenario-specific guardrails и собственные «хорошие/плохие» примеры materializable `proposal`, не меняя код `AgentRunner`.

## Арбитр (Arbiter)

Арбитр выполняет роль «физики мира» и определяет три вещи для каждого действия:
1. **Допустимость** — возможно ли действие в текущем состоянии (пространство, полномочия, существование целей).
2. **Прямые последствия** — какие `StateOp[]` следуют из намерения агента.
3. **Побочные эффекты** — свидетели, изменение неформальных отношений, привлечение внимания, создание обязательств.

Арбитр работает в двух слоях.

Для legacy/runtime typed actions (которые ещё могут приходить из старых тестов или системных контуров) арбитр делает детерминированную проверку:
1. Проверка существования целей через `EntityRegistry` (антифантомная защита).
2. Проверка полномочий агента там, где они действительно предметно значимы (`work`, `dao`, legacy `spawn`).
3. Преобразование `Action` в набор `StateOp[]` — детерминированных операций над состоянием мира.

Lexical duplicate-suppression для `create_work_item` как semantic shortcut в актуальной архитектуре намеренно выключен: проверка смысловых дублей не должна проектироваться как substring/keyword-эвристика в primary path.

Базовый когнитивный путь: агентский `proposal` оборачивается во внутренний freeform-`perform`, после чего арбитр обращается к LLM:
1. Формируется промпт с YAML-журналом мира (`WorldJournal`) и полным текстом `proposal`.
2. Перед основной materialization арбитр может попросить LLM разложить сложный ход на ordered steps (`сначала прийти`, `потом поговорить`, `затем зафиксировать`).
3. Каждый шаг materialize-ится отдельно поверх локального scratch-state, так что следующие шаги уже видят обновлённую физику мира внутри того же `perform`.
4. LLM определяет допустимость, формирует набор прямых `StateOp[]` и дополняет их побочными эффектами.
5. Документарные ops (`AddWorkNoteOp`, `SubmitWorkProposalOp`) дополнительно проходят отдельный grounding-pass: verifier получает текст candidate-документа, `proposal`, уже materialized ops и текущий world journal, после чего может переписать note/proposal в более эпистемически скромную и фактически поддержанную форму.
6. Намерения, адресованные несуществующим сущностям, отклоняются до обращения к LLM.
7. Если в `proposal` нет содержательного действия, арбитр может вернуть `approved=true` и пустой `ops`.

Арбитру доступен расширенный словарь операций для materialization:
- **Коммуникация**: `send_message` (приватные и публичные сообщения).
- **Работа**: `create_work_item`, `add_work_note`, `submit_work_proposal` (требуют capability `work`).
- **Документы**: `create_artifact`, `update_artifact` (требуют capability `work`).
- **Governance**: `open_vote`, `cast_vote`, `respond_nomination`, `modify_reputation`.
- **Инфраструктура**: `create_entity` (org/chan).
- **Физика мира**: `narrative_action` — для пространственных и физических действий (перемещение, осмотр, передача из рук в руки), с опциональными `zone_id` и `witnesses`.
- **Побочные эффекты**: `upsert_informal_link` (изменение неформальных связей), `add_information_signal` (привлечение внимания), `upsert_pending_interaction` (создание обязательств и follow-up), `resolve_pending_interaction` (закрытие обязательств).

Ключевое отличие от предыдущей архитектуры: арбитр не просто переводит `proposal` в один формальный op, а генерирует комплекс прямых последствий и побочных эффектов. Например, «переговорю с agent:X наедине в коридоре» может породить `send_message` (прямое последствие), `narrative_action` (физическая встреча) и `upsert_informal_link` (укрепление связи как побочный эффект).

Для свободного `proposal` добавлен semantic retry. Если первая materialization попытка вернула `approved=true` и пустой `ops`, но текст выглядит как содержательный ход, а не как человеческий `noop`/наблюдение, арбитр делает ещё один LLM-вызов с более жёсткой инструкцией: либо выдать конкретные `StateOp`, либо отклонить ход явно. Если и повторная попытка не материализует proposal, действие получает отказ `proposal_not_materialized_after_retry` вместо тихого пустого approve.

Конвертация `proposal -> StateOp[]` выполняется через временный `IdAllocator`, а commit в реальный allocator происходит только после полной валидации всего набора ops. Поэтому отклонённое свободное действие не должно расходовать будущие `work:`/`vote:` идентификаторы.

Важно: арбитр не является runtime-аудитором. Он отвечает за допустимость и перевод действий в операции, но не за поиск содержательных нарушений по уже совершённым событиям.

## Runtime-аудитор (RuntimeAuditor)

`RuntimeAuditor` — отдельный governance-компонент, запускаемый в конце тика после применения действий, закрытия DAO-голосований и worldgen. Его задача — выявлять сигналы риска по уже совершённым событиям и, при необходимости, инициировать управленческие последствия.

Текущая архитектура:

1. **Deterministic baseline + freeform LLM findings**: аудитор всегда строит baseline-findings только по структурным паттернам мира (self-reputation award, nomination/support vote after private contact, overdue response on open case / queue obligation), а в режимах `llm`/`hybrid` дополняет их LLM-сигналами в свободной форме. Для LLM главным когнитивным интерфейсом считаются `violation_type_freeform`, `summary` и `mechanism`, а не жёсткий выбор из фиксированного меню нарушений.
2. **Verifier pass + policy resolution**: перед actuator-слоем LLM finding проходит отдельный verifier-pass. Он получает draft finding, candidate evidence и недавние события мира, оценивает `runtime_support_level`, при необходимости предлагает канонический `violation_type`, может уточнить `target_agent_id` и даёт мягкую рекомендацию для policy-layer. Иными словами, LLM сначала описывает риск, затем отдельный verifier проверяет его runtime-groundedness, и только после этого policy-layer решает, открывать ли кейс, запрашивать ли объяснение, включать monitoring, freeze или collegial review.
3. **Case aggregation**: repeated findings не открывают бесконечную россыпь `audit_case:{finding_id}`, а схлопываются в стабильный `audit_case:*` по subject/type/target/beneficiary. В кейсе накапливаются `episode_count`, `updated_tick`, `response_due_tick`, `review_vote_id`, `monitoring`.
4. **Deterministic actuator**: findings и case-policy детерминированно преобразуются в:
   - `audit_flagged`;
   - `audit_case_opened`;
   - `audit_case_updated`;
   - `audit_explanation_requested`;
   - `audit_documents_requested`;
   - `audit_monitoring_enabled`;
   - `audit_escalated`;
   - `StateOp` для заморозки роста репутации;
   - `audit_review` vote-path для collegial review.

При `audit_review` движок больше не раскрывает состав jury через общий internal event-поток. `vote_opened` для review адресуется самим reviewers, а public/internal review-события несут только факт открытия review и его `review_vote_id`, без списка участников.

Дополнительно у открытых кейсов есть follow-up policy: если по `request_explanation` / `request_documents` истёк `response_due_tick`, аудитор либо поднимает `audit_monitoring_enabled`, либо открывает `audit_review`, либо закрывает кейс при детектированном ответе/пакете документов.

Baseline-эвристики аудитора теперь сфокусированы на структурных governance- и контактных паттернах мира, а не на искусственно материализованном queue-driven контуре.

`RuntimeAuditor` не подменяет собой `ViolationOracle` и не создаёт ground truth эксперимента. Его выход — это governance-treatment, а не пост-фактум измерение качества режима.

Нарративный агент-аудитор удалён: аудит больше не живёт как обычный `AgentRunner` с capability `audit`, а существует только как отдельный runtime-layer.

## Truth-layer и evaluation

После формирования фактических `tick_events`, но до эмиссии audit-интервенций, движок прогоняет deterministic `TruthDetector`. Он пишет sidecar `truth.jsonl` с каноническими `TruthRecord`, которые не зависят от того, сработал ли runtime-аудитор.

Опционально (`runtime.freeform_truth_enabled=true`) движок дополнительно пишет `truth_freeform.jsonl` через `FreeformTruthRecorder`. Это LLM-based post-hoc слой, который записывает нарушения в свободной форме по unified finding schema (`summary`, `mechanism`, `beneficiary`, `risk_tags`, `evidence_refs`), не подменяя собой deterministic `truth.jsonl`.

Если `truth_freeform.jsonl` присутствует, `evaluation.py` использует его как truth-source для semantic/case matching; strict exact-match по-прежнему считается только против deterministic `truth.jsonl`.

Post-hoc evaluation теперь разделён на два слоя:

- strict baseline в `evaluate_run(...)`, который остаётся полностью детерминированным и сравнивает exact `tick + subject + violation_type + target + evidence-signature`;
- отдельный semantic/case judge pass, который получает truth/runtime findings целиком и матчит их по смыслу через БЯМ, а не через hand-written label heuristics.

В ходе исполнения движок также поддерживает `status.json`: sidecar с heartbeat-обновлением на каждом тике и финальным состоянием `finished` или `failed`. Web backend использует его для более надёжного обнаружения живых CLI-прогонов.

По завершении прогона движок пишет два независимых sidecar-контура:

- `evaluation.json` — governance-eval: сравнение runtime-аудита и deterministic truth-layer;
- `fidelity.json` — метрики правдоподобия (`temporal consistency`, `identity drift`, `phantom drift`, `bureaucratic loop`, `narrating leakage`, `perform`) и опциональный semantic realism layer поверх последних событий.
- `perf_summary.json` — агрегированные runtime/performance-метрики: токены, LLM-duration, overlap, `p50/p95/max`, slowest calls, timeout/error counters, разрез по фазам (`agent`, `memory`, `auditor`, `worldgen` и т.д.), по тикам и по локальным embedding-фазам.
- `world_history.md` — читабельный markdown-sidecar: полная хронология событий мира по тикам, встроенные в соответствующие tick-блоки входы агентов (`system`, `user`, `response`) и отдельный полный trace для всех LLM-вызовов.
- `environment_summary.json` — финальный компактный снимок усиленной среды;
- `environment_timeline.jsonl` — покадровая средовая телеметрия для observability/export.

Сводка `summary.json` просто объединяет оба блока, не смешивая governance-treatment и fidelity.

`evaluation.py` сравнивает:

- `audit_flagged` из `events.jsonl`;
- `TruthRecord` из `truth.jsonl`.

Результат сохраняется в `evaluation.json` и содержит:

- `truth_total`;
- `runtime_flagged_total`;
- `true_positive`;
- `false_positive`;
- `false_negative`;
- `precision`;
- `recall`;
- `f1`;
- `semantic_true_positive` / `semantic_false_positive` / `semantic_false_negative`;
- `semantic_precision` / `semantic_recall` / `semantic_f1`;
- `case_true_positive` / `case_false_positive` / `case_false_negative`;
- `case_precision` / `case_recall` / `case_f1`;
- сводку `by_violation_type`.

Строгая часть (`true_positive`, `precision`, `recall`) по-прежнему опирается на exact-match baseline, но exact-match теперь сравнивает уже нормализованный `violation_type`, counterparty-поля и core-signature evidence, а не полный сырой JSON `evidence_refs`. Semantic- и case-часть больше не выводятся из hand-written score function по exact labels; их считает отдельный judge pass поверх truth/runtime findings и case-level представлений.

## Операции состояния (StateOp → Event)

`ops.py` определяет детерминированные операции: `SendMessageOp`, `CreateEntityOp`, `CreateAgentOp`, `CreateWorkItemOp`, `AddWorkNoteOp`, `SubmitWorkProposalOp`, `CastVoteOp`, `OpenVoteOp`, `ModifyReputationOp`, `SetVoteConsentOp`, `SetReputationFreezeOp`, `CreateArtifactOp`, `UpdateArtifactOp`, `RecordNarrativeActionOp`, а также runtime-ops для richer среды: `UpsertInformalLinkOp`, `AddInformationSignalOp`, `UpsertPendingInteractionOp`, `ResolvePendingInteractionOp`. Каждая операция применяется к `WorldState` и порождает `Event`, записываемый в `EventLog` (JSONL). Последовательное применение гарантирует детерминизм при фиксированном зерне.

`RecordNarrativeActionOp` фиксирует физические и пространственные действия агента (перемещение, осмотр, передача документа, ожидание). Это не catch-all для произвольного текста, а структурированная запись с `action_kind`, опциональным `zone_id` и списком `witnesses`. Если указаны свидетели, событие `narrative_action` адресуется только актору и свидетелям; иначе — всем внутренним агентам.

Если у `narrative_action` указан `zone_id`, операция трактует это как фактическую текущую локацию актора и обновляет `agent.zone_id` в состоянии мира. Благодаря этому арбитр может в одном и том же `perform` сначала материализовать приход в нужную зону, а затем уже допустить private contact из новой локации.

`CreateAgentOp` создаёт `AgentState` и `entity_created`, а полноценный `AgentRunner` и bootstrap памяти для нового агента регистрируются отдельным шагом после применения ops. Новый участник начинает ходить со следующего тика.

Для базовых агентов, заданных прямо в `ScenarioConfig.agents`, движок берёт `initial_reputation` из конфига и отражает его в первом `reputation_snapshot`. Это делает стартовые условия наблюдаемыми и для UI, и для post-hoc анализа.

`SetReputationFreezeOp` меняет состояние `AgentState.reputation_frozen` / `reputation_frozen_until_tick` и эмитит `reputation_frozen` или `reputation_unfrozen`. При активной заморозке positive reputation changes блокируются, а DAO не продвигает замороженного агента на новую должность.

Положительная репутация больше не начисляется за каждое `work_proposal_submitted`. Детерминированный reward-cycle привязан только к governance-мильстоунам: согласию цели голосования (`vote_target_consented`) и успешному `vote_closed` с `result="passed"`. Это уменьшает возможность фармить репутацию через бюрократический спам заметок и proposals.

## Память агента (AgentMemory)

Двухслойная архитектура управляет контекстом агента.

### Рабочая память (working buffer)

Хронологический буфер последних `working_max_entries` записей. При переполнении старейшие записи суммаризируются пакетами по `working_summarize_batch` через LLM-вызов, а суммарии помещаются в долгосрочную память.

Суммаризация теперь не срабатывает на минимальном overflow. У памяти есть дополнительный порог `working_summary_min_overflow`: пока переполнение буфера меньше этого порога, движок предпочитает подождать и не тратить отдельный LLM-вызов на слишком маленький batch.

Перед отправкой batch в суммаризатор движок также детерминированно схлопывает серийные технические записи (`environment_informal_link_updated`, `arbiter_approved`, `pending_interaction_*` и т.п.), чтобы LLM не пережёвывал десятки почти одинаковых строк.

Важно: batch удаляется из `working` только после успешного ответа суммаризатора. Если LLM-вызов падает, движок пишет `memory_llm_error`, но не теряет исходные записи рабочей памяти.

### Долгосрочная память (hybrid index)

Индекс до `long_term_max_docs` документов с гибридным поиском. Формула ранжирования:

**Score = w_r * Recency + w_v * Vector + w_b * BM25 + w_i * Importance**

- **Давность** (Recency): экспоненциальный спад по `recency_decay`.
- **Векторное сходство** (Vector): косинусная близость эмбеддингов.
- **BM25**: лексическое совпадение через собственную реализацию (`bm25.py`).
- **Важность** (Importance): статическая оценка по типу события (`importance_by_event`).

Веса настраиваются через `MemoryWeights` в конфигурации. Дедупликация: записи с косинусным сходством выше `dedup_cosine_threshold` объединяются.
В обычных прогонах `MemoryConfig.embeddings_mock=false` по умолчанию, поэтому движок использует реальные embeddings через OpenAI-совместимый API. Если ключа нет, векторная часть автоматически отключается, и retrieval остаётся в режиме BM25 + recency + importance. `embeddings_mock=true` оставлен для тестов и дешёвых локальных smoke-прогонов.

Типы записей: `persona`, `interview`, `summary`, `observation`, `result`, `reflection`.

Agent prompt использует не один общий retrieval-блок, а несколько секций: якоря персоны (`persona`), фрагменты интервью (`interview`), экспертную рефлексию (`reflection`) и оперативную память (`observation`/`result`).

Отдельно от `AgentMemory.summary` движок поддерживает короткий `story_state` в `AgentState`. Он не является второй LLM-памятью; это компактный runtime-sidecar для personal ecology, который строится движком из persona, текущей позиции и наблюдаемых событий тика.

## Генератор мира (WorldGenerator)

При включении (`enable_worldgen`) генератор мира работает в одном или двух режимах:

- **post-tick worldgen** — обратносуместимый режим по умолчанию: создаёт внешние `world_event`, `spawns`, при необходимости `environment_updates`, `entity_creations`, а также `artifact_creations` / `artifact_updates` по итогам уже совершённых действий;
- **pre-tick worldgen** (`runtime.worldgen_pre_tick=true`) — запускается до `propose_actions`, создаёт:
  - `events` как глобальные/организационные сигналы текущего тика;
  - `agent_contexts` как персональные opening contexts;
  - `scene_hooks` как необязательные сценовые поводы.

В обоих фазах worldgen получает только безопасный контекст:
- public/internal события без текста приватных сообщений;
- агрегированные сигналы закрытых private-контактов;
- state snapshot (open work items, открытые votes, вторичные акторы, компактный срез `environment`-слоя и список `art:*`-артефактов);
- краткие `story_state` агентов;
- temporal contract (`tick`, `tick_granularity`, канонические дата/время).

`worldgen_every_ticks` должен быть строго положительным числом. Нулевое значение теперь считается невалидной конфигурацией и отклоняется на этапе загрузки `ScenarioConfig`, чтобы движок не падал на modulo при проверке расписания worldgen.

Если задана каноническая временная ось (`runtime.start_date`, `runtime.tick_granularity`, `runtime.tick_duration_days`), движок дополнительно передаёт worldgen текущие дату и время симуляции. Для `hour`/`half_day` это даёт worldgen и агенту не только календарную дату, но и внутридневное положение тика.

Движок принимает `spawns` только если `runtime.allow_runtime_spawn=true`. Для совместимости worldgen по-прежнему понимает legacy-формат `list[world_event]` без блока `spawns`. Дополнительно движок требует человеко-читаемый display-name, отсекает role-only ярлыки и не принимает внутренних акторов от worldgen, если `runtime.worldgen_allow_internal_spawns=false`.

Если worldgen возвращает `entity_creations`, движок сначала детерминированно materialize-ит новые `org:*`, `chan:*`, `zone:*`, `res:*` сущности через `CreateEntityOp`, а затем сразу засеивает ими environment-layer (`institutions`, `zones`, `resource_pools`) там, где это применимо. Это позволяет worldgen сначала ввести внешнюю физику мира, а уже потом создавать связанные документы, каналы наблюдения и сигналы.

Если worldgen возвращает `environment_updates`, движок применяет их детерминированно к организациям, зонам, ресурсным пулам и информационному климату через отдельные события `environment_institution_updated`, `environment_zone_updated`, `environment_resource_updated`, `environment_information_climate_updated`.

Если worldgen возвращает `artifact_creations` или `artifact_updates`, движок аналогично применяет их детерминированно через `artifact_created` и `artifact_updated`. Для совместимости legacy-prefix `artifact:*` нормализуется в канонический `art:*` до применения ops. Тем самым документарный слой становится самостоятельной поверхностью мира, а не только текстом в `world_event`.

Для pre-tick material действует жёсткий negative contract: worldgen не должен утверждать решения существующего агента, закрывать `work item` текстом, раскрывать private-message content или подменять typed ontology строками `agent:*` / `work:*`.

## DAO-голосование (DaoEngine)

Механизм коллегиального принятия решений. При номинации на смену должности (`nominate_position_change`) открывается голосование с участием агентов из списка `dao_voters`. По умолчанию self-nomination запрещена, а цель голосования исключается из списка голосующих и должна отвечать через `respond_nomination`. Для этого ответа capability `dao` не требуется: достаточно быть текущей целью открытого vote. Голосование закрывается, когда:
- достигнут кворум (`quorum`) и порог одобрения (`pass_threshold`); или
- истекла длительность (`vote_duration_ticks`).

При одобрении должность агента меняется через `StateOp`. При необходимости согласия кандидата (`require_consent`) ожидается `respond_nomination`. Событие `vote_closed` теперь содержит не только результат, но и `reason` (`consent_missing`, `reputation_frozen`, `threshold_passed` и т.д.).

Если у цели активна заморозка репутации (`reputation_frozen=true`), голосование не проходит: `DaoEngine` возвращает `canceled`.

## Подсистема LLM

### LLMProvider (протокол)

Два основных метода:
- `generate(system, user)` — текстовый ответ (`LLMResponse`).
- `generate_structured(system, user, schema)` — структурированный ответ (`StructuredLLMResponse`) по JSON Schema.

### Реализации

- **OpenAICompatibleProvider** — провайдер для OpenAI-совместимых API с поддержкой повторных попыток, валидации JSON Schema и `tool_calls`; `provider_order` добавляется только для OpenRouter provider routing.
- **MockLLMProvider** — детерминированный провайдер для тестов. Параметр `structured_responses` задаёт словарь ответов для различных вызовов.

### EmbeddingProvider

- **OpenAIEmbeddingProvider** — через OpenAI-совместимый API.
- **MockEmbeddingProvider** — детерминированные эмбеддинги для тестов.

### LLMCaller

Обёртка над `LLMProvider` + `TraceLog`. Записывает каждый вызов (промпт, ответ, длительность) в JSONL-файл трассировки.

### LLMCache

SQLite-кеш ответов по хешу промпта — для экономии и воспроизводимости.

## Оракул (ViolationOracle)

Пост-фактум анализ нарушений. Читает `events.jsonl`, разбивает на окна по `window_ticks` тиков, отправляет каждый чанк в LLM для обнаружения нарушений. Результат — JSON с описаниями выявленных отклонений.

Рядом с ним теперь может работать `FreeformTruthRecorder`: он также читает `events.jsonl` окнами, но пишет не narrative-report для пользователя, а structured sidecar `truth_freeform.jsonl` с richer truth-записями (`summary`, `mechanism`, `beneficiary`, `evidence_refs`).

Deterministic `TruthDetector` при этом расширяется осторожно и остаётся rules-first. После удаления lexical/keyword-эвристик truth-layer фиксирует только структурно наблюдаемые паттерны мира:

- `self_reputation_award`;
- `reputation_reward_after_private_contact`;
- `self_nomination`;
- `nomination_after_private_contact`;
- `support_vote_after_private_contact`;

Для спорных кейсов runtime-аудитор может открывать `audit_review`: это отдельный collegial review path, в котором детерминированно подбираются внутренние reviewers, а результат review закрывает audit-case и при необходимости подтверждает freeze growth.

Важно: `ViolationOracle` и `evaluation.py` решают разные задачи.

- `ViolationOracle` — narrative / LLM post-hoc analysis.
- `evaluation.py` — формальное сравнение runtime-аудита с deterministic truth-layer.

## Конфигурация сценария (ScenarioConfig)

Корневая Pydantic-модель сценария. Загружается из YAML или JSON через `scenario.py`.

| Блок | Класс | Назначение |
|---|---|---|
| `llm` | `LLMConfig` | Модель, провайдер, температура, трассировка |
| `memory` | `MemoryConfig` | Буфер, индекс, веса, эмбеддинги |
| `runtime` | `RuntimeConfig` | Язык, лимит действий, история тиков, LangGraph |
| `governance` | `GovernanceConfig` | Политика должностей, голосование и настройки runtime-аудита |
| `agents` | `AgentConfig[]` | Агенты: ID, имя, персона, полномочия, стартовая репутация, должность |
| `world` | `WorldConfig` | Каналы, организации, рабочие элементы, `artifacts` и стартовый stateful environment layer |

Ключевые поля `runtime`:
- `start_date`: каноническая календарная дата тика `0`.
- `tick_granularity`: качественный масштаб тика (`hour`, `half_day`, `day`, `week`).
- `tick_duration_days`: множитель для выбранной гранулярности (`day` = дни, `hour` = часы, `half_day` = полудни, `week` = недели).
- `parallel_agents`: выполнять этап генерации решений параллельно или последовательно.
- `parallel_workers`: ограничение на количество одновременных LLM-вызовов при параллельной генерации.
- `parallel_window_seconds`: совместимый launcher-параметр окна батчирования; в текущем tick-engine весь тик обрабатывается одним batch.
- `temporal_past_slack_days`: допустимый лаг для абсолютных дат в структурированных действиях.
- `temporal_future_horizon_days`: допустимый горизонт будущих дат в структурированных действиях.
- `enrich_personas`: включить обогащение персон перед первым тиком.
- `persona_enrich_mode`: `core` (summary+biography) или `full` (summary+biography+interview+reflection).
- `spawn_secondary`: извлечь вторичных агентов из социального графа до первого тика.
- `max_secondary_per_agent`: лимит связей, извлекаемых из одной персоны.
- `max_agents`: общий потолок числа агентов в мире.
- `allow_runtime_spawn`: разрешить legacy/runtime-spawn и worldgen-spawn в ходе симуляции; когнитивный агент при этом всё равно не получает `spawn_agent` как часть своего основного интерфейса.
- `request_entity_internal_only`: разрешить `request_entity` только внутренним акторам.
- `ecology_activation_window_ticks`: окно активности для не-core ecology-акторов; если `> 0`, они ходят только при недавней релевантности.
- `worldgen_every_ticks`: положительный интервал запуска worldgen; должен быть `> 0`.
- `worldgen_pre_tick`: включить pre-tick worldgen с personal-context layer.
- `worldgen_event_budget_per_tick`: верхняя граница числа worldgen-событий за тик.
- `agent_context_budget_per_tick`: бюджет персональных контекстов на один pre-tick запуск.
- `max_scene_changes_per_tick`: лимит scene hooks / scene changes на тик.
- `max_new_actors_per_window`: лимит предложений новых акторов от worldgen за одно окно.
- `worldgen_context_scope`: `core` или `all`; по умолчанию personal contexts от pre-worldgen строятся прежде всего для core-акторов сценария.
- `worldgen_allow_internal_spawns`: разрешить worldgen создавать внутренних акторов.
- `freeform_truth_enabled`: включить post-hoc `truth_freeform.jsonl`.
- `freeform_truth_window_ticks`: размер окна для `FreeformTruthRecorder`.
- `agent_prompt`: декларативные prompt-guardrails для агентного freeform-интерфейса.

`AgentConfig` помимо `agent_id`, `name`, `persona` и `capabilities` теперь хранит `initial_reputation`, чтобы стартовая репутация была частью канонического сценария, а не только web-карточки.

Ключевые поля `governance.audit`:
- `enabled`: включить runtime-аудитор.
- `actor_id`: какой агент-идентификатор использовать как `actor_id` audit-событий.
- `mode`: `rules` / `llm` / `hybrid`; в `llm` и `hybrid` structured LLM-findings дополняются deterministic baseline-rules.
- `lookback_events`: глубина окна истории для audit detection.
- `private_contact_window_ticks`: окно приватных контактов для conflict-like heuristics.
- `obligation_window_ticks`: окно pressure/obligation-эвристик для omission-like нарушений.
- `response_window_ticks`: сколько тиков даётся на объяснение/документы до follow-up escalation.
- `min_confidence_to_flag`: минимальная уверенность для `audit_flagged`.
- `min_confidence_to_open_case`: минимальная уверенность для открытия или обновления audit-case.
- `min_confidence_to_freeze`: минимальная уверенность для `reputation_frozen`.
- `min_confidence_to_review`: порог маршрутизации в collegial review.
- `freeze_duration_ticks`: длительность заморозки в тиках.
- `case_repeat_escalation_threshold`: после скольких эпизодов кейс автоматически уходит в review/monitoring.
- `external_subject_confidence_cap`: верхняя граница confidence для внешних субъектов finding’ов.
- `collegial_review_enabled`: разрешить review-path в built-in `G3`; для `G0–G2` canonical mapping теперь обязан отключать этот флаг.

Ключевые поля `governance`:
- `require_consent`: требовать явное согласие кандидата.
- `allow_self_nomination`: разрешить или запретить self-nomination.
- `allow_target_self_vote`: разрешить или запретить голос цели за собственную номинацию.
