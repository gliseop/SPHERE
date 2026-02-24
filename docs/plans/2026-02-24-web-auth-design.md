# Аутентификация и авторизация веб-интерфейса MAGISTRY

Дата: 2026-02-24

## Контекст и мотивация

Веб-интерфейс MAGISTRY развёрнут на публичном сервере и доступен из интернета. На момент написания документа авторизации нет: любой, кто знает адрес, может просматривать результаты симуляций, изменять сценарии и запускать новые прогоны. Последнее особенно критично — запуск прогона создаёт субпроцесс и потребляет токены LLM.

Цель — добавить аутентификацию на основе JWT и ролевую модель доступа, не усложняя инфраструктуру и не вводя внешних зависимостей вроде Redis или отдельного сервиса авторизации.

## Принципиальные решения

### JWT как носитель удостоверения

JWT выбран как стандарт для FastAPI + React SPA: токен stateless, не требует серверного хранилища сессий, хорошо работает как в REST-запросах (заголовок `Authorization: Bearer`), так и в WebSocket (query param `?token=...`, поскольку браузерный API не позволяет передавать заголовки при WS-соединении).

### SQLite для учётных записей

Учётные записи хранятся в SQLite — нет избыточности внешней БД при небольшом числе пользователей. Пароли хранятся только как bcrypt-хеши. Файл `web/backend/users.db` добавляется в `.gitignore`.

### Две роли: admin и viewer

На старте достаточно двух ролей. Viewer имеет полный доступ на чтение, но не может изменять или запускать что-либо. Admin получает все права. Структура таблицы допускает добавление новых ролей без миграции схемы.

## 1. Схема базы данных

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'viewer')),
    created_at TEXT NOT NULL
);
```

Инициализация при первом старте сервера: `web/backend/database.py` создаёт файл и таблицу, если они отсутствуют.

## 2. Аутентификация

Эндпоинт `POST /api/auth/login` принимает `application/x-www-form-urlencoded` (стандарт OAuth2PasswordRequestForm FastAPI). Возвращает:

```json
{"access_token": "<jwt>", "token_type": "bearer"}
```

JWT payload: `{"sub": "<username>", "role": "<role>", "exp": <unix_timestamp>}`. Алгоритм HS256, секрет читается из переменной окружения `JWT_SECRET`. Время жизни — `JWT_EXPIRE_HOURS` (по умолчанию 24).

Конфигурация через `.env` в корне проекта (файл добавляется в `.gitignore`):

```
JWT_SECRET=<случайная строка 32+ символа, генерировать при деплое>
JWT_EXPIRE_HOURS=24
ALLOWED_ORIGIN=https://your-domain.com
```

## 3. Авторизация

В `web/backend/auth.py` определены два FastAPI-зависимости:

- `require_viewer(token)` — проверяет валидность JWT, извлекает пользователя. При невалидном токене возвращает 401.
- `require_admin(user)` — оборачивает `require_viewer`, дополнительно проверяет `role == "admin"`. При несоответствии возвращает 403.

### Матрица доступа

| Эндпоинт | viewer | admin |
|---|:---:|:---:|
| GET /api/runs | ✓ | ✓ |
| GET /api/run/{name} | ✓ | ✓ |
| GET /api/artifacts/{id} | ✓ | ✓ |
| GET /api/scenarios | ✓ | ✓ |
| GET /api/scenarios/{id} | ✓ | ✓ |
| GET /api/runs/active | ✓ | ✓ |
| WS /ws/playback/{name} | ✓ | ✓ |
| WS /ws/live | ✓ | ✓ |
| POST /api/scenarios | ✗ | ✓ |
| PUT /api/scenarios/{id} | ✗ | ✓ |
| DELETE /api/scenarios/{id} | ✗ | ✓ |
| POST /api/scenarios/{id}/run | ✗ | ✓ |
| POST /api/runs/launch | ✗ | ✓ |

### WebSocket

Браузер не поддерживает заголовки при WS-соединении. Токен передаётся как query param:

```
/ws/playback/{name}?token=<jwt>
/ws/live?token=<jwt>
```

Сервер валидирует токен после `await websocket.accept()` и закрывает соединение с кодом 1008 при невалидном токене.

### CORS

`allow_origins=["*"]` заменяется на `[ALLOWED_ORIGIN]` из переменных окружения. Если `ALLOWED_ORIGIN` не задан — разрешается только `http://localhost:5173` (dev-режим Vite).

## 4. Фронтенд

**Страница логина** — `web/frontend/src/pages/LoginPage.tsx`. Форма username/password, POST на `/api/auth/login`. При успехе токен записывается в `localStorage` (`magistry_token`), декодируется payload для извлечения `role`, после чего выполняется редирект на `/`.

**API-клиент** — `web/frontend/src/utils/apiClient.ts`. Тонкая обёртка над `fetch`, автоматически добавляющая заголовок `Authorization: Bearer`. При получении 401 очищает токен из localStorage и перенаправляет на `/login`.

**Контекст авторизации** — хук `web/frontend/src/hooks/useAuth.ts`. Читает токен из localStorage, декодирует payload (без верификации подписи на клиенте — это задача бэкенда). Предоставляет `{username, role, isAuthenticated}`.

**Защита маршрутов** — компонент `ProtectedRoute` в `App.tsx`. При `!isAuthenticated` — редирект на `/login`.

**Условный рендер по ролям** — в `ScenariosView.tsx` кнопки «Создать», «Редактировать», «Удалить», «Запустить» отображаются только при `role === 'admin'`.

**WebSocket-хуки** — `useSimulation.ts` добавляет к WS-URL `?token={token}`.

## 5. CLI управления пользователями

`web/backend/manage_users.py` — запуск через `python -m web.backend.manage_users`:

```
python -m web.backend.manage_users create --username admin --role admin
python -m web.backend.manage_users create --username alice --role viewer
python -m web.backend.manage_users list
python -m web.backend.manage_users delete --username alice
python -m web.backend.manage_users change-role --username alice --role admin
```

Пароль запрашивается через `getpass` — не передаётся аргументом командной строки. При первом запуске, если таблица пуста, предлагает создать пользователя admin.

## 6. Затрагиваемые файлы

### Создание

- `web/backend/database.py` — инициализация SQLite, функции работы с пользователями
- `web/backend/auth.py` — JWT, bcrypt, зависимости FastAPI
- `web/backend/manage_users.py` — CLI управления
- `web/frontend/src/pages/LoginPage.tsx` — страница входа
- `web/frontend/src/hooks/useAuth.ts` — контекст авторизации
- `web/frontend/src/utils/apiClient.ts` — fetch-обёртка с токеном

### Модификация

- `web/backend/main.py` — зависимости на каждый эндпоинт, CORS origin
- `web/frontend/src/App.tsx` — `ProtectedRoute`, маршрут `/login`
- `web/frontend/src/hooks/useSimulation.ts` — токен в WS-URL
- `web/frontend/src/components/ScenariosView.tsx` — скрытие кнопок для viewer

### Новые зависимости

- Python: `python-jose[cryptography]`, `passlib[bcrypt]`, `python-multipart`, `python-dotenv` (уже есть в pyproject.toml)
- npm: зависимостей не добавляется (JWT декодируется вручную, base64 атобf)
