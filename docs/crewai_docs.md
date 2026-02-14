# CrewAI Documentation (scraped 2026-02-14)

Источник: https://docs.crewai.com/
Версия: 1.9.3 (январь 2026)
Python: >=3.10, <3.14

---

## Оглавление

1. [Введение](#1-введение)
2. [Установка](#2-установка)
3. [Быстрый старт](#3-быстрый-старт)
4. [Агенты (Agents)](#4-агенты-agents)
5. [Задачи (Tasks)](#5-задачи-tasks)
6. [Экипажи (Crews)](#6-экипажи-crews)
7. [Инструменты (Tools)](#7-инструменты-tools)
8. [Процессы (Processes)](#8-процессы-processes)
9. [Языковые модели (LLMs)](#9-языковые-модели-llms)
10. [Память (Memory)](#10-память-memory)
11. [Потоки (Flows)](#11-потоки-flows)
12. [Коллаборация агентов](#12-коллаборация-агентов)
13. [Знания (Knowledge)](#13-знания-knowledge)
14. [Планирование (Planning)](#14-планирование-planning)
15. [Рассуждения (Reasoning)](#15-рассуждения-reasoning)
16. [Событийная система (Event Listeners)](#16-событийная-система-event-listeners)
17. [Тестирование (Testing)](#17-тестирование-testing)
18. [Обучение (Training)](#18-обучение-training)
19. [Создание пользовательских инструментов](#19-создание-пользовательских-инструментов)
20. [Пользовательские LLM](#20-пользовательские-llm)
21. [Подключение LLM-провайдеров](#21-подключение-llm-провайдеров)
22. [Настройка агентов](#22-настройка-агентов)
23. [Иерархический процесс](#23-иерархический-процесс)
24. [Асинхронное выполнение](#24-асинхронное-выполнение)
25. [Хуки выполнения](#25-хуки-выполнения)
26. [Хуки инструментов](#26-хуки-инструментов)
27. [Хуки LLM](#27-хуки-llm)
28. [Условные задачи](#28-условные-задачи)
29. [Ввод человека при выполнении](#29-ввод-человека-при-выполнении)
30. [CLI-интерфейс](#30-cli-интерфейс)
31. [Аннотации и декораторы](#31-аннотации-и-декораторы)
32. [Принудительный вывод инструмента как результат](#32-принудительный-вывод-инструмента-как-результат)
33. [Kickoff для коллекций](#33-kickoff-для-коллекций)
34. [Архитектура для продакшена](#34-архитектура-для-продакшена)
35. [Последовательный процесс](#35-последовательный-процесс)
36. [Работа с файлами](#36-работа-с-файлами)
37. [Проектирование эффективных агентов](#37-проектирование-эффективных-агентов)
38. [Агенты с исполнением кода](#38-агенты-с-исполнением-кода)
39. [Пользовательский агент-менеджер](#39-пользовательский-агент-менеджер)
40. [Мультимодальные агенты](#40-мультимодальные-агенты)

---

## 1. Введение

CrewAI -- фреймворк с открытым исходным кодом для построения мультиагентных AI-систем, объединяющий **Flows** (структурированные рабочие процессы) и **Crews** (совместные команды агентов). Платформа обслуживает более 100 000 сертифицированных разработчиков.

### Архитектура ядра

**Flows** выполняют роль менеджера приложения: управляют сохранением состояния, событийным выполнением и потоком управления через условную логику и ветвление.

**Crews** работают как специализированные команды агентов внутри Flows: обеспечивают ролевых агентов с возможностями автономного сотрудничества и делегированием задач на основе сильных сторон агентов.

### Цикл работы

1. Flow инициирует событие
2. Flow управляет решениями о состоянии
3. Flow делегирует сложные задачи Crew
4. Агенты Crew совместно работают над выполнением
5. Результаты возвращаются в Flow
6. Выполнение продолжается на основе результатов

### Когда использовать

- **Flows** -- для общей структуры приложения, сохранения состояния, условной логики
- **Crews** -- когда нужна автономия и мультиагентная коллаборация для решения конкретных сложных проблем

---

## 2. Установка

### Системные требования

- Python >=3.10, <3.14
- openai >= 1.13.3

### Шаг 1: Установка UV

macOS/Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Шаг 2: Установка CrewAI CLI

```bash
uv tool install crewai
uv tool list  # проверка
```

### Создание проекта

```bash
crewai create crew <project_name>
```

Структура проекта:
- `agents.yaml` -- определение AI-агентов и их ролей
- `tasks.yaml` -- конфигурация задач и рабочих процессов
- `.env` -- хранение API-ключей
- `main.py` -- точка входа
- `crew.py` -- оркестрация экипажа
- `tools/` -- пользовательские инструменты
- `knowledge/` -- база знаний

### Запуск

```bash
crewai install  # установка зависимостей
crewai run       # запуск
```

---

## 3. Быстрый старт

### Конфигурация агентов (agents.yaml)

```yaml
researcher:
  role: "{topic} Senior Data Researcher"
  goal: "Uncover cutting-edge developments in {topic}"
  backstory: "Seasoned researcher with proven expertise"

reporting_analyst:
  role: "{topic} Reporting Analyst"
  goal: "Create detailed reports based on {topic} data analysis"
  backstory: "Meticulous analyst transforming complex data into clear reports"
```

### Конфигурация задач (tasks.yaml)

```yaml
research_task:
  description: >
    Conduct thorough research about {topic}.
    Find 10 bullet points of relevant information.
  expected_output: >
    A list with 10 bullet points of most relevant information about {topic}
  agent: researcher

reporting_task:
  description: >
    Review context and expand each topic into full report section.
  expected_output: >
    A fully fledged report with main topics, each with full section.
    Formatted as markdown without '```'
  agent: reporting_analyst
  markdown: true
  output_file: report.md
```

### Реализация экипажа (crew.py)

```python
@CrewBase
class LatestAiDevelopmentCrew:
    @agent
    def researcher(self) -> Agent:
        return Agent(config=self.agents_config['researcher'], tools=[SerperDevTool()])

    @agent
    def reporting_analyst(self) -> Agent:
        return Agent(config=self.agents_config['reporting_analyst'])

    @task
    def research_task(self) -> Task:
        return Task(config=self.tasks_config['research_task'])

    @task
    def reporting_task(self) -> Task:
        return Task(config=self.tasks_config['reporting_task'])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
```

**Важно**: имена методов в Python должны совпадать с именами в YAML-файлах конфигурации.

---

## 4. Агенты (Agents)

Агент -- автономная единица, способная выполнять задачи, принимать решения на основе роли и цели, использовать инструменты, взаимодействовать с другими агентами, хранить память взаимодействий и делегировать задачи.

### Основные параметры

| Параметр | Тип | Описание |
|----------|-----|----------|
| `role` | str | Определяет функцию и экспертизу агента |
| `goal` | str | Индивидуальная цель, направляющая принятие решений |
| `backstory` | str | Контекст, обогащающий личность агента |
| `llm` | LLM/str | Языковая модель (по умолчанию GPT-4) |
| `function_calling_llm` | LLM/str | Отдельная модель для вызова инструментов |
| `max_iter` | int | Максимум итераций (по умолчанию 20) |
| `max_execution_time` | int | Таймаут в секундах |
| `max_rpm` | int | Ограничение запросов в минуту |
| `max_retry_limit` | int | Повторные попытки при ошибке (по умолчанию 2) |
| `reasoning` | bool | Включить планирование перед выполнением (по умолчанию False) |
| `multimodal` | bool | Поддержка текста и визуального контента |
| `allow_code_execution` | bool | Разрешить выполнение кода |
| `code_execution_mode` | str | "safe" (Docker) или "unsafe" |
| `inject_date` | bool | Автоматическая инъекция текущей даты |
| `allow_delegation` | bool | Делегирование задач между агентами |
| `memory` | bool | Хранение истории взаимодействий |
| `use_system_prompt` | bool | Поддержка системных сообщений (по умолчанию True) |
| `respect_context_window` | bool | Автоматическое суммирование при превышении (по умолчанию True) |

### Создание через YAML (рекомендуется)

```yaml
researcher:
  role: "{topic} Senior Data Researcher"
  goal: "Uncover cutting-edge developments"
  backstory: "Seasoned researcher with proven expertise"
```

### Создание в коде

```python
from crewai import Agent

agent = Agent(
    role="Senior Data Researcher",
    goal="Uncover cutting-edge developments",
    backstory="Seasoned researcher with proven expertise",
    tools=[SerperDevTool()],
    memory=True,
    reasoning=True,
    max_iter=20,
)
```

### Прямое взаимодействие с агентом

```python
result = agent.kickoff("user query")
# result.raw, result.pydantic, result.agent_role, result.usage_metrics
```

Поддерживает структурированный вывод через Pydantic-модели и асинхронное выполнение через `kickoff_async()`.

### Управление контекстным окном

- `respect_context_window=True` (по умолчанию) -- автоматическое суммирование при приближении к лимиту токенов
- `respect_context_window=False` -- остановка при переполнении контекста

---

## 5. Задачи (Tasks)

Задача -- конкретное задание, выполняемое агентом. Содержит описание, назначенного агента, доступные инструменты и уровень сложности.

### Модели выполнения

1. **Sequential** -- задачи выполняются в определённом порядке
2. **Hierarchical** -- назначение задач на основе ролей и экспертизы агентов

### Полная таблица атрибутов

| Параметр | Тип | Описание |
|----------|-----|----------|
| `description` | str | Описание требований задачи |
| `expected_output` | str | Критерии завершения |
| `name` | Optional[str] | Идентификатор задачи |
| `agent` | Optional[BaseAgent] | Ответственный исполнитель |
| `tools` | List[BaseTool] | Доступные инструменты |
| `context` | Optional[List[Task]] | Зависимые задачи (их вывод) |
| `async_execution` | Optional[bool] | Асинхронный режим |
| `human_input` | Optional[bool] | Требование проверки человеком |
| `markdown` | Optional[bool] | Форматирование в markdown |
| `config` | Optional[Dict] | Пользовательские параметры |
| `output_file` | Optional[str] | Путь сохранения |
| `create_directory` | Optional[bool] | Автосоздание директорий |
| `output_json` | Optional[Type[BaseModel]] | JSON-модель вывода |
| `output_pydantic` | Optional[Type[BaseModel]] | Pydantic-модель вывода |
| `callback` | Optional[Any] | Функция после завершения |
| `guardrail` | Optional[Callable] | Функция валидации вывода |
| `guardrails` | Optional[List[Callable]] | Множество валидаторов |
| `guardrail_max_retries` | Optional[int] | Лимит повторов (по умолчанию 3) |

### Создание через YAML

```yaml
research_task:
  description: >
    Conduct thorough research about {topic}
  expected_output: >
    A list with 10 bullet points
  agent: researcher

reporting_task:
  description: >
    Review context and expand each topic into full report section.
  expected_output: >
    A fully fledged report formatted as markdown
  agent: reporting_analyst
  markdown: true
  output_file: report.md
```

### Создание в коде

```python
research_task = Task(
    description="Conduct thorough research about AI Agents...",
    expected_output="A list with 10 bullet points...",
    agent=researcher
)
```

### Структура вывода (TaskOutput)

| Атрибут | Тип | Описание |
|---------|-----|----------|
| `description` | str | Описание задачи |
| `summary` | Optional[str] | Автогенерация из первых 10 слов |
| `raw` | str | Формат по умолчанию |
| `pydantic` | Optional[BaseModel] | Структурированная модель |
| `json_dict` | Optional[Dict] | JSON-представление |
| `agent` | str | Имя выполнившего агента |
| `output_format` | OutputFormat | Тип формата (RAW/JSON/Pydantic) |
| `messages` | list[LLMMessage] | Сообщения последнего выполнения |

Приоритет вывода: Pydantic > JSON > raw.

### Зависимости задач (context)

```python
research_task = Task(description="Research latest AI developments", ...)
analysis_task = Task(
    description="Analyze research findings",
    agent=analyst,
    context=[research_task]  # получает вывод research_task
)
```

### Система защитных ограничений (Guardrails)

**Функциональные:**
```python
def validate_blog_content(result: TaskOutput) -> Tuple[bool, Any]:
    if len(result.raw.split()) > 200:
        return (False, "Blog content exceeds 200 words")
    return (True, result.raw.strip())

blog_task = Task(..., guardrail=validate_blog_content)
```

**На основе LLM:**
```python
blog_task = Task(..., guardrail="Post must be under 200 words without technical jargon")
```

**Множественные:**
```python
blog_task = Task(
    ...,
    guardrails=[validate_word_count, validate_no_profanity],
    guardrail_max_retries=3
)
```

### Структурированный вывод

```python
from pydantic import BaseModel

class Blog(BaseModel):
    title: str
    content: str

task = Task(
    description="Create blog title and content",
    expected_output="Compelling blog title and content",
    agent=blog_agent,
    output_pydantic=Blog,
)
```

### Асинхронное выполнение задач

```python
list_ideas = Task(description="List 5 ideas", agent=researcher, async_execution=True)
list_history = Task(description="Research history", agent=researcher, async_execution=True)
write_article = Task(description="Write article", agent=writer, context=[list_ideas, list_history])
```

### Callback-механизмы

```python
def callback_function(output: TaskOutput):
    print(f"Task completed! Output: {output.raw}")

task = Task(..., callback=callback_function)
```

---

## 6. Экипажи (Crews)

Экипаж -- совместная группа агентов, работающих вместе над задачами.

### Ключевые атрибуты

| Параметр | Описание |
|----------|----------|
| `tasks` | Список задач |
| `agents` | Список агентов |
| `process` | Тип процесса (sequential/hierarchical) |
| `verbose` | Подробное логирование |
| `max_rpm` | Макс. запросов в минуту (переопределяет агентов) |
| `manager_llm` | LLM для менеджера (hierarchical) |
| `manager_agent` | Пользовательский агент-менеджер |
| `function_calling_llm` | LLM для вызова функций |
| `planning_llm` | LLM для планирования |
| `memory` | Включение памяти |
| `cache` | Кэширование (по умолчанию True) |
| `output_log_file` | Файл логов (JSON/TXT) |
| `stream` | Потоковый вывод в реальном времени |
| `planning` | Включить планирование |

### Создание через декораторы (рекомендуется)

```python
@CrewBase
class MyProjectCrew:
    @agent
    def researcher(self) -> Agent: ...

    @task
    def research_task(self) -> Task: ...

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)

    @before_kickoff
    def prepare(self, inputs): ...

    @after_kickoff
    def log_results(self, output): ...
```

### Структура вывода (CrewOutput)

- `raw` -- строковый формат
- `pydantic` -- Pydantic-модель
- `json_dict` -- JSON-словарь
- `tasks_output` -- список результатов задач
- `token_usage` -- метрики использования токенов

### Методы запуска

**Синхронные:**
- `kickoff()` -- стандартное выполнение
- `kickoff_for_each()` -- выполнение для каждого входа

**Асинхронные:**
- `akickoff()` / `akickoff_for_each()` -- нативный async (рекомендуется)
- `kickoff_async()` / `kickoff_for_each_async()` -- потоковый async

### Воспроизведение (Replay)

```bash
crewai replay -t <task_id>
```

---

## 7. Инструменты (Tools)

Инструменты -- функциональные возможности, которые агенты используют для выполнения задач.

### Способы создания

**1. Наследование BaseTool:**
```python
from crewai.tools import BaseTool

class MyTool(BaseTool):
    name: str = "My Tool"
    description: str = "Description of what tool does"

    def _run(self, argument: str) -> str:
        return "Tool result"
```

**2. Декоратор @tool:**
```python
from crewai.tools import tool

@tool("Tool Name")
def my_tool(argument: str) -> str:
    """Description of what tool does."""
    return "Tool result"
```

### Встроенные инструменты (30+)

- Web: SerperDevTool, WebsiteSearchTool, FirecrawlScrapeWebsiteTool
- Файлы: PDFSearchTool, CSVSearchTool, DOCXSearchTool
- Код: CodeInterpreterTool, GithubSearchTool
- Медиа: DALL-E Tool, YoutubeVideoSearchTool
- БД: PGSearchTool

---

## 8. Процессы (Processes)

1. **Sequential** -- задачи в заданном порядке, вывод каждой информирует следующую
2. **Hierarchical** -- менеджер делегирует на основе сильных сторон агентов
3. **Consensual** -- запланирован, не реализован

```python
crew = Crew(agents=my_agents, tasks=my_tasks, process=Process.sequential)
```

---

## 9. Языковые модели (LLMs)

### Конфигурация

```python
from crewai import LLM

llm = LLM(model="provider/model-id", temperature=0.7, max_tokens=4000, timeout=120)
```

### Провайдеры

| Провайдер | Переменная окружения | Модели |
|-----------|---------------------|--------|
| OpenAI | `OPENAI_API_KEY` | GPT-4o (128K), o1 (200K) |
| Anthropic | `ANTHROPIC_API_KEY` | Claude Sonnet 4 (200K) |
| Google Gemini | `GOOGLE_API_KEY` | Gemini 2.5-flash (1M) |
| Azure | `AZURE_API_KEY` | Azure OpenAI |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` | Claude, Llama, Mistral |
| Ollama | base_url localhost:11434 | Локальные модели |
| Groq | `GROQ_API_KEY` | Groq models |
| Mistral | `MISTRAL_API_KEY` | Mistral models |
| HuggingFace | `HF_TOKEN` | HF models |

### Расширенные возможности

**Extended Thinking (Anthropic):**
```python
llm = LLM(model="anthropic/claude-sonnet-4", thinking={"type": "enabled", "budget_tokens": 5000})
```

**Structured calls:**
```python
llm = LLM(model="gpt-4o", response_format=MyPydanticModel)
```

**Формат имён:** всегда `provider/model-id` (например `openai/gpt-4`).

---

## 10. Память (Memory)

Единый класс `Memory` с LLM-анализом, иерархическими областями видимости и композитным скорингом.

```
composite = semantic_weight * similarity + recency_weight * decay + importance_weight * importance
```

Хранилище по умолчанию: LanceDB в `./.crewai/memory`.

Провайдеры эмбеддингов: OpenAI, Ollama, Azure, Google, Cohere, VoyageAI, AWS Bedrock, HuggingFace, Jina.

---

## 11. Потоки (Flows)

### Основные декораторы

```python
class MyFlow(Flow[MyState]):
    @start()
    def first_step(self): return "output"

    @listen(first_step)
    def second_step(self, output): return f"Processed: {output}"
```

### Состояние

```python
class MyState(BaseModel):
    counter: int = 0

class MyFlow(Flow[MyState]):
    pass
```

### Маршрутизация

```python
@router(start_method)
def route(self):
    return "success" if self.state.ok else "failed"
```

### Условная логика

- `or_(a, b)` -- триггер при завершении ЛЮБОГО
- `and_(a, b)` -- триггер при завершении ВСЕХ

### Персистентность: `@persist` -- SQLite

### Память: `self.remember()`, `self.recall()`, `self.extract_memories()`

---

## 12. Коллаборация агентов

При `allow_delegation=True` агенты получают:
1. **Delegate Work Tool** -- назначение задач коллегам
2. **Ask Question Tool** -- запрос информации

Паттерны: Sequential, Single Task Collaboration, Hierarchical.

---

## 13. Знания (Knowledge)

Источники: StringKnowledgeSource, TXT, PDF, CSV, Excel, JSON, веб-контент.

Уровни: агент (своя коллекция) и экипаж (общая коллекция).

Хранилище: ChromaDB. Настройка: `CREWAI_STORAGE_DIR`.

---

## 14. Планирование (Planning)

```python
crew = Crew(..., planning=True, planning_llm="gpt-4o")
```

---

## 15. Рассуждения (Reasoning)

```python
agent = Agent(..., reasoning=True, max_reasoning_attempts=3)
```

---

## 16. Событийная система (Event Listeners)

```python
class MyListener(BaseEventListener):
    def setup_listeners(self, bus):
        @bus.on(CrewKickoffStartedEvent)
        def on_start(source, event): ...
```

Категории: Crew, Agent, Task, Tool, Knowledge, LLM, Flow, Memory Events.

---

## 17. Тестирование

```bash
crewai test -n 5 -m gpt-4o
```

---

## 18. Обучение

```bash
crewai train -n 5 -f model.pkl
```

---

## 19. Создание пользовательских инструментов

**BaseTool:**
```python
class MyTool(BaseTool):
    name = "My Tool"
    description = "Description"
    args_schema = MyToolInput
    def _run(self, argument): return "result"
```

**Декоратор:**
```python
@tool("Name")
def my_tool(arg: str) -> str:
    """Description."""
    return "result"
```

**Async:** через `_arun` или `async def` с `@tool`.

---

## 20. Пользовательские LLM

Наследование `BaseLLM`. Обязательно: `__init__` с `super().__init__()` и `call()`.

Опционально: `supports_function_calling()`, `supports_stop_words()`, `get_context_window_size()`.

---

## 21. Подключение LLM-провайдеров

CrewAI использует LiteLLM. По умолчанию `gpt-4o-mini`.

```python
llm = LLM(model="gpt-4", temperature=0.7, max_tokens=4000, base_url="...", api_key="...")
```

Ollama: `LLM(model="ollama/llama3.2", base_url="http://localhost:11434")`

---

## 22. Настройка агентов

| Параметр | По умолчанию |
|----------|-------------|
| `tools` | [] |
| `cache` | True |
| `verbose` | False |
| `allow_delegation` | False |
| `max_rpm` | None |
| `max_iter` | 25 |

---

## 23. Иерархический процесс

```python
crew = Crew(
    agents=[...], tasks=[...],
    manager_llm="gpt-4o",  # или manager_agent=Agent(...)
    process=Process.hierarchical,
)
```

---

## 24. Асинхронное выполнение

- `akickoff()` -- нативный async (рекомендуется)
- `kickoff_async()` -- потоковый async
- `akickoff_for_each()` -- пакетная обработка

---

## 25. Хуки выполнения

**LLM hooks:** `@before_llm_call`, `@after_llm_call`
**Tool hooks:** `@before_tool_call`, `@after_tool_call`

Before-хуки: return False для блокировки, True/None для продолжения.
After-хуки: return str для замены результата, None для сохранения.

---

## 26. Хуки инструментов

Контекст: `tool_name`, `tool_input`, `tool`, `agent`, `task`, `crew`, `tool_result`.

Модифицируйте `tool_input` in-place.

---

## 27. Хуки LLM

Контекст: `executor`, `messages`, `agent`, `task`, `crew`, `llm`, `iterations`, `response`.

Модифицируйте `messages` через `append()`, не переназначайте.

---

## 28. Условные задачи

```python
ConditionalTask(description="...", condition=is_data_missing, agent=fetcher)
```

---

## 29. Ввод человека при выполнении

```python
Task(..., human_input=True)
```

---

## 30. CLI-интерфейс

| Команда | Описание |
|---------|----------|
| `crewai create crew/flow <name>` | Создание |
| `crewai run` | Запуск |
| `crewai train -n 5` | Обучение |
| `crewai test -n 3 -m gpt-4o` | Тестирование |
| `crewai replay -t <id>` | Воспроизведение |
| `crewai reset-memories --all` | Сброс памяти |
| `crewai chat` | Интерактивная сессия |
| `crewai deploy create/push/status` | Развертывание |

---

## 31. Аннотации и декораторы

`@CrewBase`, `@agent`, `@task`, `@crew`, `@llm`, `@tool`, `@callback`, `@output_json`, `@output_pydantic`, `@cache_handler`, `@before_kickoff`, `@after_kickoff`.

---

## 32. Принудительный вывод инструмента

```python
tools=[MyTool(result_as_answer=True)]
```

---

## 33. Kickoff для коллекций

```python
crew.kickoff_for_each(inputs=[{"data": [1,2]}, {"data": [3,4]}])
```

---

## 34. Архитектура для продакшена

Flow-First: State Management + Control + Observability.

Механизмы: Guardrails, Structured Outputs, LLM Hooks, `@persist`, `kickoff_async`.

---

## 35. Последовательный процесс

```python
Crew(agents=[a,b,c], tasks=[t1,t2,t3], process=Process.sequential)
```

---

## 36. Работа с файлами

`ImageFile`, `PDFFile`, `AudioFile`, `VideoFile`, `TextFile`, `File`.

Источники: локальный путь, URL, байты.

Приоритет: Flow < Crew < Task.

---

## 37. Проектирование эффективных агентов

Правило 80/20: 80% усилий на задачи, 20% на агентов.

Фреймворк: Role (конкретная) + Goal (результат) + Backstory (экспертиза).

Ошибки: нечёткие инструкции, "задачи-боги", несоответствие описания и вывода.

---

## 38. Агенты с исполнением кода

```python
Agent(..., allow_code_execution=True)
```

Рекомендуемые модели: Claude 3.5 Sonnet, GPT-4.

---

## 39. Пользовательский агент-менеджер

```python
Crew(..., manager_agent=Agent(role="PM", allow_delegation=True), process=Process.hierarchical)
```

---

## 40. Мультимодальные агенты

```python
Agent(..., multimodal=True)  # автоматически AddImageTool
```

---

## Справочные ссылки

- Документация: https://docs.crewai.com/
- Полная карта: https://docs.crewai.com/llms.txt
- GitHub: https://github.com/crewAIInc/crewAI
- Примеры: https://github.com/crewAIInc/crewAI-examples
- PyPI: https://pypi.org/project/crewai/
- Сообщество: https://community.crewai.com/
