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
    with patch("web.backend.routes.auth.get_user_by_username", return_value=ADMIN):
        r = client.post("/api/auth/login", data={"username": "admin", "password": "secret"})
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert r.json()["token_type"] == "bearer"


def test_login_wrong_password():
    with patch("web.backend.routes.auth.get_user_by_username", return_value=ADMIN):
        r = client.post("/api/auth/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user():
    with patch("web.backend.routes.auth.get_user_by_username", return_value=None):
        r = client.post("/api/auth/login", data={"username": "ghost", "password": "x"})
    assert r.status_code == 401


def test_get_runs_no_auth_returns_401():
    r = client.get("/api/runs")
    assert r.status_code == 401


def test_get_runs_with_viewer_token():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR") as mock_dir:
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
        mock_path = MagicMock()
        mock_path.write_text = MagicMock()
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR") as mock_dir:
            mock_dir.__truediv__ = MagicMock(return_value=mock_path)
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
