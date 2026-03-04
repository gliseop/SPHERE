from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magistry_sim.llm.providers import MockLLMProvider

from magistry_lc.actions import (
    ActionType,
    NominatePositionChangeAction,
    PerformAction,
    SendMessageAction,
)
from magistry_lc.arbiter import Arbiter
from magistry_lc.config import GovernanceConfig
from magistry_lc.dao import DaoEngine
from magistry_lc.entities import EntityRecord, EntityRegistry
from magistry_lc.events import Event
from magistry_lc.id_alloc import IdAllocator
from magistry_lc.ids import EntityKind, INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from magistry_lc.llm import LLMCaller
from magistry_lc.state import AgentState, WorldState
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

