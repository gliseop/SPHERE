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


def test_list_scenarios_includes_yaml_scenario_config(tmp_path: Path):
    (tmp_path / "custom_lc.yaml").write_text(
        (
            "version: 1\n"
            "title: YAML scenario\n"
            "description: YAML description\n"
            "ticks: 7\n"
            "seed: 11\n"
            "agents:\n"
            "  - agent_id: agent:off_1\n"
            "    name: Off 1\n"
            "    internal: true\n"
            "world: {}\n"
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                r = client.get(
                    "/api/scenarios",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = {item["id"]: item for item in r.json()}
    assert payload["custom_lc"]["name"] == "YAML scenario"
    assert payload["custom_lc"]["rounds"] == 7
    assert payload["custom_lc"]["sim_config"]["title"] == "YAML scenario"


def test_get_scenario_reads_yaml_scenario_config(tmp_path: Path):
    (tmp_path / "custom_lc.yaml").write_text(
        (
            "version: 1\n"
            "title: YAML scenario\n"
            "ticks: 5\n"
            "agents:\n"
            "  - agent_id: agent:off_1\n"
            "    name: Off 1\n"
            "    internal: true\n"
            "world: {}\n"
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                r = client.get(
                    "/api/scenarios/custom_lc",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert payload["id"] == "custom_lc"
    assert payload["name"] == "YAML scenario"
    assert payload["sim_config"]["ticks"] == 5


def test_update_yaml_scenario_preserves_extension_and_saves_sim_config(tmp_path: Path):
    scenario_path = tmp_path / "custom_lc.yaml"
    scenario_path.write_text(
        (
            "version: 1\n"
            "title: Before update\n"
            "ticks: 3\n"
            "agents:\n"
            "  - agent_id: agent:off_1\n"
            "    name: Off 1\n"
            "    internal: true\n"
            "world: {}\n"
        ),
        encoding="utf-8",
    )
    payload = {
        "name": "After update",
        "description": "Updated from web",
        "scenario": "S1",
        "governance": "G1",
        "rounds": 9,
        "seed": 99,
        "agents": [{"id": "off_1", "name": "Off 1", "role": "official", "initial_reputation": 7.0}],
        "sim_config": {
            "version": 1,
            "title": "Ignored title",
            "ticks": 1,
            "seed": 42,
            "agents": [{"agent_id": "agent:off_1", "name": "Off 1", "internal": True}],
            "world": {},
        },
    }

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                r = client.put(
                    "/api/scenarios/custom_lc",
                    json=payload,
                    headers={"Authorization": f"Bearer {admin_token()}"},
                )

    assert r.status_code == 200
    assert scenario_path.exists()
    saved = scenario_path.read_text(encoding="utf-8")
    assert "After update" in saved
    assert "ticks: 9" in saved
    assert "seed: 99" in saved


def test_create_yaml_scenario_uses_next_number_across_yaml_files(tmp_path: Path):
    (tmp_path / "S7.yaml").write_text(
        "version: 1\ntitle: Existing\nticks: 1\nagents: []\nworld: {}\n",
        encoding="utf-8",
    )
    payload = {
        "name": "Created from sim_config",
        "description": "desc",
        "scenario": "S1",
        "governance": "G1",
        "rounds": 4,
        "seed": 13,
        "agents": [],
        "sim_config": {
            "version": 1,
            "title": "Created from sim_config",
            "ticks": 4,
            "seed": 13,
            "agents": [],
            "world": {},
        },
    }

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                r = client.post(
                    "/api/scenarios",
                    json=payload,
                    headers={"Authorization": f"Bearer {admin_token()}"},
                )

    assert r.status_code == 201
    assert r.json()["id"] == "S8"
    assert (tmp_path / "S8.yaml").exists()


def test_get_run_normalizes_lc_events_for_frontend_compat(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"tick":4,"event_type":"reputation_modified","actor_id":"agent:auditor",'
            '"payload":{"target_agent_id":"agent:off_1","delta":-1.5}}\n'
        ),
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
    event = r.json()["events"][0]
    assert event["round"] == 4
    assert event["agent_id"] == "agent:auditor"
    assert event["payload"]["target"] == "agent:off_1"


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


def test_get_interview_rejects_backslash_path_traversal():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        r = client.get(
            "/api/personalities/..%5Csecret/interview",
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )

    assert r.status_code == 400


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


def test_prompts_endpoint_reads_trace_sidecar_for_lc_run(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        (
            '{"role":"agent","name":"agent:off_1","tick":2,'
            '"system":"SYS","user":"USER","response":"RESP",'
            '"timestamp":"2026-03-06T10:00:00+00:00"}\n'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/prompts?agent_id=agent:off_1&round=2",
                    headers={"Authorization": f"Bearer {admin_token()}"},
                )
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) == 1
    assert payload[0]["agent_id"] == "agent:off_1"
    assert payload[0]["round"] == 2
    assert payload[0]["system_prompt"] == "SYS"
    assert payload[0]["user_prompt"] == "USER"
    assert payload[0]["response"] == "RESP"


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
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
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


def test_delete_legacy_run_removes_posthoc_sidecars(tmp_path: Path):
    run_name = "legacy_run"
    (tmp_path / f"{run_name}_events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (tmp_path / f"{run_name}_summary.json").write_text('{"status":"done"}\n', encoding="utf-8")
    (tmp_path / f"{run_name}_truth.jsonl").write_text('{"finding":"x"}\n', encoding="utf-8")
    (tmp_path / f"{run_name}_evaluation.json").write_text('{"score":1}\n', encoding="utf-8")
    (tmp_path / f"{run_name}_fidelity.json").write_text('{"score":1}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.run_control.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                with patch("web.backend.runner._RESULTS_DIR", tmp_path):
                    r = client.delete(
                        f"/api/runs/{run_name}",
                        headers={"Authorization": f"Bearer {admin_token()}"},
                    )

    assert r.status_code == 204
    assert not (tmp_path / f"{run_name}_truth.jsonl").exists()
    assert not (tmp_path / f"{run_name}_evaluation.json").exists()
    assert not (tmp_path / f"{run_name}_fidelity.json").exists()


def test_ws_playback_rejects_zero_speed(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.websocket.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                with client.websocket_connect(
                    "/ws/playback/lc_run?speed=0"
                ) as websocket:
                    websocket.send_json({"type": "auth", "token": viewer_token()})
                    payload = websocket.receive_json()

    assert payload == {"type": "error", "message": "Invalid speed"}


def test_ws_playback_skips_invalid_json_lines(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"tick":1,"event_type":"noop","payload":{}}\n'
            '{not-json}\n'
            '{"tick":2,"event_type":"noop","payload":{}}\n'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.websocket.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                with client.websocket_connect("/ws/playback/lc_run?speed=100") as websocket:
                    websocket.send_json({"type": "auth", "token": viewer_token()})
                    messages = []
                    for _ in range(6):
                        message = websocket.receive_json()
                        messages.append(message)
                        if message.get("type") == "done":
                            break

    assert not any(message.get("type") == "error" for message in messages)
    event_messages = [message for message in messages if message.get("type") in {"event", "events"}]
    assert event_messages
