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
