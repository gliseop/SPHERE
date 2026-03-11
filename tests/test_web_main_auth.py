"""Интеграционные тесты авторизации эндпоинтов."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from web.backend.main import app
from web.backend.database import User
from web.backend import auth as auth_module
from web.backend.routes import scenarios as scenarios_module

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


def test_get_run_hides_private_events_for_viewer(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"tick":1,"event_type":"message_sent","actor_id":"agent:off_1",'
            '"payload":{"to_id":"agent:off_2","private":true,"text":"secret"},'
            '"audience":["agent:off_1","agent:off_2"]}\n'
            '{"tick":1,"event_type":"world_event","payload":{"description":"public"},'
            '"audience":["aud:public"]}\n'
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
    payload = r.json()
    assert payload["total_events"] == 1
    assert [event["event_type"] for event in payload["events"]] == ["world_event"]


def test_get_run_admin_sees_private_events(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"tick":1,"event_type":"message_sent","actor_id":"agent:off_1",'
            '"payload":{"to_id":"agent:off_2","private":true,"text":"secret"},'
            '"audience":["agent:off_1","agent:off_2"]}\n'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run?include_events=true",
                    headers={"Authorization": f"Bearer {admin_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert payload["total_events"] == 1
    assert payload["events"][0]["payload"]["text"] == "secret"


def test_get_artifact_skips_invalid_jsonl_lines(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"event_type":"noop","payload":{}}\n'
            '{not-json}\n'
            '{"event_type":"document_created","payload":{"doc_id":"doc_1","title":"Report","doc_type":"memo","case_id":"case_1","content":"ok"}}\n'
        ),
        encoding="utf-8",
    )
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.routes.runs.ARTIFACTS_DIR", artifacts_dir):
                with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                    r = client.get(
                        "/api/artifacts/doc_1",
                        headers={"Authorization": f"Bearer {admin_token()}"},
                    )

    assert r.status_code == 200
    payload = r.json()
    assert payload["doc_id"] == "doc_1"
    assert payload["title"] == "Report"
    assert payload["content"] == "ok"


def test_get_artifact_requires_admin(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        '{"event_type":"document_created","payload":{"doc_id":"doc_1","content":"ok"}}\n',
        encoding="utf-8",
    )
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.routes.runs.ARTIFACTS_DIR", artifacts_dir):
                with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                    r = client.get(
                        "/api/artifacts/doc_1",
                        headers={"Authorization": f"Bearer {viewer_token()}"},
                    )

    assert r.status_code == 403


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
            "    capabilities: [message, spawn]\n"
            "    initial_reputation: 7.0\n"
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
    assert payload["agents"][0]["capabilities"] == ["message", "spawn"]
    assert payload["agents"][0]["initial_reputation"] == 7.0


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


def test_export_run_falls_back_to_input_sidecar_for_live_run(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "_input_scenario.json").write_text('{"title":"live demo"}\n', encoding="utf-8")

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/export",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert payload["scenario"] == {"title": "live demo"}


def test_export_run_hides_private_events_for_viewer(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"tick":1,"event_type":"message_sent","actor_id":"agent:off_1",'
            '"payload":{"to_id":"agent:off_2","private":true,"text":"secret"},'
            '"audience":["agent:off_1","agent:off_2"]}\n'
            '{"tick":1,"event_type":"world_event","payload":{"description":"public"},'
            '"audience":["aud:public"]}\n'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/export",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert [event["event_type"] for event in payload["events"]] == ["world_event"]


def test_get_run_scenario_normalizes_lc_config_for_frontend(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "scenario.json").write_text(
        (
            '{'
            '"version":1,'
            '"title":"LC Scenario",'
            '"description":"demo",'
            '"ticks":4,'
            '"seed":11,'
            '"agents":[{"agent_id":"agent:off_1","name":"Off 1","internal":true,"persona":{},"capabilities":["message","spawn"],"initial_reputation":7.0,"initial_title":"специалист"}],'
            '"world":{"channels":[{"channel_id":"chan:public","title":"Public"}],"orgs":[],"work_items":[]}'
            '}'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/scenario",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "LC Scenario"
    assert payload["rounds"] == 4
    assert payload["governance"] == "G0"
    assert payload["agents"][0]["id"] == "off_1"
    assert payload["agents"][0]["role"] == "official"
    assert payload["agents"][0]["capabilities"] == ["message", "spawn"]
    assert payload["agents"][0]["initial_reputation"] == 7.0


def test_get_run_scenario_falls_back_to_input_sidecar_for_live_run(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "_input_scenario.json").write_text(
        (
            '{'
            '"version":1,'
            '"title":"Live Input Scenario",'
            '"description":"demo",'
            '"ticks":6,'
            '"seed":13,'
            '"agents":[{"agent_id":"agent:off_1","name":"Off 1","internal":true,"persona":{},"capabilities":["message"],"initial_reputation":3.0,"initial_title":"специалист"}],'
            '"world":{"channels":[{"channel_id":"chan:public","title":"Public"}],"orgs":[],"work_items":[]}'
            '}'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/scenario",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "Live Input Scenario"
    assert payload["rounds"] == 6
    assert payload["seed"] == 13


def test_get_interview_rejects_backslash_path_traversal():
    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        r = client.get(
            "/api/personalities/..%5Csecret/interview",
            headers={"Authorization": f"Bearer {viewer_token()}"},
        )

    assert r.status_code == 400


def test_resolve_scenario_path_rejects_escaped_path(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")

    with patch.object(scenarios_module, "SCENARIOS_DIR", scenarios_dir):
        assert scenarios_module._resolve_scenario_path("..\\outside") is None


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


def test_prompts_endpoint_returns_empty_list_for_zero_limit(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"noop"}\n', encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        '{"role":"agent","name":"agent:off_1","tick":2,"system":"SYS","user":"USER","response":"RESP"}\n',
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.runs.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                r = client.get(
                    "/api/run/lc_run/prompts?limit=0",
                    headers={"Authorization": f"Bearer {admin_token()}"},
                )
    assert r.status_code == 200
    assert r.json() == []


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


def test_create_scenario_invalid_sim_config_does_not_leave_empty_file(tmp_path: Path):
    payload = {
        "name": "broken scenario",
        "scenario": "S1",
        "governance": "G1",
        "rounds": 2,
        "agents": [],
        "sim_config": {
            "version": 1,
            "title": "broken",
            "ticks": 1,
            "agents": [],
            "world": {},
            "unexpected": 1,
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

    assert r.status_code == 400
    assert list(tmp_path.iterdir()) == []


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


def test_launch_run_admin_rejects_invalid_scenario_id():
    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        r = client.post(
            "/api/runs/launch",
            json={"scenario": "..\\data\\empirical_benchmarks"},
            headers={"Authorization": f"Bearer {admin_token()}"},
        )

    assert r.status_code == 400


def test_template_scenarios_available_from_seed_files(tmp_path: Path):
    (tmp_path / "seed_s0_g0.json").write_text(
        json.dumps(
            {
                "name": "Чистая сделка",
                "scenario": "S0",
                "governance": "G0",
                "rounds": 5,
                "seed": 1,
                "agents": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps(
            {
                "name": "Прямой сговор",
                "scenario": "S1",
                "governance": "G1",
                "rounds": 6,
                "seed": 2,
                "agents": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            r = client.get(
                "/api/templates/scenarios",
                headers={"Authorization": f"Bearer {viewer_token()}"},
            )

    assert r.status_code == 200
    payload = {item["id"]: item for item in r.json()}
    assert payload["S0"]["title"] == "Чистая сделка"
    assert payload["S1"]["title"] == "Прямой сговор"


def test_saved_scenarios_list_excludes_builtin_seed_files(tmp_path: Path):
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps({"name": "Builtin", "scenario": "S1", "governance": "G1", "rounds": 6, "agents": []}),
        encoding="utf-8",
    )
    (tmp_path / "custom_lc.yaml").write_text(
        (
            "version: 1\n"
            "title: Custom YAML\n"
            "ticks: 2\n"
            "agents: []\n"
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
    payload = {item["id"] for item in r.json()}
    assert "custom_lc" in payload
    assert "seed_s1_g1" not in payload


def test_template_scenario_returns_normalized_config(tmp_path: Path):
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps(
            {
                "name": "Прямой сговор",
                "description": "demo",
                "scenario": "S1",
                "governance": "G1",
                "rounds": 6,
                "seed": 2,
                "agents": [{"id": "off_1", "name": "Off 1", "role": "official", "initial_reputation": 5}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            r = client.get(
                "/api/templates/scenarios/S1?governance=G2",
                headers={"Authorization": f"Bearer {viewer_token()}"},
            )

    assert r.status_code == 200
    payload = r.json()
    assert payload["title"] == "Прямой сговор"
    assert payload["ticks"] == 6
    assert payload["governance"]["audit"]["enabled"] is True
    assert payload["governance"]["audit"]["reputation_freeze_enabled"] is True


def test_template_scenario_applies_custom_governance_mode(tmp_path: Path):
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps(
            {
                "name": "Прямой сговор",
                "scenario": "S1",
                "governance": "G1",
                "rounds": 4,
                "agents": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    governance_dir = tmp_path / "governance"
    governance_dir.mkdir()
    (governance_dir / "G4.json").write_text(
        json.dumps(
            {
                "id": "G4",
                "label": "G4 — Custom",
                "description": "Custom governance mode",
                "config": {
                    "require_consent": False,
                    "allow_self_nomination": True,
                    "allow_target_self_vote": True,
                    "audit": {
                        "enabled": True,
                        "mode": "rules",
                        "reputation_freeze_enabled": True,
                        "reputation_penalty_delta": -0.5,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.routes.scenarios.GOVERNANCE_MODES_DIR", governance_dir):
                r = client.get(
                    "/api/templates/scenarios/S1?governance=G4",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 200
    payload = r.json()
    assert payload["governance"]["require_consent"] is False
    assert payload["governance"]["allow_self_nomination"] is True
    assert payload["governance"]["allow_target_self_vote"] is True
    assert payload["governance"]["audit"]["reputation_penalty_delta"] == -0.5


def test_template_scenario_rejects_governance_path_traversal(tmp_path: Path):
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps(
            {
                "name": "Прямой сговор",
                "scenario": "S1",
                "governance": "G1",
                "rounds": 4,
                "agents": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    governance_dir = tmp_path / "governance"
    governance_dir.mkdir()

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.routes.scenarios.GOVERNANCE_MODES_DIR", governance_dir):
                r = client.get(
                    "/api/templates/scenarios/S1?governance=..%5C..%5Cscenarios%5Cseed_s1_g1",
                    headers={"Authorization": f"Bearer {viewer_token()}"},
                )

    assert r.status_code == 400


def test_launch_run_admin_starts_template_process():
    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch(
            "web.backend.runner.launch_simulation",
            return_value={"run_name": "S1_G1_seed42_web", "pid": 1234},
        ):
            r = client.post(
                "/api/runs/launch",
                json={"scenario": "S1", "governance": "G1", "seed": 42, "rounds": 8},
                headers={"Authorization": f"Bearer {admin_token()}"},
            )

    assert r.status_code == 202
    assert r.json()["run_name"] == "S1_G1_seed42_web"


def test_launch_run_admin_passes_parallel_settings():
    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch(
            "web.backend.runner.launch_simulation",
            return_value={"run_name": "S1_G1_seed42_web", "pid": 1234},
        ) as mocked:
            r = client.post(
                "/api/runs/launch",
                json={
                    "scenario": "S1",
                    "governance": "G1",
                    "seed": 42,
                    "rounds": 8,
                    "parallel_agents": True,
                    "parallel_workers": 6,
                    "parallel_window": 120.0,
                },
                headers={"Authorization": f"Bearer {admin_token()}"},
            )

    assert r.status_code == 202
    assert mocked.call_args.kwargs["parallel_agents"] is True
    assert mocked.call_args.kwargs["parallel_workers"] == 6
    assert mocked.call_args.kwargs["parallel_window"] == 120.0


def test_run_scenario_admin_launches_saved_yaml(tmp_path: Path):
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

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                with patch(
                    "web.backend.runner.launch_simulation_from_config",
                    return_value={"run_name": "custom_lc_G0_seed42_web", "pid": 555},
                ):
                    r = client.post(
                        "/api/scenarios/custom_lc/run",
                        headers={"Authorization": f"Bearer {admin_token()}"},
                    )

    assert r.status_code == 202
    assert r.json()["run_name"] == "custom_lc_G0_seed42_web"


def test_run_scenario_legacy_json_without_sim_config_uses_selected_template(tmp_path: Path):
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps(
            {
                "name": "Template S1",
                "scenario": "S1",
                "governance": "G1",
                "rounds": 6,
                "agents": [
                    {
                        "id": "off_1",
                        "name": "Off 1",
                        "role": "official",
                        "initial_reputation": 7.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "legacy_saved.json").write_text(
        json.dumps(
            {
                "name": "Saved legacy",
                "scenario": "S1",
                "governance": "G2",
                "rounds": 9,
                "seed": 13,
                "agents": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                with patch(
                    "web.backend.runner.launch_simulation_from_config",
                    return_value={"run_name": "legacy_saved_G2_seed13_web", "pid": 555},
                ) as mocked:
                    r = client.post(
                        "/api/scenarios/legacy_saved/run",
                        headers={"Authorization": f"Bearer {admin_token()}"},
                    )

    assert r.status_code == 202
    payload = mocked.call_args.kwargs["scenario_config"]
    assert payload["title"] == "Saved legacy"
    assert payload["ticks"] == 9
    assert payload["seed"] == 13
    assert [agent["agent_id"] for agent in payload["agents"]] == ["agent:off_1"]
    assert payload["governance"]["audit"]["enabled"] is True
    assert payload["governance"]["audit"]["reputation_freeze_enabled"] is True


def test_run_scenario_passes_parallel_settings_from_runtime_config(tmp_path: Path):
    (tmp_path / "custom_lc.yaml").write_text(
        (
            "version: 1\n"
            "title: YAML scenario\n"
            "ticks: 5\n"
            "runtime:\n"
            "  parallel_agents: false\n"
            "  parallel_workers: 3\n"
            "  parallel_window_seconds: 90\n"
            "agents:\n"
            "  - agent_id: agent:off_1\n"
            "    name: Off 1\n"
            "    internal: true\n"
            "world: {}\n"
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                with patch(
                    "web.backend.runner.launch_simulation_from_config",
                    return_value={"run_name": "custom_lc_G0_seed42_web", "pid": 555},
                ) as mocked:
                    r = client.post(
                        "/api/scenarios/custom_lc/run",
                        headers={"Authorization": f"Bearer {admin_token()}"},
                    )

    assert r.status_code == 202
    assert mocked.call_args.kwargs["parallel_agents"] is False
    assert mocked.call_args.kwargs["parallel_workers"] == 3
    assert mocked.call_args.kwargs["parallel_window"] == 90.0


def test_delete_builtin_template_scenario_is_forbidden(tmp_path: Path):
    (tmp_path / "seed_s1_g1.json").write_text(
        json.dumps({"name": "Builtin", "scenario": "S1", "governance": "G1", "rounds": 6, "agents": []}),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=ADMIN):
        with patch("web.backend.routes.scenarios.SCENARIOS_DIR", tmp_path):
            with patch("web.backend.validators.SCENARIOS_DIR", tmp_path):
                r = client.delete(
                    "/api/scenarios/seed_s1_g1",
                    headers={"Authorization": f"Bearer {admin_token()}"},
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


def test_ws_playback_hides_private_events_for_viewer(tmp_path: Path):
    run_dir = tmp_path / "lc_run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        (
            '{"tick":1,"event_type":"message_sent","actor_id":"agent:off_1",'
            '"payload":{"to_id":"agent:off_2","private":true,"text":"secret"},'
            '"audience":["agent:off_1","agent:off_2"]}\n'
            '{"tick":2,"event_type":"world_event","payload":{"description":"tail"},'
            '"audience":["aud:public"]}\n'
        ),
        encoding="utf-8",
    )

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.websocket.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                with client.websocket_connect("/ws/playback/lc_run?speed=100") as websocket:
                    websocket.send_json({"type": "auth", "token": viewer_token()})
                    messages = []
                    for _ in range(8):
                        message = websocket.receive_json()
                        messages.append(message)
                        if message.get("type") == "done":
                            break

    streamed: list[dict] = []
    for message in messages:
        if message.get("type") == "event":
            streamed.append(message["data"])
        elif message.get("type") == "events":
            streamed.extend(message["data"])

    assert [event.get("event_type") for event in streamed] == ["world_event"]
    assert all("secret" not in json.dumps(event, ensure_ascii=False) for event in streamed)


def test_ws_live_drains_final_event_tail_before_done(tmp_path: Path):
    run_dir = tmp_path / "live_run"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    events_path.write_text(
        '{"tick":1,"event_type":"world_event","payload":{"description":"initial"}}\n',
        encoding="utf-8",
    )

    calls = {"count": 0}

    def _fake_list_active():
        calls["count"] += 1
        if calls["count"] == 1:
            return [{"run_name": "live_run", "status": "running", "pid": 1234}]
        if calls["count"] == 2:
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    '{"tick":2,"event_type":"world_event","payload":{"description":"tail"}}\n'
                )
            return []
        return []

    async def _fast_sleep(_delay: float) -> None:
        return None

    with patch("web.backend.auth.get_user_by_username", return_value=VIEWER):
        with patch("web.backend.websocket.RESULTS_DIR", tmp_path):
            with patch("web.backend.run_artifacts.RESULTS_DIR", tmp_path):
                with patch("web.backend.runner.list_active", side_effect=_fake_list_active):
                    with patch("web.backend.websocket.asyncio.sleep", new=_fast_sleep):
                        with client.websocket_connect("/ws/live?run_name=live_run") as websocket:
                            websocket.send_json({"type": "auth", "token": viewer_token()})
                            messages = []
                            for _ in range(12):
                                message = websocket.receive_json()
                                messages.append(message)
                                if message.get("type") == "done":
                                    break

    events: list[dict] = []
    for message in messages:
        if message.get("type") == "event":
            events.append(message["data"])
        elif message.get("type") == "events":
            events.extend(message["data"])

    assert any(event.get("payload", {}).get("description") == "tail" for event in events)
    assert messages[-1]["type"] == "done"
