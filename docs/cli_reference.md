# Справочник командной строки

## magistry-lc

Точка входа для движка симуляции. Три подкоманды: `run`, `compose`, `oracle`.

```bash
magistry-lc <подкоманда> [аргументы]
```

### run — запуск симуляции

```bash
magistry-lc run --scenario <путь> [--out <директория>] [--ticks <число>] [--enrich-personas] [--persona-enrich-mode full|core]
```

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--scenario` | путь | (обязательный) | Путь к сценарию (.yaml или .json) |
| `--out` | путь | `lc_results/<timestamp>` | Выходная директория (events.jsonl, trace.jsonl) |
| `--ticks` | целое | из сценария | Переопределить число тиков |
| `--enrich-personas` | флаг | `false` | Включить runtime-обогащение персон перед первым тиком |
| `--persona-enrich-mode` | `full`/`core` | из сценария | Переопределить режим обогащения персон |

### compose — генерация сценария из описания

```bash
magistry-lc compose --description <текст> --out <путь> [опции]
```

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--description` | строка | `""` | Текстовое описание сценария |
| `--description-file` | путь | — | Путь к файлу с описанием (альтернатива `--description`) |
| `--out` | путь | (обязательный) | Куда сохранить сценарий (.yaml/.json) |
| `--ticks` | целое | `25` | Число тиков |
| `--seed` | целое | `42` | Зерно генератора |
| `--language` | строка | `ru` | Язык генерации |
| `--model` | строка | из LLMConfig | Модель LLM |
| `--base-url` | строка | — | Базовый URL API |
| `--provider-order` | строка | — | Приоритет провайдеров через запятую (например, `Groq,OpenAI`) |
| `--temperature` | дробное | из LLMConfig | Температура генерации |
| `--trace` | путь | `lc_results/compose_trace.jsonl` | Путь к файлу трассировки |

### oracle — анализ нарушений

```bash
magistry-lc oracle --events <путь> --out <путь> [опции]
```

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--events` | путь | (обязательный) | Путь к events.jsonl |
| `--out` | путь | (обязательный) | Выходной JSON с нарушениями |
| `--window` | целое | `5` | Размер окна в тиках |
| `--model` | строка | из LLMConfig | Модель LLM |
| `--base-url` | строка | — | Базовый URL API |
| `--provider-order` | строка | — | Приоритет провайдеров через запятую |
| `--temperature` | дробное | из LLMConfig | Температура генерации |
| `--trace` | путь | `<out>.trace.jsonl` | Путь к файлу трассировки |

### Примеры

```bash
# Минимальный прогон
magistry-lc run --scenario scenarios/lc_minimal.yaml

# Прогон с указанием выходной директории и числа тиков
magistry-lc run --scenario scenarios/lc_minimal.yaml --out results/run_50 --ticks 50

# Реалистичный сценарий с enrichment/social graph/worldgen
magistry-lc run --scenario scenarios/procurement_tender.yaml --out results/procurement_tender_run

# Генерация сценария через LLM
magistry-lc compose --description "Тендер на ремонт дорог, 4 агента, конфликт интересов" \
    --out scenarios/tender.yaml

# Генерация из файла с описанием
magistry-lc compose --description-file docs/brief.txt \
    --out scenarios/brief.yaml --ticks 30 --seed 123

# Анализ нарушений с указанием модели
magistry-lc oracle --events results/run_50/events.jsonl \
    --out results/run_50/violations.json --model gpt-4o
```

## manage_users.py

Управление учётными записями веб-интерфейса.

```bash
python -m web.backend.manage_users <команда> [аргументы]
```

### Команды

| Команда | Описание |
|---|---|
| `create --username <имя> --role <admin\|viewer>` | Создать пользователя (пароль вводится интерактивно, минимум 8 символов) |
| `list` | Показать всех пользователей |
| `delete --username <имя>` | Удалить пользователя |
| `change-role --username <имя> --role <admin\|viewer>` | Изменить роль пользователя |

### Примеры

```bash
# Создать администратора
python -m web.backend.manage_users create --username admin --role admin

# Создать зрителя
python -m web.backend.manage_users create --username viewer1 --role viewer

# Список пользователей
python -m web.backend.manage_users list

# Повысить до администратора
python -m web.backend.manage_users change-role --username viewer1 --role admin
```

## Переменные окружения

### Обязательные

| Переменная | Описание |
|---|---|
| `OPENAI_API_KEY` | Ключ OpenAI API для LLM-вызовов (обязателен для симуляций) |
| `JWT_SECRET` | Секрет для JWT-токенов (обязателен для веб-интерфейса в продакшен-режиме) |

### LLM и эмбеддинги

| Переменная | По умолчанию | Описание |
|---|---|---|
| `OPENAI_BASE_URL` | — | OpenAI-compatible base URL. Используется для LLM и, если не задано иное, для эмбеддингов |
| `EMBEDDING_PROVIDER` | — | Не используется (эмбеддинги генерируются через OpenAI-совместимый API) |

### Веб-сервер

| Переменная | По умолчанию | Описание |
|---|---|---|
| `JWT_EXPIRE_HOURS` | `24` | Время жизни JWT-токена в часах |
| `ALLOWED_ORIGIN` | — | Разрешённый домен для CORS |
| `MAGISTRY_DEV` | `0` | Режим разработки: `1` позволяет работу без `JWT_SECRET` |
| `MAGISTRY_MAX_RUNNING` | `5` | Максимум одновременно запущенных симуляций |
| `MAGISTRY_MAX_BODY_BYTES` | `2097152` (2 МБ) | Максимальный размер тела HTTP-запроса |

### WebSocket

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MAGISTRY_WS_MAX_STR_CHARS` | `2500` | Максимальная длина WS-сообщения (символы) |
| `MAGISTRY_WS_PING_INTERVAL_S` | `15` | Интервал ping-сообщений (секунды) |
| `MAGISTRY_LIVE_HISTORY_EVENTS` | `60` | Буфер событий при подключении к текущей симуляции |
| `MAGISTRY_LIVE_GRAPH_THROTTLE_S` | `0.25` | Минимальный интервал между обновлениями графа (секунды) |
| `MAGISTRY_WS_EVENT_BATCH_SIZE` | `50` | Размер пакета WS-событий |
| `MAGISTRY_WS_EVENT_BATCH_INTERVAL_S` | `0.15` | Интервал отправки пакетов (секунды) |
| `MAGISTRY_WS_DROP_EVENT_TYPES` | `idle` | Типы событий, фильтруемые при отправке (через запятую) |
