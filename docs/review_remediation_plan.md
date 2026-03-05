# План правок по итогам повторного ревью

## Контекст

План покрывает найденные риски уровней P1–P3: безопасность/приватность, отказоустойчивость движка, согласованность инвариантов DAO, интеграцию веб-слоя с `magistry_lc` и рассинхрон документации.

## Цели

1. Закрыть P1-риски без регресса поведения симуляции.
2. Убрать системные причины `approved -> op_failed` там, где это детерминируемо заранее.
3. Привести веб-контракт и документацию к фактическому состоянию системы.

---

## Этап 1 — Safety / P1 (приоритет: максимальный)

### Задачи

- Удалить утечку приватного контента из `arbiter_approved` (редакция action payload, без сырых текстов private-сообщений).
- Добавить fail-safe обработку ошибок LLM:
  - локализация ошибки в рамках конкретного агента/узла;
  - отсутствие падения всего тика/прогона из-за единичного сбоя;
  - явное событие/лог причины деградации.
- Исправить UX запуска в вебе:
  - не "глотать" `501`;
  - показывать оператору причину недоступности запуска.

### Критерии готовности

- P1-тесты проходят: privacy regression + LLM resilience + launch error UX.
- Нет приватного текста в internal/shared событиях и журналах.
- При искусственном падении LLM симуляция продолжает работу в деградированном режиме.

### Аудит: подтверждение и уточнения

**Утечка приватного контента — подтверждена, два вектора.**

Первый вектор: `engine.py:411–415` формирует событие `arbiter_approved` с `"action": str(action)` и `audience=[INTERNAL_AUDIENCE]`. Для `SendMessageAction(private=True)` `str()` Pydantic-модели включает полный текст сообщения. Все внутренние агенты видят это через `event_visible_to_agent` (проверка `INTERNAL_AUDIENCE`). Таким образом, приватное сообщение между двумя агентами доступно всем внутренним агентам.

Второй вектор, частично закрыт: `journal.py:301–309` пишет в историю журнала `action: self._truncate(str(p.get("action") or ""), 280)` — тот же `str(action)` из payload `arbiter_approved`. При этом для события `message_sent` журнал корректно редактирует текст (`<redacted>`, строка 342), но для `arbiter_approved` проксируемый через `p["action"]` текст приватного сообщения остаётся. Журнал целиком идёт в промпт арбитра, то есть LLM-арбитр видит чужие приватные сообщения.

Исправление: в `engine.py:_apply_actions` при формировании payload `arbiter_approved` заменять `str(action)` на редактированную версию (тип + ID цели, без текста) для действий с `private=True`. Альтернативно — вынести редакцию в отдельный метод `Action.redacted_repr()`.

**LLM fail-safe — подтверждена, конкретная точка каскада.**

`engine.py:375` — `asyncio.gather(*[_one(aid) for aid in order])` в `_gather_actions` вызывается без `return_exceptions=True`. Единичный сбой LLM (таймаут, 429, сетевая ошибка) внутри `AgentRunner.propose_actions` → `LLMCaller.generate_structured` (который перевыбрасывает исключения, `caller.py:66–68`) убивает `asyncio.gather` целиком, что роняет весь тик и, следовательно, всю симуляцию.

Исправление: обернуть `_one()` в try/except, при исключении возвращать `(aid, [])` (агент пропускает ход) и писать событие `agent_llm_error` с деталями. Дополнительно полезно ввести configurable retry (1–2 попытки с экспоненциальным ожиданием) до падения в noop.

**UX 501 — корректно, но задача шире.**

`run_control.py:21–24,33–36` возвращают 501 с пояснением. Проблема не в бэкенде (код 501 + detail корректны), а в клиенте: фронтенд должен разбирать non-2xx и показывать сообщение, а не молча проваливаться. Убедиться, что `apiClient.ts` обрабатывает 501 как информативную ошибку.

---

## Этап 2 — Инварианты движка / P2

### Задачи

- Синхронизировать DAO-голосующих с capability `dao` (кворум/consent без "мертвых" голосов).
- Выровнять проверки structured/perform для `open_vote` (включая проверку внутреннего агента-цели).
- Добавить раннюю валидацию `create_work_item.participants` в арбитре.
- Зафиксировать семантику `closes_tick` (и при необходимости скорректировать момент закрытия).

### Критерии готовности

