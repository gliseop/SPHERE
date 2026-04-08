# План доработок по validation-run `procurement_tender_20260407_validation`

Дата: 2026-04-08

## Основание

План составлен по результатам разбора нового validation-run:

- `results/procurement_tender_20260407_validation/summary.json`
- `results/procurement_tender_20260407_validation/evaluation.json`
- `results/procurement_tender_20260407_validation/fidelity.json`
- `results/procurement_tender_20260407_validation/world_history.md`

Для сравнения использовался прежний run:

- `results/procurement_tender_10tick_fresh_20260406/summary.json`
- `results/procurement_tender_10tick_fresh_20260406/world_history.md`

## Краткий диагноз

Новый run стал формально чище, но менее реалистичным как организационная симуляция.

Основной сбойный профиль:

- всего `4` `arbiter_approved` против `44` `arbiter_rejected`;
- `message_sent = 0`;
- `pending_interaction_due = 0`, `pending_interaction_expired = 0`;
- сохраняются `arbiter_op_failed` на worldgen artifacts с `Unknown owner_org_id`;
- сохраняются поломки prompt-layer мотивации в `core`-режиме;
- semantic judges почти ничего не находят даже на явно проблемном run.

Иными словами: причинный overclaim уменьшился, но вместе с ним схлопнулась и живая динамика мира.

## Цель плана

Вернуть run к более живой и реалистичной materialization-динамике без возврата к запрещённым semantic hardcodes.

Приоритет:

1. сначала обогатить prompt-layer и world-context так, чтобы агент и materializer лучше понимали физику мира;
2. затем добавить только минимально необходимую runtime-поддержку там, где prompt сам по себе уже не может исправить разрыв;
3. только потом калибровать semantic judges.

## Реестр проблем и решений

| Приоритет | Проблема | Симптом в run | Где чинить | Решение |
|---|---|---|---|---|
| `P0` | Агенту и materializer не хватает контекста о физике мира | Много `unknown to_id`, `private_contact_requires_shared_zone`, `unknown work_id`; агент описывает естественный ход, но не знает, кому и где реально писать/идти | `src/sphere_lc/agent.py`, `src/sphere_lc/prompts.yaml`, частично `src/sphere_lc/arbiter.py` | Усилить prompt-layer: давать агенту и арбитру richer context о том, кто где обычно бывает, кто уже доступен, кому сначала нужно написать, если локация неочевидна, и какие `work/artifact/channel` реально есть в сцене. Первый слой решения — prompt/context enrichment, а не новый hardcoded semantic resolver. Runtime support допустим только как узкий контрактный слой для already-resolved anchors. |
| `P0` | Побочные ops materializer'а слишком хрупкие и валят весь шаг | `unknown agent_a_id`, `unknown target_agent_id`, `unsupported op_type: information_signal` | `src/sphere_lc/prompts.yaml`, `src/sphere_lc/arbiter.py` | Сначала уточнить prompt contract для side-effects: actor по умолчанию должен считаться источником связи/obligation, а `information_signal` должен называться канонически. Затем добавить минимальный runtime-tolerance слой: не ронять весь основной шаг из-за полупустого side-effect, если прямое действие уже grounded. |
| `P1` | Одобряются пустые `work_note_added` | В run несколько `work_note_added` с пустым `text` | `src/sphere_lc/prompts.yaml`, `src/sphere_lc/arbiter.py`, `src/sphere_lc/tracing.py` | Сначала выяснить причину через trace: это пустой candidate text из materializer, пустой rewrite от grounding verifier или потеря текста при normalization. После этого править prompt-layer document materialization. Только если trace покажет, что проблема переживает prompt-fix, добавлять runtime gate на пустой документ. |
| `P1` | Мотивационный блок всё ещё бит в `core`-режиме | В agent inputs встречается `Чего ты добиваешься: Волков А.С`, `Что для тебя выглядит выгодой: Марченко Т.Л` | `src/sphere_lc/persona.py`, `src/sphere_lc/agent.py`, `src/sphere_lc/prompts.yaml` | Пояснение: проблема не в самой идее motivation layer, а в том, что validation-сценарий идёт в `core`-режиме, где digest не строится и prompt падает в плохой fallback. Нужно сделать нормальный lightweight digest для `core` из `summary + biography` и убрать fallback, который подставляет имя/`story_state` как цель или выгоду. |
| `P1` | Worldgen external surface остаётся неполной | `worldgen_artifact` падает на `Unknown owner_org_id: 'org:oversight'` и `org:media` | `src/sphere_lc/worldgen.py`, `src/sphere_lc/prompts.yaml`, при необходимости `src/sphere_lc/engine.py` | Первый слой решения тоже prompt-level: worldgen должен явно понимать, что если он создаёт внешний артефакт, то сначала обязан materialize-ить внешнюю организацию через `entity_creations`. Только если после усиления prompt-contract проблема сохранится, добавлять repair/preflight в runtime. |
| `P2` | Semantic judges недочувствительны | `semantic_realism_findings_total = 0`, `semantic_true_positive = 0`, хотя run явно проблемный | `src/sphere_lc/fidelity.py`, `src/sphere_lc/auditor.py`, `src/sphere_lc/evaluation.py`, `src/sphere_lc/prompts.yaml` | Калибровать judges только после починки materialization. Затем пересмотреть prompt examples и schema-guidance для `semantic_realism`, `actuation_verifier`, `semantic_match`, чтобы они видели stall patterns, empty notes, unresolved external entities и recurrent grounding failures как значимые realism failures. |

