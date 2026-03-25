# План исправления UI мониторинга и связанных runtime-проблем

## Цель документа

Документ фиксирует результаты исследования текущего web-интерфейса SPHERE и связанных backend/runtime-контрактов по запросу на переработку мониторинга, сценарного инспектора, ленты активности, списка прогонов, отображения дат, трейсов и вторичной агентности.

Документ опирается на фактическую проверку:

- frontend-компонентов мониторинга и библиотеки сценариев;
- backend-маршрутов прогонов, сценариев, AI-генерации и WebSocket;
- структуры run-артефактов в `results/`;
- конкретного свежего прогона `results/full_ecology_10d_final_benchmark`.

## Контекст запроса

Пользователь указал на следующие проблемные зоны:

- правый сайдбар;
- шапка;
- левый сайдбар;
- экран прогонов;
- отображение даты;
- запрет emoji и требование кастомных SVG;
- возможность копировать agent trace в Markdown;
- низкое качество агентного поведения;
- пустые или слабочитаемые системные события;
- устаревший текст про `501` для secondary agents;
- отсутствие видимой продвинутой симуляции и вторичных агентов.

## Подтверждённые наблюдения

## 1. Правый сайдбар

- Максимальная ширина правой панели искусственно ограничена константой `MAX_PANEL = 400` в `web/frontend/src/App.tsx`.
- Переключатели вкладок `Активность`, `Сценарий`, `Среда` выполнены как текстовые кнопки, хотя доступная ширина панели мала.
- Раздел `Сценарий` рендерит только сокращённую summary-версию сценария, хотя backend уже возвращает `sim_config` и состав агентов.
- Раздел `Среда` показывает технические поля `queue_id`, `pressure`, `backlog`, `avg_delay_ticks` и `active_signals`, но почти не объясняет, зачем это важно пользователю и как это влияет на поведение агентов.

## 2. Шапка

- В шапке до сих пор выводятся summary-метрики `Приватных`, `Агентов`, `Очереди`, `Сигналы`.
- Метрики частично основаны на сыром транспортном состоянии и не всегда помогают понять ход симуляции.
- Шапка также продолжает выводить legacy-метаданные `scenario / governance / seed / variant`.

## 3. Левый сайдбар и открытие прогонов

- Блок `RunSelector` показывает длинный список прогонов с кнопкой `Воспроизвести`, а не компактный recent-list.
- Кнопка воспроизведения визуально отделена от управления скоростью, хотя логически это один блок.
- Сейчас открытие прогона означает запуск playback по WebSocket, а не загрузку финального snapshot-состояния.
- Механики двойного клика и отдельного режима “открыть уже полностью воспроизведённым” нет.

## 4. Дата и время

- Основная дата в UI сейчас берётся из `timestamp` события как из wall-clock времени записи в JSONL, а не из канонического времени мира.
- Для прогона `results/full_ecology_10d_final_benchmark` это приводит к несоответствию:
  - runtime start date в `scenario.json`: `2026-03-09`;
  - длительность: `10` тиков по `1` дню;
  - фактические `timestamp` в `events.jsonl`: `2026-03-24`.
- Поэтому жалоба “у последнего прогона только одна дата - вторник 24 марта” корректна: UI показывает дату запуска процесса, а не дату симулируемого мира.

## 5. Лента активности и системные события

- Детализация большинства системных событий отсутствует: generic-события рендерятся одной строкой без раскрытия payload.
- Для `world_event` есть конкретный баг: UI пытается читать `payload.narrative`, тогда как движок пишет `payload.description`. Из-за этого блок `СОБЫТИЕ` может выглядеть пустым.
- Системные события не сгруппированы как “служебные пакеты”, а смешиваются с полезной лентой.
- Пагинации нет. Клиент держит ограниченный хвост событий, что маскирует потерю контекста при длинных прогонах.

## 6. Экран прогонов

- Таблица прогонов не рассчитана на длинные имена run directory.
- `seed` продолжает выводиться как пользовательское поле, хотя по запросу его нужно скрыть.
- Колонка `Управление` часто содержит `—`, потому что backend выводит governance из regex по имени прогона, а не из сохранённых run-метаданных.
- На кастомных именах вида `full_ecology_10d_final_benchmark` regex не находит `G*`, поэтому UI теряет governance label.

