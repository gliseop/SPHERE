from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from magistry_lc.llm import MockLLMProvider

from magistry_lc.actions import (
    ActionType,
    NominatePositionChangeAction,
    PerformAction,
    SendMessageAction,
)
from magistry_lc.arbiter import Arbiter
from magistry_lc.config import GovernanceConfig, MemoryConfig
from magistry_lc.dao import DaoEngine
from magistry_lc.entities import EntityRecord, EntityRegistry
from magistry_lc.events import Event
from magistry_lc.id_alloc import IdAllocator
from magistry_lc.ids import EntityKind, INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from magistry_lc.journal import WorldJournal
from magistry_lc.llm import LLMCaller
from magistry_lc.memory import AgentMemory
from magistry_lc.state import AgentState, WorkItem, WorldState
from magistry_lc.tracing import TraceLog
from magistry_lc.worldgen import WorldGenerator


def _mk_state(*, off_1_caps: list[str], off_2_caps: list[str], off_2_wants_promotion: bool = True) -> WorldState:
    reg = EntityRegistry()
    state = WorldState(tick=0, registry=reg)

    # Channel for public messages.
    reg.register(EntityRecord(entity_id="chan:public", kind=EntityKind.CHANNEL, created_by=None, created_tick=0, meta={}))

    # Agents.
    for aid, name, caps, wants in (
        ("agent:off_1", "Off 1", off_1_caps, True),
        ("agent:off_2", "Off 2", off_2_caps, off_2_wants_promotion),
    ):
        reg.register(EntityRecord(entity_id=aid, kind=EntityKind.AGENT, created_by=None, created_tick=0, meta={"name": name}))
        state.agents[aid] = AgentState(
            agent_id=aid,
            name=name,
            internal=True,
            capabilities=list(caps),
            wants_promotion=bool(wants),
        )

    return state


def _mk_arbiter(tmp_path: Path, *, mock: MockLLMProvider) -> Arbiter:
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)
    gov = GovernanceConfig()
    return Arbiter(llm=llm, governance=gov, id_alloc=IdAllocator(), dao=DaoEngine(cfg=gov), temperature=0.0)


def test_arbiter_enforces_message_capability(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=[], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = SendMessageAction(
        type=ActionType.SEND_MESSAGE,
        to_id="agent:off_2",
        text="hi",
        private=True,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "missing_capability:message" in res[0].reason


def test_arbiter_blocks_nomination_when_target_declines_promotion(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"], off_2_wants_promotion=False)
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = NominatePositionChangeAction(
        type=ActionType.NOMINATE_POSITION_CHANGE,
        target_agent_id="agent:off_2",
        new_title="начальник",
        reason="test",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "target_declines_promotion" in res[0].reason


def test_perform_invalid_op_is_rejected_not_silently_skipped(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    mock = MockLLMProvider(
        structured_responses={
            "test-perform": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "send_message",
                        "args": {"to_id": "agent:off_2", "text": "hi", "private": True},
                    },
                    {"op_type": "unsupported_op", "args": {}},
                ],
            }
        }
    )
    arbiter = _mk_arbiter(tmp_path, mock=mock)

    act = PerformAction(type=ActionType.PERFORM, description="test-perform", target_id="", justification="")
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "perform_op_invalid" in res[0].reason


def test_worldgen_does_not_receive_private_message_text(tmp_path: Path) -> None:
    mock = MockLLMProvider()
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)
    wg = WorldGenerator(llm=llm, temperature=0.0)

    private_msg = Event(
        tick=0,
        event_type="message_sent",
        actor_id="agent:off_1",
        payload={"to_id": "agent:off_2", "private": True, "text": "SECRET"},
        audience=["agent:off_1", "agent:off_2"],
    )
    asyncio.run(wg.generate(tick=0, recent_events=[private_msg], language="ru"))

    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in trace_text

    # И дополнительно: worldgen получает только internal/public события.
    spans = [json.loads(line) for line in trace_text.splitlines() if line.strip()]
    assert spans
    user_payload = spans[-1]["user"]
    assert INTERNAL_AUDIENCE not in user_payload  # worldgen input uses normalized json, not audience tokens
    assert PUBLIC_AUDIENCE not in user_payload


def test_memory_summarizes_working_buffer(tmp_path: Path) -> None:
    mem = AgentMemory(agent_id="agent:off_1")
    cfg = MemoryConfig(working_max_entries=2, working_summarize_batch=2)

    for t in range(4):
        mem.add_working(tick=t, text=f"e{t}")

    mock = MockLLMProvider(responses={"Обнови сводку рабочей памяти агента.": "- one\n- two\n"})
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)

    asyncio.run(
        mem.maybe_summarize_working(
            llm=llm,
            language="ru",
            cfg=cfg,
            tick=10,
            temperature=0.0,
        )
    )

    assert mem.summary == "- one - two"
    assert len(mem.working) == 2