## Что изменено после комментариев

После комментариев план пересмотрен в prompt-first логике.

Что это означает practically:

1. Основной источник проблем в этом run рассматривается не как нехватка новых rule-based resolver'ов, а как бедный или неточный контекст в prompt-layer.
2. Runtime-изменения допускаются только как узкий контрактный safety layer:
   - не валить весь ход из-за второстепенного side-effect;
   - не коммитить заведомо пустой документ;
   - не создавать artifact на несуществующем owner без предварительной materialization.
3. Пункт про motivation уточнён:
   - проблема не в самой interview-grounded архитектуре;
   - проблема в плохом fallback для `core`-режима, где validation-run не получает полноценный `motivation`-digest.

## Порядок внедрения

## Шаг 1. Prompt-layer enrichment для физики контакта и адресации

### Что делать

- Усилить agent prompt и arbiter prompt следующими контекстными блоками:
  - кто и где обычно находится сейчас;
  - какие контакты доступны напрямую, а какие требуют сначала связаться или встретиться;
  - кому естественно сначала написать, если физическая локация адресата неочевидна;
  - какие `work:*`, `art:*`, `chan:*`, `org:*` реально существуют и как они называются в текущем мире.
- Добавить в prompts явные examples для ходов типа:
  - “сначала спрошу, где человек находится, потом подойду”;
  - “если не уверен в локации, сначала пиши, а не пытайся мгновенно встретиться лично”;
  - “не придумывай новых адресатов и новых служебных сущностей”.
- Только если после этого `unknown to_id` остаётся доминирующим профилем, вводить узкий runtime resolver для already-mentioned entities.

### Практическая цель

Снизить долю `unknown to_id` и неуместных `private_contact_requires_shared_zone` за счёт лучшего понимания агентом физики мира до materialization.

### Минимальные тесты

- новый prompt-level regression test: агент при неизвестной локации адресата сначала выбирает message/lookup path;
- regression: меньше rejected `perform` на простых contact scenarios;
- optional follow-up: если понадобится runtime fallback, отдельно тестировать only-safe resolution уже существующих anchors.

## Шаг 2. Prompt contract для side-effects и минимальный safety layer

### Что делать

- Уточнить arbiter prompt:
  - actor по умолчанию является источником `upsert_informal_link` и `upsert_pending_interaction`;
  - `information_signal` должен называться канонически;
  - side-effect не должен быть важнее основного наблюдаемого шага.
- Добавить минимальный runtime safety layer:
  - actor-centered defaults для пустых source-полей;
  - alias `information_signal -> add_information_signal`;
  - side-effect, который не удалось дограундить, не должен откатывать already-valid main op.

### Практическая цель

Убрать отказы из-за служебных побочных ops, когда основной наблюдаемый ход уже был правдоподобен.

### Минимальные тесты

- `perform` с пустым `agent_a_id` в `upsert_informal_link` не валит основной шаг;
- `information_signal` alias проходит в `add_information_signal`;
- пустой pending side-effect не откатывает valid `send_message` или `narrative_action`.

## Шаг 3. Root-cause анализ пустых note, потом правка document prompts

### Что делать

- Сначала по `trace.jsonl` и `world_history.md` выяснить, где именно пустеет note:
  - materializer возвращает пустой `text`;
  - grounding verifier переписывает в пустоту;
  - normalization теряет текст;
  - агентский `reply` систематически ведёт к неинформативной note.
- После локализации причины переписать prompt-layer document materialization:
  - note должна быть краткой, но не пустой;
  - note должна фиксировать только реально наблюдаемое;
  - если содержательного текста нет, лучше reject, чем пустой commit.
- Runtime gate на пустую note вводить только как финальный safety-net, а не как главное исправление.