## 7. Legacy `S / G / seed`

- `S* / G* / seed` по-прежнему глубоко зашиты в web-модели, формы сценариев, run launch и парсинг имени прогона.
- Это проявляется в:
  - `web/backend/models.py`;
  - `web/backend/routes/scenarios.py`;
  - `web/backend/routes/run_control.py`;
  - `web/backend/run_artifacts.py`;
  - `web/frontend/src/components/ScenariosView.tsx`;
  - `web/frontend/src/components/RunsView.tsx`;
  - `web/frontend/src/components/RunSelector.tsx`;
  - `web/frontend/src/App.tsx`.

## 8. Secondary agents

- Устаревшее сообщение про `501` в UI неверно: backend route `POST /api/ai/secondary-agents` существует и покрыт тестом `tests/test_web_ai.py`.
- Следовательно, проблема сейчас не в отсутствии backend route, а в том, что frontend показывает stale-текст и скрывает функциональность.
- В реальном прогоне `results/full_ecology_10d_final_benchmark` secondary-spawn включён:
  - `runtime.spawn_secondary = true`;
  - `runtime.max_secondary_per_agent = 1`;
  - `runtime.enable_worldgen = true`.
- При этом в `events.jsonl` не появились новые агенты `agent:sec_*`, `agent:fam_*`, `agent:soc_*`.
- По `trace.jsonl` видно, что social-graph extraction запускается, но в наблюдаемом прогоне quality secondary generation недостаточна: генератор в основном повторно ссылается на уже существующих участников или выдаёт кандидатов, отфильтровываемых логикой движка.

## 9. Продвинутая симуляция и среда

- Продвинутая симуляция не отсутствует как таковая. В run-артефактах реально присутствуют:
  - `world_event`;
  - `environment_operational_queue_updated`;
  - `environment_information_climate_updated`;
  - `environment_summary.json`;
  - `perf_summary.json`;
  - `trace.jsonl`.
- Проблема в том, что UI не превращает это в понятный инспектор симуляционного состояния.
- В `results/full_ecology_10d_final_benchmark/environment_summary.json` видны:
  - одна material queue `queue:audit_review`;
  - backlog `6`;
  - status `overloaded`;
  - pressure `high`;
  - сильный информационный климат `very_high` / `hot`;
  - набор latent informal links.
- То есть модель среды работает, но практически не объясняется пользователю.

## 10. Качество агентного поведения

- Жалоба на “агенты пишут дурь” подтверждается trace-артефактами.
- В sampled trace заметны:
  - повторяющиеся циклы `send_message` -> `add_work_note` -> `publish`;
  - избыточно бюрократические и шаблонные формулировки;
  - слабая конкретность реальных ставок и целей;
  - очень длинные prompts и большой аудиторный шум в контексте агента.
- Отдельно подтверждается поломка `perform`-контура:
  - в `events.jsonl` есть реальные `arbiter_rejected` с причинами вида `perform_op_invalid:...unsupported op_type: SendMessageOp`;
  - это означает, что свободные действия, которые могли бы оживить поведение, часто не проходят конвертацию в `StateOp`.

## 11. Трейсы

- Полные LLM-трейсы уже пишутся в `trace.jsonl`.
- UI умеет открывать только точечный prompt inspector через `/api/run/{name}/prompts`.
- Копирования agent trace в Markdown в продуктовой форме нет.

## 12. Иконки

- Во frontend ещё много emoji и unicode-глифов:
  - playback/live/delete/edit/collapse;
  - иконки каналов и событий;
  - предупреждения и status-маркеры.
- Это противоречит требованию заменить всё на кастомные SVG.

## Корневые причины

## 1. Legacy run metadata считаются источником истины

Backend и frontend всё ещё опираются на имя прогона и legacy-модели `scenario/governance/seed`, а не на явный metadata-sidecar.

Следствия:

