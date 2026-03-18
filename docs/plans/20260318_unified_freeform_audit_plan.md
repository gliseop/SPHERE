# План Следующего Цикла: Единая `freeform`-архитектура для Runtime-Audit, Truth И Evaluation

## Основание

Последний прогон с LLM-аудитором показал, что система вышла на новый уровень зрелости:

- `RuntimeAuditor` начал выдавать ненулевые `audit_flagged`;
- deterministic `truth.jsonl` стал ненулевым;
- `truth_freeform.jsonl` тоже стал ненулевым.

Но при этом `evaluation.json` всё ещё даёт:

- `true_positive = 0`;
- `recall = 0.0`;
- `precision = 0.0`.

На предыдущем шаге естественной инженерной реакцией было бы “выравнивать labels” между runtime findings и truth records.

Однако это создаёт неправильное направление развития:

- усиливает зависимость от ручной таксономии;
- превращает LLM-аудит в генератор hardcoded labels;
- делает эксперимент ближе к классификатору по заранее заданным типам, чем к реальному ИИ-аудиту.

Главный новый вывод:

> для ВКР правильнее не выравнивать две жёсткие онтологии, а перейти к единой свободной форме findings и отдельно строить semantic matching.

## Почему Label Alignment — Неправильный Финальный Путь

Сведение всего к:

- `preferential_treatment_for_connected_actor`
- `non_escalation_under_pressure`
- `partial_disclosure_under_deadline_pressure`

удобно для быстрой метрики, но плохо для исследовательской архитектуры.

Почему:

1. реальные нарушения в организационном процессе размыты и контекстны;
2. LLM лучше описывает механизм, выгодоприобретателя и структуру риска, чем стабильно попадает в один exact label;
3. одна и та же ситуация может одновременно быть:
   - concealment,
   - favoritism,
   - pressure not to escalate,
   - narrative manipulation;
4. forced label alignment (принудительное выравнивание меток) начинает скрывать сложность случая.

Если держаться логики раздела 1.6 ВКР, ИИ-аудит должен быть силён именно в интерпретации неструктурированной и неоднозначной информации, а не в выборе токена из фиксированного enum.

## Новый Принцип

Следующий цикл должен строиться вокруг одной идеи:

> runtime audit, deterministic truth и freeform truth должны описывать нарушения в максимально близкой свободной структуре, а не в трёх разных таксономиях с последующим ручным маппингом.

## Целевая Архитектура

Нужны три слоя, но на общем data contract.

### 1. Runtime Audit Findings

Пишутся во время симуляции.

Назначение:

- governance intervention;
- case opening;
- freeze growth;
- routing to collegial review.

### 2. Truth Records

Пишутся как канонический слой измерения.

Назначение:

- baseline truth;
- воспроизводимое post-hoc сравнение;
- экспериментальная оценка.

### 3. Freeform Truth Records

Пишутся как richer LLM-sidecar.

Назначение:

- capturing gray-zone episodes;
- richer interpretation;
- расширение смысла по сравнению с deterministic baseline.

### Ключевой Сдвиг

Эти три слоя должны различаться не форматом violation labels, а:

- временем работы;
- строгостью;
- назначением;
- режимом применения.

То есть:

- different purpose;
- same family of records.

## Единый Data Contract

Вместо жёсткого `violation_type` как центрального поля нужен единый freeform-oriented finding schema.

### Минимальная Структура

```python
class UnifiedFinding(BaseModel):
    tick: int
    subject_agent_id: str
    target_agent_id: str | None = None

    summary: str
    mechanism: str
    beneficiary: str | None = None

    risk_tags: list[str] = []
    confidence: float
    severity: Literal["low", "medium", "high"]

    evidence_refs: list[dict[str, Any]]
    notes: str = ""
```

### Что Здесь Важно

#### `summary`

Короткое человеческое описание эпизода.

#### `mechanism`

Как именно работает нарушение:

- private coordination;
- partial disclosure;
- informal favor;
- pressure not to escalate;
- narrative manipulation;
- process manipulation.

#### `beneficiary`

Кто выигрывает от поведения.

#### `risk_tags`

Не один жёсткий label, а список тегов.

Например:

- `conflict_of_interest`
- `preferential_treatment`
- `non_disclosure`
- `pressure`
- `manipulation`

Это не core метрика, а вспомогательный semantic scaffold (каркас семантики).

#### `evidence_refs`

Центральное поле.

Именно evidence должно быть основой сравнения, а не label equality.

## Что Делать С Deterministic Truth

Deterministic truth полностью убирать не надо.

Но его нужно перестроить.

### Вместо Этого

Сейчас:

- truth = `violation_type + rationale`

Нужно:

- truth = structured record с `summary`, `mechanism`, `beneficiary`, `risk_tags`, `evidence_refs`.

Даже если source остаётся rules-first, запись должна быть ближе к unified schema.

### Что Это Даёт

Тогда deterministic truth станет:

- не “другим миром labels”;
- а строгой версией того же самого finding language.

## Что Делать С RuntimeAuditor

LLM-аудитор уже пишет findings в richer форме.

Нужно:

- перестать делать `violation_type` основным ключом сравнения;
- сохранить `summary`, `mechanism`, `beneficiary`, `evidence_refs` как first-class output;
- `risk_tags` генерировать как несколько тегов, а не одну обязательную метку.

### Практический Вывод

