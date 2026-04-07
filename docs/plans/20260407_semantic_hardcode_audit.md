# Аудит запрещённых semantic hardcodes

Дата: 2026-04-07

## Цель

Проверить primary paths и смежные helper-слои на решения, нарушающие архитектурные принципы 8-13 из `AGENTS.md`: hand-written semantic fixes, keyword/substring rubric'и, lexical post-processing как основной способ понимания смысла, а также жёсткое semantic coercion в realism/truth/evaluation/fidelity.

## Область аудита

- `src/sphere_lc/arbiter.py`
- `src/sphere_lc/worldgen.py`
- `src/sphere_lc/auditor.py`
- `src/sphere_lc/truth.py`
- `src/sphere_lc/evaluation.py`
- `src/sphere_lc/fidelity.py`
- `src/sphere_lc/prompts.py`
- `src/sphere_lc/utils.py`
- `web/backend/routes/ai.py`
- `web/backend/routes/personalities.py`

## Краткий вывод

После недавней реализации stepwise `perform`, document grounding, interview-grounded motivation layer и worldgen `entity_creations` самые рискованные semantic hardcodes сместились не в `arbiter.py` и не в `worldgen.py`, а в post-hoc и governance-sidecar слои:

1. `fidelity.py` всё ещё считает часть realism-метрик через lexical rubric'и.
2. `auditor.py` частично принудительно переводит freeform findings в маленькую hand-written каноническую таксономию до policy-layer.
3. `auditor.py` использует violation-specific rule scoring для привязки evidence.
4. `evaluation.py` называет matching semantic, но фактически остаётся label-exact и не делает настоящего semantic сопоставления по `summary` / `mechanism`.

## Findings

### P0. `fidelity.py` всё ещё определяет realism через lexical rubric'и

Файлы и строки:

- `src/sphere_lc/fidelity.py:20-52`
- `src/sphere_lc/fidelity.py:126-150`
- `src/sphere_lc/fidelity.py:367-380`

Проблема:

- `narrating_leakage_total` считается через `_LEAK_ACTION_HINTS` и `_STRONG_STATE_LEAK_HINTS`.
- `bureaucratic_loop_total` частично завязан на `_BUREAUCRATIC_TYPES`.
- Это именно hand-written lexical rubric для realism/fidelity, то есть прямое нарушение принятых архитектурных ограничений.

Почему это критично:

- Лексические списки в realism-sidecar начинают играть роль semantic judge без явного БЯМ-verifier pass.
- Метрика легко будет давать ложные срабатывания на phrasing drift и пропускать те же проблемы в других формулировках.
- Внутри одного и того же проекта уже существует более корректный путь: `augment_fidelity_with_semantic_judge(...)`.

Что делать:

1. Убрать lexical detection из `narrating_leakage_total` и `bureaucratic_loop_total` как primary mechanism.
2. Оставить в deterministic fidelity только structural counters:
   - temporal violations;
   - phantom rejections;
   - perform approvals;
   - raw world/reputation counts;
   - при необходимости технические name-format checks.
3. Перенести leakage/loop/document overclaim/external-surface-gap в отдельный semantic realism judge.
4. Если нужен hybrid слой, сделать его как explicit verifier pass с category schema, а не как `any(token in normalized ...)`.

### P1. `auditor.py` принудительно канонизирует freeform findings через hand-written inference

Файлы и строки:

- `src/sphere_lc/auditor.py:863-916`
- `src/sphere_lc/auditor.py:957-1060`
- `src/sphere_lc/auditor.py:1088-1117`

Проблема:

- `_normalize_violation_type(...)` и `_infer_canonical_violation_type(...)` принудительно переводят non-canonical finding в один из нескольких заранее заданных `violation_type`.
- Перевод делается не через отдельный БЯМ-verifier, а через hand-written связку `evidence_types` + `recent_private_contacts` + `has_yes_vote`.
- Затем `_baseline_recommended_action(...)` выбирает управленческую реакцию по этому же small fixed menu.

Почему это проблема:

- Здесь policy-layer и semantic interpretation смешаны в одном месте.
- Freeform finding формально разрешён архитектурой, но в runtime-path он быстро сжимается обратно в жёсткую taxonomy.
- Новые классы institutional risk будут либо теряться, либо насильно маппиться в ближайший канонический ярлык.

Что делать:

1. Разделить:
   - freeform risk finding;
   - verifier/actuation pass;
   - policy mapping.
2. Вместо `_infer_canonical_violation_type(...)` ввести отдельный БЯМ-verifier, который отвечает структурой вида:
   - `actuation_class`;
   - `runtime_support_level`;
   - `policy_relevance`;
   - `recommended_escalation`.
