# Веб-интерфейс

Веб-интерфейс SPHERE состоит из серверной части на FastAPI и клиентской на React 19 с D3.js-визуализацией.

## Серверная часть

### Предпочтительный запуск

Для текущей среды web UI рекомендуется запускать контейнером:

```bash
docker compose up --build -d web
```

Контейнерный launcher собирает фронтенд в image, затем запускает FastAPI, который раздаёт и API, и готовую статику. Bootstrap-пользователь для логина создаётся из `SPHERE_ADMIN_USERNAME` / `SPHERE_ADMIN_PASSWORD`; по умолчанию это `sphere_admin` / `SphereDocker123!`.

### Структура

```
web/backend/
├── main.py           # FastAPI-приложение: WebSocket, middleware, точка входа
├── routes/           # REST-эндпоинты (выделены из main.py)
│   ├── auth.py       # POST /api/auth/login
│   ├── runs.py       # Прогоны: список, детали, snapshot, trace/export, удаление
│   ├── run_control.py # Запуск и остановка симуляций
│   ├── scenarios.py  # CRUD сценариев
│   ├── agent_types.py # CRUD типов агентов
│   ├── personalities.py # CRUD архетипов и интервью
│   ├── governance.py # CRUD режимов управления
│   ├── ai.py         # AI-генерация (личность, тип агента, вспомогательные агенты)
│   └── templates.py  # Встроенные шаблоны сценариев и режимов
├── auth.py           # JWT-аутентификация, bcrypt, роли
├── database.py       # SQLite через встроенный sqlite3 / aiosqlite
├── constants.py      # Enum-значения (GovernanceMode, ScenarioId, NEUTRALIZATION_TECHNIQUES)
├── runner.py         # Фоновый запуск симуляций
├── run_artifacts.py  # Единый поиск run-артефактов (directory + переходные legacy-sidecars)
├── graph_state.py    # Построение графа связей для визуализации
├── visibility.py     # Role-based фильтрация/редактура event-потока для REST/WS
└── manage_users.py   # CLI управления пользователями
```

### Аутентификация

Система использует JWT-токены на основе `PyJWT` с алгоритмом HS256. Пароли хешируются через `bcrypt`. Две роли:

- **admin** — полный доступ: запуск симуляций, управление сценариями, удаление прогонов
- **viewer** — только чтение: просмотр прогонов, событий, графов

Токен выдаётся через OAuth2 password flow (`POST /api/auth/login`). Время жизни настраивается через `JWT_EXPIRE_HOURS` (по умолчанию 24 часа, при невалидном значении используется fallback). Секрет задаётся через `JWT_SECRET` и должен быть не короче 32 байт; в режиме разработки (`SPHERE_DEV=1`) при отсутствии явного секрета backend использует стабильный dev-secret, чтобы токены не отваливались при reload и multi-worker запуске.

Для event-потока роли различаются не только правами на маршруты, но и видимостью данных. `admin` видит весь `events.jsonl`, включая private/direct сообщения и `llm_call`; `viewer` получает только общий слой (`aud:public`, `aud:internal`). Для shared-событий с потенциально чувствительным payload backend дополнительно редактирует содержимое (`document_created.content`, private `message_sent.text`/`content`). Audience-less legacy stream не считается API-контрактом.

### REST API

#### Аутентификация

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/auth/login` | Получить JWT-токен |

#### Прогоны (результаты симуляций)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/runs` | Список всех прогонов |
| GET | `/api/run/{name}` | Детали прогона (события, граф, environment-summary, метрики) |
| GET | `/api/run/{name}/snapshot` | Финальное состояние прогона + хвост событий для monitor snapshot |
| GET | `/api/run/{name}/export` | Экспорт полного прогона, включая environment sidecars |
| GET | `/api/run/{name}/scenario` | Конфигурация сценария прогона |
| GET | `/api/run/{name}/prompts` | Промпты и ответы LLM (только `admin`, limit ≤ 1000) |
| GET | `/api/run/{name}/trace.md` | Markdown-экспорт LLM trace (только `admin`) |
| GET | `/api/artifacts/{doc_id}` | Артефакты (сгенерированные документы, только `admin`) |
| DELETE | `/api/runs/{run_name}` | Удалить прогон |

