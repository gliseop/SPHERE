# План Редизайна `RuntimeAuditor` В Сторону Настоящего LLM-Аудита

## Основание

Текущий `RuntimeAuditor` в [src/magistry_lc/auditor.py](../../src/magistry_lc/auditor.py) стоит в правильном архитектурном месте, но реализован как `rules-first` модуль.

Это делает его полезным для v1-проверок, но недостаточным для исследовательской постановки ВКР.

По разделу 1.6 в [chapter_1.md](../../chapter_1.md) гибридная система должна включать:

1. автоматизированный непрерывный аудит на основе искусственного интеллекта;
2. репутационный механизм;
3. коллегиальный механизм рассмотрения спорных случаев.

Ключевой тезис этого раздела:

> БЯМ-аудитор должен уметь анализировать неструктурированную информацию, социальный контекст и паттерны, не заданные заранее жёсткими правилами.

Отсюда следует:

> current `rules-first RuntimeAuditor` — это не финальная форма ИИ-аудита для ВКР, а только временная operational stub (операционная заглушка).

## Главный Вывод

Новый цикл должен не “подкрутить правила”, а заменить сам принцип работы `RuntimeAuditor`.

Нужна архитектура:

- детекция и интерпретация нарушений — через LLM;
- преобразование findings в реальные governance-последствия — детерминированно по policy;
- акцент не на наказание, а на:
  - сигнализацию;
  - открытие кейса;
  - заморозку темпа роста репутации;
  - передачу спорного случая на коллегиальное review.

## Почему Правил Недостаточно

Текущий модуль видит только узкий класс паттернов:

- `self_reputation_award`;
- `reputation_reward_after_private_contact`;
- `self_nomination`;
- `nomination_after_private_contact`;
- `support_vote_after_private_contact`.

Даже после последних расширений truth-layer уже умеет видеть:

- `preferential_treatment_for_connected_actor`;
- `non_escalation_under_pressure`;
- `partial_disclosure_under_deadline_pressure`;

а `truth_freeform.jsonl` показывает ещё более богатые классы:

- `transparency theater`;
- `strategic non-disclosure`;
- `conflict of interest concealment`;
- `process manipulation`;
- `pressure not to escalate`.

Текущий `RuntimeAuditor` эти классы в принципе не покрывает, потому что:

- не работает с семантикой текста;
- не интерпретирует конфликтующие мотивации;
- не видит gray-zone поведения;
- зависит от заранее пришитой rule-таксономии.

## Что Должен Делать Новый LLM-Аудитор

Новый `RuntimeAuditor` должен отвечать не на вопрос:

> совпало ли событие с одной из пяти hardcoded схем?

а на вопрос:

> сформировался ли в текущем тике содержательный сигнал отклонения, интереса, сокрытия, давления или манипуляции, который требует управленческой реакции?

Это должен быть:

- не “сюжетный агент-аудитор”;
- не post-hoc oracle;
- а именно **online AI auditor** как governance layer.

## Граница Между Слоями

### `agent:auditor`

Это персонаж мира.

Он:

- участвует в сюжете;
- может ошибаться;
- может быть объектом давления;
- не должен быть последней инстанцией истины.

### `RuntimeAuditor`

Это operational AI auditor.

Он:

- работает на каждом тике;
- анализирует уже совершённые события;
- создаёт audit findings;
- запускает governance consequences.

### `TruthDetector` / `FreeformTruthRecorder`

Это measurement layer.

Они:

- не вмешиваются в мир;
- нужны для post-hoc оценки режима;
- не должны подменять `RuntimeAuditor`.

## Предлагаемая Архитектура LLM-Аудита

Новый `RuntimeAuditor` должен состоять из трёх частей.

### 1. `AuditContextBuilder`

Собирает компактный аудит-контекст.

На вход LLM-аудитору нужно подавать:

- `tick_events` текущего тика;
- ограниченное окно `recent_events`;
- state snapshot;
- открытые дела и голоса;
- статус репутации и frozen-state;
- историю уже открытых audit cases;
- опционально агрегированные признаки риска.

Не нужно подавать:

- `trace.jsonl`;
- сырые prompts/responses других LLM;
- весь мир без фильтрации;
- произвольный внутренний дебаг.

### 2. `LLMAuditJudge`

Это один structured LLM-вызов на тик.

Он должен вернуть:

- список findings;
- со свободной семантикой нарушения;
- но по фиксированной схеме.

### 3. `AuditActuator`

Это deterministic policy-layer.

Он:

- превращает findings в события и ops;
- удерживает governance в управляемом состоянии;
- не даёт LLM напрямую мутировать `WorldState`.

Именно здесь должна жить вся жёсткая политика:

- freeze / no-freeze;
- route to review;
- request docs;
- case open / close.

## Новая Finding-Схема

Нужна richer finding-модель.

Минимальный вариант:

```python
class AuditFinding(BaseModel):
    finding_id: str
    tick: int
    subject_agent_id: str
    target_agent_id: str | None = None

    violation_type_freeform: str
    risk_family: Literal[
        "conflict_of_interest",
        "preferential_treatment",
        "non_disclosure",
        "pressure_not_to_escalate",
        "process_manipulation",
        "narrative_manipulation",
        "other",
    ]

    summary: str
    mechanism: str
    beneficiary: str | None = None
    confidence: float
    evidence_refs: list[dict[str, Any]]

    recommended_action: Literal[
        "none",
        "signal_only",
        "open_case",
        "request_explanation",
        "request_documents",
        "freeze_reputation_growth",
        "route_to_collegial_review",
        "heightened_monitoring",
        "close_case",
    ]
```

Ключевая идея:

- `risk_family` ограничен для downstream-интерпретации;
- `violation_type_freeform` и `summary` остаются LLM-rich;
- hardcoded taxonomies на уровне detection не нужны.

## Какие Действия Должен Уметь ИИ-Аудитор

Здесь нужна жёсткая привязка к 1.6 ВКР.

### Что Подходит

#### `signal_only`

Сформировать мягкий risk signal без немедленной санкции.

#### `open_case`

Открыть audit case, чтобы зафиксировать проблему институционально.

#### `request_explanation`

Потребовать объяснение от субъекта кейса.

#### `request_documents`

Потребовать документы, подтверждения, артефакты, материалы.

#### `freeze_reputation_growth`

Именно это лучше всего соответствует тексту ВКР:

- не штраф;
- не отъём накопленного;
- а остановка темпа карьерного роста и доступа к ресурсам.

#### `route_to_collegial_review`

Передать спорный кейс на отдельное коллегиальное рассмотрение.

#### `heightened_monitoring`

Сохранить кейс в наблюдении без немедленного freeze.

#### `close_case`

Закрыть кейс при достаточном объяснении или отсутствии оснований.

### Что Не Подходит Как Основной Путь

#### `reputation_penalty`

В текущем коде penalty ещё возможен, но это плохо согласуется с ВКР, где репутация:

- только накапливается;
- а реакция на подозрительное поведение — это freeze growth, а не минус.

#### Прямой “приговор” одним LLM-вызовом

LLM-аудитор не должен сразу:

- наказывать;
- снимать с должности;
- лишать ресурсов;
- закрывать вопрос окончательно.

Его роль:

- сигнал;
- case;
- freeze growth;
- route to review.

## Репутационный Контур: Как Согласовать С ВКР

Если строго следовать [chapter_1.md](../../chapter_1.md), то:

- отрицательных репутационных начислений лучше избегать;
- репутация должна работать как скорость продвижения, а не штрафной баланс;
- аудитор должен прежде всего замораживать траекторию роста.

Отсюда практический вывод:

- `reputation_penalty_delta` нужно убрать из default policy;
- `freeze_reputation_growth` сделать основным сильным действием;
- direct penalty оставить только как экспериментальный режим вне базовой архитектуры ВКР.

## Коллегиальное Review: Что Это Значит Для Кода

Текущий DAO не совсем то, что описано в ВКР.

В тексте ВКР речь идёт о:

- случайно отобранной группе;
- не связанной с кейсом;
- анонимном независимом голосовании;
- рассмотрении именно спорных кейсов аудитора.

Значит, `route_to_collegial_review` лучше не маппить напрямую в текущий fixed-voter DAO.

Нужен отдельный контур:

- `CollegialReviewEngine` или `PeerReviewEngine`;
- со случайным отбором рецензентов;
- с проверкой отсутствия связи с кейсом;
- с анонимным голосованием;
- с детерминированным outcome.

## Политика Доступа Аудитора

Здесь нужно оставаться верным границам ВКР:

- рядовые сотрудники и граждане сохраняют приватность;
- лица с руководящими полномочиями принимают расширенную прозрачность как условие доступа к ресурсам;
- высшее руководство имеет иммунитет как отдельное проектное ограничение.

Поэтому LLM-аудитору не нужен “тотальный рентген” мира.

Рекомендуемый режим:

- `internal` как default;
- `full_internal` — только для исследовательских прогонов;
- `metadata_only` — как контрольный режим.

## Переходный План Реализации

### Фаза 0. Зафиксировать Новую Policy-Позицию

Нужно явно решить:

- `RuntimeAuditor` становится LLM-based;
- rules-first детекция — fallback/debug mode, а не целевая архитектура;
- default punitive penalty убирается;
- основной сильный инструмент — freeze growth + case routing.

### Фаза 1. Новый Structured Output

Сделать новую LLM-схему finding-ов.

Цель:

- убрать hardcoded detection taxonomy;
- сохранить управляемый output contract.

### Фаза 2. LLM Detection Layer

Заменить `_rule_findings()` на LLM-judge или сделать:

- `mode="llm"` как основной путь;
- `mode="rules"` оставить только для smoke/debug/baseline.

### Фаза 3. Deterministic Actuator

Сделать policy-mapper:

- `signal_only` -> `audit_flagged`;
- `open_case` -> `audit_case_opened`;
- `freeze_reputation_growth` -> freeze op;
- `route_to_collegial_review` -> `review_case_opened`;
- `request_documents` -> новый audit event / work item;
- `request_explanation` -> новый audit event / message request.

### Фаза 4. Colleagial Review Engine

Добавить отдельный review layer для спорных кейсов аудитора.

### Фаза 5. Сравнительные Прогоны

Сравнить:

1. current rules-first auditor;
2. LLM-auditor without review;
3. LLM-auditor + reputation freeze;
4. LLM-auditor + freeze + collegial review.

## Критерий Успеха

Новый LLM-аудитор считается успешным, если:

1. начинает видеть те классы нарушений, которые уже фиксирует `truth.jsonl` и `truth_freeform.jsonl`;
2. выдаёт непустые `audit_flagged` в прогонах, где truth уже ненулевой;
3. остаётся интерпретируемым через structured findings и evidence refs;
4. не превращается в непрозрачный punitive oracle;
5. лучше соответствует 1.6 ВКР, чем текущий rules-first модуль.

## Статус

Это план следующего крупного архитектурного шага.

Главная его идея:

> `RuntimeAuditor` нужно не “расширять правилами”, а переводить в настоящий LLM-based online auditor с мягкими governance-действиями, согласованными с текстом ВКР.
