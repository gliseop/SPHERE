# SPHERE — мета-двигатель агентной симуляции

SPHERE — исследовательская платформа для агентной симуляции организационных процессов. Система моделирует деятельность муниципальных и государственных организаций с помощью когнитивных LLM-агентов, действующих автономно в рамках заданных полномочий и ресурсов. Каждый агент обладает уникальной личностью, заданной текстовой биографией и нарративным интервью, потоком памяти с гибридным поиском и способностью к адаптивному поведению.

Платформа создана для магистерской диссертации, посвящённой оценке гибридных управленческих систем (AI + DAO) в контексте противодействия коррупции. Ключевая гипотеза: сочетание AI-аудита, репутационного механизма и децентрализованного голосования снижает уровень нарушений по сравнению с традиционным контролем или его отсутствием. Теоретические основания и обзор литературы — в [docs/chapters/chapter_1.md](docs/chapters/chapter_1.md).

Проект открыт под лицензией MIT и находится в исследовательском статусе: API и формат сценариев ещё могут меняться, но ядро, веб-интерфейс, сценарии и документация доступны для проверки, воспроизведения экспериментов и доработки.

## Возможности

Движок симуляции не привязан к конкретной предметной области — он оперирует универсальными абстракциями: свободное действие, полномочие (`work`, `dao`, `spawn` как legacy/runtime-слой), сущность (агент, канал, организация, рабочий элемент). Конкретные сценарии — закупки, найм, согласование бюджета — задаются конфигурацией в YAML/JSON, а не кодом.

Когнитивные агенты больше не выбирают typed action menu: они формулируют один свободный `proposal` на тик, а гибридный арбитр переводит этот proposal во внутренние детерминированные операции: сообщения, рабочие изменения, DAO-голосования, создание сущностей и другие последствия мира. Отдельный `RuntimeAuditor` в governance-слое анализирует уже совершённые события, эмитит audit-сигналы и может запускать заморозку роста репутации и коллегиальное review. Перед первым тиком движок может обогащать персоны в режиме `full` (биография + интервью + expert reflection), извлекать вторичных агентов из социального графа и в ходе симуляции добавлять новых участников через worldgen и системные runtime-контуры, но только с человеко-читаемыми именами и без machine-like display-name. Веб-интерфейс (FastAPI + React 19 + D3.js) обеспечивает визуализацию социального графа, ленту событий и управление симуляциями.

## Что входит в репозиторий

- `src/sphere_lc/` — движок симуляции, когнитивный цикл агентов, арбитр, runtime-аудитор, DAO-логика, worldgen, post-hoc evaluation и CLI.
- `web/` — FastAPI backend и React/Vite frontend для запуска, просмотра и анализа прогонов.
- `scenarios/` — готовые муниципальные сценарии и template-сценарии G0-G3.
- `docs/` — архитектура, форматы данных, руководство по запуску, тестирование и главы исследовательской работы.
- `tests/` — pytest-покрытие движка, backend и интеграционных контуров.

## Быстрый старт

### Установка

```bash
git clone <repository-url> && cd SPHERE
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
pip install -e ".[lc,dev]"
```

Группа `dev` включает не только `pytest`, но и зависимости веб-слоя, необходимые для коллекции и запуска тестов (`fastapi`, `aiofiles`, `python-multipart`, `PyJWT`, `bcrypt`).

### Настройка

```bash
cp .env.example .env
# Отредактировать .env: указать OPENAI_API_KEY.
# Для OpenRouter задайте OPENAI_BASE_URL=https://openrouter.ai/api/v1
# и при необходимости OPENROUTER_PROVIDER_ORDER=Groq
```

### Запуск симуляции

```bash
# Контрольный baseline-сценарий
sphere-lc run --scenario scenarios/template_s0_g0.json --out results/template_s0_g0_run

# Богатый demo-сценарий
sphere-lc run --scenario scenarios/procurement_tender.json --out results/procurement_tender_run

# Исследовательский core-governance run: без worldgen, без secondary-spawn
sphere-lc run --scenario scenarios/procurement_tender_core_governance.json --out results/procurement_tender_core_run

# Исследовательский full-ecology run: full-persona + secondary-spawn + worldgen
sphere-lc run --scenario scenarios/procurement_tender_full_ecology.json --out results/procurement_tender_full_run

# Baseline-серия для главы 2: один procurement-сценарий в режимах G0-G3
python scripts/run_chapter2_baseline.py --scenario scenarios/procurement_tender_core_governance.json --out results/chapter2_procurement_baseline --repeats 3

# Генерация сценария из текстового описания (LLM)
sphere-lc compose --description "Тендер на ремонт дорог, 4 агента, конфликт интересов" --out scenarios/composed.json

# Пост-фактум анализ нарушений (LLM-оракул, чанкинг по events.jsonl)
sphere-lc oracle --events results/template_s0_g0_run/events.jsonl --out results/template_s0_g0_run/violations.json
```