`/api/runs` и связанные endpoints читают оба формата артефактов: legacy `results/*_events.jsonl` и directory-based `results/{run_name}/events.jsonl`. CLI `sphere-lc run` по умолчанию пишет прогоны именно в `results/<timestamp>`, поэтому такие запуски сразу видны web UI без дополнительного `--out`.
Для directory-based LC-run движок дополнительно пишет sidecar-файлы `scenario.json`, `names.json`, `status.json`, `trace.jsonl`, `summary.json`, `environment_summary.json` и `environment_timeline.jsonl`, чтобы web UI и экспорт могли загрузить не только конфиг и итоговые метрики, но и отдельную телеметрию усиленной среды.
Для активного directory-based прогона `GET /api/run/{name}/scenario` и `GET /api/run/{name}/export` умеют читать и ранний launcher-sidecar `_input_scenario.json`, поэтому конфиг доступен сразу после старта, ещё до записи финального `scenario.json`.
Для SPHERE-LC backend дополнительно нормализует события к legacy-совместимому виду (`tick` → `round`, `actor_id` → `agent_id`, `target_agent_id` → `payload.target`), а `/api/run/{name}/prompts` читает LLM-трейсы из `trace.jsonl`, если они вынесены из `events.jsonl`.
Перед отдачей `GET /api/run/{name}`, `GET /api/run/{name}/snapshot` и `GET /api/run/{name}/export` backend применяет `audience`-policy: viewer не получает point-to-point события, адресованные только конкретным `agent:*`, а admin по-прежнему видит полный поток. Это же правило используется и для построения `graph_state`, чтобы скрытые события не просачивались через побочные изменения графа. В ответ `GET /api/run/{name}` теперь также входит компактный `environment`-блок, `GET /api/run/{name}/snapshot` возвращает финальный graph-state и tail событий для monitor snapshot, а `GET /api/run/{name}/export` дополнительно включает `environment` и `environment_timeline`.

Для directory-based run backend теперь предпочитает sidecar `run.json` как источник UI-метаданных прогона. Это позволяет хранить `display_name`, `scenario_title`, `governance_label`, `ticks_total`, параметры runtime и симуляционный диапазон дат. Старый разбор имени прогона через regex остаётся fallback только для legacy артефактов без `run.json`.

При выдаче событий backend дополнительно материализует симуляционное время из runtime-конфига (`start_date`, `tick_granularity`, `tick_duration_days`) и добавляет в события поля `simulated_date`, `simulated_time`, `simulated_timestamp`. Благодаря этому monitor и timeline могут опираться на каноническое время мира, а не только на wall-clock `timestamp` записи в JSONL.

