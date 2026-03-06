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
