# AGENTS.md — инструкции для AI-агентов

## Назначение проекта

MAGISTRY — мета-двигатель для агентной симуляции организационных процессов. Система моделирует деятельность муниципальных организаций с помощью когнитивных LLM-агентов, исследуя эффективность гибридных управленческих систем (AI-аудит + децентрализованное голосование) в противодействии коррупции. Краткая концепция — в [CONCEPT.README.md](CONCEPT.README.md), полная архитектура — в [ARCHITECTURE.md](ARCHITECTURE.md), статья на которую опираемся в [docs/2411.10109v1.pdf](docs/2411.10109v1.pdf)

## Структура репозитория

```
MAGISTRY/
├── src/magistry_sim/           # Движок симуляции (Python 3.12+)
│   ├── config.py               # ScenarioConfig, AgentProfile, Capability, ResourcePool
│   ├── enums.py                # GovernanceMode (G0–G3), ScenarioId (S0–S6)
│   ├── scenarios.py            # Реализованные сценарии S0–S2
│   ├── scenarios_v4.py         # Шаблоны расширенных сценариев
│   ├── environment.py          # Синхронная среда (раундовая)
│   ├── async_environment.py    # Асинхронная среда (непрерывное время)
│   ├── state.py                # WorldState — глобальное состояние мира
│   ├── state_ops.py            # StateOp — атомарные операции над состоянием
│   ├── cases.py                # Case, Proposal, Note, Vote (свободная модель)
│   ├── agents.py               # AgentRunner, MockAgentRunner, LLMAgentRunner
│   ├── cognitive_runner.py     # CognitiveAgentRunner — когнитивный цикл Park et al.
│   ├── memory.py               # MemoryStream — поток памяти с гибридным поиском
│   ├── reflection.py           # Рефлексия — обобщения высшего уровня
│   ├── planning.py             # Стратегическое и тактическое планирование
│   ├── bm25.py                 # Собственная реализация BM25
│   ├── personality.py          # HEXACO, DarkTriad, NeutralizationTechnique
│   ├── interviews.py           # 30 вопросов, 8 доменов, InterviewFragmentIndex
│   ├── persona_generator.py    # Параллельная генерация персон через LLM
│   ├── biography.py            # Генерация биографий агентов
│   ├── context.py              # build_situation — ситуационные сводки
│   ├── tools/
│   │   ├── actions.py          # open_case, submit_proposal, resolve_case,
│   │   │                       # add_note, file_report, cast_vote, move_to
│   │   └── communication.py    # talk_to, talk_to_threaded
│   ├── arbiter.py              # LLM-арбитр свободных действий
│   ├── document_forge.py       # Генерация документов ГОСТ-формата
│   ├── conversation.py         # ConversationManager, Thread, ChannelType
│   ├── world_rules.py          # Правила мира для арбитра
│   ├── sim_clock.py            # SimClock, WorkSchedule
│   ├── scheduler.py            # Scheduler — приоритетная очередь пробуждений
│   ├── locations.py            # Location, LocationManager
│   ├── narrator.py             # WorldNarrator — нарративные описания
│   ├── world_generator.py      # WorldGenerator — генерация начального мира
│   ├── event_generator.py      # Генерация внешних событий
│   ├── llm.py                  # LLMProvider, create_provider, кеш
│   ├── tracing.py              # LLMTracer — журнал промптов
│   ├── graph.py                # Социальный граф (NetworkX)
│   ├── resources.py            # Ресурсы агентов, лимиты
│   ├── events.py               # EventLog (JSONL)
│   ├── metrics.py              # Матрица ошибок, сводные метрики
│   ├── statistics.py           # Статистический анализ батчей
│   ├── oracle.py               # Оракул — определение нарушений
│   ├── reputation.py           # Социальный капитал, заморозка
│   ├── batch.py                # BatchRunner — пакетные прогоны
│   ├── validation.py           # Валидация конфигураций
│   └── cli.py                  # Интерфейс командной строки
├── web/
│   ├── backend/
│   │   ├── main.py             # FastAPI-сервер (REST + WebSocket, ~2500 строк)
│   │   ├── auth.py             # JWT-аутентификация, роли
│   │   ├── database.py         # SQLite через aiosqlite
│   │   ├── runner.py           # Фоновый запуск симуляций
│   │   ├── graph_state.py      # Построение графа для визуализации
│   │   └── manage_users.py     # CLI управления пользователями
│   └── frontend/
│       └── src/
│           ├── App.tsx         # Главный компонент
│           ├── components/     # SimGraph, EventFeed, AgentPanel и др.
│           ├── hooks/          # useAuth, useSimulation
│           └── utils/          # apiClient, payload, time
├── tests/                      # 55+ тестовых файлов
├── data/
│   ├── agent_types/            # Шаблоны типов агентов (JSON)
│   ├── personalities/          # Архетипы личности (JSON)
│   ├── interviews/             # Данные интервью (JSON)
│   └── governance_modes/       # Конфигурации режимов управления
├── scenarios/                  # JSON-конфигурации сценариев
├── results/                    # Результаты прогонов (JSONL, артефакты)
├── docs/                       # Техническая документация
├── ARCHITECTURE.md             # Полная архитектура системы
├── CONCEPT.README.md           # Концепция ВКР
├── README.md                   # Точка входа для разработчика
└── pyproject.toml              # Конфигурация проекта
```