def test_langgraph_world_graph_supports_checkpoint_path(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")

    from magistry_lc.entities import EntityRegistry
    from magistry_lc.graphs import build_world_graph

    async def _gather(gs: dict) -> dict:
        return {"proposed": {}}

    async def _apply(gs: dict) -> dict:
        return {"tick_events": []}

    app = build_world_graph(
        gather_actions_node=_gather,
        apply_actions_node=_apply,
        debug=False,
        checkpoint_path=tmp_path / "langgraph.sqlite",
    )

    state = WorldState(tick=0, registry=EntityRegistry())
    out = asyncio.run(app.ainvoke({"world": state, "events_history": []}))
    assert isinstance(out, dict)


def test_world_journal_tracks_history_and_caps() -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    journal = WorldJournal.from_state(
        state=state,
        store_max_work_items=3,
        store_max_votes=3,
        history_max_entries=5,
    )

    ev1 = Event(
        tick=0,
        event_type="arbiter_approved",
        actor_id="agent:off_1",
        payload={"action_index": 0, "reason": "ok", "action": "noop", "ops": []},
        audience=[INTERNAL_AUDIENCE],
    )
    ev2 = Event(
        tick=0,
        event_type="message_sent",
        actor_id="agent:off_1",
        payload={"to_id": "agent:off_2", "private": True, "text": "hi"},
        audience=["agent:off_1", "agent:off_2"],
    )
    journal.apply_events(state=state, events=[ev1, ev2])

    d = journal.to_dict()
    assert len(d["history"]) == 2
    assert d["history"][0]["type"] == "arbiter_approved"
    assert d["history"][1]["type"] == "message_sent"

    for i in range(5):
        wid = f"work:w{i}"
        state.work_items[wid] = WorkItem(work_id=wid, work_type="t", title=f"T{i}")
        journal.apply_events(
            state=state,
            events=[
                Event(
                    tick=0,
                    event_type="work_item_created",
                    actor_id="agent:off_1",
                    payload={"work_id": wid, "work_type": "t", "title": f"T{i}", "description": "", "participants": []},
                    audience=[INTERNAL_AUDIENCE],
                )
            ],
        )
    assert len(journal.work_items) <= 3


def test_world_journal_redacts_private_messages_and_tracks_world_events() -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    journal = WorldJournal.from_state(state=state, history_max_entries=10)

    journal.apply_events(
        state=state,
        events=[
            Event(
                tick=0,
                event_type="message_sent",
                actor_id="agent:off_1",
                payload={"to_id": "agent:off_2", "private": True, "text": "SECRET"},
                audience=["agent:off_1", "agent:off_2"],
            ),
            Event(
                tick=0,
                event_type="world_event",
                actor_id=None,
                payload={"description": "Storm"},
                audience=[INTERNAL_AUDIENCE],
            ),
        ],
    )

    yaml_text = journal.to_yaml()
    assert "SECRET" not in yaml_text

    d = journal.to_dict()
    assert any(e.get("type") == "world_event" for e in d["history"])


def test_composer_normalizes_or_falls_back_on_invalid_ids(tmp_path: Path) -> None:
    from magistry_lc.composer import WorldComposer

    mock = MockLLMProvider(
        structured_responses={
            "compose-bad-ids": {
                "title": "t",
                "description": "d",
                "agents": [
                    {"agent_id": "", "name": "A", "internal": True, "persona": "p"},
                    {"agent_id": "off_1", "name": "B", "internal": True, "persona": "p"},
                    {"agent_id": "agent:off_1", "name": "C", "internal": True, "persona": "p"},
                ],
                "world": {
                    "channels": [{"channel_id": "public", "title": "public"}],
                    "orgs": [{"org_id": "", "title": "o"}],
                    "work_items": [
                        {
                            "work_id": "",
                            "work_type": "task",
                            "title": "w",
                            "description": "",
                            "participants": ["off_1", "agent:off_1", ""],
                        }
                    ],
                },
            }
        }
    )

    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)
    composer = WorldComposer(llm=llm, temperature=0.0, generate_personas=False)
    cfg = asyncio.run(composer.compose(description="compose-bad-ids", ticks=1, seed=1, language="ru"))

    agent_ids = [a.agent_id for a in cfg.agents]
    assert len(agent_ids) == len(set(agent_ids))
    assert all(aid.startswith("agent:") for aid in agent_ids)

    assert all(ch.channel_id.startswith("chan:") for ch in cfg.world.channels)
    assert all(o.org_id.startswith("org:") for o in cfg.world.orgs)
    assert all(w.work_id.startswith("work:") for w in cfg.world.work_items)

    known_agents = set(agent_ids)
    for w in cfg.world.work_items:
        assert all(pid in known_agents for pid in w.participants)
