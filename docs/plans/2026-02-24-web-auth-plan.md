# Аутентификация и авторизация веб-интерфейса: план реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Защитить веб-интерфейс MAGISTRY с помощью JWT + SQLite: роль admin (полный доступ) и viewer (только чтение).

**Architecture:** FastAPI Depends-зависимости (`require_viewer` / `require_admin`) навешиваются на каждый эндпоинт. Токен передаётся через заголовок `Authorization: Bearer` для REST и через query param `?token=` для WebSocket. Frontend хранит токен в localStorage и добавляет его к каждому запросу через apiClient.

**Tech Stack:** python-jose[cryptography], passlib[bcrypt], sqlite3 (stdlib), python-dotenv (уже в pyproject.toml), React/TypeScript без роутера (login-страница рендерится через state).

---

## Задача 1: Модуль database.py — хранилище пользователей

**Файлы:**
- Создать: `web/backend/database.py`
- Создать: `tests/test_web_database.py`

### Шаг 1: Написать падающий тест

Создать файл `tests/test_web_database.py`:

```python
"""Тесты SQLite-хранилища учётных записей."""
import pytest
import web.backend.database as db_module


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_users.db")
    db_module.init_db()


def test_create_and_get_user():
    user = db_module.create_user("alice", "hash123", "admin")
    assert user.username == "alice"
    assert user.role == "admin"
    got = db_module.get_user_by_username("alice")
    assert got is not None
    assert got.id == user.id


def test_get_nonexistent_user():
    assert db_module.get_user_by_username("nobody") is None


def test_duplicate_username_raises():
    db_module.create_user("alice", "hash1", "admin")
    with pytest.raises(Exception):
        db_module.create_user("alice", "hash2", "viewer")


def test_list_users():
    db_module.create_user("alice", "h1", "admin")
    db_module.create_user("bob", "h2", "viewer")
    users = db_module.list_users()
    assert len(users) == 2
    assert {u.username for u in users} == {"alice", "bob"}


def test_delete_user():
    db_module.create_user("alice", "h1", "admin")
    assert db_module.delete_user("alice") is True
    assert db_module.get_user_by_username("alice") is None


def test_update_role():
    db_module.create_user("alice", "h1", "viewer")
    assert db_module.update_role("alice", "admin") is True
    assert db_module.get_user_by_username("alice").role == "admin"
```

### Шаг 2: Убедиться, что тест падает

```bash
cd /home/development/MAGISTRY && .venv/bin/pytest tests/test_web_database.py -v
```

Ожидаемый результат: `ModuleNotFoundError: No module named 'web.backend.database'`

### Шаг 3: Написать реализацию

Создать `web/backend/database.py`:

```python
"""SQLite-хранилище учётных записей пользователей MAGISTRY."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "users.db"


@dataclass
class User:
    """Учётная запись пользователя."""

    id: int
    username: str
    password_hash: str
    role: str  # 'admin' | 'viewer'
    created_at: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Создать таблицу users, если она не существует."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    UNIQUE NOT NULL,
                password_hash TEXT   NOT NULL,
                role         TEXT    NOT NULL CHECK(role IN ('admin', 'viewer')),
                created_at   TEXT    NOT NULL
            )
        """)


def create_user(username: str, password_hash: str, role: str) -> User:
    """Создать нового пользователя.

    Args:
        username: Уникальное имя пользователя.
        password_hash: bcrypt-хеш пароля.
        role: 'admin' или 'viewer'.

    Returns:
        Созданный User с назначенным id.

    Raises:
        sqlite3.IntegrityError: Если username уже занят.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, created_at),
        )
        return User(
            id=cursor.lastrowid,
            username=username,
            password_hash=password_hash,
            role=role,
            created_at=created_at,
        )


def get_user_by_username(username: str) -> User | None:
    """Найти пользователя по имени.

    Args:
        username: Имя пользователя.

    Returns:
        User или None, если не найден.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            role=row["role"],
            created_at=row["created_at"],
        )


def list_users() -> list[User]:
    """Вернуть всех пользователей, отсортированных по id.

    Returns:
        Список User.
    """
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [
            User(
                id=r["id"],
                username=r["username"],
                password_hash=r["password_hash"],
                role=r["role"],
                created_at=r["created_at"],
            )
            for r in rows
        ]


def delete_user(username: str) -> bool:
    """Удалить пользователя.

    Args:
        username: Имя пользователя.

    Returns:
        True если удалён, False если не найден.
    """
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cursor.rowcount > 0


def update_role(username: str, role: str) -> bool:
    """Изменить роль пользователя.

    Args:
        username: Имя пользователя.
        role: Новая роль ('admin' или 'viewer').

    Returns:
        True если обновлено, False если не найден.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET role = ? WHERE username = ?", (role, username)
        )
        return cursor.rowcount > 0
```

