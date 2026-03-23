# AGENTS.md — инструкции для AI-агентов

## Назначение проекта

MAGISTRY — мета-двигатель для агентной симуляции организационных процессов. Система моделирует деятельность муниципальных организаций с помощью когнитивных LLM-агентов, исследуя эффективность гибридных управленческих систем (AI-аудит + децентрализованное голосование) в противодействии коррупции.

### Теоретическая база

[chapter_1.md](chapter_1.md) — первая глава ВКР, содержащая обзор литературы и обоснование подхода. Глава охватывает проблему принципала-агента в иерархических организациях (Йенсен, Меклинг), четыре категории подходов к снижению агентских издержек (институциональные, технологические, поведенческие, децентрализованные), эволюцию агентного моделирования от детерминированных правил к БЯМ-агентам, а также формулирует исследовательский пробел — отсутствие инструмента для экспериментальной оценки гибридных управленческих механизмов в среде с адаптивными агентами. Раздел 1.6 описывает архитектуру предлагаемой гибридной системы (ИИ-аудитор, репутационный механизм, коллегиальное рассмотрение), включая границы применимости и обоснование проектных решений.

[docs/2411.10109v1.pdf](docs/2411.10109v1.pdf) — Park J.S. et al. «Generative Agent Simulations of 1,000 People» (2024). Ключевая статья, определяющая архитектуру когнитивного агента в MAGISTRY. Авторы показали, что генеративные агенты на основе двухчасовых интервью достигают нормализованной точности 0,85 в воспроизведении индивидуального поведения (GSS). Из статьи заимствованы: приоритет текстового описания над числовыми параметрами, механизм экспертной рефлексии, система нарративных интервью с гибридным поиском по фрагментам (fragment-based retrieval).

### Архитектурная документация

Навигатор по архитектуре (карта модулей, путь данных) — в [docs/architecture_guide.md](docs/architecture_guide.md).

Описание движка симуляции, когнитивного цикла, арбитра, памяти и worldgen — в [docs/simulation_engine.md](docs/simulation_engine.md).

Форматы данных (`ScenarioConfig`, события, персоны, sidecar-артефакты) — в [docs/data_formats.md](docs/data_formats.md).

Подсистема «Персона = Интервью» (цепочка генерации, фрагментный поиск, назначение личностей) — в [docs/persona_interview_design.md](docs/persona_interview_design.md).

Отдельный runtime-аудитор и его встраивание в тик — в [docs/runtime_auditor_design.md](docs/runtime_auditor_design.md).

Веб-интерфейс, REST и WebSocket — в [docs/web_interface.md](docs/web_interface.md).

Практические инструкции по установке, CLI и тестам — в [docs/getting_started.md](docs/getting_started.md), [docs/cli_reference.md](docs/cli_reference.md), [docs/testing.md](docs/testing.md).

## Структура репозитория

Ниже перечислены исходники и важные служебные файлы. Generated/runtime-артефакты (`__pycache__/`, `web/frontend/dist/`, `web/frontend/node_modules/`, локальные `results/*`) в дерево не включены как источник истины.