- некорректная колонка `Управление`;
- утечки `seed` в UI;
- слабая работа с кастомными сценариями;
- неполные метаданные в monitor view.

## 2. UI мониторинга реализован как thin client поверх урезанного потока

Monitor получает события, граф и environment slice, но почти не имеет отдельного inspector-слоя для:

- полного payload события;
- арбитражного контекста;
- связанного work item / artifact / vote;
- полноценного `ScenarioConfig`.

## 3. Симуляционное время не отделено от времени записи артефакта

`timestamp` в EventLog используется как время записи события, а UI трактует его как время мира.

## 4. Семантика среды не переведена в язык пользователя

Очереди, сигналы, pressure, climate и informal links существуют как runtime-layer, но UI показывает их почти без интерпретации.

## 5. `perform`-контур нестабилен

Свободное действие задумано как путь для неформальных или нетиповых действий, но в текущем виде часто отвергается из-за рассинхронизации между LLM-output и `_op_from_llm`.

## 6. Secondary-agent контур не доведён до наблюдаемого продукта

Backend route уже есть, runtime spawn уже есть, но:

- frontend скрывает генератор;
- качество extractor/generation недостаточно стабильно;
- UI не показывает, что secondary generation сработала или отфильтровалась.

## 7. Activity feed не масштабируется

Без pagination, без server-side page API и без сворачивания служебных пакетов длинные прогоны становятся плохо читаемыми.

## Целевое состояние

После исправлений мониторинг должен перейти в следующий режим:

- run — это first-class объект с явными метаданными, а не строка имени;
- шапка показывает только действительно полезные summary-индикаторы;
- правый сайдбар работает как инспектор с icon tabs и раскрытием деталей;
- `Сценарий` показывает весь сценарий и весь background агентов;
- `Среда` объясняет causal impact, а не перечисляет сырые поля;
- `Активность` умеет детализировать событие, сворачивать служебный шум и подгружать историю постранично;
- открытие прогона по умолчанию показывает финальное состояние;
- playback — это отдельный осознанный режим;
- trace можно копировать и выгружать в Markdown;
- secondary agents доступны из web UI;
- все иконки переведены на локальные SVG;
- `seed` скрыт из пользовательского интерфейса;
- `S/G` больше не являются первичными пользовательскими сущностями.

## План решений

## Поток 1. Контракт метаданных прогона

### Цель

Перестать извлекать ключевые UI-метаданные из имени run directory.

### Изменения

- Ввести явный `run metadata` sidecar для каждого прогона.
- Хранить в нём:
  - `run_name`;
  - display title;
  - scenario title;
  - governance label;
  - start/end wall-clock;
  - start/end simulated date;
  - ticks total;
  - runner type;
  - флаг completed;
  - internal seed;
  - references на `scenario.json`, `summary.json`, `environment_summary.json`, `trace.jsonl`.
- Оставить `parse_run_name()` только как fallback для legacy runs.

### Эффект

- корректная колонка `Управление`;
- исчезновение `seed` из пользовательского UI;
- нормальная поддержка кастомных имён прогонов;
- надёжные summary-метаданные в monitor.

## Поток 2. Исправление даты и времени

### Цель

Развести wall-clock и simulated time.

### Изменения

- В monitor и timeline показывать симуляционную дату как primary value.
- Wall-clock оставить только в debug/tooltip.
- Использовать:
  - `runtime.start_date`;
  - `runtime.tick_granularity`;
  - `runtime.tick_duration_days`;
  - текущий `tick`.
- В event inspector отображать обе шкалы:
  - симуляционную;
  - фактическую дату записи события.

### Эффект

- исчезает ложное ощущение, что весь прогон “жил только во вторник 24 марта”;
- временная шкала начинает соответствовать миру, а не времени работы процесса.

## Поток 3. Правый сайдбар как полноценный inspector

### Цель

Перестроить правую панель из узкой текстовой tab-strip в инспектор состояния.

### Изменения

- Убрать верхний clamp `400px`.
- Ввести icon-only tabs через локальные SVG с tooltip на hover.
- Сделать панель растягиваемой до разумной доли viewport.
- Перевести содержимое панели на три самостоятельных инспектора:
  - `Активность`;
  - `Сценарий`;
  - `Среда`.