- Нет новых детерминированных путей `arbiter_approved -> arbiter_op_failed` для перечисленных кейсов.
- DAO-тесты отражают новую/зафиксированную математику кворума и согласия.

### Аудит: подтверждение и пропущенные кейсы

**DAO-голосующие vs capability `dao` — подтверждено, уточнение.**

`dao.py:18–22` — `eligible_voters` возвращает `cfg.dao_voters` (если задан) или `state.get_internal_agent_ids()` (все внутренние). Ни в одном пути нет фильтрации по capability `dao`. При этом `arbiter.py:448` требует capability `dao` для `CastVoteAction`. Результат: агент без `dao` есть в `vote.voters` → его голос ожидается для кворума, но он не может проголосовать → кворум может не быть достигнут системно.

Дополнительно: `vote.voters` фиксируется в момент создания голосования. Если `dao_voters` не задан, то список привязывается к текущему составу внутренних агентов на момент `OpenVoteOp`, а не на момент закрытия. Если за время голосования появятся новые агенты — они не смогут участвовать. Это детерминированное поведение, но его нужно документировать.

**open_vote: structured vs perform — подтверждено.**

`arbiter.py:421` (NominatePositionChangeAction) проверяет `not target.internal` и отклоняет. `arbiter.py:678–698` (`_op_from_llm` для open_vote) проверяет `target_agent_id not in state.agents` и `not wants_promotion`, но **не проверяет** `target.internal`. Через `perform` можно открыть голосование за внешнего агента. `OpenVoteOp.apply` тоже не проверяет `internal` — сработает, но `ChangePositionOp.apply:403` при закрытии поймает `"Cannot change position of external agent"` → `arbiter_op_failed`.

**create_work_item.participants — подтверждено.**

`arbiter.py:346–365` (CreateWorkItemAction) не проверяет, что `action.participants` содержат существующих агентов. `CreateWorkItemOp.apply:122–126` проверяет `ensure_kind(pid, AGENT)` и `pid not in state.agents` → ValueError → `arbiter_op_failed`. Детерминированный пробел: нужна ранняя проверка в арбитре.

**closes_tick — корректная семантика, нуждается в документировании.**

`dao.py:26`: `tick >= vote.closes_tick`. С `closes_tick = tick + vote_duration_ticks`, голосование длится ровно `vote_duration_ticks` тиков (от tick создания включительно, до closes_tick не включительно). Закрытие происходит в начале тика `closes_tick`, до сбора действий агентов. Семантика корректна и согласована с `engine.py:234` (DAO-закрытие вызывается после apply_actions каждого тика). Нужна фиксация в документации/комментариях.

**ПРОПУЩЕНО: CastVoteAction — арбитр не проверяет, что агент в списке голосующих (P2).**

`arbiter.py:447–458`: для `CastVoteAction` арбитр проверяет capability `dao` и существование `vote_id`, но **не проверяет** `agent_id in vote.voters`. `CastVoteOp.apply:321–322` проверяет это и выбрасывает ValueError. Ещё один детерминированный путь `approved → op_failed`. Арбитр должен проверять `agent_id in state.votes[action.vote_id].voters`.

**ПРОПУЩЕНО: RespondNominationAction — арбитр не проверяет, что агент является целью голосования (P2).**

`arbiter.py:460–471`: для `RespondNominationAction` арбитр проверяет capability `dao` и существование `vote_id`, но **не проверяет** `agent_id == vote.target_agent_id`. `SetVoteConsentOp.apply:349–350` проверяет это: `"Only target agent can respond to nomination"`. Любой агент с `dao` может попытаться ответить на чужую номинацию — арбитр одобрит, ops отклонит.

**ПРОПУЩЕНО: CastVoteOp молча перезаписывает голос (P3, к обсуждению).**

`ops.py:323` — `vote.votes[self.actor_id] = self.choice` без проверки на повторное голосование. Агент может менять свой голос неограниченно. Если это проектное решение — документировать. Если нет — добавить проверку `if self.actor_id in vote.votes: raise ValueError("already voted")`.

**ПРОПУЩЕНО: нет защиты от параллельных голосований за одну цель (P3).**

Два агента в одном тике могут открыть два голосования за одного и того же `target_agent_id`. Оба пройдут арбитраж, оба сработают в ops. Если оба пройдут — будет два `ChangePositionOp` при закрытии. Второй не сломается (просто повторно запишет `new_title`), но это семантически некорректно: один агент получает две смены должности.