```text
MAGISTRY/
├── src/magistry_lc/            # Движок симуляции (LangChain/LangGraph)
│   ├── __init__.py             # Пакет
│   ├── cli.py                  # CLI `magistry-lc`
│   ├── config.py               # ScenarioConfig + Runtime/Governance/LLM/Memory + world.environment (incl. operational_queues/informal_links/population_blueprints) + world.artifacts + temporal pending-follow-up knobs
│   ├── scenario.py             # Load/save YAML/JSON сценариев
│   ├── ids.py                  # Типизированные ID и аудитории (aud:*)
│   ├── entities.py             # EntityRegistry + EntityRecord (антифантомы)
│   ├── id_alloc.py             # Детерминированное выделение новых ID
│   ├── state.py                # WorldState (agents/work_items/artifacts/pending_interactions/votes + environment-layer incl. operational_queues) + AgentState.story_state + org/zone binding
│   ├── persona.py              # PersonaArtifact/Library/Generator + SocialGraphExtractor
│   ├── memory.py               # Память агента (buffer + hybrid retrieval)
│   ├── actions.py              # Action[] (structured + spawn_agent + perform)
│   ├── agent.py                # AgentRunner (1 LLM-вызов на ход, motivation block, daily context)
│   ├── ops.py                  # Детерминированные StateOp -> Event, включая CreateAgentOp
│   ├── arbiter.py              # Hybrid arbiter (caps + YAML-journal + LLM perform)
│   ├── auditor.py              # RuntimeAuditor (LLM-first detection + deterministic audit actuator + collegial review)
│   ├── dao.py                  # DAO vote closure + position policy
│   ├── engine.py               # WorldEngine (environment/artifact/operational-queue init, scripted events, pending follow-up queues, micro-reactions, pre/post worldgen, deterministic apply)
│   ├── worldgen.py             # WorldGenerator (pre/post tick: external events, scene hooks, spawns, environment updates, artifact creations/updates)
│   ├── composer.py             # WorldComposer (LLM -> ScenarioConfig + persona enrichment)
│   ├── oracle.py               # ViolationOracle + FreeformTruthRecorder (LLM post-hoc analysis)
│   ├── truth.py                # TruthDetector + TruthLog (deterministic truth-layer sidecar)
│   ├── evaluation.py           # Post-hoc evaluation against truth.jsonl
│   ├── fidelity.py             # Post-hoc fidelity metrics + summary separation
│   ├── events.py               # EventLog (JSONL) — "истина" мира
│   ├── tracing.py              # TraceLog (JSONL) — prompts/responses отдельно
│   ├── llm/                    # LLM-провайдеры и утилиты (пакет)
│   │   ├── __init__.py         # Реэкспорт: LLMProvider, EmbeddingProvider, create_provider и др.
│   │   ├── protocols.py        # Протоколы LLMProvider, LLMResponse, StructuredLLMResponse
│   │   ├── providers.py        # OpenAICompatibleProvider, MockLLMProvider
│   │   ├── embeddings.py       # EmbeddingProvider, OpenAIEmbeddingProvider, MockEmbeddingProvider
│   │   ├── cache.py            # LLMCache
│   │   ├── caller.py           # LLMCaller (обёртка провайдера + trace)
│   │   ├── _utils.py           # LLMCallError и вспомогательные функции
│   │   └── _debug_logger.py    # Отладочное логирование LLM-вызовов
│   ├── bm25.py                 # Собственная реализация BM25 (гибридный поиск)
│   ├── embeddings.py           # Async batch embeddings + cache
│   ├── journal.py              # Инкрементальный YAML-журнал мира для арбитра
│   ├── utils.py                # Мелкие утилиты (например, redact_numbers)
│   └── graphs.py               # LangGraph (tick graph + SqliteSaver checkpoints)
├── web/
│   ├── start.sh                # Dev-launcher: backend + frontend
│   ├── start_e2e.sh            # Подготовка окружения для Playwright smoke
│   ├── scripts/
│   │   └── generate_test_run.py # Генерация тестового прогона для UI
│   ├── backend/
│   │   ├── main.py             # FastAPI-сервер (точка входа)
│   │   ├── websocket.py        # Live/playback WebSocket + батчинг событий
│   │   ├── _middleware.py      # Middleware (например, лимит размера body)
│   │   ├── auth.py             # JWT-аутентификация и роли
│   │   ├── database.py         # SQLite-хранилище пользователей (sqlite3)
│   │   ├── models.py           # Pydantic-модели API
│   │   ├── settings.py         # Пути, лимиты и env-настройки backend
│   │   ├── validators.py       # Валидация run/scenario/library IDs
│   │   ├── runner.py           # Фоновый запуск симуляций и stop/list active
│   │   ├── run_artifacts.py    # Поиск артефактов прогонов (directory + legacy sidecars)
│   │   ├── graph_state.py      # Построение графа для визуализации
│   │   ├── visibility.py       # Role-based фильтрация/редактура event-потока
│   │   ├── constants.py        # Enum-значения и справочные константы
│   │   ├── manage_users.py     # CLI управления пользователями
│   │   ├── requirements.txt    # Изолированные зависимости backend
│   │   └── routes/             # REST-эндпоинты
│   │       ├── auth.py         # Логин / токены
│   │       ├── runs.py         # Чтение прогонов и артефактов
│   │       ├── run_control.py  # Запуск, остановка и удаление прогонов
│   │       ├── scenarios.py    # CRUD пользовательских сценариев
│   │       ├── templates.py    # Seed-сценарии и governance templates
│   │       ├── agent_types.py  # CRUD библиотек типов агентов
│   │       ├── personalities.py # CRUD личностей и интервью
│   │       ├── governance.py   # CRUD пользовательских governance modes
│   │       └── ai.py           # LLM-генерация personality/agent-type + 501 legacy routes
│   └── frontend/
│       ├── package.json        # React/Vite/Playwright/Tailwind toolchain
│       ├── playwright/
│       │   └── smoke.spec.ts   # E2E smoke-тесты
│       ├── public/             # Статические файлы
│       └── src/
│           ├── App.tsx         # Главный компонент приложения
│           ├── App.css         # Локальные стили App
│           ├── main.tsx        # Точка входа React
│           ├── index.css       # Глобальные стили
│           ├── constants.ts    # UI-константы
│           ├── types.ts        # Типы frontend
│           ├── pages/
│           │   └── LoginPage.tsx # Экран логина
│           ├── components/     # SimGraph, RunsView, ScenarioPanel, ActivityFeed и др.
│           ├── hooks/          # useAuth, useSimulation
│           ├── utils/          # apiClient, payload, time
│           └── styles/
│               └── hud.css     # Основная HUD-тема интерфейса
├── tests/                      # pytest-модули движка и web backend
│   ├── conftest.py             # Общая подготовка окружения тестов
│   ├── test_magistry_lc_smoke.py
│   ├── test_magistry_lc_cli.py
│   ├── test_magistry_lc_review_fixes.py
│   ├── test_persona_enrichment.py
│   ├── test_worldgen_personal_ecology.py
│   ├── test_truth_evaluation.py
│   ├── test_auditor.py
│   ├── test_embedding.py
│   ├── test_graph_state.py
│   ├── test_web_auth.py
│   ├── test_web_main_auth.py
│   ├── test_web_database.py
│   ├── test_web_runner.py
│   └── test_web_ai.py
├── data/
│   ├── agent_types/            # Шаблоны типов агентов (JSON)
│   ├── personalities/          # Архетипы личности (JSON)
│   ├── interviews/             # Данные интервью (JSON)
│   └── governance_modes/       # Конфигурации пользовательских режимов управления
├── scenarios/                  # YAML/JSON-конфигурации сценариев и seed-шаблоны
├── results/                    # Результаты прогонов (JSONL, sidecars, логи)
├── docs/
│   ├── architecture_guide.md   # Карта модулей и путь данных
│   ├── simulation_engine.md    # Подробности движка симуляции
│   ├── data_formats.md         # Форматы конфигураций и артефактов
│   ├── web_interface.md        # REST/WebSocket и web UI
│   ├── testing.md              # Тестовые паттерны
│   ├── cli_reference.md        # CLI-справочник
│   ├── getting_started.md      # Установка и запуск
│   ├── overview.md             # Системный обзор
│   ├── runtime_auditor_design.md # Дизайн runtime-аудитора
│   ├── persona_interview_design.md # Подсистема «Персона = Интервью»
│   ├── plans/                  # Рабочие планы и follow-up документы
│   └── 2411.10109v1.pdf        # Ключевая научная статья
├── scripts/
│   └── collect_for_chatgpt.py  # Сборка контекста репозитория в один файл
├── .env.example                # Пример переменных окружения
├── AGENTS.md                   # Главная карта проекта для AI-агентов
├── chapter_1.md                # Теоретическая глава ВКР
├── README.md                   # Точка входа для разработчика
├── pyproject.toml              # Python-пакет и pytest-конфигурация
└── ui-kit-light.html           # HTML-референс визуального стиля интерфейса
```

