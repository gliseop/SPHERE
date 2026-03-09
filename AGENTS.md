# AGENTS.md — инструкции для AI-агентов

## Назначение проекта

MAGISTRY — мета-двигатель для агентной симуляции организационных процессов. Система моделирует деятельность муниципальных организаций с помощью когнитивных LLM-агентов, исследуя эффективность гибридных управленческих систем (AI-аудит + децентрализованное голосование) в противодействии коррупции.

### Теоретическая база

[chapter_1.md](chapter_1.md) — первая глава ВКР, содержащая обзор литературы и обоснование подхода. Глава охватывает проблему принципала-агента в иерархических организациях (Йенсен, Меклинг), четыре категории подходов к снижению агентских издержек (институциональные, технологические, поведенческие, децентрализованные), эволюцию агентного моделирования от детерминированных правил к БЯМ-агентам, а также формулирует исследовательский пробел — отсутствие инструмента для экспериментальной оценки гибридных управленческих механизмов в среде с адаптивными агентами. Раздел 1.6 описывает архитектуру предлагаемой гибридной системы (ИИ-аудитор, репутационный механизм, коллегиальное рассмотрение), включая границы применимости и обоснование проектных решений.

[docs/2411.10109v1.pdf](docs/2411.10109v1.pdf) — Park J.S. et al. «Generative Agent Simulations of 1,000 People» (2024). Ключевая статья, определяющая архитектуру когнитивного агента в MAGISTRY. Авторы показали, что генеративные агенты на основе двухчасовых интервью достигают нормализованной точности 0,85 в воспроизведении индивидуального поведения (GSS). Из статьи заимствованы: приоритет текстового описания над числовыми параметрами, механизм экспертной рефлексии, система нарративных интервью с гибридным поиском по фрагментам (fragment-based retrieval).

### Архитектурная документация

Навигатор по архитектуре (карта модулей, путь данных) — в [docs/architecture_guide.md](docs/architecture_guide.md).

Подсистема «Персона = Интервью» (цепочка генерации, фрагментный поиск, назначение личностей) — в [docs/persona_interview_design.md](docs/persona_interview_design.md).

## Структура репозитория

```
MAGISTRY/
├── src/magistry_lc/            # Движок симуляции (LangChain/LangGraph)
│   ├── __init__.py             # Пакет
│   ├── cli.py                  # CLI `magistry-lc`
│   ├── config.py               # ScenarioConfig + Runtime/Governance/LLM/Memory
│   ├── scenario.py             # Load/save YAML/JSON сценариев
│   ├── ids.py                  # Типизированные ID и аудитории (aud:*)
│   ├── entities.py             # EntityRegistry + EntityRecord (антифантомы)
│   ├── id_alloc.py             # Детерминированное выделение новых ID
│   ├── state.py                # WorldState (agents/work_items/votes)
│   ├── persona.py              # PersonaArtifact/Library/Generator + SocialGraphExtractor
│   ├── memory.py               # Память агента (buffer + hybrid retrieval)
│   ├── actions.py              # Action[] (structured + spawn_agent + perform)
│   ├── agent.py                # AgentRunner (1 LLM-вызов на ход)
│   ├── ops.py                  # Детерминированные StateOp -> Event, включая CreateAgentOp
│   ├── arbiter.py              # Hybrid arbiter (caps + YAML-journal + LLM perform)
│   ├── auditor.py              # RuntimeAuditor (rules-first detection + governance interventions)
│   ├── dao.py                  # DAO vote closure + position policy
│   ├── engine.py               # WorldEngine (enrichment, social graph, динамический spawn, детерминированный apply)
│   ├── worldgen.py             # WorldGenerator (external events + spawn suggestions, без приватных утечек)
│   ├── composer.py             # WorldComposer (LLM → ScenarioConfig + persona enrichment)
│   ├── oracle.py               # ViolationOracle (чанкинг по events.jsonl)
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
│   ├── backend/
│   │   ├── main.py             # FastAPI-сервер (точка входа, WebSocket)
│   │   ├── routes/             # REST-эндпоинты (auth, runs, scenarios, ai и др.)
│   │   ├── auth.py             # JWT-аутентификация, роли
│   │   ├── database.py         # SQLite через aiosqlite
│   │   ├── runner.py           # Фоновый запуск симуляций
│   │   ├── run_artifacts.py    # Поиск артефактов прогонов (legacy + directory)
│   │   ├── graph_state.py      # Построение графа для визуализации
│   │   ├── constants.py        # Enum-значения (GovernanceMode, ScenarioId)
│   │   └── manage_users.py     # CLI управления пользователями
│   └── frontend/
│       └── src/
│           ├── App.tsx         # Главный компонент
│           ├── components/     # SimGraph, EventFeed, AgentPanel и др.
│           ├── hooks/          # useAuth, useSimulation
│           └── utils/          # apiClient, payload, time
├── tests/                      # Тесты (10+ файлов: движок, аудит, эмбеддинги, веб)
├── data/
│   ├── agent_types/            # Шаблоны типов агентов (JSON)
│   ├── personalities/          # Архетипы личности (JSON)
│   ├── interviews/             # Данные интервью (JSON)
│   └── governance_modes/       # Конфигурации режимов управления
├── scenarios/                  # JSON-конфигурации сценариев
├── results/                    # Результаты прогонов (JSONL, артефакты)
├── docs/                       # Техническая документация
├── README.md                   # Точка входа для разработчика
└── pyproject.toml              # Конфигурация проекта
```