## Архитектурные принципы

При работе с кодом необходимо соблюдать следующие принципы:

1. **Движок не знает предметной области.** Ядро оперирует абстракциями (дело, предложение, полномочие, ресурс). Никаких `if case_type == "procurement"` в движке. Специфика — в конфигурации сценариев.

2. **Полномочия вместо ролей.** Агент определяется набором полномочий (`Capability`), а не жёсткой ролью. Один агент может открывать дела, подавать предложения и голосовать одновременно.

3. **Агенты максимально свободны.** Агент — автономная сущность, которая живёт в мире: наблюдает, запоминает, планирует и действует исходя из собственных целей и личности. Агент не ограничен фиксированным набором действий — он может предпринять любое действие в рамках физики мира (полномочия, ресурсы, локации, расписание, социальный граф). Встроенные инструменты (`open_case`, `talk_to` и т.д.) — быстрый путь для типовых операций; произвольные действия оцениваются LLM-арбитром. Среда обеспечивает физику, но не диктует поведение.

4. **Числовые параметры скрыты от LLM.** Значения `greed`, `fear`, `honesty`, HEXACO, Dark Triad переводятся в текстовые описания. LLM видит только текст, а не числа.

5. **Когнитивный цикл по Park et al.** Наблюдение → память → рефлексия → планирование → действие. Не упрощать этот цикл.

6. **Две среды исполнения.** `Environment` (синхронная, раундовая) для простых сценариев; `AsyncEnvironment` (асинхронная, непрерывное время) для реалистичных. Обе должны поддерживаться.

7. **Свободная модель дел.** `case_type` и `stage` — произвольные строки. Никаких `CASE_REGISTRY` или `CaseSchema`.

## Стек и зависимости

- Python 3.12+, Pydantic 2.0+, NetworkX 3.0+, OpenAI 1.0+, Rich 13.7+
- Дополнительно: rank-bm25 0.2.2+, sentence-transformers 3.0+ (для локальных эмбеддингов)
- Веб: FastAPI 0.115+, aiosqlite, PyJWT; React 19, D3.js 7, Vite
- Тесты: pytest 9.0+, pytest-asyncio 0.23+

## Команды

```bash
# Тесты
pytest                                    # все тесты
pytest tests/test_environment.py          # конкретный модуль
pytest -k "test_cognitive"                # по паттерну

# CLI-симуляция
magistry-sim --scenario S0                # синхронный режим
magistry-sim --scenario S1 --mode async   # асинхронный режим
magistry-sim --list-scenarios             # список сценариев

# Веб-интерфейс
cd web && bash start.sh                   # сервер + фронтенд

# Пакетный запуск
magistry-sim --scenario S0 --batch --batch-runs 10 --batch-modes G0,G2,G3
```

## Соглашения по коду

- **Документация**: Google docstrings для всех публичных функций и классов.
- **Модели данных**: Pydantic `BaseModel` для конфигураций и сериализуемых структур; `dataclass` для внутренних структур.
- **Типизация**: полная аннотация типов, `from __future__ import annotations`.
- **Логирование**: `logging.getLogger(__name__)`, уровни DEBUG/INFO/WARNING.
- **Тестирование**: каждый модуль — свой тестовый файл `tests/test_<module>.py`. Используются `MockAgentRunner` и `MockLLMProvider` из `conftest.py`.
- **Асинхронность**: `asyncio_mode = "auto"` в pytest; async-тесты через `pytest-asyncio`.

## Ведение документации

При изменении кода необходимо обновлять документацию. Правила:

| Что изменилось | Какой документ обновить |
|---|---|
| Новый инструмент агента | `ARCHITECTURE.md` (раздел 6), `docs/simulation_engine.md` |
| Новый модуль в `src/magistry_sim/` | `ARCHITECTURE.md` (раздел 17), `AGENTS.md` (дерево), `docs/architecture_guide.md` |
| Изменение модели данных (`config.py`, `state.py`, `cases.py`) | `ARCHITECTURE.md` (раздел 3), `docs/data_formats.md` |
| Новый REST/WebSocket-эндпоинт | `docs/web_interface.md` |
| Новый сценарий или режим управления | `ARCHITECTURE.md` (разделы 10–11), `README.md`, `docs/simulation_engine.md` |
| Изменение когнитивного цикла | `ARCHITECTURE.md` (раздел 14), `docs/simulation_engine.md` |
| Новый CLI-аргумент | `docs/cli_reference.md` |
| Новая зависимость | `README.md` (стек), `docs/getting_started.md` |
| Новый тестовый паттерн | `docs/testing.md` |

## Типичные задачи