## Архитектурные принципы

При работе с кодом необходимо соблюдать следующие принципы:

1. **Движок не знает предметной области.** Ядро оперирует абстракциями (действие, полномочие, сущность). Специфика — в конфигурации сценариев (YAML/JSON).

2. **Полномочия вместо ролей.** Агент определяется набором полномочий (`message`, `work`, `dao`, `audit`, `spawn`), а не жёсткой ролью. Арбитр проверяет полномочия при каждом действии.

3. **Агенты максимально свободны.** Агент — автономная сущность с собственными целями и личностью. Структурированные действия (`send_message`, `add_work_note`, `submit_proposal`) — типовой путь; произвольные действия оцениваются LLM-арбитром. Среда обеспечивает физику, но не диктует поведение.

4. **Текстовые описания вместо числовых параметров.** Личность агента задаётся биографией и текстовой характеристикой, а не числовыми шкалами (обосновано в [chapter_1.md](chapter_1.md), раздел 1.4, по результатам Park et al. [28]).

5. **Антифантомная защита.** `EntityRegistry` блокирует действия, адресованные несуществующим сущностям. Все ID типизированы (`agent:`, `chan:`, `org:`, `work:`).

6. **Детерминированный apply.** Агенты ходят параллельно, но результаты применяются к состоянию мира последовательно и детерминированно.

