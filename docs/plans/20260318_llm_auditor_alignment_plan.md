# План Следующего Цикла: Выравнивание LLM-Аудитора С Truth-Layer И Активация Review/Freeze

## Основание

Прогон `full_ecology_personal_ecology_llm_audit_50t_20260318_1` подтвердил, что переход на LLM-based `RuntimeAuditor` состоялся:

- `runtime_flagged_total` стал ненулевым;
- появились `audit_flagged`;
- появились `audit_case_opened`;
- появились `audit_explanation_requested`;
- появились `audit_monitoring_enabled`.

То есть архитектурная цель предыдущего цикла достигнута:

> online AI-auditor действительно начал работать как governance-layer, а не как пустой rules-first stub.

Однако сразу проявились три новых узких разрыва:

1. `evaluation.json` по-прежнему даёт `true_positive = 0`;
2. auditor ни разу не дошёл до `route_to_collegial_review`;
3. auditor ни разу не дошёл до `freeze_reputation_growth`.

Это означает:

> следующая проблема уже не в наличии LLM-аудита, а в его согласовании с truth ontology и policy-actuator.

## Ключевой Диагноз

### 1. Runtime-аудитор Видит Нарушения, Но Называет Их Иначе

В последнем прогоне deterministic truth пишет:

- `non_escalation_under_pressure`;
- `partial_disclosure_under_deadline_pressure`;
- `preferential_treatment_for_connected_actor`.

LLM-аудитор пишет:

- `preferential_treatment_for_connected_actor`;
- `non_disclosure_under_deadline_pressure`;
- `pressure_not_to_escalate`;
- `process_manipulation`;
- `narrative_manipulation`;
- `non_escalation_under_pressure`.

Содержательно это близкие классы.

Но для `evaluation.py` это пока разные сущности.

Следствие:

- auditor уже даёт meaningful findings;
- но post-hoc matching не умеет признать их совпадением.

### 2. LLM-Аудитор Пока Слишком Осторожен В Policy Output

Он часто выбирает:

- `signal_only`;
- `open_case`;
- `request_explanation`;
- `heightened_monitoring`.

Но почти не выбирает:

- `route_to_collegial_review`;
- `freeze_reputation_growth`.

Это делает его правдоподобным как мягкий наблюдатель, но ещё недостаточно сильным как governance-mechanism из 1.6 ВКР.

### 3. Review И Freeze Реализованы, Но Не Активируются

Это уже не отсутствующий код, а policy/propt calibration problem.

То есть:

- path существует;
- тестами покрыт;
- но в реальном run модель его почти не выбирает.

## Цели Следующего Цикла

Нужны три результата.

### 1. Поднять Non-Zero `true_positive`

Хотя бы часть runtime findings должна матчиться с deterministic truth.

### 2. Добиться Реального Использования `route_to_collegial_review`

Не в тесте, а в реальном 50-тиковом прогоне.

### 3. Добиться Реального Использования `freeze_reputation_growth`

Для high-confidence cases, согласованных с ВКР.

## Направление Изменений 1. Taxonomy Alignment

### Проблема

Сейчас runtime labels и truth labels живут в двух соседних, но разных онтологиях.

Из-за этого:

- semantic overlap есть;
- metric overlap — нулевой.

### Что Нужно Сделать

Добавить явный слой нормализации.

Есть два допустимых варианта.

#### Вариант A. Нормализовать runtime findings на этапе генерации

В LLM prompt аудитора явно задать:

- preferred canonical labels;
- иерархию приоритетов;
- правило: если кейс соответствует deterministic truth family, используй именно этот label.

Пример:

- не `non_disclosure_under_deadline_pressure`, а `partial_disclosure_under_deadline_pressure`;
- не `pressure_not_to_escalate`, а `non_escalation_under_pressure`, если это ближе к truth contract.

#### Вариант B. Нормализовать в `evaluation.py`

Сделать mapping-слой:

- `non_disclosure_under_deadline_pressure` -> `partial_disclosure_under_deadline_pressure`
- `pressure_not_to_escalate` -> `non_escalation_under_pressure`
- `process_manipulation` -> `preferential_treatment_for_connected_actor` only when evidence supports it

Рекомендуемый вариант:

- делать оба слоя;
- но primary alignment лучше выполнить в prompt + schema policy,
- а `evaluation.py` использовать как safety-net, а не как основное место semantic repair.

### Практическая Формула

Следующий run должен использовать:

- `violation_type` — максимально canonical;
- `violation_type_freeform` — richer description.

То есть:

> свободная семантика остаётся, но metric label должен быть более стабилен.

## Направление Изменений 2. Policy Calibration Для `route_to_collegial_review`

### Проблема

Модель в реальном run почти не считает кейсы достаточно спорными/тяжёлыми для review.

### Что Нужно Изменить

В system prompt аудитора нужно явно задать:

- если кейс затрагивает конфликт интересов у core-управленца;
- если есть beneficiary;
- если evidence многослойный и case high-stakes;
- если публичные и приватные сигналы противоречат друг другу;

то предпочтительный выход — `route_to_collegial_review`.

### Когда Review Особенно Уместен