### Шаг 4: Убедиться, что тест проходит

```bash
.venv/bin/pytest tests/test_web_database.py -v
```

Ожидаемый результат: 6 passed

### Шаг 5: Коммит

```bash
git add web/backend/database.py tests/test_web_database.py
git commit -m "feat: add user database module (SQLite)"
```

---

## Задача 2: Модуль auth.py — JWT и зависимости FastAPI

**Файлы:**
- Создать: `web/backend/auth.py`
- Создать: `tests/test_web_auth.py`

Сначала установить зависимости:

```bash
cd /home/development/MAGISTRY && .venv/bin/pip install "python-jose[cryptography]>=3.3.0" "passlib[bcrypt]>=1.7.4"
```

Добавить в `web/backend/requirements.txt`:

```
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
```

### Шаг 1: Написать падающий тест

Создать файл `tests/test_web_auth.py`:

```python
"""Тесты JWT-аутентификации и FastAPI-зависимостей."""
from __future__ import annotations

import pytest
from unittest.mock import patch

import web.backend.auth as auth_module
from web.backend.database import User

ADMIN = User(id=1, username="admin", password_hash="placeholder", role="admin", created_at="2026-01-01T00:00:00+00:00")
VIEWER = User(id=2, username="alice", password_hash="placeholder", role="viewer", created_at="2026-01-01T00:00:00+00:00")


def test_hash_and_verify():
    h = auth_module.hash_password("my_password")
    assert auth_module.verify_password("my_password", h)
    assert not auth_module.verify_password("wrong", h)


def test_create_and_decode_token():
    token = auth_module.create_access_token("admin", "admin")
    payload = auth_module.decode_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"


def test_expired_token_raises():
    from jose import jwt, JWTError
    from datetime import datetime, timezone
    expired_payload = {
        "sub": "admin",
        "role": "admin",
        "exp": datetime(2000, 1, 1, tzinfo=timezone.utc),
    }
    token = jwt.encode(expired_payload, auth_module._JWT_SECRET, algorithm="HS256")
    with pytest.raises(JWTError):
        auth_module.decode_token(token)


@pytest.mark.asyncio
async def test_require_viewer_valid_token():
    token = auth_module.create_access_token("admin", "admin")
    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        user = await auth_module.require_viewer(token)
    assert user.username == "admin"


@pytest.mark.asyncio
async def test_require_viewer_invalid_token():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.require_viewer("invalid.token.here")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_admin_with_admin():
    user = await auth_module.require_admin(ADMIN)
    assert user.role == "admin"


@pytest.mark.asyncio
async def test_require_admin_with_viewer():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.require_admin(VIEWER)
    assert exc_info.value.status_code == 403
```

### Шаг 2: Убедиться, что тест падает

```bash
.venv/bin/pytest tests/test_web_auth.py -v
```

Ожидаемый результат: `ModuleNotFoundError: No module named 'web.backend.auth'`

### Шаг 3: Написать реализацию

Создать `web/backend/auth.py`:

```python
"""JWT-аутентификация, хеширование паролей и FastAPI-зависимости."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from .database import User, get_user_by_username

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

_JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-secret-CHANGE-IN-PRODUCTION")
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS: int = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))


def hash_password(password: str) -> str:
    """Вернуть bcrypt-хеш пароля."""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверить пароль против bcrypt-хеша."""
    return _pwd_context.verify(plain, hashed)


def create_access_token(username: str, role: str) -> str:
    """Создать JWT-токен.

    Args:
        username: Имя пользователя (sub).
        role: Роль пользователя.

    Returns:
        Строка JWT-токена.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Декодировать и проверить JWT.

    Args:
        token: Строка JWT.

    Returns:
        Словарь payload.

    Raises:
        jose.JWTError: При невалидном или истёкшем токене.
    """
    return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])


async def require_viewer(token: str = Depends(_oauth2_scheme)) -> User:
    """FastAPI-зависимость: любой аутентифицированный пользователь.

    Args:
        token: Bearer-токен из заголовка Authorization.

    Returns:
        User из базы данных.

    Raises:
        HTTPException 401: При невалидном или отсутствующем токене.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не аутентифицирован",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        username: str = payload.get("sub", "")
        if not username:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user_by_username(username)
    if user is None:
        raise credentials_exception
    return user


async def require_admin(user: User = Depends(require_viewer)) -> User:
    """FastAPI-зависимость: только пользователи с ролью admin.

    Args:
        user: Текущий пользователь от require_viewer.

    Returns:
        User если роль admin.

    Raises:
        HTTPException 403: Если роль не admin.
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав",
        )
    return user


def verify_ws_token(token: str | None) -> User | None:
    """Проверить токен для WebSocket (без FastAPI Depends).

    Args:
        token: JWT-строка или None.

    Returns:
        User или None при невалидном токене.
    """
    if not token:
        return None
    try:
        payload = decode_token(token)
        username = payload.get("sub", "")
        if not username:
            return None
        return get_user_by_username(username)
    except JWTError:
        return None
```