7. **Приоритет полноценной агентности среды.** Если вычислительный бюджет позволяет, среду следует насыщать множеством реальных мелких агентов, а не заменять их жёстко зашитыми когортами, суррогатными агрегатами или псевдо-акторами. Агрегация допустима как вынужденный технический компромисс, но не как вариант по умолчанию.

## Стек и зависимости

- Python 3.12+, Pydantic 2.0+, NetworkX 3.0+, OpenAI 1.0+, Rich 13.7+, python-dotenv 1.0+
- LangChain/LangGraph: langgraph 0.2+, langchain-core 0.2+, PyYAML 6.0+
- Дополнительно: rank-bm25 0.2.2+ (гибридный поиск), SciPy 1.12+ (опциональная статистика)
- Web backend: FastAPI, uvicorn, aiofiles, python-multipart, PyJWT, bcrypt, встроенный `sqlite3`
- Frontend: React 19, TypeScript 5.9, Vite 7, D3.js 7, react-markdown, Tailwind CSS 3, Playwright
- Тесты: pytest 9.0+, pytest-asyncio 0.23+, frontend smoke через Playwright

## Команды

```bash
# Установка
pip install -e ".[lc,dev]"

# Тесты
pytest
pytest tests/test_web_runner.py
pytest tests/test_persona_enrichment.py -k worldgen
pytest tests/test_worldgen_personal_ecology.py

# MAGISTRY-LC
magistry-lc run --scenario scenarios/lc_minimal.yaml --out results/lc_minimal_run
magistry-lc run --scenario scenarios/procurement_tender_full_ecology.yaml --out results/procurement_tender_full_run --enrich-personas --persona-enrich-mode full
magistry-lc compose --description "Короткое описание" --out scenarios/lc_composed.yaml
magistry-lc oracle --events results/lc_minimal_run/events.jsonl --out results/lc_minimal_run/violations.json

# Веб-интерфейс
cd web && bash start.sh
cd web/frontend && npm run build
cd web/frontend && npm run test:e2e
```

## Соглашения по коду

- **Документация**: Google docstrings для всех публичных функций и классов.
- **Модели данных**: Pydantic `BaseModel` для конфигураций и сериализуемых структур; `dataclass` для внутренних структур.
- **Типизация**: полная аннотация типов, `from __future__ import annotations`.
- **Логирование**: `logging.getLogger(__name__)`, уровни DEBUG/INFO/WARNING.
- **Тестирование**: тесты лежат в `tests/test_<area>.py`; общая подготовка окружения — в `tests/conftest.py` (JWT secret, отключение реального OpenAI, добавление `src/` в `sys.path`). Для изоляции использовать `MockLLMProvider`, `MockEmbeddingProvider`, `monkeypatch` и локальные fake/stub-классы в самих тестовых файлах.
- **Асинхронность**: `asyncio_mode = "auto"` в pytest; async-тесты через `pytest-asyncio`.

## Ведение документации

При изменении кода необходимо обновлять документацию. Это обязательная часть каждой задачи, а не отдельный шаг «на потом».

### Принципы

1. **Самоподдержание `AGENTS.md`.** Этот файл — главная карта проекта. При любом структурном изменении (новый/удалённый модуль, изменение дерева файлов, устранение технического долга) обновить соответствующий раздел `AGENTS.md` в рамках той же задачи.

2. **Приоритеты обновлений.** Обязательные обновления — те, без которых документация становится ложной (дерево файлов, таблица технического состояния, `docs/architecture_guide.md`). Желательные — расширение описаний в `docs/` для удобства будущей работы. Обязательные блокируют завершение задачи, желательные — нет.

3. **Формат.** При обновлении документа — читать существующий стиль целевого файла и следовать ему. Не вводить новые форматы и условные обозначения без необходимости. Планы пишем в `docs/plans/`.

4. **Язык и терминология.** Основной язык коммуникации, планов, отчётов и технической документации — русский. Английские слова, термины, аббревиатуры и узкие технические выражения использовать только при необходимости. Если английский термин всё же используется, сразу давать краткое пояснение на русском в скобках.

### Таблица обновлений

