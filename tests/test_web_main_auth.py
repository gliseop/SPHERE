"""Интеграционные тесты авторизации эндпоинтов."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
        r = client.get(
            "/api/runs",
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )
    assert r.status_code == 200


def test_get_runs_supports_directory_artifacts(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/runs",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )
    assert r.status_code == 200
    names = {item.get("name") for item in r.json()}
    assert "lc_run" in names


def test_get_run_reads_directory_events(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        '{"event_type":"message_sent","payload":{"to_id":"chan:public","text":"x"}}\n',
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run?include_events=true",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "lc_run"
    assert payload["total_events"] == 1
    assert isinstance(payload["events"], list) and len(payload["events"]) == 1


def test_export_run_reads_directory_sidecars(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "scenario.json").write_text('{"title":"demo"}\n', encoding="utf-8")
    (run_dir / "names.json").write_text('{"agent:1":"Alice"}\n', encoding="utf-8")
    (run_dir / "summary.json").write_text('{"score":1}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/export",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "lc_run"
    assert payload["scenario"] == {"title": "demo"}
    assert payload["names"] == {"agent:1": "Alice"}
    assert payload["summary"] == {"score": 1}


def test_prompts_endpoint_requires_admin(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"llm_call"}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/prompts",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )
    assert r.status_code == 403


def test_prompts_endpoint_caps_limit(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"llm_call"}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/prompts?limit=5001",
                    headers={"Authorization": f"Bearer {admin_token()}"},
                )
    assert r.status_code == 400


def test_create_scenario_viewer_gets_403():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        r = client.post(
            "/api/scenarios",
            json={"name": "test"},
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )
    assert r.status_code == 403


def test_create_scenario_admin_gets_201(tmp_path: Path):
    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
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


def test_delete_directory_run(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "summary.json").write_text('{"status":"done"}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.run_control.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                with patch("web.backend.runner._RESULTS_DIR", tmp_path):
                    r = client.delete(
                        "/api/runs/lc_run",
                        headers={"Authorization": f"Bearer {admin_token()}"},
                    )
    assert r.status_code == 204
    assert not run_dir.exists()
