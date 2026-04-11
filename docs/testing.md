# Руководство по тестированию

Описание структуры тестов, используемых паттернов и команд запуска.

## Структура каталога

Все тесты расположены в `tests/` в корне проекта.

```
tests/
├── conftest.py                         # Глобальные фикстуры и настройки
├── __init__.py
│
│  # Движок sphere_lc
├── test_sphere_lc_smoke.py           # Smoke-тесты: конфигурация, движок, агент, арбитр,
│                                       # DAO, composer, oracle, worldgen, память
├── test_sphere_lc_review_fixes.py    # Регрессионные тесты: journal history, persona retry,
│                                       # ID-нормализация, action schema
├── test_persona_behavioral_fidelity.py # Влияние motivation/persona на proposals агента
├── test_governance_progression.py     # Интеграционный smoke G0 vs G1 по audit/truth
├── test_embedding.py                   # Провайдеры эмбеддингов (Mock + интеграция)
│
│  # Веб-интерфейс
├── test_graph_state.py                 # Построение графа для визуализации
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
markers = ["slow: long-running integration tests"]
```

Параметр `asyncio_mode = "auto"` означает, что все асинхронные тестовые функции (`async def test_...`) автоматически распознаются и запускаются через `pytest-asyncio` без необходимости явно проставлять `@pytest.mark.asyncio`.

Маркер `slow` используется для многотиковых интеграционных smoke-тестов, которые прогоняют полноценный `WorldEngine` и проверяют сквозной контракт нескольких подсистем сразу.

Глобальный `conftest.py` устанавливает переменную окружения `JWT_SECRET` для тестов веб-модуля.

## Команды запуска

```bash
# Все тесты
pytest

# Конкретный файл
pytest tests/test_sphere_lc_smoke.py

# С выводом деталей
pytest -v

# С остановкой на первой ошибке
pytest -x

# Только тесты движка (без веб-тестов)
pytest tests/test_sphere_lc_smoke.py tests/test_sphere_lc_review_fixes.py tests/test_embedding.py

# Быстрая проверка
pytest -x -q

# Только slow-интеграции
pytest -m slow
```

Зависимости для тестов устанавливаются отдельно:

```bash
pip install -e ".[dev,lc]"
```

Группа `dev` включает `pytest>=9.0`, `pytest-asyncio>=0.23`, `rank-bm25>=0.2.2` и зависимости веб-слоя, нужные для коллекции тестов (`fastapi`, `aiofiles`, `python-multipart`, `PyJWT`, `bcrypt`). Группа `lc` включает `langgraph`, `langchain-core` и `pyyaml`.

## Паттерны тестирования

### MockLLMProvider и MockEmbeddingProvider

`MockLLMProvider` (`src/sphere_lc/llm/providers.py`) возвращает фиксированные текстовые и структурированные ответы без обращения к внешним API. Параметр `structured_responses` позволяет задать словарь ответов для различных вызовов.

`MockEmbeddingProvider` (`src/sphere_lc/llm/embeddings.py`) генерирует детерминированные эмбеддинги заданной размерности, что обеспечивает воспроизводимость тестов семантического поиска.

```python
from sphere_lc.llm import MockLLMProvider, MockEmbeddingProvider

llm = MockLLMProvider(structured_responses={"perform_action": verdict_data})
embedder = MockEmbeddingProvider(dimensions=16)
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

### Slow-интеграции и baseline-серия

`tests/test_governance_progression.py` — минимальный автоматический smoke, который проверяет базовый разрыв между `G0` и `G1`: при одинаковом сценарии truth-layer фиксирует нарушение в обоих прогонах, но runtime-аудит должен сработать только когда governance-контур включён. В качестве supported-сигнала используется `self_nomination`, чтобы тест не зависел от уже удалённой narrative-capability `audit`.

Полная ручная серия для главы 2 запускается отдельно через `scripts/run_chapter2_baseline.py`. Скрипт создаёт `comparative_report.json`, где:

- `runs` содержит результаты отдельных прогонов;
- `by_mode` содержит усреднённые метрики по каждому governance-режиму;
- `precision` / `recall` / `f1` относятся к эффективности управленческого контура;
- блок `fidelity` нужен для проверки, не куплено ли улучшение за счёт деградации правдоподобия.

## Таблица покрытия

| Модуль | Тестовый файл |
|---|---|
| `sphere_lc` (конфигурация, движок, агент, арбитр, DAO, composer, oracle, worldgen, память) | `test_sphere_lc_smoke.py` |
| `sphere_lc` (journal history, persona retry, ID-нормализация, action schema) | `test_sphere_lc_review_fixes.py` |
| `sphere_lc` (влияние persona/motivation на prompt и proposals) | `test_persona_behavioral_fidelity.py` |
| `sphere_lc` (интеграционный smoke G0 vs G1) | `test_governance_progression.py` |
| `sphere_lc.llm.embeddings` | `test_embedding.py` |
| `web/backend/graph_state.py` | `test_graph_state.py` |
| `web/backend/auth.py` | `test_web_auth.py` |
| `web/backend/database.py` | `test_web_database.py` |
| `web/backend/main.py` | `test_web_main_auth.py` |
| `web/backend/runner.py` | `test_web_runner.py` |

## Добавление новых тестов

При создании нового модуля в `src/sphere_lc/` следует создать соответствующий тестовый файл в `tests/`. Тесты должны быть самодостаточными и не зависеть от внешних сервисов (OpenAI API, реальных баз данных). Для этого используются `MockLLMProvider`, `MockEmbeddingProvider` и фикстуры с временными директориями.

Асинхронные тесты объявляются как `async def test_...` — `pytest-asyncio` обработает их автоматически благодаря `asyncio_mode = "auto"`.