### Эффект

- больше полезной ширины;
- меньше визуального шума;
- панель становится местом чтения, а не только просмотра summary.

## Поток 4. Новый сценарный инспектор

### Цель

Показать весь сценарий и весь background агентов, а не только summary-блок.

### Изменения

- Перестроить `ScenarioPanel` вокруг полного `sim_config`.
- Отображать:
  - title;
  - description;
  - runtime;
  - governance;
  - world channels;
  - orgs;
  - work items;
  - world artifacts;
  - environment defaults;
  - параметры worldgen/runtime.
- Для каждого агента показывать:
  - typed id;
  - display name;
  - capabilities;
  - initial title;
  - internal/external;
  - org/zone binding;
  - persona summary;
  - biography;
  - interview;
  - reflections;
  - source personality archetype.

### UX-форма

- accordion / tree inspector;
- быстрый поиск по агентам;
- локальная кнопка `Copy MD` / `Copy JSON` для выбранного блока;
- явное выделение secondary agents и worldgen/runtime-spawned actors.

### Эффект

- пользователь действительно видит “ВСЁ, что касается сценария + агентов”.

## Поток 5. Новый инспектор среды

### Цель

Сделать `Среду` смысловым слоем, а не отображением debug-полей.

### Изменения

- Расширить backend-shape environment и не терять полезные поля при `graph_state`.
- Для каждой очереди показывать:
  - человекочитаемый заголовок;
  - кто страдает от перегрузки;
  - почему очередь возникла;
  - trend;
  - влияние на поведение агентов;
  - связанные `world_event`, complaints, publications, artifacts.
- Для каждого сигнала показывать:
  - тип;
  - источник;
  - сила;
  - кого затрагивает;
  - возможные governance-следствия.
- Выделить отдельный блок для:
  - information climate;
  - informal links;
  - material queues;
  - pending pressure.

### Эффект

- пользователь понимает, что означают очереди, сигналы и перегрузки;
- environment становится читаемым explanatory layer.

## Поток 6. Activity feed и event details

### Цель

Сделать ленту читаемой и масштабируемой.

### Изменения

- Исправить `world_event`: читать `payload.description`.
- Ввести event inspector по клику на любое событие.
- В inspector показывать:
  - human summary;
  - raw JSON;
  - связанные сущности;
  - source event type;
  - audience;
  - linked work item / artifact / vote / case.
- Системные сообщения между действиями агента группировать в сворачиваемые “служебные пакеты”.
- Для однотипных событий делать summary-группы:
  - `entity_created`;
  - `work_item_created`;
  - `arbiter_approved`;
  - `arbiter_rejected`;
  - `pending_interaction_*`;
  - `audit_case_*`.
- Добавить постраничную подгрузку истории:
  - cursor или `offset + limit`;
  - lazy prepend старых блоков;
  - separate live tail.

### Эффект

- activity feed становится useful timeline, а не шумным потоком.

## Поток 7. Левый сайдбар и режимы открытия прогона

### Цель

Сделать левую панель компактной и ориентированной на быстрый вход в последний run.

### Изменения

- Показывать только последние `3` прогона.
- Перенести `play` в один блок со скоростью.
- Убрать отдельную большую кнопку `Воспроизвести`.
- Добавить:
  - одинарный клик — выбрать;
  - двойной клик — открыть финальный snapshot;
  - явную play-icon кнопку — запуск playback.

### Отдельный режим snapshot-open

- При открытии run из списка или со страницы `Прогоны` загружать:
  - финальный graph state;
  - финальную environment snapshot;
  - последние N событий;
  - full meta.
- Playback оставлять только как отдельную интеракцию.

### Эффект

- монитор по умолчанию открывается в useful final state;
- playback не мешает простому анализу завершённого прогона.

## Поток 8. Экран прогонов

### Цель

Сделать runs table компактной и устойчивой к длинным именам.

### Изменения