#### A. Conflict Of Interest Cases

Если:

- есть личная связь;
- есть приватная коммуникация;
- есть управленческое решение;
- и case затрагивает доступ к ресурсу/контракту.

#### B. Transparency Theater / Strategic Non-Disclosure

Если:

- публичный нарратив расходится с приватной координацией;
- нельзя жёстко доказать злой умысел;
- но риск высок.

#### C. Pressure On Subordinates

Если:

- есть pressure not to escalate;
- но кейс не настолько жёсткий, чтобы freeze запускать автоматически.

В таких случаях review лучше соответствует ВКР, чем прямое freeze.

## Направление Изменений 3. Policy Calibration Для `freeze_reputation_growth`

### Проблема

LLM-аудитор пока слишком редко считает кейс достаточным для freeze.

### Что Нужно Сделать

Нужно чётче описать в policy, что freeze growth уместен, если:

- confidence высокая;
- subject — internal actor с управленческой ролью;
- case касается:
  - conflict of interest,
  - preferential treatment,
  - sustained non-disclosure,
  - repeated pressure not to escalate;
- мягкие сигналы уже не первый раз повторяются.

### Важно

Freeze должен оставаться:

- не штрафом;
- не “приговором”;
- а именно ограничением карьерного роста и доступа к ресурсу.

Это полностью согласуется с 1.6 ВКР.

## Направление Изменений 4. Case Persistence

### Проблема

Сейчас findings largely per-tick.

Но для review/freeze нужны не только instant signals, а case history.

### Что Нужно Сделать

Открытый `audit_case` должен сильнее влиять на следующий tick-аудит.

Например:

- если subject уже в открытом кейсе и приходит новый согласованный signal;
- confidence следующего finding должна повышаться;
- recommended_action должен эскалироваться:
  - `signal_only` -> `open_case`
  - `open_case` -> `request_explanation`
  - `request_explanation` -> `route_to_collegial_review`
  - `route_to_collegial_review` -> `freeze_reputation_growth` only if review confirms.

То есть нужен не просто тик-за-тиком judge, а judge with institutional memory (институциональной памятью кейса).

## Направление Изменений 5. Evaluation Beyond Exact-Match

### Проблема

Даже после частичной нормализации часть LLM findings останется богаче, чем deterministic truth.

### Что Делать

Не ломая основной `evaluation.json`, добавить:

- secondary metric layer:
  - family-level match;
  - semantic family recall;
  - strict recall vs relaxed recall.

Пример:

- strict: exact `violation_type`;
- relaxed: same `risk_family` or same normalized family.

Это особенно полезно для интерпретации `freeform`/LLM-аудита в исследовании.

Но:

- основной baseline `evaluation.json` лучше сохранить строгим;
- relaxed evaluation можно писать в отдельный sidecar.

## Изменения По Модулям

### `auditor.py`

Нужно:

- добавить canonical label guidance;
- усилить policy conditions для `route_to_collegial_review`;
- усилить policy conditions для `freeze_reputation_growth`;
- учитывать уже открытые audit cases как escalation context.

### `evaluation.py`

Нужно:

- добавить normalization map для близких labels;
- возможно ввести optional relaxed mode или отдельный `evaluation_relaxed.json`.

### `state.py` / `engine.py`

Нужно:

- использовать `audit_cases` как persistent context для следующего тика;
- не только хранить кейсы, но и реально учитывать их в detection prompt.

### `docs`

Нужно:

- явно разделить:
  - strict evaluation,
  - relaxed evaluation,
  - freeform truth.

## Фазы Реализации

### Фаза 0. Canonical Alignment

Сделать:

- canonical label guidance в auditor prompt;
- basic normalization в `evaluation.py`.

Цель:

- уйти от `0 TP`.

### Фаза 1. Review Activation

Сделать:

- stronger routing conditions to `route_to_collegial_review`;
- case classes, где review считается preferred path.

Цель:

- получить ненулевой `review_case_opened` в реальном run.

### Фаза 2. Freeze Activation

Сделать:

- stronger policy for `freeze_reputation_growth`;
- subject-role-aware escalation.

Цель:

- получить хотя бы единичные `reputation_frozen` без возврата к punitive penalties.

### Фаза 3. Case Memory Escalation

Сделать:

- escalation logic across ticks through open audit cases.

Цель:

- перейти от isolated findings к институциональной траектории кейса.

### Фаза 4. Re-run

Новый целевой прогон:

- `full_ecology_personal_ecology_llm_audit_50t`

И сравнение:

1. previous LLM-audit run;
2. aligned LLM-audit run.

## Критерий Успеха

Следующий цикл успешен, если:

1. `true_positive > 0`;
2. `review_case_opened > 0`;
3. `reputation_frozen > 0`;
4. при этом penalties не становятся основным инструментом;
5. audit по-прежнему соответствует мягкой governance-логике ВКР.

## Статус

Это план следующего шага после `full_ecology_personal_ecology_llm_audit_50t_20260318_1`.

Главный новый вывод:

> LLM-аудитор уже заработал, теперь его нужно не “включить”, а выровнять по ontology, policy и escalation semantics.