### Шаг 4: Убедиться, что тест проходит

```bash
.venv/bin/pytest tests/test_web_auth.py -v
```

Ожидаемый результат: 7 passed

### Шаг 5: Коммит

```bash
git add web/backend/auth.py tests/test_web_auth.py web/backend/requirements.txt
git commit -m "feat: add JWT auth module with FastAPI dependencies"
```

---

## Задача 3: Обновить main.py — логин, защита эндпоинтов, CORS

**Файлы:**
- Изменить: `web/backend/main.py`
- Создать: `tests/test_web_main_auth.py`

### Шаг 1: Написать падающий тест

Создать `tests/test_web_main_auth.py`:

```python
"""Интеграционные тесты авторизации эндпоинтов."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from web.backend.main import app
from web.backend.database import User
from web.backend import auth as auth_module

client = TestClient(app, raise_server_exceptions=False)

ADMIN = User(id=1, username="admin", password_hash=auth_module.hash_password("secret"), role="admin", created_at="2026-01-01T00:00:00+00:00")
VIEWER = User(id=2, username="alice", password_hash=auth_module.hash_password("pass"), role="viewer", created_at="2026-01-01T00:00:00+00:00")


def admin_token() -> str:
    return auth_module.create_access_token("admin", "admin")


def viewer_token() -> str:
    return auth_module.create_access_token("alice", "viewer")


def test_login_success():
    with patch("web.backend.main.get_user_by_username", return_value=ADMIN):
        r = client.post("/api/auth/login", data={"username": "admin", "password": "secret"})
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert r.json()["token_type"] == "bearer"


def test_login_wrong_password():
    with patch("web.backend.main.get_user_by_username", return_value=ADMIN):
        r = client.post("/api/auth/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user():
    with patch("web.backend.main.get_user_by_username", return_value=None):
        r = client.post("/api/auth/login", data={"username": "ghost", "password": "x"})
    assert r.status_code == 401


def test_get_runs_no_auth_returns_401():
    r = client.get("/api/runs")
    assert r.status_code == 401


def test_get_runs_with_viewer_token():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.main.RESULTS_DIR") as mock_dir:
            mock_dir.glob.return_value = []
            r = client.get(
                "/api/runs",
                headers={"Authorization": f"Bearer {viewer_token()}"},
            )
    assert r.status_code == 200


def test_create_scenario_viewer_gets_403():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        r = client.post(
            "/api/scenarios",
            json={"name": "test"},
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )
    assert r.status_code == 403


def test_create_scenario_admin_gets_201():
    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.main.SCENARIOS_DIR") as mock_dir:
            mock_path = MagicMock()
            mock_dir.__truediv__ = lambda self, x: mock_path
            mock_path.write_text = MagicMock()
            r = client.post(
                "/api/scenarios",
                json={"name": "test scenario"},
                headers={"Authorization": f"Bearer {admin_token()}"},
            )
    assert r.status_code == 201


def test_delete_scenario_viewer_gets_403():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        r = client.delete(
            "/api/scenarios/00000000-0000-0000-0000-000000000001",
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )
    assert r.status_code == 403


def test_launch_run_viewer_gets_403():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        r = client.post(
            "/api/runs/launch",
            json={"scenario": "S1"},
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )
    assert r.status_code == 403
```

### Шаг 2: Убедиться, что тест падает

```bash
.venv/bin/pytest tests/test_web_main_auth.py -v
```

Ожидаемый результат: большинство тестов упадут (нет /api/auth/login, нет защиты эндпоинтов).

### Шаг 3: Обновить main.py

В начале файла после импортов добавить загрузку .env и инициализацию БД:

```python
# после строк "from __future__ import annotations" и существующих import-ов
from dotenv import load_dotenv
load_dotenv()

from .auth import require_viewer, require_admin, verify_password, create_access_token, verify_ws_token
from .database import init_db, get_user_by_username

# ... существующие константы RESULTS_DIR etc. ...

# после создания app = FastAPI(...):
init_db()
```

Заменить CORS middleware:

```python
import os as _os
_ALLOWED_ORIGIN = _os.environ.get("ALLOWED_ORIGIN", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Добавить эндпоинт логина (до существующих GET-эндпоинтов):

```python
from fastapi import Depends
from fastapi.security import OAuth2PasswordRequestForm

@app.post("/api/auth/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    """Аутентификация пользователя.

    Args:
        form_data: username и password из form-encoded тела.

    Returns:
        Словарь с access_token и token_type.

    Raises:
        HTTPException 401: При неверных учётных данных.
    """
    from fastapi import HTTPException
    user = get_user_by_username(form_data.username)
    if user is None or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return {
        "access_token": create_access_token(user.username, user.role),
        "token_type": "bearer",
    }
```

Добавить зависимость `require_viewer` ко всем GET/активным эндпоинтам и `require_admin` к записывающим. Изменить сигнатуры:

```python
# Было:
@app.get("/api/runs")
async def list_runs() -> list[dict]:

# Стало:
@app.get("/api/runs")
async def list_runs(_user: User = Depends(require_viewer)) -> list[dict]:


# Было:
@app.get("/api/run/{name}")
async def get_run(name: str) -> dict:

# Стало:
@app.get("/api/run/{name}")
async def get_run(name: str, _user: User = Depends(require_viewer)) -> dict:


# Аналогично для:
# GET /api/artifacts/{doc_id}     -> Depends(require_viewer)
# GET /api/scenarios              -> Depends(require_viewer)
# GET /api/scenarios/{id}         -> Depends(require_viewer)
# GET /api/runs/active            -> Depends(require_viewer)

# POST /api/scenarios             -> Depends(require_admin)
# PUT  /api/scenarios/{id}        -> Depends(require_admin)
# DELETE /api/scenarios/{id}      -> Depends(require_admin)
# POST /api/scenarios/{id}/run    -> Depends(require_admin)
# POST /api/runs/launch           -> Depends(require_admin)
```

В WebSocket-эндпоинтах добавить валидацию токена после `await websocket.accept()`:

```python
@app.websocket("/ws/playback/{name}")
async def ws_playback(websocket: WebSocket, name: str, token: str | None = None, speed: float = 1.0) -> None:
    await websocket.accept()
    if verify_ws_token(token) is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=1008)
        return
    # ... остаток существующего кода без изменений ...

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket, token: str | None = None) -> None:
    await websocket.accept()
    if verify_ws_token(token) is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=1008)
        return
    # ... остаток существующего кода без изменений ...
```

Добавить тип `User` в импорт для аннотаций (в начале файла, вместе с другими импортами из .database):

```python
from .database import User, init_db, get_user_by_username
```

### Шаг 4: Убедиться, что тест проходит

```bash
.venv/bin/pytest tests/test_web_main_auth.py -v
```

Ожидаемый результат: 10 passed

Также убедиться, что все остальные тесты не сломались:

```bash
.venv/bin/pytest tests/ -v --ignore=tests/test_e2e_cognitive.py -q
```

### Шаг 5: Коммит

```bash
git add web/backend/main.py tests/test_web_main_auth.py
git commit -m "feat: add JWT auth to all backend endpoints and WebSocket"
```

---

## Задача 4: CLI управления пользователями

**Файлы:**
- Создать: `web/backend/manage_users.py`

Тест не нужен — CLI с `getpass` не поддаётся unit-тестированию без excessive mocking. Вместо этого — ручная проверка.

### Шаг 1: Создать `web/backend/manage_users.py`

```python
"""CLI управления учётными записями пользователей MAGISTRY.

Использование:
    python -m web.backend.manage_users create --username admin --role admin
    python -m web.backend.manage_users list
    python -m web.backend.manage_users delete --username alice
    python -m web.backend.manage_users change-role --username alice --role admin
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .auth import hash_password
from .database import (
    create_user,
    delete_user,
    get_user_by_username,
    init_db,
    list_users,
    update_role,
)


def cmd_create(args: argparse.Namespace) -> None:
    """Создать нового пользователя с паролем, введённым интерактивно."""
    init_db()
    if get_user_by_username(args.username) is not None:
        print(f"Ошибка: пользователь '{args.username}' уже существует", file=sys.stderr)
        sys.exit(1)
    password = getpass.getpass(f"Пароль для '{args.username}': ")
    confirm = getpass.getpass("Повторите пароль: ")
    if password != confirm:
        print("Ошибка: пароли не совпадают", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Ошибка: пароль должен быть не короче 8 символов", file=sys.stderr)
        sys.exit(1)
    user = create_user(args.username, hash_password(password), args.role)
    print(f"Создан пользователь '{user.username}' с ролью '{user.role}' (id={user.id})")


def cmd_list(_args: argparse.Namespace) -> None:
    """Вывести всех пользователей."""
    init_db()
    users = list_users()
    if not users:
        print("Нет пользователей. Создайте первого: python -m web.backend.manage_users create --username admin --role admin")
        return
    print(f"{'ID':>4}  {'Username':<20}  {'Role':<10}  Created")
    print("-" * 60)
    for u in users:
        print(f"{u.id:>4}  {u.username:<20}  {u.role:<10}  {u.created_at}")


def cmd_delete(args: argparse.Namespace) -> None:
    """Удалить пользователя."""
    init_db()
    if not delete_user(args.username):
        print(f"Ошибка: пользователь '{args.username}' не найден", file=sys.stderr)
        sys.exit(1)
    print(f"Пользователь '{args.username}' удалён")


def cmd_change_role(args: argparse.Namespace) -> None:
    """Изменить роль пользователя."""
    init_db()
    if not update_role(args.username, args.role):
        print(f"Ошибка: пользователь '{args.username}' не найден", file=sys.stderr)
        sys.exit(1)
    print(f"Роль пользователя '{args.username}' изменена на '{args.role}'")


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Управление пользователями MAGISTRY",
        prog="python -m web.backend.manage_users",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="Создать пользователя")
    p_create.add_argument("--username", required=True, help="Имя пользователя")
    p_create.add_argument("--role", required=True, choices=["admin", "viewer"], help="Роль")
    p_create.set_defaults(func=cmd_create)

    p_list = subparsers.add_parser("list", help="Список всех пользователей")
    p_list.set_defaults(func=cmd_list)

    p_delete = subparsers.add_parser("delete", help="Удалить пользователя")
    p_delete.add_argument("--username", required=True, help="Имя пользователя")
    p_delete.set_defaults(func=cmd_delete)

    p_change = subparsers.add_parser("change-role", help="Изменить роль")
    p_change.add_argument("--username", required=True, help="Имя пользователя")
    p_change.add_argument("--role", required=True, choices=["admin", "viewer"], help="Новая роль")
    p_change.set_defaults(func=cmd_change_role)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

### Шаг 2: Ручная проверка

```bash
cd /home/development/MAGISTRY
python -m web.backend.manage_users --help
python -m web.backend.manage_users list
# Ожидаемый вывод: "Нет пользователей..."
```

### Шаг 3: Коммит

```bash
git add web/backend/manage_users.py
git commit -m "feat: add user management CLI"
```

---

## Задача 5: Конфигурация — .env, .gitignore, start.sh

**Файлы:**
- Создать: `.env.example`
- Изменить: `.gitignore` (или создать если нет)
- Изменить: `web/start.sh`

### Шаг 1: Создать `.env.example`

```bash
cat > /home/development/MAGISTRY/.env.example << 'EOF'
# Скопировать в .env и заполнить перед деплоем.
# Сгенерировать JWT_SECRET: python -c "import secrets; print(secrets.token_hex(32))"

JWT_SECRET=CHANGE_THIS_TO_A_RANDOM_64_CHAR_HEX_STRING
JWT_EXPIRE_HOURS=24
ALLOWED_ORIGIN=https://your-domain.com
EOF
```

### Шаг 2: Обновить .gitignore

Проверить наличие `.gitignore` в корне:

```bash
ls /home/development/MAGISTRY/.gitignore 2>/dev/null || echo "не существует"
```

Добавить строки (если файл есть — дописать, если нет — создать):

```
.env
web/backend/users.db
```

### Шаг 3: Обновить web/start.sh

После строки `set -e` добавить загрузку `.env`:

```bash
# Загрузить переменные окружения из .env (если файл существует)
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ROOT_DIR/.env"
    set +a
    echo "Загружены переменные из .env"
fi
```

### Шаг 4: Проверить

```bash
cd /home/development/MAGISTRY && cat .env.example
```

### Шаг 5: Коммит

```bash
git add .env.example .gitignore web/start.sh
git commit -m "feat: add .env config, update .gitignore and start.sh"
```

---

## Задача 6: Frontend — apiClient.ts и useAuth.ts

**Файлы:**
- Создать: `web/frontend/src/utils/apiClient.ts`
- Создать: `web/frontend/src/hooks/useAuth.ts`

### Шаг 1: Создать `web/frontend/src/utils/apiClient.ts`

```typescript
/**
 * Fetch-обёртка с JWT-аутентификацией.
 * Автоматически добавляет Authorization: Bearer к каждому запросу.
 * При 401 очищает токен и перенаправляет на /login.
 */

const TOKEN_KEY = 'magistry_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = getToken()
  const headers: Record<string, string> = { ...extra }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

async function request(url: string, init: RequestInit): Promise<Response> {
  const res = await fetch(url, init)
  if (res.status === 401) {
    clearToken()
    window.location.href = '/?login=1'
  }
  return res
}

export const apiClient = {
  get: (url: string) =>
    request(url, { headers: authHeaders() }),

  post: (url: string, body?: unknown) =>
    request(url, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    }),

  put: (url: string, body?: unknown) =>
    request(url, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    }),

  delete: (url: string) =>
    request(url, { method: 'DELETE', headers: authHeaders() }),
}
```

### Шаг 2: Создать `web/frontend/src/hooks/useAuth.ts`

```typescript
import { useState, useCallback } from 'react'
import { getToken, setToken, clearToken } from '../utils/apiClient'

export interface AuthUser {
  username: string
  role: 'admin' | 'viewer'
}

function decodeJwtPayload(token: string): AuthUser | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const padded = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(padded)
    const payload = JSON.parse(json) as { sub?: string; role?: string; exp?: number }
    if (!payload.sub || !payload.role) return null
    if (payload.exp && payload.exp * 1000 < Date.now()) return null
    return { username: payload.sub, role: payload.role as 'admin' | 'viewer' }
  } catch {
    return null
  }
}

export interface AuthState {
  user: AuthUser | null
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<string | null>
  logout: () => void
}

export function useAuth(): AuthState {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const token = getToken()
    return token ? decodeJwtPayload(token) : null
  })

  const login = useCallback(
    async (username: string, password: string): Promise<string | null> => {
      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ username, password }).toString(),
        })
        if (!res.ok) {
          const data = (await res.json().catch(() => ({}))) as { detail?: string }
          return data.detail ?? 'Ошибка входа'
        }
        const data = (await res.json()) as { access_token: string }
        setToken(data.access_token)
        setUser(decodeJwtPayload(data.access_token))
        return null  // null = успех
      } catch {
        return 'Ошибка сети'
      }
    },
    []
  )

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
  }, [])

  return { user, isAuthenticated: user !== null, login, logout }
}
```

### Шаг 3: Убедиться, что TypeScript компилируется без ошибок

```bash
cd /home/development/MAGISTRY/web/frontend && npx tsc --noEmit
```

Ожидаемый результат: нет ошибок.

### Шаг 4: Коммит

```bash
git add web/frontend/src/utils/apiClient.ts web/frontend/src/hooks/useAuth.ts
git commit -m "feat: add frontend apiClient and useAuth hook"
```

---

## Задача 7: Frontend — LoginPage.tsx и CSS

**Файлы:**
- Создать: `web/frontend/src/pages/LoginPage.tsx` (создать папку `pages/` если не существует)
- Изменить: `web/frontend/src/styles/hud.css`

### Шаг 1: Создать `web/frontend/src/pages/LoginPage.tsx`

```tsx
import { useState } from 'react'

interface Props {
  onLogin: (username: string, password: string) => Promise<string | null>
}

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    const err = await onLogin(username, password)
    setLoading(false)
    if (err) setError(err)
  }

  return (
    <div className="login-page">
      <div className="login-card hud-panel">
        <div className="corner tl accent" />
        <div className="corner tr" />
        <div className="corner bl" />
        <div className="corner br accent" />
        <h1 className="login-title">MAGISTRY</h1>
        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-field">
            <label>Имя пользователя</label>
            <input
              className="hud-input"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </div>
          <div className="form-field">
            <label>Пароль</label>
            <input
              className="hud-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          {error && (
            <div className="badge danger" style={{ textAlign: 'center', padding: '0.5rem' }}>
              {error}
            </div>
          )}
          <button
            className="btn-clipped primary full-width"
            type="submit"
            disabled={loading || !username || !password}
          >
            {loading ? 'Вход...' : 'Войти'}
          </button>
        </form>
      </div>
    </div>
  )
}
```

### Шаг 2: Добавить стили в конец `web/frontend/src/styles/hud.css`

```css
/* ──────────────────────── Login page ──────────────────────── */

.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--bg);
}

.login-card {
  position: relative;
  padding: 2rem 2.5rem;
  width: 360px;
  background: var(--panel-bg);
  border: 1px solid var(--border);
}

.login-title {
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: 0.25em;
  color: var(--accent);
  margin: 0 0 2rem;
  text-align: center;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
```

### Шаг 3: Убедиться, что TypeScript компилируется

```bash
cd /home/development/MAGISTRY/web/frontend && npx tsc --noEmit
```

### Шаг 4: Коммит

```bash
git add web/frontend/src/pages/LoginPage.tsx web/frontend/src/styles/hud.css
git commit -m "feat: add LoginPage component and login styles"
```

---

## Задача 8: App.tsx — защита маршрутов и логаут

**Файлы:**
- Изменить: `web/frontend/src/App.tsx`

### Шаг 1: Добавить auth gate в App.tsx

В начале файла добавить импорты:

```typescript
import { useAuth } from './hooks/useAuth'
import { LoginPage } from './pages/LoginPage'
```

Внутри компонента App, сразу после `const { state, mode, startPlayback, startLive, disconnect } = useSimulation()`:

```typescript
const auth = useAuth()
```

После объявления всех useState добавить проверку авторизации:

```typescript
if (!auth.isAuthenticated) {
  return <LoginPage onLogin={auth.login} />
}
```

В header добавить блок с именем пользователя и кнопкой выхода. Найти место сразу после кнопки смены темы:

```tsx
{/* После кнопки темы */}
<span
  className="hud-header-stat-label"
  style={{ marginLeft: '0.5rem', fontSize: '0.6rem', opacity: 0.7 }}
>
  {auth.user?.username}
  {' '}
  <span className={`badge small ${auth.user?.role === 'admin' ? 'success' : ''}`}>
    {auth.user?.role}
  </span>
</span>
<button
  className="btn-clipped small danger"
  onClick={auth.logout}
  title="Выйти"
  style={{ marginLeft: '0.25rem' }}
>
  ✕
</button>
```

### Шаг 2: Проверить компиляцию

```bash
cd /home/development/MAGISTRY/web/frontend && npx tsc --noEmit
```

### Шаг 3: Коммит

```bash
git add web/frontend/src/App.tsx
git commit -m "feat: add auth gate and logout button to App"
```

---

## Задача 9: useSimulation.ts — токен в WebSocket URL

**Файлы:**
- Изменить: `web/frontend/src/hooks/useSimulation.ts`

### Шаг 1: Обновить useSimulation.ts

Добавить импорт в начале файла:

```typescript
import { getToken } from '../utils/apiClient'
```

Изменить `startPlayback`:

```typescript
// Было:
const url = `${proto}//${window.location.host}/ws/playback/${run.name}?speed=${speed}`

// Стало:
const token = getToken()
const tokenParam = token ? `&token=${encodeURIComponent(token)}` : ''
const url = `${proto}//${window.location.host}/ws/playback/${run.name}?speed=${speed}${tokenParam}`
```

Изменить `startLive`:

```typescript
// Было:
const url = `${proto}//${window.location.host}/ws/live`

// Стало:
const token = getToken()
const tokenParam = token ? `?token=${encodeURIComponent(token)}` : ''
const url = `${proto}//${window.location.host}/ws/live${tokenParam}`
```

### Шаг 2: Проверить компиляцию

```bash
cd /home/development/MAGISTRY/web/frontend && npx tsc --noEmit
```

### Шаг 3: Коммит

```bash
git add web/frontend/src/hooks/useSimulation.ts
git commit -m "feat: pass JWT token in WebSocket URLs"
```

---

## Задача 10: ScenariosView.tsx и RunSelector.tsx — apiClient + скрытие кнопок

**Файлы:**
- Изменить: `web/frontend/src/components/ScenariosView.tsx`
- Изменить: `web/frontend/src/components/RunSelector.tsx`
- Изменить: `web/frontend/src/App.tsx` (передать role в ScenariosView и RunSelector)

### Шаг 1: Обновить ScenariosView.tsx

Добавить импорт:

```typescript
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'
```

Изменить сигнатуру компонента:

```typescript
// Было:
export function ScenariosView({ onLaunch, onStartLive }: { onLaunch?: () => void; onStartLive?: () => void }) {

// Стало:
export function ScenariosView({ onLaunch, onStartLive, user }: {
  onLaunch?: () => void
  onStartLive?: () => void
  user: AuthUser | null
}) {
```

Заменить все вызовы `fetch`:

```typescript
// Было:
fetch('/api/scenarios').then(r => r.json()).then(setScenarios)

// Стало:
apiClient.get('/api/scenarios').then(r => r.json()).then(setScenarios)

// Было:
const res = await fetch(url, { method, headers: {...}, body: JSON.stringify(editing) })

// Стало:
const res = editing.id
  ? await apiClient.put(url, editing)
  : await apiClient.post(url, editing)

// Было:
await fetch(`/api/scenarios/${id}`, { method: 'DELETE' })

// Стало:
await apiClient.delete(`/api/scenarios/${id}`)

// Было:
const res = await fetch(`/api/scenarios/${id}/run`, { method: 'POST' })

// Стало:
const res = await apiClient.post(`/api/scenarios/${id}/run`)
```

Скрыть admin-кнопки в списке сценариев:

```tsx
{/* В scenarios-header: показывать "Создать" только admin */}
<div className="scenarios-header">
  <span>Сценарии ({scenarios.length})</span>
  {user?.role === 'admin' && (
    <button className="btn-clipped primary" onClick={() => setEditing({ ...EMPTY_SCENARIO })}>
      + Создать сценарий
    </button>
  )}
</div>

{/* В scenario-card-actions: показывать ▶ ✎ ✕ только admin */}
<div className="scenario-card-actions">
  {user?.role === 'admin' && (
    <>
      <button className="btn-clipped success small" onClick={() => s.id && handleRun(s.id)} title="Запустить">▶</button>
      <button className="btn-clipped small" onClick={() => setEditing({ ...s })} title="Редактировать">✎</button>
      <button className="btn-clipped danger small" onClick={() => s.id && handleDelete(s.id)} title="Удалить">✕</button>
    </>
  )}
</div>
```

В редакторе скрыть кнопку "Сохранить и запустить" для viewer:

```tsx
{editing.id && user?.role === 'admin' && (
  <button className="btn-clipped success" ...>▶ Сохранить и запустить</button>
)}
```

### Шаг 2: Обновить RunSelector.tsx

Добавить импорт:

```typescript
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'
```

Изменить сигнатуру:

```typescript
interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: () => void
  speed: number
  onSpeedChange: (s: number) => void
  mode: string
  user: AuthUser | null  // добавить
}
```

Заменить вызовы `fetch`:

```typescript
// useEffect для загрузки runs:
apiClient.get('/api/runs').then(r => r.json()).then(setRuns).catch(console.error)

// handleLaunch:
const res = await apiClient.post('/api/runs/launch', {
  scenario: launchScenario,
  governance: launchGovernance,
  seed: launchSeed ? Number(launchSeed) : 42,
  runner: launchRunner,
})
```

Скрыть launcher для viewer:

```tsx
{/* Launcher показывать только admin */}
{user?.role === 'admin' && (
  <div style={{ padding: '0.5rem 0.875rem', borderTop: '1px solid var(--border)' }}>
    <button ... onClick={() => setShowLauncher(!showLauncher)} ...>
      {showLauncher ? '▲ Скрыть' : '▼ Запустить новый'}
    </button>
    {showLauncher && ( /* форма запуска */ )}
  </div>
)}
```

### Шаг 3: Обновить App.tsx — передать user в ScenariosView и RunSelector

```tsx
// Было:
<RunSelector onPlayback={...} onLive={...} speed={speed} onSpeedChange={setSpeed} mode={mode} />

// Стало:
<RunSelector onPlayback={...} onLive={...} speed={speed} onSpeedChange={setSpeed} mode={mode} user={auth.user} />

// Было:
<ScenariosView onLaunch={() => setView('monitor')} onStartLive={startLive} />

// Стало:
<ScenariosView onLaunch={() => setView('monitor')} onStartLive={startLive} user={auth.user} />
```

### Шаг 4: Проверить компиляцию

```bash
cd /home/development/MAGISTRY/web/frontend && npx tsc --noEmit
```

Ожидаемый результат: нет ошибок.

### Шаг 5: Запустить все Python-тесты

```bash
cd /home/development/MAGISTRY && .venv/bin/pytest tests/ -q --ignore=tests/test_e2e_cognitive.py
```

Ожидаемый результат: все тесты проходят.

### Шаг 6: Коммит

```bash
git add web/frontend/src/components/ScenariosView.tsx \
        web/frontend/src/components/RunSelector.tsx \
        web/frontend/src/App.tsx
git commit -m "feat: integrate apiClient in components, hide admin UI for viewers"
```

---

## Итоговая проверка

### Backend

```bash
cd /home/development/MAGISTRY
.venv/bin/pytest tests/test_web_database.py tests/test_web_auth.py tests/test_web_main_auth.py -v
```

Ожидаемый результат: 23 passed.

### Frontend TypeScript

```bash
cd web/frontend && npx tsc --noEmit && echo "TypeScript OK"
```

### Полная сборка

```bash
cd web/frontend && npm run build
```

Ожидаемый результат: `dist/` собран без ошибок.

### Создать первого пользователя (первый деплой)

```bash
cd /home/development/MAGISTRY
python -m web.backend.manage_users create --username admin --role admin
python -m web.backend.manage_users list
```

### Финальный коммит (если осталось что-то незакоммиченное)

```bash
git add -A
git commit -m "feat: web authentication complete (JWT + SQLite + roles)"
```
