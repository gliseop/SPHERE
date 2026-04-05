"""Tests for web graph state reconstruction (dependency-free module)."""

from web.backend.graph_state import build_graph_state, normalize_event_compat


def test_graph_state_adds_edge_from_message_sent():
    graph = build_graph_state(
        [
            {
                "event_type": "message_sent",
                "agent_id": "off_1",
                "payload": {"to_id": "biz_1", "private": True},
            }
        ]
    )
    assert {n["id"] for n in graph["nodes"]} == {"off_1", "biz_1"}
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert {edge["source"], edge["target"]} == {"off_1", "biz_1"}
    assert edge["strength"] == 0.2


def test_graph_state_caps_thread_strength_per_thread_id():
    # 12 messages -> 1.2 but should cap to 1.0 per thread.
    events = []
    for i in range(12):
        sender = "off_1" if i % 2 == 0 else "biz_1"
        to_id = "biz_1" if sender == "off_1" else "off_1"
        events.append(
            {
                "event_type": "message",
                "agent_id": sender,
                "payload": {
                    "to_id": to_id,
                    "thread_id": "T-001",
                    "private": False,
                    "content": f"m{i}",
                },
            }
        )
    graph = build_graph_state(events)
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["strength"] == 1.0


def test_graph_state_adds_node_from_entity_created_agent_event():
    graph = build_graph_state(
        [
            {
                "event_type": "entity_created",
                "payload": {
                    "entity_id": "agent:sec_wife",
                    "kind": "agent",
                    "meta": {"name": "Волкова Н.И."},
                },
            }
        ]
    )
    assert {n["id"] for n in graph["nodes"]} == {"agent:sec_wife"}
    assert graph["nodes"][0]["name"] == "Волкова Н.И."


def test_graph_state_sums_across_threads():
    # Two different threads can each contribute up to 1.0.
    events = []
    for t in ("T-001", "T-002"):
        for i in range(10):
            events.append(
                {
                    "event_type": "message",
                    "agent_id": "off_1",
                    "payload": {
                        "to_id": "biz_1",
                        "thread_id": t,
                        "private": False,
                        "content": f"{t}-{i}",
                    },
                }
            )
    graph = build_graph_state(events)
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["strength"] == 2.0


def test_normalize_event_compat_maps_lc_fields_to_legacy_aliases():
    event = normalize_event_compat(
        {
            "tick": 3,
            "event_type": "message_sent",
            "actor_id": "agent:off_1",
            "payload": {"to_id": "agent:off_2", "text": "hello"},
        }
    )

    assert event["round"] == 3
    assert event["agent_id"] == "agent:off_1"
    assert event["payload"]["content"] == "hello"


def test_graph_state_applies_lc_reputation_changes_to_target_agent():
    graph = build_graph_state(
        [
            {
                "tick": 2,
                "event_type": "reputation_modified",
                "actor_id": "agent:auditor",
                "payload": {"target_agent_id": "agent:off_1", "delta": -2.5},
            }
        ]
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["agent:off_1"]["reputation"] == 7.5
    assert nodes["agent:auditor"]["reputation"] == 10.0


def test_graph_state_unfreezes_target_agent_from_lc_event():
    graph = build_graph_state(
        [
            {
                "tick": 1,
                "event_type": "reputation_frozen",
                "actor_id": "agent:auditor",
                "payload": {"target_agent_id": "agent:off_1"},
            },
            {
                "tick": 2,
                "event_type": "reputation_unfrozen",
                "actor_id": None,
                "payload": {"target_agent_id": "agent:off_1"},
            },
        ]
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["agent:off_1"]["reputation_frozen"] is False


def test_graph_state_marks_typed_governance_agent_without_reputation():
    graph = build_graph_state(
        [
            {
                "event_type": "entity_created",
                "payload": {
                    "entity_id": "agent:auditor",
                    "kind": "agent",
                    "meta": {"name": "Аудитор"},
                },
            }
        ]
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["agent:auditor"]["has_reputation"] is False


def test_graph_state_ignores_channel_targets_in_agent_graph():
    graph = build_graph_state(
        [
            {
                "tick": 1,
                "event_type": "message_sent",
                "actor_id": "agent:off_1",
                "payload": {"to_id": "chan:public", "private": False, "text": "notice"},
            }
        ]
    )

    assert {node["id"] for node in graph["nodes"]} == {"agent:off_1"}
    assert graph["edges"] == []


def test_graph_state_updates_position_title_from_position_changed():
    graph = build_graph_state(
        [
            {
                "tick": 2,
                "event_type": "position_changed",
                "payload": {"target_agent_id": "agent:off_1", "new_title": "руководитель отдела"},
            }
        ]
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["agent:off_1"]["position_title"] == "руководитель отдела"


def test_graph_state_hides_reputation_for_external_agent_snapshot():
    graph = build_graph_state(
        [
            {
                "tick": 0,
                "event_type": "reputation_snapshot",
                "payload": {
                    "target_agent_id": "agent:contractor",
                    "score": 0.0,
                    "internal": False,
                    "frozen": False,
                    "title": "подрядчик",
                },
            }
        ]
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["agent:contractor"]["has_reputation"] is False


def test_graph_state_tracks_environment_signals_without_queue_layer():
    graph = build_graph_state(
        [
            {
                "tick": 1,
                "event_type": "environment_operational_queue_updated",
                "payload": {
                    "queue_id": "queue:permits",
                    "backlog": 7,
                    "capacity_per_tick": 2,
                    "avg_delay_ticks": 3,
                    "status": "overloaded",
                    "pressure": "Жалобы растут.",
                    "owner_org_id": "org:city_hall",
                },
            },
            {
                "tick": 1,
                "event_type": "environment_information_climate_updated",
                "payload": {
                    "active_signals": [
                        "очередь queue:permits перегружена",
                        "публичное давление по queue:permits",
                    ]
                },
            },
        ]
    )

    assert "queues" not in graph["environment"]
    assert graph["environment"]["active_signals"] == [
        "очередь queue:permits перегружена",
        "публичное давление по queue:permits",
    ]