- Обрезать длинные названия с ellipsis.
- Убрать seed column из пользовательского представления.
- Перевести action icons на SVG.
- Колонку `Управление` заполнять из run metadata, а не из regex по имени.
- При необходимости сократить/объединить колонки:
  - имя;
  - дата;
  - сценарий;
  - управление;
  - размер;
  - действия.

### Эффект

- таблица влезает;
- исчезают бессмысленные прочерки;
- пользователь видит семантические данные, а не legacy-шаблоны.

## Поток 9. Удаление `S / G / seed` из UX

### Цель

Скрыть legacy-идентификаторы из пользовательского слоя.

### Изменения

- Не показывать `seed` в monitor, runs, scenario cards, launch form, selector.
- Перевести интерфейс launch/edit сценариев с логики “S/G override” на логику:
  - сценарий;
  - режим управления;
  - runtime;
  - optional advanced config.
- Везде, где возможно, показывать человеческие названия сценария и governance mode.

### Эффект

- UI становится ближе к текущей гибкой архитектуре и перестаёт транслировать legacy-модель.

## Поток 10. SVG-иконки

### Цель

Убрать emoji и случайные unicode-glyph icons из интерфейса.

### Изменения

- Ввести локальный набор SVG-иконок:
  - play;
  - live;
  - stop;
  - delete;
  - edit;
  - collapse/expand;
  - activity;
  - scenario;
  - environment;
  - message/private/public;
  - alert;
  - world event;
  - prompt/trace.
- Перевести все UI-кнопки и event markers на этот набор.

### Эффект

- единый визуальный язык;
- исчезновение emoji из продуктового интерфейса.

## Поток 11. Trace export в Markdown

### Цель

Добавить удобную выгрузку и копирование agent trace в Markdown.

### Изменения

- Добавить backend endpoint для чтения `trace.jsonl` в структурированном виде с фильтрами:
  - `role`;
  - `agent_id`;
  - `tick_from`;
  - `tick_to`;
  - `limit`.
- Добавить server-side renderer в Markdown:
  - заголовок run;
  - агент;
  - tick;
  - system;
  - user;
  - response;
  - usage;
  - duration.
- Во frontend добавить:
  - `Copy MD`;
  - `Download MD`;
  - быструю кнопку в scenario/agent/run inspector.

### Эффект

- трейс можно сразу выносить в исследовательские заметки, review и отчёты.

## Поток 12. Возвращение secondary agents в web UI

### Цель

Перестать скрывать уже существующую функциональность и сделать её наблюдаемой.

### Изменения

- Удалить stale-текст про `501`.
- Вернуть в `ScenariosView` генератор вторичных агентов.
- Поддержать:
  - prompt;
  - family count;
  - society count;
  - replace existing;
  - preview generated agents before save.
- После генерации явно показывать:
  - кого добавили;
  - кого пропустили;
  - по какой причине.

### Эффект

- web UI перестаёт скрывать capability, уже существующую на backend.

## Поток 13. Качество secondary generation и surfacing runtime ecology

### Цель

Сделать secondary agents и внешнюю ecology реально заметными в run.

### Изменения

- Усилить social-graph extraction, чтобы он не переоткрывал существующих агентов как новых кандидатов.
- Сделать более жёсткий feedback-loop по причинам фильтрации secondary candidates.
- Добавить в summary/metadata run сведения:
  - был ли включён `spawn_secondary`;
  - сколько secondary candidates получено;
  - сколько реально создано;
  - сколько отклонено и почему.
- Surfacing в UI:
  - secondary agents badge;
  - worldgen-spawn badge;
  - runtime-spawn badge.

### Эффект

- пользователь сможет понять, что вторичная агентность сработала или не сработала, и почему.

## Поток 14. Улучшение качества агентных действий

### Цель

Снизить ощущение “агенты ходят вокруг чего-то” и вернуть более субъектное поведение.

### Изменения

- Сократить и очистить agent prompt:
  - меньше raw dump событий;
  - меньше повторов памяти;
  - меньше бюрократического шума;
  - яснее stakes, obligations и реальные options.
