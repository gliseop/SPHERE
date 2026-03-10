# Быстрый старт

Руководство по установке и запуску MAGISTRY.

## Предварительные требования

- **Python 3.12+** — движок симуляции
- **Node.js 18+** и **npm** — сборка веб-интерфейса (опционально)
- **Ключ OpenAI API** — для работы когнитивных агентов (тесты не требуют ключа)

## Установка

### Клонирование и виртуальное окружение

```bash
git clone <repository-url>
cd MAGISTRY
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
```

### Установка зависимостей

```bash
# Движок + тесты
pip install -e ".[lc,dev]"
```

Группы зависимостей:
- `lc` — движок MAGISTRY-LC: LangChain/LangGraph, PyYAML, OpenAI, Rich
- `dev` — pytest, pytest-asyncio, rank-bm25 и веб-зависимости тестового контура (`fastapi`, `aiofiles`, `python-multipart`, `PyJWT`, `bcrypt`)

### Настройка переменных окружения

```bash
cp .env.example .env
```

Отредактируйте `.env`:

| Переменная | Обязательна | Описание |
|---|---|---|
| `OPENAI_API_KEY` | Да (для симуляций) | Ключ OpenAI API (или совместимого провайдера, например OpenRouter) |
| `JWT_SECRET` | Да (для веб) | Секрет для JWT-токенов длиной не менее 32 байт, генерируется: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_EXPIRE_HOURS` | Нет | Время жизни токена, по умолчанию 24 часа |
| `MAGISTRY_DEV` | Нет | `1` для режима разработки (если `JWT_SECRET` не задан, backend создаёт одноразовый секрет на текущий процесс) |

### Установка фронтенда (опционально)

```bash
cd web/frontend
npm install
cd ../..
```

## Запуск CLI-симуляции

### Запуск симуляции по сценарию

```bash
# Минимальный сценарий (YAML)
magistry-lc run --scenario scenarios/lc_minimal.yaml --out results/lc_minimal_run
```

### Переопределение числа тиков

```bash
magistry-lc run --scenario scenarios/lc_minimal.yaml --ticks 50 --out results/long_run
```

### Генерация сценария из описания (LLM)

```bash
magistry-lc compose --description "Кумовство при найме в муниципальном учреждении" --out scenarios/composed.yaml
```

Команда генерирует сценарий и обогащает персоны (биография + интервью), поэтому делает несколько LLM-вызовов (примерно 1 на агента).

Описание можно передать из файла:

```bash
magistry-lc compose --description-file docs/scenario_brief.txt --out scenarios/composed.yaml
```

### Анализ нарушений (оракул)

Пост-фактум анализ журнала событий чанками через LLM:

```bash
magistry-lc oracle --events results/lc_minimal_run/events.jsonl --out results/lc_minimal_run/violations.json
```

Полный список аргументов CLI — в [cli_reference.md](./cli_reference.md).

## Запуск веб-интерфейса

Скрипт `web/start.sh` собирает фронтенд и запускает FastAPI-сервер.

```bash
# Запуск на порту 8765 (по умолчанию)
bash web/start.sh

# Запуск на другом порту
bash web/start.sh --port 3000
```

Сервер доступен по адресу `http://localhost:8765`. Для первого входа создайте пользователя:

```bash
python web/backend/manage_users.py add admin admin_password --role admin
```

Подробности о веб-интерфейсе — в [web_interface.md](./web_interface.md).

## Запуск тестов

```bash
# Все тесты
pytest

# Конкретный модуль
pytest tests/test_magistry_lc_smoke.py

# По паттерну
pytest -k "test_arbiter"

# С подробным выводом
pytest -v
```

Тесты используют `MockLLMProvider` и `MockEmbeddingProvider` и не требуют ключа OpenAI API. Конфигурация: `asyncio_mode = "auto"`, что позволяет писать async-тесты без дополнительных декораторов.

Подробности о тестировании — в [testing.md](./testing.md).

## Следующие шаги

- [Обзор системы](./overview.md) — архитектура и ключевые понятия
- [Движок симуляции](./simulation_engine.md) — агент, арбитр, память, оракул
- [Навигатор по архитектуре](./architecture_guide.md) — карта модулей и путь данных