`AuditFinding` нужно переделать так, чтобы:

- `violation_type` перестал быть обязательным core identifier;
- `violation_type_freeform` перестал быть “дополнением”;
- на первом месте стояли `summary + mechanism + evidence + beneficiary`.

## Что Делать С `truth_freeform.jsonl`

Этот слой уже ближе всего к нужной архитектуре.

Но его нужно:

- привести к тому же unified schema;
- перестать считать “вольным приложением” к hardcoded truth;
- сделать вторым truth contour, а не побочным sidecar.

## Новый Принцип Evaluation

### Старый Принцип

`runtime_finding.violation_type == truth_record.violation_type`

### Новый Принцип

`runtime_finding ~= truth_record`

То есть нужен matcher, а не label equality.

## Новый Компонент: `FindingMatcher`

Нужен отдельный модуль сравнения.

Он должен решать:

описывают ли два finding-а один и тот же эпизод?

### Критерии Совпадения

#### 1. Subject Match

Совпадает ли `subject_agent_id`.

#### 2. Target / Beneficiary Match

Совпадает ли:

- `target_agent_id`;
- или `beneficiary`;
- или semantic overlap по связанному актору.

#### 3. Temporal Proximity

Находятся ли записи в допустимом окне по тикам.

Например:

- exact tick;
- или `±1 tick`, если finding описывает развернувшийся эпизод.

#### 4. Evidence Overlap

Совпадают ли:

- `event_type`;
- `tick`;
- `vote_id`;
- `target_agent_id`;
- timestamp / work_id / channel hints.

#### 5. Semantic Similarity

Сходны ли `summary + mechanism`.

Это может быть:

- отдельный LLM matcher;
- или embeddings + rule overlap;
- или гибрид.

## Рекомендуемая Архитектура Matcher

### Stage 1. Deterministic Candidate Filtering

Сначала отбираются только пары finding-ов, у которых:

- совпадает subject;
- ticks достаточно близки;
- есть overlap по evidence или target.

### Stage 2. Semantic Adjudication

Потом отдельный matcher решает:

- same incident;
- related but not same;
- different.

Именно здесь допустим LLM.

### Почему Так Лучше

Потому что:

- LLM не перебирает все пары подряд;
- интерпретация остаётся ограниченной;
- matching становится richer, но не теряет управляемость.

## Как Это Отразится На Sidecars

### `truth.jsonl`

Остаётся, но в unified schema.

### `truth_freeform.jsonl`

Остаётся, тоже в unified schema.

### `audit findings` inside `events.jsonl`

Тоже ближе к unified schema.

### `evaluation.json`

Строгий baseline можно сохранить, но уже не как simple label equality.

### Новый Sidecar

Рекомендуется добавить:

- `evaluation_match.json`

или

- `evaluation_semantic.json`

Содержимое:

- `strict_tp`
- `strict_fp`
- `strict_fn`
- `semantic_tp`
- `semantic_fp`
- `semantic_fn`
- объяснения match decisions.

## Что Делать С `risk_tags`

Они нужны, но не как жёсткая taxonomic cell (ячейка таксономии).

Правильная роль `risk_tags`:

- coarse navigation;
- аналитическая группировка;
- исследовательская визуализация.

Неправильная роль:

- единственный ключ сопоставления.

## Изменения По Модулям

### 1. `auditor.py`

Нужно:

- перестроить `AuditFinding` в unified schema;
- убрать центральность `violation_type`;
- усилить output around:
  - `summary`
  - `mechanism`
  - `beneficiary`
  - `risk_tags`
  - `evidence_refs`

### 2. `truth.py`

Нужно:

- перестроить `TruthRecord` в совместимую unified форму;
- даже для rules-first truth писать richer structured content.

### 3. `oracle.py`

Нужно:

- привести `FreeformTruthRecord` к той же unified форме;
- не держать его как принципиально отдельную модель.

### 4. `evaluation.py`

Нужно:

- переписать evaluation как matching layer;
- добавить candidate generation + semantic matching;
- отказаться от label equality как основной логики.

### 5. `docs`

Нужно:

- явно описать unified finding architecture;
- разделить strict and semantic evaluation;
- объяснить, почему это методологически лучше соответствует LLM-аудиту.

## Фазы Реализации

### Фаза 0. Ввести Unified Schema

Цель:

- одинаковая semantic форма для runtime findings, deterministic truth и freeform truth.

### Фаза 1. Переписать Evaluation На Matching

Цель:

- сравнивать эпизоды, а не labels.

### Фаза 2. Добавить Semantic Evaluation Sidecar

Цель:

- не ломая baseline, получить richer исследовательскую метрику.

### Фаза 3. Повторный Полный Прогон

Цель:

- проверить, превратился ли ненулевой runtime audit в ненулевой semantic TP.

## Критерий Успеха

Следующий цикл считается успешным, если:

1. runtime findings и truth records начинают матчиться хотя бы семантически;
2. exact label equality перестаёт быть единственным каналом успеха;
3. unified freeform architecture делает поведение аудитора и truth-layer более интерпретируемым, а не менее;
4. метрики становятся ближе к реальной логике LLM-аудита, описанной в ВКР.

## Статус

Это план следующего архитектурного шага после `full_ecology_personal_ecology_llm_audit_50t_20260318_1`.

Главная идея:

> не выравнивать labels, а перейти к единому свободному языку findings и отдельному semantic matcher.