## Архитектурные принципы

При работе с кодом необходимо соблюдать следующие принципы:

1. **Движок не знает предметной области.** Ядро оперирует абстракциями (действие, полномочие, сущность). Специфика — в конфигурации сценариев (YAML).

2. **Полномочия вместо ролей.** Агент определяется набором полномочий (`message`, `work`, `dao`, `audit`, `spawn`), а не жёсткой ролью. Арбитр проверяет полномочия при каждом действии.

3. **Агенты максимально свободны.** Агент — автономная сущность с собственными целями и личностью. Структурированные действия (`send_message`, `add_work_note`, `submit_proposal`) — типовой путь; произвольные действия оцениваются LLM-арбитром. Среда обеспечивает физику, но не диктует поведение.

4. **Текстовые описания вместо числовых параметров.** Личность агента задаётся биографией и текстовой характеристикой, а не числовыми шкалами (обосновано в [chapter_1.md](chapter_1.md), раздел 1.4, по результатам Park et al. [28]).

5. **Антифантомная защита.** `EntityRegistry` блокирует действия, адресованные несуществующим сущностям. Все ID типизированы (`agent:`, `chan:`, `org:`, `work:`).

6. **Детерминированный apply.** Агенты ходят параллельно, но результаты применяются к состоянию мира последовательно и детерминированно.

## Стек и зависимости

- Python 3.12+, Pydantic 2.0+, OpenAI 1.0+, Rich 13.7+
- LangChain/LangGraph: langgraph 0.2+, langchain-core 0.2+, PyYAML 6.0+
- Дополнительно: rank-bm25 0.2.2+ (гибридный поиск в памяти)
- Веб: FastAPI 0.115+, aiosqlite, PyJWT; React 19, D3.js 7, Vite
- Тесты: pytest 9.0+, pytest-asyncio 0.23+

## Команды

```bash
# Тесты
pytest                                    # все тесты
pytest tests/test_magistry_lc_smoke.py    # конкретный модуль
pytest -k "test_arbiter"                  # по паттерну

# MAGISTRY-LC
pip install -e ".[lc]"
magistry-lc run --scenario scenarios/lc_minimal.yaml --out results/lc_minimal_run
magistry-lc compose --description "Короткое описание" --out scenarios/lc_composed.yaml
magistry-lc oracle --events results/lc_minimal_run/events.jsonl --out results/lc_minimal_run/violations.json

# Веб-интерфейс
cd web && bash start.sh                   # сервер + фронтенд
```

## Соглашения по коду

- **Документация**: Google docstrings для всех публичных функций и классов.
- **Модели данных**: Pydantic `BaseModel` для конфигураций и сериализуемых структур; `dataclass` для внутренних структур.
- **Типизация**: полная аннотация типов, `from __future__ import annotations`.
- **Логирование**: `logging.getLogger(__name__)`, уровни DEBUG/INFO/WARNING.
- **Тестирование**: каждый модуль — свой тестовый файл `tests/test_<module>.py`. Используются `MockAgentRunner` и `MockLLMProvider` из `conftest.py`.
- **Асинхронность**: `asyncio_mode = "auto"` в pytest; async-тесты через `pytest-asyncio`.

## Ведение документации

При изменении кода необходимо обновлять документацию. Это обязательная часть каждой задачи, а не отдельный шаг «на потом».

### Принципы

1. **Самоподдержание `AGENTS.md`.** Этот файл — главная карта проекта. При любом структурном изменении (новый/удалённый модуль, изменение дерева файлов, устранение технического долга) обновить соответствующий раздел `AGENTS.md` в рамках той же задачи.

2. **Приоритеты обновлений.** Обязательные обновления — те, без которых документация становится ложной (дерево файлов, таблица технического состояния, `docs/architecture_guide.md`). Желательные — расширение описаний в `docs/` для удобства будущей работы. Обязательные блокируют завершение задачи, желательные — нет.

3. **Формат.** При обновлении документа — читать существующий стиль целевого файла и следовать ему. Не вводить новые форматы и условные обозначения без необходимости. Планы пишем в docs/plans.

4. **Язык и терминология.** Основной язык коммуникации, планов, отчётов и технической документации — русский. Английские слова, термины, аббревиатуры и узкие технические выражения использовать только при необходимости. Если английский термин всё же используется, сразу давать краткое пояснение на русском в скобках.

### Таблица обновлений

