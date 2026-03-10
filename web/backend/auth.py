"""JWT-аутентификация, хеширование паролей и FastAPI-зависимости."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import InvalidTokenError

from .database import User, get_user_by_username

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_MIN_JWT_SECRET_BYTES = 32
_DEV_JWT_SECRET = "magistry-dev-secret-0123456789abcdef0123456789abcdef"

_DEV_MODE = os.environ.get("MAGISTRY_DEV", "").strip() == "1"
_JWT_SECRET_ENV = (os.environ.get("JWT_SECRET") or "").strip()
if _JWT_SECRET_ENV:
    _JWT_SECRET: str | None = _JWT_SECRET_ENV
elif _DEV_MODE:
    _JWT_SECRET = _DEV_JWT_SECRET
else:
    _JWT_SECRET = None
_JWT_ALGORITHM = "HS256"
try:
    _JWT_EXPIRE_HOURS = max(1, int((os.environ.get("JWT_EXPIRE_HOURS") or "24").strip()))
except ValueError:
    _JWT_EXPIRE_HOURS = 24

_JWT_SECRET_ERR = (
    "JWT_SECRET is required. Set JWT_SECRET or export MAGISTRY_DEV=1 for dev mode."
)
_JWT_SECRET_LEN_ERR = (
    "JWT_SECRET must be at least 32 bytes for HS256. "
    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
)


def validate_jwt_secret() -> None:
    """Проверить, что JWT_SECRET доступен.

    Используется на старте FastAPI, чтобы не завершать процесс на import-time.
    """
    _require_jwt_secret()


def _require_jwt_secret() -> str:
    if not _JWT_SECRET:
        raise RuntimeError(_JWT_SECRET_ERR)
    if len(_JWT_SECRET.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
        raise RuntimeError(_JWT_SECRET_LEN_ERR)
    return _JWT_SECRET


def hash_password(password: str) -> str:
    """Вернуть bcrypt-хеш пароля.

    Args:
        password: Открытый пароль.

    Returns:
        bcrypt-хеш в виде строки.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Проверить пароль против bcrypt-хеша.

    Args:
        plain: Открытый пароль.
        hashed: bcrypt-хеш.

    Returns:
        True если совпадает.
    """
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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
    return jwt.encode(payload, _require_jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Декодировать и проверить JWT.

    Args:
        token: Строка JWT.

    Returns:
        Словарь payload.

    Raises:
        jwt.InvalidTokenError: При невалидном или истёкшем токене.
    """
    return jwt.decode(token, _require_jwt_secret(), algorithms=[_JWT_ALGORITHM])


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
    except InvalidTokenError:
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
    except InvalidTokenError:
        return None