3. Канонический `violation_type` оставить только как optional internal compatibility field, а не как обязательный semantic sink.
4. Policy-layer должен работать по `actuation_class`, а не по hand-written попытке угадать “настоящий” violation type из пары event-types.

### P1. `auditor.py` использует violation-specific evidence scoring

Файлы и строки:

- `src/sphere_lc/auditor.py:1206-1275`

Проблема:

- `_evidence_score(...)` вручную начисляет разные веса в зависимости от `finding.violation_type`.
- Например:
  - для `preferential_treatment_for_connected_actor` усиливается private `message_sent`;
  - для `non_escalation_under_pressure` усиливаются `message_sent` / `work_note_added` / `work_item_created` / `work_proposal_submitted`;
  - для `partial_disclosure_under_deadline_pressure` усиливается публичное `message_sent` в `chan:*` / `org:*`.

Почему это проблема:

- Это уже не структурный bind по субъекту/цели/времени, а hand-written semantic rubric.
- Причём rubric сидит в evidence-binding слое, то есть влияет на то, какие факты аудитор потом будет считать “подтверждением” finding'а.

Что делать:

1. Оставить в deterministic candidate retrieval только нейтральные structural признаки:
   - субъект;
   - цель;
   - временная близость;
   - прямые ссылки на `target_agent_id`, `to_id`, `case_id`, `work_id`.
2. После retrieval запускать отдельный verifier/binder pass, который ранжирует candidate evidence уже через БЯМ.
3. Если нужен fallback без LLM, он должен быть generic и не зависеть от конкретного `violation_type`.

### P1. `evaluation.py` остаётся label-exact и не даёт настоящего semantic matching

Файлы и строки:

- `src/sphere_lc/evaluation.py:396-455`
- `src/sphere_lc/evaluation.py:340-355`

Проблема:

- `_finding_match_score(...)` не использует `summary` и `mechanism` содержательно.
- `_violation_type_match(...)` требует точного строкового совпадения.
- `_case_key(...)` строится как `subject + violation_type + counterparty`.
- В результате semantic/case evaluation остаётся жёстко привязанным к совпадению labels, даже если два finding'а содержательно эквивалентны.

Почему это проблема:

- Это не keyword heuristic в узком смысле, но это всё ещё hidden hard coupling на fixed labels.
- Такой evaluation penalize'ит как раз тот свободный semantic layer, который проект пытается развивать через freeform truth и LLM-first audit.

Что делать:

1. Перестать трактовать `violation_type` как обязательный semantic anchor.
2. Сделать настоящий semantic matcher:
   - similarity `summary`;
   - similarity `mechanism`;
   - overlap `risk_tags`;
   - overlap `beneficiary` / `target`;
   - evidence overlap;
   - optional verifier pass для borderline cases.
3. `violation_type` оставить лишь как одну из features, а не как hard gate.
4. Для case-level matching перейти от fixed tuple к normalized episode representation или отдельному case-clustering pass.

## Не зафиксировано как нарушение в этом аудите

### `arbiter.py`

Новый путь с `perform_plan` и `document_grounding` соответствует принятым ограничениям лучше старой схемы. Основное semantic adjudication здесь уже вынесено в explicit БЯМ-passes.

### `worldgen.py`

`entity_creations` и post/pre-worldgen materialization выглядят архитектурно корректно: генератор мира через БЯМ может доопределять внешнюю физику, а deterministic layer только валидирует и применяет контракт.

### `truth.py`

Текущий `TruthDetector` остаётся rules-first, но в пределах допустимой structural truth зоны: он не делает keyword-based выводы по свободному тексту, а опирается на наблюдаемые event-структуры (`vote_opened`, `vote_cast`, `reputation_modified`, private contact window).

### `utils.py` и `web/backend/routes/ai.py`

Лексические проверки имён, slug'ов, role-label и duplicate-name пока не считаются нарушением этого аудита. Это технический слой format/ID validation, а не semantic reasoning о коррупции, мотивах, risk episodes или document realism. Но эти helper'ы не должны расползаться в governance/truth/fidelity semantics.

## Рекомендуемый порядок исправлений

1. `fidelity.py`: удалить lexical realism heuristics из primary counters.
2. `auditor.py`: вынести canonical coercion из `_normalize_violation_type(...)` в отдельный verifier/actuation pass.
3. `auditor.py`: заменить violation-specific `_evidence_score(...)` на generic retrieval + verifier binder.
4. `evaluation.py`: переделать semantic/case matching так, чтобы он перестал зависеть от exact label equality.

## Итог

Первичный аудит выполнен. Наиболее чистыми после недавнего рефакторинга выглядят `arbiter.py`, `worldgen.py` и `truth.py`. Основной остаточный semantic hardcode debt локализован в `fidelity.py`, `auditor.py` и `evaluation.py`.