- Исправить `perform`-контур:
  - синхронизировать ожидаемые `op_type` между arbiter schema и `_op_from_llm`;
  - устранить rejection-ветки вида `unsupported op_type: SendMessageOp`.
- Добавить post-hoc метрики по качеству действия:
  - доля `noop`;
  - доля `perform`;
  - доля rejected `perform`;
  - доля новых meaningful entity/work/artifact effects.

### Эффект

- больше реальных действий и меньше формального кружения вокруг already-open work items.

## Поток 15. Product observability

### Цель

Сделать продвинутую симуляцию наблюдаемой без чтения raw artifacts на диске.

### Изменения

- Вынести в UI:
  - perf summary;
  - environment summary;
  - simulation configuration flags;
  - worldgen on/off;
  - spawn_secondary on/off;
  - число реально созданных secondary/runtime actors;
  - summary по queues/signals/climate.

### Эффект

- пользователь прямо в продукте видит, насколько run был “full ecology”, а не гадает по косвенным симптомам.

## Порядок внедрения

## Этап 1. Блокеры корректности

- Исправить `world_event` в activity feed.
- Убрать stale `501` из UI.
- Ввести run metadata sidecar.
- Развести simulated date и wall-clock.
- Починить `perform` conversion path.

## Этап 2. Основной UX monitor

- Новый правый сайдбар.
- Новый `ScenarioPanel`.
- Новый `EnvironmentPanel`.
- Event inspector.
- SVG icon system.

## Этап 3. Runs и playback

- Recent-3 list в левом сайдбаре.
- Snapshot-open по умолчанию.
- Компактный runs table.
- Удаление `seed` из UX.

## Этап 4. Trace и advanced simulation surfacing

- Markdown export trace.
- Surfacing perf/environment/runtime flags.
- Secondary-agent generator в web UI.

## Этап 5. Качество симуляции

- Улучшение agent prompt.
- Улучшение secondary generation.
- Метрики meaningful behavior.

## Тестовый контур

## Backend

- Тесты на новый run metadata endpoint / sidecar reading.
- Тесты на корректное simulated date отображение в API.
- Тесты на trace Markdown export.
- Тесты на возвращённый secondary-agents route из web UI flow.
- Тесты на `perform` success path без `unsupported op_type`.

## Frontend

- Тест на заполнение `world_event` content.
- Тест на раскрытие event details.
- Тест на snapshot-open completed run.
- Тест на truncation long run name.
- Тест на отсутствие `seed` в runs/monitor/scenario cards.
- Тест на SVG icon usage вместо emoji.

## E2E

- Запуск прогона и открытие финального monitor snapshot.
- Переход в `Сценарий` с просмотром biography/background.
- Просмотр `Среды` с queue/signal explanation.
- Раскрытие системного события и просмотр payload.
- Генерация secondary agents из web UI.
- Копирование agent trace в Markdown.

## Риски и ограничения

- Полный отказ от `parse_run_name()` в один шаг рискован для уже существующих legacy runs; нужен fallback-режим.
- Перевод monitor на snapshot-open и pagination меняет привычную механику playback, поэтому потребуется аккуратная UI-разметка режимов.
- Новый сценарный инспектор может стать слишком тяжёлым без lazy rendering и accordions.
- Улучшение agent behavior без фикса `perform` не даст заметного результата.
- Возврат secondary-agent UI без quality-pass по generation приведёт к разочарованию, если не показать skipped reasons.

## Итог

Проблема носит не косметический, а архитектурно-продуктовый характер:

- monitor всё ещё живёт на legacy-модели `S/G/seed`;
- сложная симуляция уже частично существует, но плохо surfaced;
- activity feed не справляется с системным потоком;
- время мира смешано со временем записи;
- secondary agents скрыты и одновременно недостаточно устойчивы;
- agent loop перегружен и не использует `perform` как рабочий канал живого поведения.

Соответственно, исправление должно идти не как набор isolated UI tweaks, а как связанный пакет:

- новые контракты данных прогонов;
- новый inspector-based monitor UI;
- новая семантика среды и событий;
- новая модель открытия run;
- нормальный trace export;
- исправление `perform` и secondary-agent pipeline.
