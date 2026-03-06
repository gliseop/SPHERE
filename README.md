# MAGISTRY — мета-двигатель управления

MAGISTRY (Multi-Agent Governance and Institutional Simulation for Testing and Research Yield) — исследовательская платформа для агентной симуляции организационных процессов. Система моделирует деятельность муниципальных и государственных организаций с помощью когнитивных LLM-агентов, действующих автономно в рамках заданных полномочий и ресурсов. Каждый агент обладает уникальной личностью, заданной текстовой биографией и нарративным интервью, потоком памяти с гибридным поиском и способностью к адаптивному поведению.

Платформа создана для магистерской диссертации, посвящённой оценке гибридных управленческих систем (AI + DAO) в контексте противодействия коррупции. Ключевая гипотеза: сочетание AI-аудита, репутационного механизма и децентрализованного голосования снижает уровень нарушений по сравнению с традиционным контролем или его отсутствием. Теоретические основания и обзор литературы — в [chapter_1.md](chapter_1.md).

## Возможности

Движок симуляции не привязан к конкретной предметной области — он оперирует универсальными абстракциями: действие, полномочие (`message`, `work`, `dao`, `audit`, `spawn`), сущность (агент, канал, организация, рабочий элемент). Конкретные сценарии — закупки, найм, согласование бюджета — задаются конфигурацией в YAML, а не кодом.

Агенты формируют структурированные действия (отправка сообщений, рабочие заметки, предложения, голосования, `spawn_agent`), которые проходят через гибридный арбитр: проверка полномочий, антифантомная валидация через `EntityRegistry` и YAML-журнал мира. Отдельный `RuntimeAuditor` в governance-слое анализирует уже совершённые события, эмитит audit-сигналы и может запускать заморозку репутации. Перед первым тиком движок может обогащать персоны, извлекать вторичных агентов из социального графа и в ходе симуляции добавлять новых участников через `spawn_agent` или worldgen. Веб-интерфейс (FastAPI + React 19 + D3.js) обеспечивает визуализацию социального графа, ленту событий и управление симуляциями.

## Быстрый старт

### Установка

```bash
git clone <repository-url> && cd MAGISTRY
python -m venv .venv && source .venv/bin/activate
pip install -e ".[lc,dev]"
```

### Настройка

```bash
cp .env.example .env
# Отредактировать .env: указать OPENAI_API_KEY; при OpenRouter/OpenAI-compatible
# можно также задать OPENAI_BASE_URL, и runtime подхватит его по умолчанию
```

### Запуск симуляции

```bash
# Минимальный сценарий
magistry-lc run --scenario scenarios/lc_minimal.yaml --out results/lc_minimal_run

# Богатый сценарий с enrichment/social graph/worldgen
magistry-lc run --scenario scenarios/procurement_tender.yaml --out results/procurement_tender_run

# Генерация сценария из текстового описания (LLM)
magistry-lc compose --description "Тендер на ремонт дорог, 4 агента, конфликт интересов" --out scenarios/composed.yaml

# Пост-фактум анализ нарушений (LLM-оракул, чанкинг по events.jsonl)
magistry-lc oracle --events results/lc_minimal_run/events.jsonl --out results/lc_minimal_run/violations.json
```

### Запуск веб-интерфейса

```bash
cd web && bash start.sh
```

Подробные инструкции — в [docs/getting_started.md](docs/getting_started.md).

## Архитектура

```mermaid
graph TB
    subgraph Движок["Движок симуляции (magistry_lc)"]
        ENGINE[WorldEngine]
        AGENT[AgentRunner]
        ARB[Arbiter]
        AUD[RuntimeAuditor]
        MEM[Memory + BM25]
        ENT[EntityRegistry]
    end

    subgraph Сервер["Сервер (FastAPI)"]
        REST[REST API]
        WS[WebSocket]
        AUTH[JWT Auth]
    end

    subgraph Клиент["Клиент (React 19)"]
        GRAPH[SimGraph D3.js]
        FEED[EventFeed]
        PANEL[AgentPanel]
    end

    ENGINE --> REST
    REST --> WS
    WS --> GRAPH
    WS --> FEED
    AGENT --> ENGINE
    MEM --> AGENT
    ARB --> ENGINE
    AUD --> ENGINE
    ENT --> ARB
```

Полное описание — в [docs/architecture_guide.md](docs/architecture_guide.md).

## Документация

| Документ | Описание |
|---|---|
| [docs/architecture_guide.md](docs/architecture_guide.md) | Навигатор по архитектуре: карта модулей, путь данных |
| [chapter_1.md](chapter_1.md) | Глава 1 ВКР: теоретические основания, обзор литературы, гибридная система |
| [AGENTS.md](AGENTS.md) | Инструкции для AI-агентов: правила, соглашения, задачи |
| [docs/overview.md](docs/overview.md) | Обзор системы для технического читателя |
| [docs/getting_started.md](docs/getting_started.md) | Пошаговое руководство по установке и запуску |
| [docs/testing.md](docs/testing.md) | Тестирование: структура, паттерны, покрытие |
| [docs/cli_reference.md](docs/cli_reference.md) | Справочник CLI: аргументы, переменные окружения |
| [docs/web_interface.md](docs/web_interface.md) | Веб-интерфейс: API, WebSocket, компоненты |
| [docs/simulation_engine.md](docs/simulation_engine.md) | Движок: агент, арбитр, память, оракул |
| [docs/data_formats.md](docs/data_formats.md) | Форматы данных: сценарии, события, персоны |
| [docs/evaluation_separation_plan.md](docs/evaluation_separation_plan.md) | План разделения runtime-механизмов, truth-layer и post-hoc оценки |
| [docs/runtime_auditor_design.md](docs/runtime_auditor_design.md) | Технический дизайн отдельного runtime-auditor и его встраивания в тик симуляции |
| [docs/persona_interview_design.md](docs/persona_interview_design.md) | Подсистема персон: генерация, интервью, фрагментный поиск |
| [docs/project_assessment.md](docs/project_assessment.md) | Развёрнутая исследовательская и инженерная оценка проекта |

## Режимы управления

Сценарии конфигурируются через YAML. Раздел `governance` определяет набор механизмов контроля:

| Компонент | Описание |
|---|---|
| Runtime-аудитор | Отдельный rules-first governance-модуль, выявляющий signals/findings по событиям тика |
| Репутация | Репутация и временная заморозка продвижения/начислений как санкционный слой |
| DAO-голосование | Коллегиальное анонимное рассмотрение спорных случаев |

Теоретическое обоснование каждого компонента — в [chapter_1.md](chapter_1.md), раздел 1.6.

## Стек технологий

| Компонент | Технология | Версия |
|---|---|---|
| Язык | Python | 3.12+ |
| Модели данных | Pydantic | 2.0+ |
| Оркестрация | LangGraph | 0.2+ |
| LLM-провайдер | OpenAI API (совместимый) | 1.0+ |
| Веб-сервер | FastAPI | 0.115+ |
| Клиент | React | 19 |
| Визуализация | D3.js | 7 |
| Тестирование | pytest + pytest-asyncio | 9.0+ |
| Лексический поиск | rank-bm25 | 0.2.2+ |
| CLI | Rich | 13.7+ |