#### Живая симуляция

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/scenarios/{scenario_id}/run` | Запустить сохранённый сценарий |
| POST | `/api/runs/launch` | Запустить сценарий по `scenario id` с runtime-overrides |
| GET | `/api/runs/active` | Список активных симуляций (`external`, `stop_supported`) |
| POST | `/api/runs/{run_name}/stop` | Остановить симуляцию |

Web launcher запускает `sphere_lc` как отдельный subprocess и пишет артефакты в `results/{run_name}/`. Backend больше не поддерживает сохранённые legacy-карточки сценариев; пользовательские сценарии должны храниться как полноценный `ScenarioConfig`. Если при сохранении web-карточки поле `sim_config` пусто, backend сначала материализует выбранный шаблон `S/G`, а затем накладывает на него overrides из UI и сохраняет уже полный `ScenarioConfig`.

`POST /api/runs/launch` принимает также runtime-overrides `parallel_agents`, `parallel_workers` и `parallel_window`. Backend переносит их в `ScenarioConfig.runtime` конкретного запуска, поэтому они отражаются в `_input_scenario.json` и не теряются между UI и subprocess launcher'ом.

Внешние CLI-прогоны и ранее запущенные web-launcher subprocess теперь попадают в `/api/runs/active` не по одному только свежему `events.jsonl`, а по sidecar-файлу `status.json`/`*_status.json` со статусом `running` и свежим heartbeat (`updated_at`). Это позволяет переживать рестарт backend и снижает число ложноположительных «живых» прогонов после аварийного завершения без `summary.json`.

Метаданные прогона (`scenario`, `governance`, `variant`) извлекаются из правого суффикса имени прогона, поэтому пользовательское название сценария может содержать фрагменты вида `G2` или `G10` без поломки карточки прогона и WebSocket `meta`.

#### Сценарии

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/scenarios` | Список сценариев |
| GET | `/api/scenarios/{id}` | Детали сценария |
| POST | `/api/scenarios` | Создать сценарий |
| PUT | `/api/scenarios/{id}` | Обновить сценарий |
| DELETE | `/api/scenarios/{id}` | Удалить сценарий |

Маршруты сценариев работают прежде всего с `*.json` и поддерживают только полноценный `ScenarioConfig`. YAML всё ещё может читаться как compatibility input, но встроенные и сохраняемые через web сценарии теперь считаются `json-only`. Backend возвращает web-совместимую карточку сценария и кладёт исходный конфиг в поле `sim_config`, чтобы фронтенд мог редактировать его без потери данных. Старый web-формат (`name/scenario/governance/agents` без полного `ScenarioConfig`) больше не поддерживается и должен быть мигрирован вручную.

`/api/scenarios` теперь показывает только пользовательские сценарии. Встроенные template-файлы (`template_s*_g*.json`) считаются template-backend'ом для `/api/templates/scenarios/*`, не выдаются в CRUD-списке и не могут быть изменены или удалены через `/api/scenarios/{id}`.

При round-trip между `ScenarioConfig` и web-карточкой backend сохраняет `agents[].capabilities`, `agents[].initial_reputation` и runtime-поля параллелизации, чтобы обычное редактирование сценария не стирало нестандартные capability-наборы и стартовые условия эксперимента.

#### Типы агентов

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/agent-types` | Список типов |
| POST | `/api/agent-types` | Создать тип |
| PUT | `/api/agent-types/{id}` | Обновить тип |
| DELETE | `/api/agent-types/{id}` | Удалить тип |

#### Личности (архетипы)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/personalities` | Список архетипов |
| POST | `/api/personalities` | Создать архетип |
| PUT | `/api/personalities/{id}` | Обновить архетип |
| DELETE | `/api/personalities/{id}` | Удалить архетип |
| GET | `/api/personalities/{id}/interview` | Получить интервью |
| POST | `/api/personalities/{id}/interview/generate` | Сгенерировать и сохранить интервью через LLM |
| DELETE | `/api/personalities/{id}/interview` | Удалить интервью |

#### Режимы управления

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/governance-modes` | Список режимов |
| POST | `/api/governance-modes` | Создать режим |
| PUT | `/api/governance-modes/{id}` | Обновить режим |
| DELETE | `/api/governance-modes/{id}` | Удалить режим |

Для пользовательских `G*` режимов backend требует валидный governance-config: либо в поле `config`, либо прямо в корне JSON без служебных полей (`id`, `label`, `description`, `custom`). Режим без такого конфига больше не создаётся, чтобы UI/API не выдавали фиктивный `G4+`, который runtime потом не сможет применить.

#### AI-генерация

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/ai/prompt-templates` | Вернуть централизованные YAML prompt templates для admin UI |
| POST | `/api/ai/generate-personality` | Сгенерировать личность через LLM |
| POST | `/api/ai/generate-agent-type` | Сгенерировать тип агента через LLM |
| POST | `/api/ai/secondary-agents` | Сгенерировать secondary/family/society акторов и вернуть обновлённый scenario payload |