При запуске `sphere-lc run` движок пишет `events.jsonl`, `trace.jsonl`, `truth.jsonl`, `evaluation.json`, `fidelity.json` и `summary.json` в директорию прогона. Если `--out` не указан, используется `results/<timestamp>`, поэтому прогон сразу доступен web-интерфейсу.

PowerShell может подтянуть `.env` в текущий process без ручного `set`:

```powershell
. .\scripts\Import-DotEnv.ps1
python -m sphere_lc.cli run --scenario scenarios/procurement_tender.json --out results/procurement_tender_run
```

### Запуск веб-интерфейса

Предпочтительный путь для этой среды:

```bash
docker compose up --build -d web
```

После старта UI доступен по адресу `http://localhost:8765`. По умолчанию контейнер создаёт dev-пользователя:

- логин: `sphere_admin`
- пароль: `SphereDocker123!`

При необходимости переопределите `SPHERE_ADMIN_USERNAME` и `SPHERE_ADMIN_PASSWORD` через окружение или `.env` до запуска `docker compose`.

Локальный fallback без Docker:

```bash
cd web && bash start.sh
```

Скрипт `web/start.sh` сам находит Python как в `.venv/bin`, так и в `.venv/Scripts`, поэтому одинаково подходит для Unix- и Windows-окружений с Git Bash.

Подробные инструкции — в [docs/getting_started.md](docs/getting_started.md).

## Архитектура

```mermaid
graph TB
    subgraph Движок["Движок симуляции (sphere_lc)"]
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
| [docs/chapters/chapter_1.md](docs/chapters/chapter_1.md) | Глава 1 ВКР: теоретические основания, обзор литературы, гибридная система |
| [docs/chapters/chapter_2.md](docs/chapters/chapter_2.md) | Глава 2 ВКР: проектирование платформы и план эксперимента |
| [docs/chapters/chapter_3.md](docs/chapters/chapter_3.md) | Глава 3 ВКР: экспериментальные результаты |
| [AGENTS.md](AGENTS.md) | Инструкции для AI-агентов: правила, соглашения, задачи |
| [docs/overview.md](docs/overview.md) | Обзор системы для технического читателя |
| [docs/getting_started.md](docs/getting_started.md) | Пошаговое руководство по установке и запуску |
| [docs/testing.md](docs/testing.md) | Тестирование: структура, паттерны, покрытие |
| [docs/cli_reference.md](docs/cli_reference.md) | Справочник CLI: аргументы, переменные окружения |
| [docs/web_interface.md](docs/web_interface.md) | Веб-интерфейс: API, WebSocket, компоненты |
| [docs/simulation_engine.md](docs/simulation_engine.md) | Движок: агент, арбитр, память, оракул |
| [docs/data_formats.md](docs/data_formats.md) | Форматы данных: сценарии, события, персоны |
| [docs/persona_interview_design.md](docs/persona_interview_design.md) | Подсистема персон: генерация, интервью, фрагментный поиск |
| [docs/auditor_refactor_plan.md](docs/auditor_refactor_plan.md) | План рефакторинга runtime-аудитора |

## Режимы управления

Сценарии конфигурируются через YAML/JSON. Раздел `governance` определяет набор механизмов контроля:

| Компонент | Описание |
|---|---|
| Runtime-аудитор | Отдельный LLM-first governance-модуль, выявляющий signals/findings по событиям тика |
| Репутация | Репутация и временная заморозка продвижения/начислений как санкционный слой |
| DAO-голосование | Коллегиальное анонимное рассмотрение спорных случаев |

Теоретическое обоснование каждого компонента — в [docs/chapters/chapter_1.md](docs/chapters/chapter_1.md), раздел 1.6.

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

## Лицензия

Код распространяется по лицензии MIT. Полный текст — в [LICENSE](LICENSE).
