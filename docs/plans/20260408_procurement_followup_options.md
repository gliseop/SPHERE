# Варианты следующего этапа после `procurement_tender_20260408_validation_after_promptfix`

Дата: 2026-04-08

## Контекст

После последнего цикла правок validation-run улучшился по нескольким ключевым симптомам:

- `arbiter_approved`: `4 -> 13`
- `arbiter_rejected`: `44 -> 35`
- `message_sent`: `0 -> 23`
- `narrative_action`: `2 -> 9`
- пустые `work_note_added`: `5 -> 0`
- `arbiter_op_failed`: `2 -> 0`
- `Unknown owner_org_id`: `72 -> 0`
- broken stakes в prompt-layer (`Чего ты добиваешься: Волков А.С` и т.п.): `24 -> 0`

Но остались заметные проблемы:

- `unknown to_id` всё ещё главный rejection-профиль;
- `private_contact_requires_shared_zone` по-прежнему частый стоп-фактор;
- `pending_interaction_due = 17`, `pending_interaction_expired = 4`;
- `worldgen_artifact_dependency_missing` стал диагностируемым, но его всё ещё много;
- governance и semantic judges по-прежнему молчат (`audit_flagged = 0`, `semantic_realism_findings_total = 0`).

## Цель документа

Дать три реалистичных варианта следующего этапа работ с понятными trade-off'ами.

## Вариант A. Остаться в prompt-first логике

### Суть

Продолжать усиливать prompt-layer и контекст, почти не трогая runtime-логику.

### Что делать

1. Усилить spatial/contact examples в `agent` и `arbiter` prompts:
   - если не знаешь точную локацию адресата, сначала пиши или уточняй;
   - не пытайся личный контакт как первый шаг без подтверждённой co-location;
   - если у тебя есть `agent:*`, используй именно его, а не display-name/fuzzy form.
2. Уточнить prompt contract для `perform_plan`:
   - если конечная цель личный разговор, но совместная зона не гарантирована, первым шагом должен быть message/lookup, а не meet.
3. Уточнить worldgen prompt:
   - если dependency missing повторяется, это значит, что output-контракт по `entity_creations` всё ещё недостаточно явный.
4. Калибровать judges через richer examples:
   - repeated `unknown to_id`;
   - organizational stall;
   - expired pending interactions.

### Плюсы

- Максимально соответствует принятым архитектурным ограничениям.
- Минимальный риск незаметно скатиться в semantic hardcodes.
- Дешевле по коду и менее конфликтно для существующего runtime.

### Минусы

- Может не дожать `unknown to_id`, если проблема уже не только в prompt, а в отсутствии runtime affordance.
- Может дать ещё один частичный прогресс вместо резкого улучшения.

### Когда выбирать

Если приоритет — сохранить чистоту архитектуры и не вводить новые runtime-mechanisms до предела.

## Вариант B. Сбалансированный путь

### Суть

Сохранить prompt-first как основной принцип, но добавить минимальные runtime affordances там, где повторяющийся сбой уже стабилен и предсказуем.

### Что делать

1. Всё из варианта A по prompt-layer.
2. Добавить узкий runtime path для contact-affordance:
   - если step явно адресован существующему `agent:*`, но personal contact невозможен из-за зоны, не валить весь ход сразу;
   - позволить materializer честно деградировать в `send_message` или `pending_interaction`, если это уже содержится в человеческом смысле шага.
3. Добавить follow-up policy для pending interactions:
   - expired reply-обязательства не просто истекают, а попадают в отдельный semantic pressure/judge context;
   - при повторном expiry возникает richer runtime signal.
4. Перевести judges на новый набор симптомов уже после этого.

### Плюсы

- Наиболее вероятный путь к следующему заметному улучшению realism без сильного архитектурного дрейфа.
- Бьёт именно по двум главным остаточным симптомам: `unknown to_id` и `pending_interaction_expired`.
- Не требует строить большой новый planner-движок.

### Минусы

- Уже требует аккуратной границы между допустимой affordance-логикой и скрытым semantic resolver'ом.
- Нужны хорошие regression-тесты, чтобы не начать silently auto-correct'ить смысл.

### Когда выбирать

Рекомендуемый вариант, если цель — получить следующий заметный прирост реалистичности в коротком цикле.

## Вариант C. Агрессивный runtime-step orchestration

### Суть

Сделать следующий шаг materialization существенно умнее за счёт richer internal planner/runtime choreography.

### Что делать

1. Расширить `perform_plan` в сторону явных affordance-типов:
   - `lookup`;
   - `reach_out`;
   - `move`;
   - `meet`;
   - `record`.
2. Разрешить planner'у порождать промежуточные safe ops, которых не было явно сказано в proposal, если они необходимы для физически правдоподобного перехода.
3. Сделать pending interactions first-class частью orchestration, а не только побочным следом.
4. После этого полностью пересобрать semantic judges под новую richer event surface.

### Плюсы

- Потенциально самый сильный прирост realism.
- Может резко снизить syntactic stall patterns.
- Даёт более естественный офисный ритм.

### Минусы

- Самый высокий риск выйти за рамки принятых ограничений и начать “додумывать” поведение вместо честной materialization.
- Сильно больше кода, больше новых failure modes.
- Потребует отдельной синхронизации с `AGENTS.md` и docs.

### Когда выбирать

Только если после варианта A или B станет ясно, что текущая freeform -> stepwise materialization принципиально слишком бедна.

## Сравнение вариантов

| Вариант | Скорость внедрения | Риск архитектурного дрейфа | Ожидаемый прирост realism | Рекомендация |
|---|---|---|---|---|
| `A` | Высокая | Низкий | Умеренный | Хороший safe path |
| `B` | Средняя | Средний | Высокий | Рекомендуемый |
| `C` | Низкая | Высокий | Потенциально очень высокий | Только как следующий этап |

## Рекомендуемый выбор

Рекомендую `Вариант B`.

Причина:

- `A` уже частично был реализован и дал заметное улучшение;
- значит следующий прирост, скорее всего, требует не отказа от prompt-first, а его усиления плюс узкий runtime affordance layer;
- `C` пока слишком тяжёл и рискован, особенно до того, как будут выжаты возможности `B`.

## Практический план для варианта B

1. Усилить prompt-layer на тему contact reachability и промежуточного message/lookup шага.
2. Добавить минимальный runtime affordance для failed private contact:
   - не auto-fix любого шага;
   - только safe degradation там, где уже есть явный адресат и смысл контакта не меняется.
3. Пересобрать pending-interaction follow-up policy, чтобы `expired` не были просто таймерным мусором.
4. Повторить validation-run.
5. Только после этого калибровать judges.

## Целевые метрики следующего прогона

- `arbiter_approved > 13`
- `arbiter_rejected < 35`
- `unknown to_id < 7`
- `private_contact_requires_shared_zone` заметно ниже текущего уровня
- `pending_interaction_expired = 0..1`
- `audit_flagged >= 1` или `semantic_realism_findings_total >= 1`

## Итог

Следующий разумный шаг — не переписывать движок заново, а сделать ещё один цикл `prompt-first + narrow runtime affordance`, то есть вариант `B`.