| Что изменилось | Какой документ обновить | Приоритет |
|---|---|---|
| Добавлен/удалён/переименован файл в `src/magistry_lc/` | `AGENTS.md` (дерево файлов) | Обязательный |
| Новый модуль в `src/magistry_lc/` | `AGENTS.md` (дерево) | Обязательный |
| Устранён технический долг из таблицы | `AGENTS.md` (раздел «Техническое состояние») — удалить или обновить строку | Обязательный |
| Появился новый технический долг | `AGENTS.md` (раздел «Техническое состояние») — добавить строку | Обязательный |
| Новый инструмент агента | `docs/simulation_engine.md`, `docs/architecture_guide.md` | Обязательный |
| Изменение модели данных (`config.py`, `state.py`) | `docs/data_formats.md`, `docs/simulation_engine.md` | Обязательный |
| Новый сценарий или режим управления | `README.md`, `docs/simulation_engine.md` | Обязательный |
| Изменение когнитивного цикла | `docs/simulation_engine.md` | Обязательный |
| Новый REST/WebSocket-эндпоинт | `docs/web_interface.md` | Желательный |
| Новый CLI-аргумент | `docs/cli_reference.md` | Желательный |
| Новая зависимость | `README.md` (стек), `docs/getting_started.md` | Желательный |
| Новый тестовый паттерн | `docs/testing.md` | Желательный |
| Изменена таблица «Типичные задачи» или «Известные особенности» | `AGENTS.md` — соответствующий раздел | Обязательный |

## Типичные задачи

| Задача | Ключевые файлы |
|---|---|
| Добавить сценарий | `scenarios/` (YAML), `config.py` |
| Изменить арбитра | `arbiter.py`, `journal.py`, `ops.py` |
| Изменить runtime-аудит | `auditor.py`, `engine.py`, `ops.py`, `config.py` |
| Изменить truth/evaluation/fidelity | `truth.py`, `evaluation.py`, `fidelity.py`, `engine.py`, `docs/data_formats.md` |
| Изменить агентский цикл | `agent.py`, `memory.py`, `actions.py` |
| Изменить социальный граф / динамический спавн | `persona.py`, `engine.py`, `actions.py`, `ops.py`, `worldgen.py` |
| Добавить LLM-провайдера | `llm/providers.py`, `llm/__init__.py` |
| Изменить генерацию мира | `worldgen.py`, `composer.py` |
| Добавить REST-эндпоинт | `web/backend/routes/` |
| Добавить WebSocket-событие | `web/backend/main.py`, `events.py` |
| Добавить React-компонент | `web/frontend/src/components/` |
| Изменить оракула | `oracle.py` |
| Изменить DAO-голосование | `dao.py`, `ops.py` |

## Известные особенности

- **Зависимость от OpenAI-совместимого API**: для запуска симуляции требуется `OPENAI_API_KEY` (или совместимый эндпоинт, например OpenRouter). Тесты используют `MockLLMProvider` и не требуют ключа.
- **Эмбеддинги**: поддерживаются mock-режим и реальные провайдеры через OpenAI-совместимый API. Тесты используют `MockEmbeddingProvider`.
- **Динамический спавн**: вторичные и runtime-спавненные агенты получают только безопасный capability-набор (`message`/`work`), без `audit` и без права порождать следующих агентов.
- **Имена новых агентов**: secondary-spawn, runtime-spawn и worldgen-spawn принимают только человеко-читаемые имена; role-alias и machine-like display-name отклоняются или маппятся на уже существующего актора.
- **Runtime-аудитор v1**: `RuntimeAuditor` реализован как отдельный rules-first модуль, а не как обычный `AgentRunner`; он пишет audit-сигналы и может замораживать репутацию, но не заменяет post-hoc `ViolationOracle`.
- **Truth/evaluation/fidelity sidecars**: каждый прогон теперь может писать `truth.jsonl` (deterministic truth-layer), `evaluation.json` (governance-eval), `fidelity.json` (правдоподобие и структурная дисциплина) и `summary.json` (разделённая сводка), отдельно от `events.jsonl` и `trace.jsonl`.
- **DAO по умолчанию**: self-nomination и self-vote цели отключены; нормальный путь для кандидата — `respond_nomination`, а `vote_closed` пишет детерминированную причину результата.
- **Веб-интерфейс**: ряд эндпоинтов, зависевших от удалённого `magistry_sim`, возвращают HTTP 501 (заглушки).
- **Legacy launcher в backend**: `web/backend/runner.py` функции `launch_simulation*` отключены и явно бросают `RuntimeError`, пока веб-запуск не мигрирован на `magistry_lc`.
- **Артефакты прогонов в web API**: чтение и мониторинг поддерживают оба формата — `results/*_events.jsonl` и `results/{run_name}/events.jsonl`.

## Техническое состояние кодовой базы

### Завершённые миграции

| Миграция | Описание |
|----------|---------|
| Удаление `magistry_sim` | Старый движок удалён целиком. Все нужные модули (`bm25.py`, `llm/`) перенесены в `magistry_lc`. Зависимость через `deps.py` устранена. |
| Веб-эндпоинты `magistry_sim` | Эндпоинты, зависевшие от старого движка, заглушены (HTTP 501) или переведены на `magistry_lc.llm`. |