| Что изменилось | Какой документ обновить | Приоритет |
|---|---|---|
| Добавлен/удалён/переименован файл в `src/magistry_lc/` | `AGENTS.md` (дерево файлов) | Обязательный |
| Добавлен/удалён/переименован файл в `web/backend/`, `web/frontend/src/`, `scripts/`, `web/scripts/` | `AGENTS.md` (дерево файлов), при необходимости `docs/web_interface.md` / `docs/getting_started.md` | Обязательный |
| Новый модуль в `src/magistry_lc/` | `AGENTS.md` (дерево), `docs/architecture_guide.md` | Обязательный |
| Устранён технический долг из таблицы | `AGENTS.md` (раздел «Техническое состояние») — удалить или обновить строку | Обязательный |
| Появился новый технический долг | `AGENTS.md` (раздел «Техническое состояние») — добавить строку | Обязательный |
| Новый инструмент агента | `docs/simulation_engine.md`, `docs/architecture_guide.md` | Обязательный |
| Изменение модели данных (`config.py`, `state.py`) | `docs/data_formats.md`, `docs/simulation_engine.md` | Обязательный |
| Новый сценарий или режим управления | `README.md`, `docs/simulation_engine.md` | Обязательный |
| Изменение когнитивного цикла | `docs/simulation_engine.md` | Обязательный |
| Изменение WebSocket/live visibility контракта | `docs/web_interface.md`, `AGENTS.md` (известные особенности) | Обязательный |
| Изменение auth/role policy или сценария доступа к артефактам | `docs/web_interface.md`, `AGENTS.md` (известные особенности) | Обязательный |
| Новый REST/WebSocket-эндпоинт | `docs/web_interface.md` | Желательный |
| Новый CLI-аргумент | `docs/cli_reference.md` | Желательный |
| Новая зависимость | `README.md` (стек), `docs/getting_started.md` | Желательный |
| Новый тестовый паттерн (`pytest`/Playwright) | `docs/testing.md` | Желательный |
| Изменена таблица «Типичные задачи» или «Известные особенности» | `AGENTS.md` — соответствующий раздел | Обязательный |

## Типичные задачи

| Задача | Ключевые файлы |
|---|---|
| Добавить сценарий | `scenarios/`, `config.py`, `scenario.py` |
| Изменить арбитра | `arbiter.py`, `journal.py`, `ops.py` |
| Изменить runtime-аудит | `auditor.py`, `engine.py`, `ops.py`, `config.py` |
| Изменить truth/evaluation/fidelity | `truth.py`, `evaluation.py`, `fidelity.py`, `engine.py`, `docs/data_formats.md` |
| Изменить агентский цикл | `agent.py`, `memory.py`, `engine.py`, `actions.py` |
| Изменить слой среды / temporal runtime | `config.py`, `state.py`, `engine.py`, `worldgen.py`, `journal.py`, `docs/data_formats.md` |
| Изменить социальный граф / динамический спавн | `persona.py`, `engine.py`, `actions.py`, `ops.py`, `worldgen.py` |
| Добавить LLM-провайдера | `llm/providers.py`, `llm/__init__.py`, `llm/caller.py` |
| Изменить генерацию мира | `worldgen.py`, `engine.py`, `config.py`, `composer.py` |
| Изменить web-launcher / артефакты прогонов | `web/backend/runner.py`, `web/backend/run_artifacts.py`, `web/backend/routes/run_control.py`, `tests/test_web_runner.py` |
| Изменить live WebSocket / visibility | `web/backend/websocket.py`, `web/backend/visibility.py`, `web/backend/graph_state.py`, `web/frontend/src/hooks/useSimulation.ts`, `web/frontend/src/components/SimGraph.tsx` |
| Изменить auth / роли / пользователей | `web/backend/auth.py`, `web/backend/database.py`, `web/backend/manage_users.py`, `web/frontend/src/hooks/useAuth.ts`, `web/frontend/src/pages/LoginPage.tsx` |
| Изменить CRUD библиотек (личности / типы агентов / governance) | `web/backend/routes/personalities.py`, `web/backend/routes/agent_types.py`, `web/backend/routes/governance.py`, `web/backend/routes/templates.py`, `data/` |
| Добавить REST-эндпоинт | `web/backend/routes/` |
| Добавить React-компонент или страницу | `web/frontend/src/components/`, `web/frontend/src/pages/`, `web/frontend/src/App.tsx` |
| Изменить оракула | `oracle.py` |
| Изменить DAO-голосование | `dao.py`, `ops.py` |

## Известные особенности

