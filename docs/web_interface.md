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
├── run_artifacts.py  # Единый поиск run-артефактов (legacy + directory)
├── graph_state.py    # Построение графа связей для визуализации
└── manage_users.py   # CLI управления пользователями
```

### Аутентификация

Система использует JWT-токены на основе `PyJWT` с алгоритмом HS256. Пароли хешируются через `bcrypt`. Две роли:

- **admin** — полный доступ: запуск симуляций, управление сценариями, удаление прогонов
- **viewer** — только чтение: просмотр прогонов, событий, графов

Токен выдаётся через OAuth2 password flow (`POST /api/auth/login`). Время жизни настраивается через `JWT_EXPIRE_HOURS` (по умолчанию 24 часа, при невалидном значении используется fallback). Секрет задаётся через `JWT_SECRET` и должен быть не короче 32 байт; в режиме разработки (`MAGISTRY_DEV=1`) при отсутствии явного секрета backend генерирует одноразовый секрет на текущий процесс.

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
| DELETE | `/api/runs/{run_name}` | Удалить прогон |

`/api/runs` и связанные endpoints читают оба формата артефактов: legacy `results/*_events.jsonl` и directory-based `results/{run_name}/events.jsonl`. CLI `magistry-lc run` по умолчанию пишет прогоны именно в `results/<timestamp>`, поэтому такие запуски сразу видны web UI без дополнительного `--out`.
Для directory-based LC-run движок дополнительно пишет sidecar-файлы `scenario.json`, `names.json`, `trace.jsonl` и `summary.json`, чтобы web UI мог загрузить конфиг прогона, человеко-читаемые имена агентов и prompt-inspector без отдельной конвертации.
Для MAGISTRY-LC backend дополнительно нормализует события к legacy-совместимому виду (`tick` → `round`, `actor_id` → `agent_id`, `target_agent_id` → `payload.target`), а `/api/run/{name}/prompts` читает LLM-трейсы из `trace.jsonl`, если они вынесены из `events.jsonl`.

#### Живая симуляция

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/scenarios/{scenario_id}/run` | Заглушка (HTTP 501, legacy launcher удалён) |
| POST | `/api/runs/launch` | Заглушка (HTTP 501, legacy launcher удалён) |
| GET | `/api/runs/active` | Список активных симуляций (`external`, `stop_supported`) |
| POST | `/api/runs/{run_name}/stop` | Остановить симуляцию |

#### Сценарии

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/scenarios` | Список сценариев |
| GET | `/api/scenarios/{id}` | Детали сценария |
| POST | `/api/scenarios` | Создать сценарий |
| PUT | `/api/scenarios/{id}` | Обновить сценарий |
| DELETE | `/api/scenarios/{id}` | Удалить сценарий |

Маршруты сценариев читают файлы `*.json`, `*.yaml` и `*.yml`. Если файл содержит полноценный `ScenarioConfig`, backend возвращает web-совместимую карточку сценария и кладёт исходный конфиг в поле `sim_config`, чтобы фронтенд мог редактировать его без потери данных.

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
| POST | `/api/personalities/{id}/interview/generate` | Сгенерировать интервью через LLM |
| DELETE | `/api/personalities/{id}/interview` | Удалить интервью |

#### Режимы управления

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/governance-modes` | Список режимов |
| POST | `/api/governance-modes` | Создать режим |
| PUT | `/api/governance-modes/{id}` | Обновить режим |
| DELETE | `/api/governance-modes/{id}` | Удалить режим |

#### AI-генерация

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/ai/generate-personality` | Сгенерировать личность через LLM |
| POST | `/api/ai/generate-agent-type` | Сгенерировать тип агента через LLM |
| POST | `/api/ai/secondary-agents` | Сгенерировать вспомогательных агентов |

#### Шаблоны и отладка

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/templates/scenarios` | Встроенные шаблоны сценариев |
| GET | `/api/templates/scenarios/{id}` | Конкретный шаблон |
| GET | `/api/templates/governance` | Шаблоны режимов управления |
| GET | `/api/artifacts/{doc_id}` | Артефакты (сгенерированные документы) |
| GET | `/api/debug/llm-log` | Журнал LLM-вызовов |

### WebSocket-протокол

Два WebSocket-эндпоинта:

- `/ws/live` — мониторинг активного прогона (автовыбор или `run_name` в query).
- `/ws/playback/{name}` — воспроизведение сохранённого прогона.

После установления WebSocket-соединения клиент обязан первым сообщением отправить JSON вида `{"type":"auth","token":"<JWT>"}`. JWT больше не передаётся в query string, чтобы не утекать в URL-логи и историю браузера.

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

**useSimulation** — WebSocket-подключение к живой симуляции: получение событий, обновление графа, управление статусом.