---

## Этап 3 — Веб-интеграция / P2

### Задачи

- Поддержать чтение прогонов в обоих форматах артефактов:
  - legacy `results/*_events.jsonl`;
  - `magistry_lc`-каталог с `events.jsonl`.
- Улучшить управление активными прогонами:
  - корректно маркировать `external`;
  - запретить/скрыть stop там, где он гарантированно не поддерживается;
  - показывать ошибки non-2xx.
- Сделать удаление прогона "честным" (не подавлять ошибки файловой системы без сигнала клиенту).
- Ограничить endpoint промптов:
  - роль `admin`;
  - верхний предел `limit`.
- Укрепить эксплуатационные места:
  - безопасный парсинг `JWT_EXPIRE_HOURS`;
  - атомарная генерация ID для сценариев/режимов.

### Критерии готовности

- `/api/runs` и related endpoints видят и legacy, и новые прогоны.
- Оператор в UI получает корректный статус действий (launch/stop/delete).
- Эндпоинт промптов не доступен viewer и не допускает неограниченной выборки.

### Аудит: подтверждение и уточнения

**Двойной формат прогонов — подтверждено, затронуто больше эндпоинтов, чем описано.**

`runs.py:34–38` (`list_runs`) ищет только `*_events.jsonl` в `RESULTS_DIR`. `runs.py:78` (`get_run`) строит путь `RESULTS_DIR / f"{name}_events.jsonl"`. `runs.py:167` (`export_run`) аналогично. `runs.py:252` (`get_run_prompts`) аналогично. Ни один из них не обрабатывает magistry_lc формат (директория `results/{name}/events.jsonl`).

`run_control.py:92–103` (`delete_run`) удаляет только файлы `_events.jsonl`, `_names.json`, `_summary.json`, `_stdout.log`, `_stderr.log`. Директории magistry_lc не затрагиваются.

`runner.py:195–196` (`_discover_external_runs`) ищет только `*_events.jsonl` — внешние magistry_lc прогоны (директории с events.jsonl внутри) не обнаруживаются.

Исправление шире, чем описано: нужно единообразное определение «что такое прогон» (абстракция `RunLocator`), которое понимает оба формата, и адаптация всех 6+ мест.

**Удаление прогона — подтверждено, `except OSError: continue` без уведомления.**

`run_control.py:102–103` молча проглатывает ошибки файловой системы. Если файл заблокирован (другой процесс), оператор не узнает об этом.

**Промпты — подтверждено.**

`runs.py:236` использует `require_viewer`, а не `require_admin`. Промпты содержат системные инструкции и полный контекст — это чувствительные данные. Параметр `limit` не ограничен сверху (в отличие от `get_run`, где стоит `limit > 10_000`).

**JWT_EXPIRE_HOURS — подтверждено.**

`auth.py:25`: `_JWT_EXPIRE_HOURS: int = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))` — модульный уровень, без try/except. Некорректное значение (например, `"8h"` или пустая строка) роняет импорт всего модуля `auth.py`, что делает весь бэкенд неработоспособным. Для сравнения: `runner.py:25–27` делает то же с `MAGISTRY_MAX_RUNNING`, но с try/except и дефолтом.

**ПРОПУЩЕНО: python-jose устарел, фактическая зависимость не соответствует документации (P3).**

`auth.py:10`: `from jose import JWTError, jwt`. Зависимость — `python-jose[cryptography]>=3.3.0` (из `web/backend/requirements.txt`). Библиотека python-jose не обновлялась с 2022 года и считается де-факто заброшенной. В документации (`docs/web_interface.md`) было ошибочно указано PyJWT. Рекомендация: мигрировать на PyJWT (установлен параллельно, версия 2.7.0) в рамках этого этапа.

**ПРОПУЩЕНО: `_discover_external_runs` — порог 60 секунд слишком агрессивен (P3).**

`runner.py:31`: `_EXTERNAL_ALIVE_THRESHOLD = 60`. Если LLM-вызов занимает больше 60 секунд (что реально при больших промптах и дешёвых провайдерах), файл событий не обновляется, и прогон ложно считается завершённым. Рекомендация: увеличить до 300 секунд или привязаться к lockfile.

---

## Этап 4 — Документация и контракт / P2-P3

### Задачи