- **Зависимость от OpenAI-совместимого API**: для запуска симуляции и LLM-генерации через работающие AI-эндпоинты (`generate-personality`, `generate-agent-type`) требуется `OPENAI_API_KEY` или совместимый эндпоинт. Тесты используют `MockLLMProvider` и не требуют ключа.
- **Стоимость LLM-вызовов**: в текущем исследовательском контуре стоимость считается приемлемой. При проектировании worldgen, вторичных агентов, enrichment и других когнитивных контуров не нужно по умолчанию поднимать вопрос цены или упрощать архитектуру ради экономии токенов; первичный критерий — исследовательская ценность и правдоподобие среды.
- **Дешёвые модели и плотная ecology**: наличие очень дешёвых моделей делает допустимым большое количество мелких параллельных агентов. Неприемлемо не само масштабирование агентности, а замена потенциально полноценных акторов жёстко зашитыми суррогатами только ради упрощения рантайма.
- **Эмбеддинги**: в обычных прогонах по умолчанию используются реальные embeddings через OpenAI-совместимый API; при отсутствии ключа движок деградирует в BM25-only retrieval. `MockEmbeddingProvider` и `embeddings_mock=true` оставлены для тестов и дешёвых smoke-прогонов.
- **Агентские промпты**: `AgentRunner` сообщает агенту текущее время мира (`tick` и каноническую дату, если она задана), но не говорит агенту, что он находится в симуляции.
- **Объём prompt-контекста**: не сжимать агентские и worldgen-промпты вручную только ради уменьшения токенов. Для ведения большого контекста полагаться на штатные механизмы памяти, суммаризации, compaction (компакции) и другие встроенные алгоритмы управления контекстом; большой объём сам по себе не считается дефектом.
- **Temporal contract runtime**: `RuntimeConfig` поддерживает `tick_granularity` (`hour` / `half_day` / `day` / `week`) и каноническое world-time. `tick_duration_days` теперь трактуется как множитель выбранной гранулярности; для legacy-конфигов с `day` поведение остаётся прежним.
- **Динамический спавн**: вторичные и runtime-спавненные агенты получают только безопасный capability-набор (`message`/`work`), без `audit` и без права порождать следующих агентов.
- **Ecology activation**: при `runtime.ecology_activation_window_ticks > 0` не-core акторы ходят не каждый тик, а только когда недавно были затронуты событиями, hook-ами или собственным созданием. Это сохраняет богатую ecology без захвата сюжета внешними акторами.
- **Имена новых агентов**: secondary-spawn, runtime-spawn и worldgen-spawn принимают только человеко-читаемые имена; role-alias и machine-like display-name отклоняются или маппятся на уже существующего актора.
- **Scripted events + pre-tick worldgen**: сценарий может задавать `scripted_events`, а `runtime.worldgen_pre_tick=true` включает personal-ecology слой до хода агентов: `agent_daily_context`, `scene_hooks`, глобальные сигналы и `story_state` агента. Эти prompt-layer данные не подменяют детерминированный apply.
- **Stateful environment layer**: `WorldState` теперь содержит отдельный `environment`-слой (`world.environment` в сценарии): режимы организаций, зоны, ресурсные пулы и информационный климат. Он инициализируется из конфига, отражается в YAML-журнале и safe `state_snapshot` для worldgen; post-worldgen теперь также может детерминированно менять его через `environment_updates` и события `environment_*_updated`.
- **Operational queues**: `environment.operational_queues` хранит материальные очереди и backlog’и (`backlog`, `capacity_per_tick`, `avg_delay_ticks`, `status`, `pressure`). Ресурсное давление теперь может не только создавать `resource_alert`, но и детерминированно перегружать такие очереди, эмитить `environment_operational_queue_updated`, материализовать `queue_alert`-артефакты и поверх этого запускать локальный complaint/publication цикл с публичными `world_event`. Отдельно появился per-tick queue process: без реакции backlog и delay сами деградируют, а при реальной work-активности релевантных агентов очередь может восстановиться до `recovering` / `stable`. При тяжёлой service-degradation этот же контур теперь умеет deterministic runtime-spawn внешних акторов (`queue_complainant`, `queue_reporter`) с безопасными capabilities, seeded pending-follow-up и связью `shared_issue`, так что они могут запускать собственные message/publication chains вокруг проблемной очереди. Их действия, в свою очередь, детерминированно создают internal response obligations (`external_queue_complaint_response`, `media_response`), артефакты `external_complaint` / `press_inquiry` и дополнительное давление в `information_climate`.
- **Документарный слой мира**: сценарий и runtime теперь поддерживают `art:*`-артефакты как first-class сущности (`world.artifacts`, `artifact_created`, `artifact_updated`). Они попадают в `WorldState`, журнал мира, worldgen snapshot и релевантный prompt агента.
- **Неформальные связи**: `environment.informal_links` теперь хранит латентные связи между агентами и может обновляться как из конфига/worldgen, так и детерминированно по самому ходу симуляции (например, через private contact и coordination).
- **Population blueprints**: `world.environment.population_blueprints` позволяет систематически наращивать периферийную агентность вокруг `org:*` / `zone:*`. На текущем этапе поддерживаются bootstrap-заполнение среды и elastic-доращивание после `environment_change`.
- **Local reaction windows**: поверх основного батча действий движок теперь может запускать локальные reaction windows внутри того же тика (`runtime.micro_reaction_rounds`). Они дают ограниченному набору агентов быстрый follow-up на события текущего тика и делают runtime менее жёстко синхронным даже без полной замены tick-engine.
- **Pending follow-up queue**: `WorldState.pending_interactions` хранит короткие локальные обязательства и ожидающие ответы, переживающие один или несколько тиков. Движок умеет детерминированно создавать их из приватных сообщений, документарных и ресурсных сдвигов, эмитить `pending_interaction_due`, показывать их агенту в prompt и реактивировать периферию даже после выпадения исходного события из обычного activation-window.
- **Risky personal contexts**: `agent_daily_context` теперь может нести не только общий фон, но и richer pressure-поля (`private_pressure`, `opportunity`, `exposure_risk`). Это считается допустимым средовым давлением, а не прямой директивой агенту.
- **`request_entity` по умолчанию внутренний**: при `runtime.request_entity_internal_only=true` внешние/ecology-акторы не могут бесконтрольно разворачивать публичную инфраструктуру (`chan:*`/`org:*`) через `request_entity`.
- **Runtime-аудитор**: `RuntimeAuditor` существует только как отдельный runtime governance-layer, а не как narrative-agent. Он сочетает deterministic baseline rules с LLM-findings, нормализует `violation_type`, детерминированно привязывает `evidence_refs`, агрегирует повторяющиеся finding’и в стабильные `audit_case:*`, умеет ставить response-deadline на объяснения/документы и эскалировать просроченные кейсы в monitoring / collegial review. Queue-driven external complaints и media-response obligations теперь тоже входят в baseline-аудит как `service_degradation_response_ignored`, так что service-degradation влияет уже и на governance/escalation path.
- **Сюжетные аудиторы удалены**: narrative-агенты с capability `audit` больше не поддерживаются. Аудит существует только как отдельный runtime governance-layer, а не как персонаж симуляции.
- **Collegial review**: спорные audit-case могут маршрутизироваться в отдельный collegial review через `audit_review` vote-path с детерминированным составом жюри и закрытием кейса по итогам review.
- **Deterministic truth + freeform truth**: `truth.jsonl` остаётся формальным baseline для evaluation, а `truth_freeform.jsonl` — отдельным LLM-sidecar для richer post-hoc записи нарушений в свободной форме по схеме. Эти два слоя не смешиваются.
- **Unified findings**: runtime audit, deterministic truth и freeform truth постепенно приводятся к общей finding-структуре (`summary`, `mechanism`, `beneficiary`, `risk_tags`, `evidence_refs`). Exact `violation_type` больше не считается единственным носителем смысла.
- **Tender-domain truth heuristics**: deterministic truth-layer теперь может фиксировать не только nomination/reputation-паттерны, но и некоторые доменные серые зоны вроде `preferential_treatment_for_connected_actor`, `non_escalation_under_pressure`, `partial_disclosure_under_deadline_pressure`.
- **Semantic evaluation**: `evaluation.json` теперь содержит не только strict метрики exact-match, но и semantic matching (`semantic_true_positive`, `semantic_precision`, `semantic_recall`, `semantic_f1`) через finding matcher.
- **Status/truth/evaluation/fidelity sidecars**: каждый прогон может писать `status.json` (heartbeat и финальный статус `running`/`finished`/`failed`), `truth.jsonl` (deterministic truth-layer), `evaluation.json` (governance-eval), `fidelity.json` (правдоподобие и структурная дисциплина), `summary.json` (разделённая сводка), а также `environment_summary.json` и `environment_timeline.jsonl` для отдельной телеметрии усиленной среды.
- **DAO по умолчанию**: self-nomination и self-vote цели отключены; нормальный путь для кандидата — `respond_nomination`, а `vote_closed` пишет детерминированную причину результата. `governance.position_policy` в v1 поддерживает только `dao`; `auto` отклоняется при валидации.
- **Веб-launcher на `magistry_lc`**: `POST /api/scenarios/{id}/run` и `POST /api/runs/launch` запускают `magistry_lc` как subprocess, пишут артефакты в `results/{run_name}/` и показываются в `/api/runs/active` как обычные API-запуски.
- **Built-in seed-сценарии**: `seed_s*_g*.json` используются только как backing-файлы для `/api/templates/scenarios/*` и уже хранятся как полноценный `ScenarioConfig`; backend не показывает их в CRUD-списке `/api/scenarios` и не позволяет менять/удалять через сценарные маршруты.
- **Отказ от legacy web-сценариев**: старый формат JSON-карточек (`name/scenario/governance/agents` без полного `ScenarioConfig`) больше не поддерживается. Web backend сохраняет пользовательские сценарии только как полный `ScenarioConfig`; если `sim_config` пуст, при сохранении сначала материализуется выбранный шаблон `S/G`, а затем поверх него накладываются overrides из UI.
- **Custom governance modes**: пользовательские `G*`-режимы должны содержать валидный `GovernanceConfig` (в поле `config` или в корне JSON); backend применяет их при подстановке шаблона и round-trip сценария, а не игнорирует как неизвестный `G4+`.
- **Оставшиеся заглушки web API**: HTTP 501 сохраняется только для `POST /api/personalities/{personality_id}/interview/generate` и `POST /api/ai/secondary-agents`; web UI не должен показывать активные кнопки для этих маршрутов. Маршруты генерации personality/agent-type уже работают через `magistry_lc.llm`.
- **Артефакты прогонов в web API**: чтение и мониторинг поддерживают оба формата — `results/*_events.jsonl` и `results/{run_name}/events.jsonl`.
- **Role-based visibility в web API/WS**: `viewer` получает только shared-события (`aud:public` / `aud:internal`); point-to-point private events скрываются, чувствительные payload'ы shared-событий редактируются, а `GET /api/artifacts/{doc_id}` доступен только `admin`. Audience-less legacy event-stream не считается поддерживаемым контрактом API.
- **Локальные артефакты в рабочем дереве**: в репозитории могут присутствовать `results/`, `web/backend/users.db`, `web/frontend/dist/`, `web/frontend/node_modules/` и `__pycache__/`. Источником истины при чтении и редактировании считать `src/`, `web/backend/`, `web/frontend/src/`, `docs/`, `tests/`, `data/` и `scenarios/`.