| Задача | Ключевые файлы |
|---|---|
| Добавить новый тип дела | `scenarios.py`, `config.py` (Need, Capability) |
| Добавить инструмент агента | `tools/actions.py`, `environment.py` (TOOL_DISPATCH), `context.py` |
| Изменить когнитивный цикл | `cognitive_runner.py`, `memory.py`, `reflection.py`, `planning.py` |
| Добавить тип личности | `personality.py`, `interviews.py`, `persona_generator.py` |
| Добавить режим управления | `enums.py`, `scenarios.py` (add_governance_agents), `environment.py` |
| Добавить сценарий | `scenarios.py` или `scenarios_v4.py`, `enums.py` (ScenarioId) |
| Добавить REST-эндпоинт | `web/backend/main.py` |
| Добавить WebSocket-событие | `web/backend/main.py`, `events.py` |
| Добавить React-компонент | `web/frontend/src/components/` |
| Изменить ситуационную сводку | `context.py` (build_situation) |
| Добавить метрику | `metrics.py`, `oracle.py` |
| Изменить арбитра | `arbiter.py`, `world_rules.py`, `state_ops.py` |
| Добавить тип документа | `document_forge.py` (DocType, _GOST_INSTRUCTIONS) |
| Добавить локацию | `locations.py`, конфигурация сценария |
| Изменить генерацию мира | `world_generator.py`, `narrator.py`, `event_generator.py` |

## Известные особенности

- **Крупный `main.py`**: `web/backend/main.py` (~2500 строк) содержит все REST-эндпоинты, WebSocket-обработчики и middleware в одном файле. При рефакторинге учитывать, что множество компонентов зависят от общего состояния приложения.
- **Два `conftest.py`**: корневой `tests/conftest.py` содержит основные фикстуры; отдельных конфигураций для подкаталогов нет.
- **Асинхронный режим**: `AsyncEnvironment` использует `asyncio.run()` из CLI, но в веб-интерфейсе запускается через фоновый поток (`runner.py`).
- **Зависимость от OpenAI**: для запуска когнитивного агента требуется `OPENAI_API_KEY`. Тесты используют `MockLLMProvider` и не требуют ключа.
- **Локальные эмбеддинги**: при `EMBEDDING_PROVIDER=local` используется `sentence-transformers` (требует установки `pip install -e ".[search]"`).

## Техническое состояние кодовой базы

Этот раздел фиксирует устаревшие компоненты и направления рефакторинга. При работе с перечисленными модулями следует учитывать их статус.

### Устаревшие компоненты

| Компонент | Описание | Статус |
|-----------|---------|--------|
| `CrewAIAgentRunner`, `_build_crewai_tools` (`agents.py`) | Runner на CrewAI и обвязка. Заменён `CognitiveAgentRunner` | Удалён |
| `--runner crewai` (`cli.py`, `runner.py`) | Путь запуска CrewAI runner | Удалён |
| `scenarios_v4.py`, `test_scenarios_v4.py` | Шаблоны сценариев, не интегрированные в основной `scenarios.py` | Удалён |
| `TOOL_DISPATCH` (`environment.py`) | Фиксированный набор из 8 инструментов. Целевая модель — произвольные действия через Arbiter + `perform_action`. В `AsyncEnvironment` переход уже начался: `talk_to_threaded` и `create_document` обрабатываются вне `TOOL_DISPATCH` | Переходный период |
| `talk_to` (`tools/communication.py`) | Синхронная однорепликовая версия. В async-режиме заменена на `talk_to_threaded` (многорепликовые диалоги через `ConversationManager`) | Частично устаревший |

### Требует рефакторинга

| Компонент | Проблема | Рекомендация |
|-----------|----------|-------------|
| `web/backend/main.py` (~2575 строк) | Монолит: 65 эндпоинтов, Pydantic-модели, валидация, WebSocket, конфигурация | Разделить на `routes/`, `models.py`, `websocket.py`, `validators.py` |
| `agents.py` (~1225 строк) | Четыре runner'а в одном файле, включая устаревший CrewAI | Разделить; CrewAI вынести или удалить |
| `agents.py` + `cognitive_runner.py` | Дублирование JSON-парсинга (`_parse_json_actions`, `_extract_json_array`, `_extract_json_object`) | Общий модуль `json_parser.py` |
| `environment.py` + `async_environment.py` (~1945 строк) | ~50% дублирования логики (`_init_state`, `_generate_needs`, `_deliver_observations`) | Базовый класс `BaseEnvironment` |
| `llm.py` (~1212 строк) | 13 провайдеров, кеширование, логирование — всё вместе | Разделить на `llm_providers.py`, `embedding_providers.py`, `llm_cache.py` |
| Система инструментов | Два параллельных пути обработки (TOOL_DISPATCH и Arbiter) плюс специальные обработчики в AsyncEnvironment | Унификация через Arbiter как единый путь; встроенные инструменты — оптимизация |

Подробности — в [ARCHITECTURE.md](ARCHITECTURE.md), раздел 26.
