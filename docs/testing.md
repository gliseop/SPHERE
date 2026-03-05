# Руководство по тестированию

Описание структуры тестов, используемых паттернов и команд запуска.

## Структура каталога

Все тесты расположены в `tests/` в корне проекта.

```
tests/
├── conftest.py                         # Глобальные фикстуры и настройки
├── __init__.py
│
│  # Движок magistry_lc
├── test_magistry_lc_smoke.py           # Smoke-тесты: конфигурация, движок, агент, арбитр,
│                                       # DAO, composer, oracle, worldgen, память
├── test_magistry_lc_review_fixes.py    # Регрессионные тесты: journal history, persona retry,
│                                       # ID-нормализация, action schema
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
```

Параметр `asyncio_mode = "auto"` означает, что все асинхронные тестовые функции (`async def test_...`) автоматически распознаются и запускаются через `pytest-asyncio` без необходимости явно проставлять `@pytest.mark.asyncio`.

Глобальный `conftest.py` устанавливает переменную окружения `JWT_SECRET` для тестов веб-модуля.

## Команды запуска

```bash
# Все тесты
pytest

# Конкретный файл
pytest tests/test_magistry_lc_smoke.py

# С выводом деталей
pytest -v

# С остановкой на первой ошибке
pytest -x

# Только тесты движка (без веб-тестов)
pytest tests/test_magistry_lc_smoke.py tests/test_magistry_lc_review_fixes.py tests/test_embedding.py

# Быстрая проверка
pytest -x -q
```

Зависимости для тестов устанавливаются отдельно:

```bash
pip install -e ".[dev,lc]"
```

Группа `dev` включает `pytest>=9.0`, `pytest-asyncio>=0.23` и `rank-bm25>=0.2.2`. Группа `lc` включает `langgraph`, `langchain-core` и `pyyaml`.

## Паттерны тестирования

### MockLLMProvider и MockEmbeddingProvider

`MockLLMProvider` (`src/magistry_lc/llm/providers.py`) возвращает фиксированные текстовые и структурированные ответы без обращения к внешним API. Параметр `structured_responses` позволяет задать словарь ответов для различных вызовов.

`MockEmbeddingProvider` (`src/magistry_lc/llm/embeddings.py`) генерирует детерминированные эмбеддинги заданной размерности, что обеспечивает воспроизводимость тестов семантического поиска.

```python
from magistry_lc.llm import MockLLMProvider, MockEmbeddingProvider

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

## Таблица покрытия

| Модуль | Тестовый файл |
|---|---|
| `magistry_lc` (конфигурация, движок, агент, арбитр, DAO, composer, oracle, worldgen, память) | `test_magistry_lc_smoke.py` |
| `magistry_lc` (journal history, persona retry, ID-нормализация, action schema) | `test_magistry_lc_review_fixes.py` |
| `magistry_lc.llm.embeddings` | `test_embedding.py` |
| `web/backend/graph_state.py` | `test_graph_state.py` |
| `web/backend/auth.py` | `test_web_auth.py` |
| `web/backend/database.py` | `test_web_database.py` |
| `web/backend/main.py` | `test_web_main_auth.py` |
| `web/backend/runner.py` | `test_web_runner.py` |

## Добавление новых тестов

При создании нового модуля в `src/magistry_lc/` следует создать соответствующий тестовый файл в `tests/`. Тесты должны быть самодостаточными и не зависеть от внешних сервисов (OpenAI API, реальных баз данных). Для этого используются `MockLLMProvider`, `MockEmbeddingProvider` и фикстуры с временными директориями.

Асинхронные тесты объявляются как `async def test_...` — `pytest-asyncio` обработает их автоматически благодаря `asyncio_mode = "auto"`.