## Техническое состояние кодовой базы

### Открытый технический долг

| Область | Описание |
|----------|---------|
| Runtime latency в full-ecology прогонах | Реальные 20-50k-token prompts на `openai/gpt-oss-120b` через OpenRouter дают long-tail latency. Дополнительные факторы: `provider_order=["Groq"]` без latency-aware routing, отсутствие коротких per-role timeout/fallback, не трассируемые embeddings и последовательная `memory`-суммаризация. |
| Strict audit evaluation | Даже после выравнивания payload/evidence strict exact-match в `evaluation.json` остаётся слишком хрупким на живых прогонах; semantic matching уже даёт сигнал, но exact всё ещё часто уходит в `0 TP`. Нужна дальнейшая нормализация target/evidence или case-level matching. |
| Слишком синхронный temporal runtime | Локальные reaction windows и pending-follow-up queue уже появились, но мир всё ещё в основном живёт крупными глобальными тиками. Для richer full-ecology среды нужен ещё менее жёсткий temporal/runtime-контур с большим количеством мелких параллельных акторов, локальных очередей и неодновременных процессов без обязательной синхронизации всего мира на каждом шаге. |

### Завершённые миграции

| Миграция | Описание |
|----------|---------|
| Удаление `magistry_sim` | Старый движок удалён целиком. Все нужные модули (`bm25.py`, `llm/`) перенесены в `magistry_lc`. Зависимость через `deps.py` устранена. |
| Веб-launcher на `magistry_lc` | Запуск и мониторинг прогонов переведены на `magistry_lc` и directory-based артефакты в `results/{run_name}/`, при сохранении совместимости с legacy sidecars. |
| Веб-эндпоинты после миграции `magistry_sim` | Основные сценарные, runner-, template- и AI-маршруты переведены на `magistry_lc` или локальные библиотеки. Не мигрированы только генерация интервью и вторичных агентов, поэтому эти два маршрута сохраняют HTTP 501. |
