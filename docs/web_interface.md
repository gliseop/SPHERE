# Веб-интерфейс

Веб-интерфейс MAGISTRY состоит из серверной части на FastAPI и клиентской на React 19 с D3.js-визуализацией.

## Серверная часть

### Структура

```
web/backend/
├── main.py           # FastAPI-приложение: WebSocket, middleware, точка входа
├── routes/           # REST-эндпоинты (выделены из main.py)
│   ├── auth.py       # POST /api/auth/login
│   ├── runs.py       # Прогоны: список, детали, экспорт, удаление
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

Токен выдаётся через OAuth2 password flow (`POST /api/auth/login`). Время жизни настраивается через `JWT_EXPIRE_HOURS` (по умолчанию 24 часа, при невалидном значении используется fallback). Секрет задаётся через `JWT_SECRET` и должен быть не короче 32 байт; в режиме разработки (`MAGISTRY_DEV=1`) при отсутствии явного секрета backend использует стабильный dev-secret, чтобы токены не отваливались при reload и multi-worker запуске.

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
| GET | `/api/run/{name}` | Детали прогона (события, граф, метрики) |
| GET | `/api/run/{name}/export` | Экспорт полного прогона |
| GET | `/api/run/{name}/scenario` | Конфигурация сценария прогона |
| GET | `/api/run/{name}/prompts` | Промпты и ответы LLM (только `admin`, limit ≤ 1000) |
| GET | `/api/artifacts/{doc_id}` | Артефакты (сгенерированные документы, только `admin`) |
| DELETE | `/api/runs/{run_name}` | Удалить прогон |

`/api/runs` и связанные endpoints читают оба формата артефактов: legacy `results/*_events.jsonl` и directory-based `results/{run_name}/events.jsonl`. CLI `magistry-lc run` по умолчанию пишет прогоны именно в `results/<timestamp>`, поэтому такие запуски сразу видны web UI без дополнительного `--out`.
Для directory-based LC-run движок дополнительно пишет sidecar-файлы `scenario.json`, `names.json`, `status.json`, `trace.jsonl` и `summary.json`, чтобы web UI мог загрузить конфиг прогона, человеко-читаемые имена агентов, heartbeat-статус и prompt-inspector без отдельной конвертации.
Для активного directory-based прогона `GET /api/run/{name}/scenario` и `GET /api/run/{name}/export` умеют читать и ранний launcher-sidecar `_input_scenario.json`, поэтому конфиг доступен сразу после старта, ещё до записи финального `scenario.json`.
Для MAGISTRY-LC backend дополнительно нормализует события к legacy-совместимому виду (`tick` → `round`, `actor_id` → `agent_id`, `target_agent_id` → `payload.target`), а `/api/run/{name}/prompts` читает LLM-трейсы из `trace.jsonl`, если они вынесены из `events.jsonl`.
Перед отдачей `GET /api/run/{name}` и `GET /api/run/{name}/export` backend теперь применяет `audience`-policy: viewer не получает point-to-point события, адресованные только конкретным `agent:*`, а admin по-прежнему видит полный поток. Это же правило используется и для построения `graph_state`, чтобы скрытые события не просачивались через побочные изменения графа.

#### Живая симуляция

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/scenarios/{scenario_id}/run` | Запустить сохранённый сценарий |
| POST | `/api/runs/launch` | Запустить сценарий по `scenario id` с runtime-overrides |
| GET | `/api/runs/active` | Список активных симуляций (`external`, `stop_supported`) |
| POST | `/api/runs/{run_name}/stop` | Остановить симуляцию |

Web launcher запускает `magistry_lc` как отдельный subprocess и пишет артефакты в `results/{run_name}/`. Backend больше не поддерживает сохранённые legacy-карточки сценариев; пользовательские сценарии должны храниться как полноценный `ScenarioConfig`. Если при сохранении web-карточки поле `sim_config` пусто, backend сначала материализует выбранный шаблон `S/G`, а затем накладывает на него overrides из UI и сохраняет уже полный `ScenarioConfig`.

`POST /api/runs/launch` принимает также runtime-overrides `parallel_agents`, `parallel_workers` и `parallel_window`. Backend переносит их в `ScenarioConfig.runtime` конкретного запуска, поэтому они отражаются в `_input_scenario.json` и не теряются между UI и subprocess launcher'ом.

Внешние CLI-прогоны и ранее запущенные web-launcher subprocess теперь попадают в `/api/runs/active` не по одному только свежему `events.jsonl`, а по sidecar-файлу `status.json`/`*_status.json` со статусом `running` и свежим heartbeat (`updated_at`). Это позволяет переживать рестарт backend и снижает число ложноположительных «живых» прогонов после аварийного завершения без `summary.json`.

Метаданные прогона (`scenario`, `governance`, `seed`, `variant`) извлекаются из правого суффикса имени прогона, поэтому пользовательское название сценария может содержать фрагменты вида `G2` или `G10` без поломки карточки прогона и WebSocket `meta`.

#### Сценарии

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/scenarios` | Список сценариев |
| GET | `/api/scenarios/{id}` | Детали сценария |
| POST | `/api/scenarios` | Создать сценарий |
| PUT | `/api/scenarios/{id}` | Обновить сценарий |
| DELETE | `/api/scenarios/{id}` | Удалить сценарий |

Маршруты сценариев читают файлы `*.json`, `*.yaml` и `*.yml`, но поддерживают только полноценный `ScenarioConfig`. Backend возвращает web-совместимую карточку сценария и кладёт исходный конфиг в поле `sim_config`, чтобы фронтенд мог редактировать его без потери данных. Старый web-формат (`name/scenario/governance/agents` без полного `ScenarioConfig`) больше не поддерживается и должен быть мигрирован вручную.

`/api/scenarios` теперь показывает только пользовательские сценарии. Встроенные seed-файлы (`seed_s*_g*.json`) считаются template-backend'ом для `/api/templates/scenarios/*`, не выдаются в CRUD-списке и не могут быть изменены или удалены через `/api/scenarios/{id}`.

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
| POST | `/api/personalities/{id}/interview/generate` | Маршрут временно возвращает `501`; web UI не показывает активную кнопку |
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
| POST | `/api/ai/generate-personality` | Сгенерировать личность через LLM |
| POST | `/api/ai/generate-agent-type` | Сгенерировать тип агента через LLM |
| POST | `/api/ai/secondary-agents` | Сгенерировать вспомогательных агентов (маршрут пока возвращает 501; web UI скрывает интерактивную кнопку) |

#### Шаблоны и отладка

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/templates/scenarios` | Встроенные шаблоны сценариев |
| GET | `/api/templates/scenarios/{id}` | Конкретный шаблон как `ScenarioConfig` |
| GET | `/api/templates/governance` | Шаблоны режимов управления |
| GET | `/api/debug/llm-log` | Журнал LLM-вызовов |

Шаблоны сценариев собираются из поддерживаемых `seed_s*_g*.json` сценариев репозитория, которые также хранятся как валидный `ScenarioConfig`. Endpoint `GET /api/templates/scenarios/{id}` принимает optional query `governance=G*` и возвращает уже нормализованный `ScenarioConfig`, пригодный для web launcher'а и редактора. Для built-in режимов (`G0..G3`) backend применяет жёстко заданные пресеты; для пользовательских `G*` он загружает конфиг из `data/governance_modes/{id}.json`.
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
| `meta` | сервер → клиент | Метаданные прогона (`scenario`, `governance`, `seed`, `run_name`, `names`) |
| `event` | сервер → клиент | Одно событие |
| `events` | сервер → клиент | Пакет событий |
| `graph_state` | сервер → клиент | Текущее состояние графа |
| `ping` | сервер → клиент | keepalive |
| `done` | сервер → клиент | Поток завершён |
| `error` | сервер → клиент | Ошибка (например, unauthorized/run not found) |

Оптимизация: события пакетируются (`MAGISTRY_WS_EVENT_BATCH_SIZE`, по умолчанию 50 штук каждые 0.15 с), обновления графа троттлятся (`MAGISTRY_LIVE_GRAPH_THROTTLE_S`, по умолчанию 0.25 с). Типы событий из `MAGISTRY_WS_DROP_EVENT_TYPES` (по умолчанию `idle`) фильтруются и не передаются клиенту.

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

### Хуки

**useAuth** — управление JWT-токеном: вход, выход, проверка авторизации, перехват 401-ответов.

**useSimulation** — WebSocket-подключение к живой симуляции: получение событий, обновление графа, управление статусом. При потере авторизации активное соединение закрывается явно, а не только при unmount React-дерева.
