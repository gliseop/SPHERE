"""Тесты JWT-аутентификации и FastAPI-зависимостей."""
from __future__ import annotations

import importlib
import pytest
from unittest.mock import patch

import web.backend.auth as auth_module
from web.backend.database import User

ADMIN = User(id=1, username="admin", password_hash="placeholder", role="admin", created_at="2026-01-01T00:00:00+00:00")
VIEWER = User(id=2, username="alice", password_hash="placeholder", role="viewer", created_at="2026-01-01T00:00:00+00:00")
_LONG_TEST_SECRET = "test-jwt-secret-0123456789abcdef0123456789abcdef"


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
    import jwt
    from jwt import InvalidTokenError
    from datetime import datetime, timezone
    expired_payload = {
        "sub": "admin",
        "role": "admin",
        "exp": datetime(2000, 1, 1, tzinfo=timezone.utc),
    }
    token = jwt.encode(expired_payload, auth_module._JWT_SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        auth_module.decode_token(token)


def test_jwt_expire_hours_invalid_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_EXPIRE_HOURS", "not-a-number")
    reloaded = importlib.reload(auth_module)
    try:
        assert reloaded._JWT_EXPIRE_HOURS == 24
    finally:
        monkeypatch.setenv("JWT_EXPIRE_HOURS", "24")
        importlib.reload(reloaded)


def test_validate_jwt_secret_rejects_short_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", "short-secret")
    monkeypatch.delenv("MAGISTRY_DEV", raising=False)
    reloaded = importlib.reload(auth_module)
    try:
        with pytest.raises(RuntimeError, match="at least 32 bytes"):
            reloaded.validate_jwt_secret()
    finally:
        monkeypatch.setenv("JWT_SECRET", _LONG_TEST_SECRET)
        importlib.reload(reloaded)


def test_dev_mode_uses_stable_fallback_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("MAGISTRY_DEV", "1")
    reloaded = importlib.reload(auth_module)
    try:
        reloaded.validate_jwt_secret()
        assert reloaded._JWT_SECRET is not None
        assert len(reloaded._JWT_SECRET.encode("utf-8")) >= 32
        token = reloaded.create_access_token("alice", "viewer")
        reloaded_again = importlib.reload(reloaded)
        assert reloaded_again.decode_token(token)["sub"] == "alice"
    finally:
        monkeypatch.setenv("JWT_SECRET", _LONG_TEST_SECRET)
        monkeypatch.delenv("MAGISTRY_DEV", raising=False)
        importlib.reload(reloaded)


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
