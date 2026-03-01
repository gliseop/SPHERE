# Руководство по тестированию

Описание структуры тестов, используемых паттернов и команд запуска.

## Структура каталога

Все тесты расположены в `tests/` в корне проекта. Именование файлов следует шаблону `test_<модуль>.py`, что обеспечивает прямое соответствие между тестируемым модулем и его тестами.

```
tests/
├── conftest.py                         # Глобальные фикстуры и настройки
├── __init__.py
│
│  # Ядро симуляции
├── test_state.py                       # WorldState, EventLog
├── test_state_ops.py                   # StateOp, применение операций
├── test_environment.py                 # Синхронная среда (Environment)
├── test_environment_v5.py              # Расширенные тесты среды
├── test_async_environment.py           # Асинхронная среда (AsyncEnvironment)
├── test_async_environment_private.py   # Внутренние методы AsyncEnvironment
├── test_models.py                      # Pydantic-модели конфигурации
├── test_scenarios.py                   # Встроенные сценарии S0–S2
├── test_scenarios_v4.py                # Шаблоны сценариев v4
│
│  # Когнитивный агент
├── test_cognitive_runner.py            # CognitiveAgentRunner
├── test_memory.py                      # MemoryStream, MemoryRecord
├── test_reflection.py                  # Рефлексия, фокусные точки
├── test_planning.py                    # Стратегическое и тактическое планирование
├── test_embedding.py                   # Провайдеры эмбеддингов
│
│  # Личность и персона
├── test_personality.py                 # HEXACO, Dark Triad, архетипы
├── test_biography.py                   # Генерация биографий
├── test_persona_generator.py           # PersonaGenerator
├── test_interviews.py                  # Интервью, fragment-based retrieval
│
│  # Инструменты и взаимодействие
├── test_tools.py                       # Восемь инструментов агента
├── test_conversation.py                # ConversationManager, треды
├── test_talk_to_threaded.py            # talk_to_threaded: многорепликовые диалоги
├── test_document_forge.py              # DocumentForge, типы документов
├── test_locations.py                   # Локации и перемещения
│
│  # Управление и арбитраж
├── test_arbiter.py                     # LLM-арбитр
├── test_agents.py                      # MockAgentRunner, профили
├── test_context.py                     # Ситуационные сводки
├── test_context_time.py                # Сводки с временными метками
│
│  # Инфраструктура
├── test_llm.py                         # LLMProvider, MockLLMProvider
├── test_tracing.py                     # LLMTracer, журналирование
├── test_sim_clock.py                   # SimClock, WorkSchedule
├── test_scheduler.py                   # Scheduler (приоритетная очередь)
├── test_config_time.py                 # Временные параметры конфигурации
│
│  # Метрики и анализ
├── test_metrics.py                     # Вычисление метрик
├── test_statistics.py                  # Статистический анализ
├── test_oracle.py                      # Oracle (оценка результатов)
├── test_reputation.py                  # Репутационная система
├── test_resources.py                   # Ресурсы агентов
├── test_graph.py                       # Социальный граф
├── test_graph_state.py                 # Граф для веб-визуализации
├── test_events_compat.py              # Совместимость форматов событий
│
│  # Нарратив и генерация
├── test_narrator.py                    # WorldNarrator
├── test_world_generator.py             # Генерация мира
├── test_event_generator.py             # Генерация событий
├── test_scenario_generator.py          # Генерация сценариев
├── test_validation.py                  # Валидация конфигураций
│
│  # CLI
├── test_cli.py                         # Аргументы командной строки
├── test_batch.py                       # Пакетный запуск
│
│  # Веб-интерфейс
├── test_web_auth.py                    # JWT-аутентификация
├── test_web_database.py                # SQLite-хранилище пользователей
├── test_web_main_auth.py               # Интеграционные тесты эндпоинтов
└── test_web_runner.py                  # Фоновый запуск симуляций
```

## Конфигурация

