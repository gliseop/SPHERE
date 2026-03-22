# Анализ прогона `full_ecology_25t_20260322_fast_rerun`

## Что запускалось

- Сценарий: `results/full_ecology_25t_20260321_fast_rerun_input.json`
- Отличия от предыдущего локального прогона:
  - убран `provider_order=["Groq"]`
  - включён `memory.embeddings_mock=true`
- Каталог результата: `results/full_ecology_25t_20260322_fast_rerun`

## Статус

Прогон завершён штатно:

- `status.json`: `state=finished`
- последний tick: `24` (то есть 25 тиков выполнены полностью)

## Итоговые метрики

### Governance / evaluation

- `truth_total=27`
- `runtime_flagged_total=107`
- strict: `precision=0.0093`, `recall=0.037`, `f1=0.0149`
- semantic: `precision=0.2336`, `recall=0.9259`, `f1=0.3731`

По типам:

- `preferential_treatment_for_connected_actor`: truth 8 / signals 52
- `support_vote_after_private_contact`: truth 12 / signals 24
- `non_escalation_under_pressure`: truth 4 / signals 15
- `partial_disclosure_under_deadline_pressure`: truth 3 / signals 10

### Fidelity

- `temporal_violations_total=0`
- `identity_machine_name_total=0`
- `identity_role_alias_total=0`
- `phantom_rejection_total=1`
- `world_event_total=21`
- `narrating_leakage_total=1`
- `perform_approved_total=0`

## Что получилось по скорости

### Полный завершённый rerun всё ещё очень медленный

Суммарно traced LLM-время:

- `memory`: ~2267 s
- `agent`: ~1412 s
- `auditor`: ~1051 s

Самые медленные вызовы:

- `memory / agent:sec_aleksei_volkov`, tick 17: ~146 s
- `agent / agent:deputy`, tick 5: ~146 s
- `auditor / runtime_auditor`, tick 16: ~116 s
- `auditor / runtime_auditor`, tick 10: ~109 s
- `memory / agent:head`, tick 21: ~101 s

### Сравнение честного общего префикса `tick<=16`

Чтобы не путать эффект настроек с простым ростом длины прогона, сравнивался одинаковый префикс `tick<=16`.

Результат:

- у fast-rerun `memory` уже набрал ~1228 s против ~792 s у оборванного прогона;
- `auditor`: ~702 s против ~404 s;
- `agent`: ~891 s против ~991 s.

Вывод:

- снятие pin на Groq и mock-эмбеддинги не дали решающего ускорения;
- лёгкое улучшение есть только по `agent`-вызовам;
- основная тяжесть сместилась в `memory` и `auditor`.

## Интерпретация

### 1. Гипотеза “это только embeddings/Groq” не подтвердилась

Если бы главным bottleneck был Groq-pin или реальные embeddings, после их снятия/заглушки должны были заметно упасть хотя бы `auditor` и `memory`. Этого не произошло.

### 2. Основной bottleneck сейчас — growth в governance/memory loop

На префиксе `tick<=16` fast-rerun генерирует намного больше:

- `audit_flagged`
- `audit_case_updated`
- `audit_escalated`
- `audit_explanation_requested`

Это раздувает:

- видимые агентам события;
- контекст runtime-аудитора;
- объём рабочей памяти и суммаризаций.

Иными словами, routing tweak уменьшил часть сетевой неопределённости, но сам динамический governance-контур стал активнее и тем самым увеличил нагрузку на следующие тики.

### 3. Главный тормоз — не `agent`, а `memory` + `auditor`

После твика стало видно, что даже если agent-layer не главный, post-tick стоимость огромна:

- последовательные memory summaries;
- один большой audit LLM call на тик;
- всё это на фоне растущего event history.

## Практический вывод

Для реального ускорения следующий шаг должен быть не в смене routing одного провайдера, а в сокращении объёма post-tick работы:

1. ограничить/редуцировать контекст `RuntimeAuditor`;
2. дебаунсить или батчить `memory`-суммаризацию;
3. перестать кормить агентам тяжёлые payload’ы `audit_case_updated` / `arbiter_op_failed`;
4. только после этого добавлять per-role timeout и fallback model.

## Что пробовать дальше

1. `memory`: суммаризировать не каждый тик, а раз в N тиков или при явном переполнении.
2. `auditor`: снизить `lookback_events`, урезать `open_audit_cases` / `pending_obligations`.
3. `agent`: заменить сырой replay `visible_events[-50:]` на компактный event digest.
4. Ввести per-role timeout:
   - `agent`: 30 s
   - `memory`: 20-25 s
   - `auditor`: 45 s
5. После этого повторить тот же 25-тик run и сравнить уже не общие суммы, а wall-clock на одинаковом префиксе.
