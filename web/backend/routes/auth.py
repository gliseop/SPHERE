"""Маршрут аутентификации: POST /api/auth/login."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from web.backend.auth import create_access_token, verify_password
from web.backend.database import get_user_by_username

router = APIRouter(tags=["auth"])


@router.post("/api/auth/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    """Аутентификация пользователя.

    Args:
        form_data: username и password из form-encoded тела.

    Returns:
        Словарь с access_token и token_type.

    Raises:
        HTTPException 401: При неверных учётных данных.
    """
    user = get_user_by_username(form_data.username)
    if user is None or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return {
        "access_token": create_access_token(user.username, user.role),
        "token_type": "bearer",
    }