Конфигурация pytest задана в `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Параметр `asyncio_mode = "auto"` означает, что все асинхронные тестовые функции (`async def test_...`) автоматически распознаются и запускаются через `pytest-asyncio` без необходимости явно проставлять `@pytest.mark.asyncio`.

Глобальный `conftest.py` устанавливает переменную окружения `JWT_SECRET` для тестов веб-модуля, чтобы JWT-аутентификация работала в тестовом режиме без явной настройки секрета.

## Команды запуска

```bash
# Все тесты
pytest

# Конкретный файл
pytest tests/test_memory.py

# Конкретный класс или метод
pytest tests/test_environment.py::TestEnvironment::test_run_s0_g0

# С выводом деталей
pytest -v

# С остановкой на первой ошибке
pytest -x

# Только тесты ядра (без веб-тестов)
pytest tests/ --ignore=tests/test_web_auth.py --ignore=tests/test_web_database.py --ignore=tests/test_web_main_auth.py --ignore=tests/test_web_runner.py
```

Зависимости для тестов устанавливаются отдельно:

```bash
pip install -e ".[dev]"
```

Группа `dev` включает `pytest>=9.0`, `pytest-asyncio>=0.23` и `rank-bm25>=0.2.2`.

## Паттерны тестирования

### MockAgentRunner

`MockAgentRunner` (`src/magistry_sim/agents.py`) — детерминированный исполнитель, который заменяет LLM-вызовы предопределёнными действиями. Используется в тестах, где поведение агента не является предметом проверки, а проверяется логика среды, инструментов или метрик.

```python
from magistry_sim.agents import MockAgentRunner

runner = MockAgentRunner()
env = Environment(scenario=config, runner=runner)
result = env.run()
```

### MockLLMProvider и MockEmbeddingProvider

`MockLLMProvider` (`src/magistry_sim/llm.py`) возвращает фиксированные текстовые и структурированные ответы без обращения к внешним API. Параметр `structured_responses` позволяет задать словарь ответов для различных вызовов.

`MockEmbeddingProvider` генерирует детерминированные эмбеддинги заданной размерности, что обеспечивает воспроизводимость тестов семантического поиска.

```python
from magistry_sim.llm import MockLLMProvider, MockEmbeddingProvider

llm = MockLLMProvider(structured_responses={"perform_action": verdict_data})
embedder = MockEmbeddingProvider(dimensions=16)
runner = CognitiveAgentRunner(llm_provider=llm, embedder=embedder)
```

### Контекстные переменные (contextvars)

Инструменты агентов (`tools/actions.py`, `tools/communication.py`) получают состояние мира через контекстные переменные `current_state`, `current_agent_id`, `current_runner`. В тестах контекст устанавливается вручную:

```python
from magistry_sim.tools import current_state, current_agent_id, current_runner

t1 = current_state.set(state)
t2 = current_agent_id.set("off_1")
t3 = current_runner.set(MockAgentRunner())
# ... тесты ...
current_state.reset(t1)
current_agent_id.reset(t2)
current_runner.reset(t3)
```

### Временные директории и monkeypatch

Тесты веб-модуля используют `tmp_path` и `monkeypatch` для изоляции файловой системы. Это позволяет каждому тесту работать с чистой базой данных и результатами, не затрагивая реальные данные.

```python
@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_users.db")
    db_module.init_db()
```

### FastAPI TestClient

Интеграционные тесты веб-эндпоинтов используют `TestClient` из Starlette для синхронного вызова HTTP-эндпоинтов без запуска сервера:

```python
from fastapi.testclient import TestClient
from web.backend.main import app

client = TestClient(app, raise_server_exceptions=False)
r = client.post("/api/auth/login", data={"username": "admin", "password": "secret"})
assert r.status_code == 200
```

### Idle-раннеры для асинхронной среды

Тесты `AsyncEnvironment` используют минимальные раннеры, которые всегда возвращают пустой список действий. Это изолирует тестирование среды от логики принятия решений.

```python
class _IdleRunner:
    def run_turn(self, agent_id, situation, tools, state):
        return []
    def run_reply(self, agent_id, message, sender_id, context, state):
        return ""
