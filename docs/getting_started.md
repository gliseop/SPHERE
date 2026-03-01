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
# Основные зависимости + тесты + поиск
pip install -e ".[dev,search]"
```

Группы зависимостей:
- `dev` — pytest, pytest-asyncio, rank-bm25 (для тестов)
- `search` — rank-bm25, sentence-transformers (для локальных эмбеддингов)
- `stats` — scipy (для статистического анализа пакетных прогонов)

### Настройка переменных окружения

```bash
cp .env.example .env
```

Отредактируйте `.env`:

| Переменная | Обязательна | Описание |
|---|---|---|
| `OPENAI_API_KEY` | Да (для симуляций) | Ключ OpenAI API |
| `EMBEDDING_PROVIDER` | Нет | `openai` (по умолчанию) или `local` (sentence-transformers) |
| `JWT_SECRET` | Да (для веб) | Секрет для JWT-токенов, генерируется: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_EXPIRE_HOURS` | Нет | Время жизни токена, по умолчанию 24 часа |
| `MAGISTRY_DEV` | Нет | `1` для режима разработки (позволяет запуск без JWT_SECRET) |

### Установка фронтенда (опционально)

```bash
cd web/frontend
npm install
cd ../..
```

## Запуск CLI-симуляции

### Список сценариев

```bash
magistry-sim --list-scenarios
```

### Синхронный режим (раундовый)

Агенты ходят по очереди в случайном порядке, один раунд — один ход каждого агента.

```bash
# Базовый сценарий S0 (чистая сделка, 3 агента, без коррупции)
magistry-sim --scenario S0 --runner cognitive

# С режимом управления G2 (аудитор + репутация)
magistry-sim --scenario S1 --governance G2

# Сохранение журнала событий и метрик
magistry-sim --scenario S0 --jsonl results/run.jsonl --summary-json results/metrics.json
```

### Асинхронный режим (непрерывное время)

Каждое действие имеет длительность, агенты пробуждаются по расписанию.

```bash
magistry-sim --scenario S1 --mode async --runner cognitive

# С параллельной обработкой агентов
magistry-sim --scenario S1 --mode async --parallel-agents --parallel-workers 4
```

### Пакетный запуск

Множество прогонов для статистического анализа.

```bash
# 10 прогонов для каждого режима G0, G2, G3
magistry-sim --scenario S0 --batch --batch-runs 10 --batch-modes G0,G2,G3 --output-dir batch_results
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
pytest tests/test_environment.py

# По паттерну
pytest -k "test_cognitive"

# С подробным выводом
pytest -v
```

Тесты используют `MockAgentRunner` и `MockLLMProvider` и не требуют ключа OpenAI API. Конфигурация: `asyncio_mode = "auto"`, что позволяет писать async-тесты без дополнительных декораторов.

Подробности о тестировании — в [testing.md](./testing.md).

## Следующие шаги

- [Обзор системы](./overview.md) — архитектура и ключевые понятия
- [Движок симуляции](./simulation_engine.md) — когнитивный агент, среды, личность
- [Архитектура](../ARCHITECTURE.md) — полное описание системы