- Синхронизировать `README.md`, `docs/web_interface.md`, `docs/architecture_guide.md`, `AGENTS.md` с фактическим поведением.
- Обновить:
  - статус launch-endpoints (доступно/недоступно);
  - фактический WebSocket протокол;
  - список stub-эндпоинтов;
  - актуальные зависимости backend-а.
- Добавить блок "migration status / known limitations".

### Критерии готовности

- В документации нет endpoint-ов и протоколов, противоречащих коду.
- Статус миграции `magistry_sim -> magistry_lc` прозрачно описан для разработчика и оператора.

### Аудит: статус

Документация (`README.md`, `AGENTS.md`, `docs/overview.md`, `docs/architecture_guide.md`, `docs/simulation_engine.md`, `docs/data_formats.md`, `docs/cli_reference.md`, `docs/getting_started.md`, `docs/web_interface.md`, `docs/persona_interview_design.md`, `docs/testing.md`) была полностью обновлена в рамках предыдущей сессии рефакторинга. Все ссылки на `magistry_sim`, `ARCHITECTURE.md` и `CognitiveAgentRunner` исправлены. Блок «Завершённые миграции» добавлен в `AGENTS.md`.

Оставшиеся пробелы: (1) `docs/web_interface.md` указывает PyJWT, но код использует python-jose; (2) документация не описывает поведение stub-эндпоинтов `run_scenario` и `launch_run` из клиентской стороны; (3) нет секции «Известные ограничения веб-слоя» с перечнем того, что не работает (запуск, остановка внешних, удаление magistry_lc прогонов).

---

## Порядок выполнения и выпуск

- 4 отдельных PR (по этапам), без смешивания P1 и P3 в одном пакете.
- После каждого PR:
  - целевые тесты;
  - краткий changelog;
  - проверка, что документация не ушла в рассинхрон.

---

## Минимальный набор новых тестов

- `test_privacy_no_private_text_in_internal_events`
- `test_engine_survives_single_llm_failure`
- `test_perform_open_vote_matches_structured_checks`
- `test_create_work_item_participants_validated_in_arbiter`
- `test_runs_api_reads_directory_based_magistry_lc_run`
- `test_prompts_endpoint_admin_only_and_limit_capped`

### Аудит: дополнения к тестам

- `test_cast_vote_non_voter_rejected_by_arbiter` — проверка, что арбитр отклоняет голос от агента, отсутствующего в `vote.voters` (после исправления).
- `test_respond_nomination_non_target_rejected_by_arbiter` — проверка, что арбитр отклоняет respond_nomination от агента, не являющегося целью голосования.
- `test_eligible_voters_filters_by_dao_capability` — проверка, что `DaoEngine.eligible_voters` возвращает только агентов с capability `dao`.
- `test_arbiter_approved_redacts_private_message_in_journal` — проверка, что `journal.py` не содержит текста приватных сообщений в истории через `arbiter_approved`.
- `test_jwt_expire_hours_invalid_env` — проверка, что некорректное `JWT_EXPIRE_HOURS` не роняет импорт.

---

## Сводная таблица дополнений по итогам аудита

| Находка | Приоритет | Этап | Файл | Строки |
|---|---|---|---|---|
| `arbiter_approved` утечка через журнал (второй вектор) | P1 | 1 | `journal.py` | 301–309 |
| `asyncio.gather` без `return_exceptions` | P1 | 1 | `engine.py` | 375 |
| `CastVoteAction`: арбитр не проверяет voter eligibility | P2 | 2 | `arbiter.py` | 447–458 |
| `RespondNominationAction`: арбитр не проверяет target identity | P2 | 2 | `arbiter.py` | 460–471 |
| Нет защиты от параллельных голосований за одну цель | P3 | 2 | `arbiter.py` | 413–445 |
| `CastVoteOp` молча перезаписывает голос | P3 | 2 | `ops.py` | 323 |
| python-jose устарел, мигрировать на PyJWT | P3 | 3 | `auth.py` | 10 |
| `_EXTERNAL_ALIVE_THRESHOLD` = 60 с — слишком мало | P3 | 3 | `runner.py` | 31 |
| `_discover_external_runs` не видит magistry_lc прогоны | P2 | 3 | `runner.py` | 195–196 |
| `docs/web_interface.md`: PyJWT вместо python-jose | P3 | 4 | `docs/web_interface.md` | — |
