# Справочник командной строки

## sphere-lc

Точка входа для движка симуляции. Три подкоманды: `run`, `compose`, `oracle`.

```bash
sphere-lc <подкоманда> [аргументы]
```

Для отладки и для web-launcher эквивалентно работает модульный вызов:

```bash
python -m sphere_lc.cli <подкоманда> [аргументы]
```

### run — запуск симуляции

```bash
sphere-lc run --scenario <путь> [--out <директория>] [--ticks <число>] [--enrich-personas] [--persona-enrich-mode full|core]
```

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--scenario` | путь | (обязательный) | Путь к сценарию (.json; YAML также читается как compatibility input) |
| `--out` | путь | `results/<timestamp>` | Выходная директория (events.jsonl, trace.jsonl) |
| `--ticks` | целое | из сценария | Переопределить число тиков |
| `--enrich-personas` | флаг | `false` | Включить runtime-обогащение персон перед первым тиком |
| `--persona-enrich-mode` | `full`/`core` | из сценария | Переопределить режим обогащения персон |

### compose — генерация сценария из описания

```bash
sphere-lc compose --description <текст> --out <путь> [опции]
```

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--description` | строка | `""` | Текстовое описание сценария |
| `--description-file` | путь | — | Путь к файлу с описанием (альтернатива `--description`) |
| `--out` | путь | (обязательный) | Куда сохранить сценарий (.json; YAML допустим только как compatibility output) |
| `--ticks` | целое | `25` | Число тиков |
| `--language` | строка | `ru` | Язык генерации |
| `--model` | строка | из LLMConfig | Модель LLM |
| `--base-url` | строка | — | Базовый URL API; если не указан, берётся из `OPENAI_BASE_URL` |
| `--provider-order` | строка | — | Опциональный приоритет провайдеров через запятую для OpenRouter (например, `Groq,OpenAI`); если не указан, берётся из `OPENROUTER_PROVIDER_ORDER` |
| `--temperature` | дробное | из LLMConfig | Температура генерации |
| `--trace` | путь | `results/compose_trace.jsonl` | Путь к файлу трассировки |

### oracle — анализ нарушений

```bash
sphere-lc oracle --events <путь> --out <путь> [опции]
```

| Аргумент | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--events` | путь | (обязательный) | Путь к events.jsonl |
| `--out` | путь | (обязательный) | Выходной JSON с нарушениями |
| `--window` | целое | `5` | Размер окна в тиках |
| `--model` | строка | из LLMConfig | Модель LLM |
| `--base-url` | строка | — | Базовый URL API; если не указан, берётся из `OPENAI_BASE_URL` |
| `--provider-order` | строка | — | Опциональный приоритет провайдеров через запятую для OpenRouter; если не указан, берётся из `OPENROUTER_PROVIDER_ORDER` |
| `--temperature` | дробное | из LLMConfig | Температура генерации |
| `--trace` | путь | `<out>.trace.jsonl` | Путь к файлу трассировки |

### Примеры

```bash
# Минимальный прогон
sphere-lc run --scenario scenarios/lc_minimal.json

# Прогон с указанием выходной директории и числа тиков
sphere-lc run --scenario scenarios/lc_minimal.json --out results/run_50 --ticks 50

# Реалистичный сценарий с enrichment/social graph/worldgen
sphere-lc run --scenario scenarios/procurement_tender.json --out results/procurement_tender_run

# Генерация сценария через LLM
sphere-lc compose --description "Тендер на ремонт дорог, 4 агента, конфликт интересов" \
    --out scenarios/tender.json

# Генерация из файла с описанием
sphere-lc compose --description-file docs/brief.txt \
    --out scenarios/brief.json --ticks 30

# Анализ нарушений с указанием модели
sphere-lc oracle --events results/run_50/events.jsonl \
    --out results/run_50/violations.json --model nvidia/nemotron-3-super-120b-a12b
```

## manage_users.py

Управление учётными записями веб-интерфейса.
CLI подхватывает `.env` автоматически, поэтому `SPHERE_USERS_DB` и другие
переменные можно хранить рядом с проектом.

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
| `JWT_SECRET` | Секрет для JWT-токенов длиной не менее 32 байт (обязателен для веб-интерфейса в продакшен-режиме) |

### LLM и эмбеддинги

| Переменная | По умолчанию | Описание |
|---|---|---|
| `OPENAI_BASE_URL` | — | OpenAI-compatible base URL. Используется для LLM и, если не задано иное, для эмбеддингов |
| `EMBEDDING_PROVIDER` | — | Не используется (эмбеддинги генерируются через OpenAI-совместимый API) |

### Веб-сервер

| Переменная | По умолчанию | Описание |
|---|---|---|
| `JWT_EXPIRE_HOURS` | `24` | Время жизни JWT-токена в часах |
| `SPHERE_USERS_DB` | `web/backend/users.db` | Путь к SQLite-базе пользователей для backend и `manage_users.py` |
| `ALLOWED_ORIGIN` | — | Разрешённый домен для CORS |
| `SPHERE_DEV` | `0` | Режим разработки: `1` позволяет запуск без явного `JWT_SECRET`, при этом backend генерирует одноразовый секрет на процесс |
| `SPHERE_MAX_RUNNING` | `5` | Максимум одновременно запущенных симуляций |
| `SPHERE_MAX_BODY_BYTES` | `2097152` (2 МБ) | Максимальный размер тела HTTP-запроса |

### WebSocket

| Переменная | По умолчанию | Описание |
|---|---|---|
| `SPHERE_WS_MAX_STR_CHARS` | `2500` | Максимальная длина WS-сообщения (символы) |
| `SPHERE_WS_PING_INTERVAL_S` | `15` | Интервал ping-сообщений (секунды) |
| `SPHERE_LIVE_HISTORY_EVENTS` | `60` | Буфер событий при подключении к текущей симуляции |
| `SPHERE_LIVE_GRAPH_THROTTLE_S` | `0.25` | Минимальный интервал между обновлениями графа (секунды) |
| `SPHERE_WS_EVENT_BATCH_SIZE` | `50` | Размер пакета WS-событий |
| `SPHERE_WS_EVENT_BATCH_INTERVAL_S` | `0.15` | Интервал отправки пакетов (секунды) |
| `SPHERE_WS_DROP_EVENT_TYPES` | `idle` | Типы событий, фильтруемые при отправке (через запятую) |