`POST /api/personalities/{id}/interview/generate` читает профиль личности из `data/personalities/{id}.json`, генерирует полное интервью `v2` (30 вопросов) и сохраняет его в `data/interviews/{id}.json`. Ответ совпадает с сохранённым payload и сразу пригоден для отображения в `PersonalitiesView`.

`POST /api/ai/secondary-agents` принимает `SecondaryAgentsPayload`, использует текущий `ScenarioConfig` (из `sim_config` или template `S/G`), просит LLM предложить concrete family/society actors и возвращает обычный web-scenario payload с обновлённым `sim_config`, списком `added_agents` и summary-блоком `secondary_generation`. Новые акторы получают typed id вида `agent:fam_*` / `agent:soc_*`, безопасные capabilities (`message`/`work`) и привязку к `org_id` / `zone_id` якорного агента, если она у него есть.

`GET /api/ai/prompt-templates` отдаёт raw templates из общего `src/sphere_lc/prompts.yaml`. Frontend использует этот endpoint как источник system/user defaults для editable AI-форм, поэтому prompt wording больше не дублируется в TypeScript.

#### Шаблоны и отладка

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/templates/scenarios` | Встроенные шаблоны сценариев |
| GET | `/api/templates/scenarios/{id}` | Конкретный шаблон как `ScenarioConfig` |
| GET | `/api/templates/governance` | Шаблоны режимов управления |
| GET | `/api/debug/llm-log` | Журнал LLM-вызовов |

Шаблоны сценариев собираются из поддерживаемых `template_s*_g*.json` сценариев репозитория, которые также хранятся как валидный `ScenarioConfig` (включая built-in `S3 -> template_s3_g3.json` для коллегиального review). Endpoint `GET /api/templates/scenarios/{id}` принимает optional query `governance=G*` и возвращает уже нормализованный `ScenarioConfig`, пригодный для web launcher'а и редактора. Для built-in режимов (`G0..G3`) backend теперь использует canonical mapping из `sphere_lc.governance_modes`:

- `G0`: аудит выключен;
- `G1`: аудит включён, но без freeze и без collegial review;
- `G2`: аудит включён, freeze включён, collegial review выключен;
- `G3`: аудит включён, freeze включён, collegial review включён.

Для пользовательских `G*` backend по-прежнему загружает конфиг из `data/governance_modes/{id}.json`.
Идентификатор `governance` валидируется как безопасный library-id: backend не читает произвольные JSON-файлы вне `data/governance_modes/`, даже если в query передать path-like строку.

### WebSocket-протокол

Два WebSocket-эндпоинта:

- `/ws/live` — мониторинг активного прогона (автовыбор или `run_name` в query).
- `/ws/playback/{name}` — воспроизведение сохранённого прогона.

После установления WebSocket-соединения клиент обязан первым сообщением отправить JSON вида `{"type":"auth","token":"<JWT>"}`. JWT больше не передаётся в query string, чтобы не утекать в URL-логи и историю браузера.

При logout или client-side событии `auth:logout` фронтенд должен явно закрывать активное `/ws/live` или `/ws/playback` соединение до перехода на форму входа, чтобы не оставлять аутентифицированный поток событий открытым после завершения сессии.

WebSocket-поток использует ту же visibility-policy, что и REST: `viewer` получает только shared-события и уже отредактированные payload'ы, `admin` — полный raw-поток. Это относится и к bootstrap-фазе (`graph_state` + tail истории), и к live-delta при дочитывании `events.jsonl`.

Фактические типы сообщений:

| Тип | Направление | Описание |
|---|---|---|
| `auth` | клиент → сервер | Первое сообщение после подключения: JWT-аутентификация |
| `meta` | сервер → клиент | Метаданные прогона (`display_name`, `scenario_title`, `governance_label`, runtime-время мира, `run_name`, `names`) |
| `event` | сервер → клиент | Одно событие |
| `events` | сервер → клиент | Пакет событий |
| `graph_state` | сервер → клиент | Текущее состояние графа и компактный environment-срез |
| `ping` | сервер → клиент | keepalive |
| `done` | сервер → клиент | Поток завершён |
| `error` | сервер → клиент | Ошибка (например, unauthorized/run not found) |

Оптимизация: события пакетируются (`SPHERE_WS_EVENT_BATCH_SIZE`, по умолчанию 50 штук каждые 0.15 с), обновления графа троттлятся (`SPHERE_LIVE_GRAPH_THROTTLE_S`, по умолчанию 0.25 с). Типы событий из `SPHERE_WS_DROP_EVENT_TYPES` (по умолчанию `idle`) фильтруются и не передаются клиенту. Для UI-событий backend также обрезает длинные payload-строки для WS-транспорта, но сохраняет полные данные в REST и `trace.md`/`prompts`.

## Клиентская часть

### Структура

```
web/frontend/src/
├── App.tsx                     # Главный компонент, маршрутизация
├── main.tsx                    # Точка входа
├── constants.ts                # Константы (URL API)
├── types.ts                    # TypeScript-типы
├── components/
│   ├── SimGraph.tsx            # Визуализация графа (D3.js force-directed)
│   ├── EventFeed.tsx           # Лента событий
│   ├── EventTimeline.tsx       # Временная шкала событий
│   ├── AgentPanel.tsx          # Карточка агента
│   ├── AgentList.tsx           # Список агентов
│   ├── ScenarioPanel.tsx       # Конструктор сценария
│   ├── ScenariosView.tsx       # Обзор сценариев
│   ├── RunsView.tsx            # Управление прогонами
│   ├── RunSelector.tsx         # Выбор прогона
│   ├── RoundScrubber.tsx       # Покадровое воспроизведение
│   ├── PersonalitiesView.tsx   # Управление архетипами
│   ├── AgentTypesView.tsx      # Управление типами агентов
│   ├── ActivityFeed.tsx        # Лента активности
│   ├── EnvironmentPanel.tsx    # Срез среды: очереди и активные сигналы
│   ├── NodeTooltip.tsx         # Всплывающая подсказка на графе
│   └── Markdown.tsx            # Отображение Markdown (react-markdown)
├── hooks/
│   ├── useAuth.ts              # Авторизация: токен, вход, выход
│   └── useSimulation.ts        # WebSocket: подключение, события, граф
├── utils/
│   ├── apiClient.ts            # HTTP-клиент с JWT
│   ├── payload.ts              # Обработка данных событий
│   └── time.ts                 # Форматирование времени
└── pages/
    └── LoginPage.tsx           # Страница авторизации
```

### Ключевые компоненты

**SimGraph** — интерактивная визуализация социального графа через D3.js force-directed layout. Узлы — агенты (размер зависит от активности), рёбра — связи (толщина зависит от силы). Поддерживается перетаскивание, масштабирование, всплывающие подсказки.

**EventFeed** и **EventTimeline** — лента событий в реальном времени. Показывает действия агентов, решения, сообщения. Обновляется через WebSocket.

**RoundScrubber** — покадровое воспроизведение завершённой симуляции. Позволяет перемещаться по раундам и наблюдать эволюцию графа и событий.

**AgentPanel** — детальная карточка агента: профиль, полномочия, ресурсы, история действий, связи.

**EnvironmentPanel** — отдельная вкладка HUD `Среда`, которая показывает compact environment slice из `graph_state`: operational queues, backlog/delay/capacity, давление очередей и активные сигналы информационного климата.

### Хуки

**useAuth** — управление JWT-токеном: вход, выход, проверка авторизации, перехват 401-ответов.

**useSimulation** — WebSocket-подключение к живой симуляции: получение событий, обновление графа, environment slice и управление статусом. При потере авторизации активное соединение закрывается явно, а не только при unmount React-дерева.