```

## Сквозные тесты

Файл `test_e2e_cognitive.py` содержит сквозные тесты, проверяющие полный цикл симуляции с когнитивным агентом. Тесты охватывают все реализованные сценарии (S0, S1, S2) и режимы управления (G0, G2, G3), проверяя корректное завершение, вычислимость метрик, накопление воспоминаний, создание планов и взаимодействие с governance-агентами (аудитор, присяжные).

Сквозные тесты используют `MockLLMProvider` и `MockEmbeddingProvider`, поэтому не требуют ключа OpenAI API и выполняются быстро.

## Таблица покрытия

| Модуль | Тестовый файл |
|---|---|
| `environment.py` | `test_environment.py`, `test_environment_v5.py` |
| `async_environment.py` | `test_async_environment.py`, `test_async_environment_private.py` |
| `state.py` | `test_state.py` |
| `state_ops.py` | `test_state_ops.py` |
| `config.py` | `test_models.py`, `test_config_time.py` |
| `cases.py` | `test_state.py`, `test_tools.py` |
| `scenarios.py` | `test_scenarios.py` |
| `scenarios_v4.py` | `test_scenarios_v4.py` |
| `cognitive_runner.py` | `test_cognitive_runner.py`, `test_e2e_cognitive.py` |
| `memory.py` | `test_memory.py`, `test_e2e_cognitive.py` |
| `reflection.py` | `test_reflection.py` |
| `planning.py` | `test_planning.py` |
| `personality.py` | `test_personality.py` |
| `biography.py` | `test_biography.py` |
| `persona_generator.py` | `test_persona_generator.py` |
| `interviews.py` | `test_interviews.py` |
| `tools/actions.py` | `test_tools.py` |
| `tools/communication.py` | `test_tools.py`, `test_talk_to_threaded.py` |
| `conversation.py` | `test_conversation.py` |
| `document_forge.py` | `test_document_forge.py` |
| `locations.py` | `test_locations.py` |
| `arbiter.py` | `test_arbiter.py` |
| `agents.py` | `test_agents.py` |
| `context.py` | `test_context.py`, `test_context_time.py` |
| `llm.py` | `test_llm.py` |
| `tracing.py` | `test_tracing.py` |
| `sim_clock.py` | `test_sim_clock.py` |
| `scheduler.py` | `test_scheduler.py` |
| `metrics.py` | `test_metrics.py` |
| `statistics.py` | `test_statistics.py` |
| `oracle.py` | `test_oracle.py` |
| `reputation.py` | `test_reputation.py` |
| `resources.py` | `test_resources.py` |
| `graph.py` | `test_graph.py` |
| `narrator.py` | `test_narrator.py` |
| `world_generator.py` | `test_world_generator.py` |
| `event_generator.py` | `test_event_generator.py` |
| `enums.py` | `test_scenarios.py`, `test_environment.py` |
| `cli.py` | `test_cli.py` |
| `web/backend/auth.py` | `test_web_auth.py` |
| `web/backend/database.py` | `test_web_database.py` |
| `web/backend/main.py` | `test_web_main_auth.py` |
| `web/backend/runner.py` | `test_web_runner.py` |
| `web/backend/graph_state.py` | `test_graph_state.py` |

## Добавление новых тестов

При создании нового модуля в `src/magistry_sim/` следует создать соответствующий `tests/test_<имя_модуля>.py`. Тесты должны быть самодостаточными и не зависеть от внешних сервисов (OpenAI API, реальных баз данных). Для этого используются mock-провайдеры и фикстуры с временными директориями.

Асинхронные тесты (для `AsyncEnvironment` и веб-эндпоинтов) объявляются как `async def test_...` — `pytest-asyncio` обработает их автоматически благодаря `asyncio_mode = "auto"`.
