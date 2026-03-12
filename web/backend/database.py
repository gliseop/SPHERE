"""SQLite-хранилище учётных записей пользователей MAGISTRY."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DB_PATH = Path(__file__).parent / "users.db"
DB_PATH: Path | None = None
_DOTENV_LOADED = False


@dataclass
class User:
    """Учётная запись пользователя."""

    id: int
    username: str
    password_hash: str
    role: str  # 'admin' | 'viewer'
    created_at: str


def _connect() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_dotenv_if_available() -> None:
    """Подгрузить `.env`, если библиотека доступна."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        _DOTENV_LOADED = True
        return

    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()
    _DOTENV_LOADED = True


def _resolve_db_path() -> Path:
    """Определить путь к users.db с учётом `.env` и test override."""
    if DB_PATH is not None:
        return Path(DB_PATH)
    _load_dotenv_if_available()
    raw = (os.environ.get("MAGISTRY_USERS_DB") or "").strip()
    return Path(raw) if raw else _DEFAULT_DB_PATH


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