### Практическая цель

Заменить текущий паттерн “approved, но `text == ''`” на либо содержательную note, либо честный reject.

### Минимальные тесты

- trace-based regression test на локализованную причину пустой note;
- новый тест на то, что пустая note не коммитится как валидный документарный след.

## Шаг 4. Lightweight motivation digest для `core`

### Что делать

- Расширить `PersonaGenerator.generate_core(...)`, чтобы он по итогам `summary + biography` тоже строил lightweight `motivation`.
- В `AgentRunner._motivation_block(...)` сделать fallback более строгим:
  - сначала `persona.motivation`;
  - затем мягкий текстовый fallback;
  - никогда не использовать имя как `goal/gain`.

### Практическая цель

Убрать повторяющийся broken stakes-block в коротких и validation-сценариях без full interview enrichment.

### Пояснение к этому шагу

Идея не в том, чтобы снова “усилить психологию” как отдельный проект. Идея в том, чтобы убрать плохой fallback:

- сейчас `full`-режим уже умеет работать с нормальным `motivation`;
- validation-run идёт в `core`, и там prompt-layer проваливается в имя/`story_state`;
- из-за этого агент принимает более плоские решения ещё до арбитра.

### Минимальные тесты

- новый тест на `core`-persona с non-empty `motivation`;
- regression-тест на отсутствие `Чего ты добиваешься: <имя>` в user prompt.

## Шаг 5. Prompt-first починка worldgen external surface

### Что делать

- Уточнить worldgen prompt:
  - внешний artifact не должен появляться раньше external owner;
  - если нужен `org:media`, `org:oversight` или иная внешняя поверхность, сначала вернуть `entity_creations`.
- После prompt-fix посмотреть, остаётся ли `Unknown owner_org_id`.
- Только если остаётся, добавить минимальный preflight verifier в runtime:
  - проверить зависимости artifact перед apply;
  - если dependency не существует и не создана в `entity_creations`, писать явный diagnostic event;
  - optional repair-pass допустим только как fallback после prompt-level исправления.

### Практическая цель

Сделать внешнюю институциональную поверхность мира доопределяемой и причинно непрерывной.

### Минимальные тесты

- regression: artifact не падает на `Unknown owner_org_id`, если внешняя org указана в `entity_creations`;
- новый тест на diagnostic path, если worldgen нарушил этот контракт.

## Шаг 6. Калибровка semantic judges

### Что делать

- После внедрения шагов 1-5 снова прогнать procurement validation scenario.
- По новому run пересмотреть prompts:
  - `fidelity.semantic_judge`;
  - `auditor.actuation_verifier`;
  - `evaluation.semantic_match`.
- Добавить contrastive examples на:
  - repeated `unknown to_id`;
  - empty note;
  - failed worldgen external artifact;
  - organizational stall при высоком количестве rejects.

### Практическая цель

Сделать judges чувствительными к реальным realism failures, а не только к уже богатому и хорошо materialized миру.

### Минимальные тесты

- synthetic test, где `semantic_realism_findings_total > 0` на repeated `unknown to_id`;
- synthetic test, где `evaluation.semantic_true_positive > 0` на содержательно эквивалентных findings после полного run.

## Тестовый контур

После каждого шага прогонять:

- `pytest tests/test_sphere_lc_review_fixes.py`
- `pytest tests/test_auditor.py`
- `pytest tests/test_truth_evaluation.py`
- `pytest tests/test_worldgen_personal_ecology.py`

После шагов 1-5 обязательно повторить:

- `sphere-lc run --scenario scenarios/procurement_tender.json --out results/procurement_tender_<date>_validation`

## Целевые метрики следующего validation-run

Минимальный инженерный target:

- `arbiter_approved` заметно больше `4`;
- `arbiter_rejected` заметно меньше `44`;
- `message_sent > 0`;
- `work_note_added` без пустых `text`;
- `arbiter_op_failed` по `Unknown owner_org_id` отсутствуют;
- в prompt-layer больше нет broken stakes вроде `Чего ты добиваешься: Волков А.С`.

Желательный target:

- появляется хотя бы один `audit_flagged` или `semantic_realism_finding` на содержательно проблемном procurement episode;
- run сохраняет причинную связность без возврата к старым document overclaims и pending-expiration failures.

## Итог

Этот план не предлагает новых keyword/substring heuristics. Все исправления строятся вокруг:

- лучшего target grounding;
- более надёжной materialization side-effects;
- non-empty document grounding;
- interview- и biography-grounded motivation;
- worldgen dependency repair;
- отдельной калибровки semantic judges после починки мира, а не вместо неё.
