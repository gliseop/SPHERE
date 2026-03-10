from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magistry_lc.auditor import RuntimeAuditor
from magistry_lc.config import AuditRuntimeConfig, ScenarioConfig
from magistry_lc.engine import RunArtifacts, WorldEngine
from magistry_lc.entities import EntityRecord, EntityRegistry
from magistry_lc.events import Event
from magistry_lc.ids import EntityKind
from magistry_lc.llm import MockLLMProvider
from magistry_lc.ops import ModifyReputationOp
from magistry_lc.state import AgentState, Vote, WorldState


def _mk_state() -> WorldState:
    reg = EntityRegistry()
    state = WorldState(tick=0, registry=reg)
    for aid, caps in (
        ("agent:auditor", ["audit"]),
        ("agent:off_1", ["dao", "message"]),
        ("agent:off_2", ["dao", "message"]),
    ):
        reg.register(
            EntityRecord(
                entity_id=aid,
                kind=EntityKind.AGENT,
                created_by=None,
                created_tick=0,
                meta={"name": aid},
            )
        )
        state.agents[aid] = AgentState(
            agent_id=aid,
            name=aid,
            internal=True,
            capabilities=list(caps),
        )
    return state


def test_modify_reputation_positive_gain_blocked_while_frozen() -> None:
    state = _mk_state()
    state.tick = 2
    state.agents["agent:off_1"].reputation = 1.0
    state.agents["agent:off_1"].reputation_frozen = True
    state.agents["agent:off_1"].reputation_frozen_until_tick = 5

    events = ModifyReputationOp(
        actor_id="agent:auditor",
        target_agent_id="agent:off_1",
        delta=2.0,
        reason="reward",
    ).apply(state)

    assert state.agents["agent:off_1"].reputation == 1.0
    assert events[0].event_type == "reputation_gain_blocked"


def test_runtime_auditor_flags_nomination_after_private_contact() -> None:
    state = _mk_state()
    state.tick = 1
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True))

    recent_events = [
        Event(
            tick=0,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={"to_id": "agent:off_2", "private": True, "text": "secret"},
            audience=["agent:off_1", "agent:off_2"],
        )
    ]
    tick_events = [
        Event(
            tick=1,
            event_type="vote_opened",
            actor_id="agent:off_1",
            payload={
                "vote_id": "vote:1",
                "target_agent_id": "agent:off_2",
                "new_title": "lead",
            },
        )
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events + tick_events,
        )
    )

    assert outcome.findings
    assert outcome.findings[0].violation_type == "nomination_after_private_contact"
    assert [event.event_type for event in outcome.events] == ["audit_flagged", "audit_case_opened"]
    assert not outcome.ops


def test_runtime_auditor_flags_support_vote_after_private_contact() -> None:
    state = _mk_state()
    state.tick = 2
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_2",
        created_tick=1,
        closes_tick=3,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
    )
    auditor = RuntimeAuditor(
        cfg=AuditRuntimeConfig(
            enabled=True,
            reputation_penalty_delta=-0.5,
        )
    )

    recent_events = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={"to_id": "agent:off_2", "private": True, "text": "secret"},
            audience=["agent:off_1", "agent:off_2"],
        )
    ]
    tick_events = [
        Event(
            tick=2,
            event_type="vote_cast",
            actor_id="agent:off_1",
            payload={"vote_id": "vote:1", "choice": "yes"},
        )
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
        )
    )

    assert outcome.findings
    assert outcome.findings[0].violation_type == "support_vote_after_private_contact"
    assert any(event.event_type == "audit_flagged" for event in outcome.events)
    assert any(event.event_type == "audit_escalated" for event in outcome.events)
    assert any(op.__class__.__name__ == "SetReputationFreezeOp" for op in outcome.ops)


def test_runtime_auditor_keeps_distinct_targets_for_same_violation_type() -> None:
    state = _mk_state()
    state.agents["agent:off_3"] = AgentState(
        agent_id="agent:off_3",
        name="agent:off_3",
        internal=True,
        capabilities=["dao", "message"],
    )
    state.registry.register(
        EntityRecord(
            entity_id="agent:off_3",
            kind=EntityKind.AGENT,
            created_by=None,
            created_tick=0,
            meta={"name": "agent:off_3"},
        )
    )
    state.tick = 2
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_2",
        created_tick=1,
        closes_tick=3,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
    )
    state.votes["vote:2"] = Vote(
        vote_id="vote:2",
        vote_type="position_change",
        created_by="agent:off_3",
        created_tick=1,
        closes_tick=3,
        target_agent_id="agent:off_3",
        new_title="lead",
        voters=["agent:off_1"],
    )
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True))

    recent_events = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={"to_id": "agent:off_2", "private": True, "text": "secret"},
            audience=["agent:off_1", "agent:off_2"],
        ),
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={"to_id": "agent:off_3", "private": True, "text": "secret"},
            audience=["agent:off_1", "agent:off_3"],
        ),
    ]
    tick_events = [
        Event(
            tick=2,
            event_type="vote_cast",
            actor_id="agent:off_1",
            payload={"vote_id": "vote:1", "choice": "yes"},
        ),
        Event(
            tick=2,
            event_type="vote_cast",
            actor_id="agent:off_1",
            payload={"vote_id": "vote:2", "choice": "yes"},
        ),
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
        )
    )

    assert len(outcome.findings) == 2
    assert {
        finding.target_agent_id
        for finding in outcome.findings
    } == {"agent:off_2", "agent:off_3"}


def test_runtime_auditor_freezes_self_reputation_award() -> None:
    state = _mk_state()
    state.tick = 3
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, freeze_duration_ticks=2))

    tick_events = [
        Event(
            tick=3,
            event_type="reputation_modified",
            actor_id="agent:auditor",
            payload={"target_agent_id": "agent:auditor", "delta": 1.0, "reason": "self"},
        )
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=tick_events,
        )
    )

    assert any(event.event_type == "audit_flagged" for event in outcome.events)
    assert any(event.event_type == "audit_escalated" for event in outcome.events)
    assert outcome.ops

    emitted = []
    for op in outcome.ops:
        emitted.extend(op.apply(state))

    assert state.agents["agent:auditor"].reputation_frozen is True
    assert state.agents["agent:auditor"].reputation_frozen_until_tick == 5
    assert [event.event_type for event in emitted] == ["reputation_frozen"]


def test_runtime_auditor_keeps_repeated_same_tick_findings_distinct() -> None:
    state = _mk_state()
    state.tick = 3
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True))

    tick_events = [
        Event(
            tick=3,
            event_type="reputation_modified",
            actor_id="agent:auditor",
            payload={"target_agent_id": "agent:auditor", "delta": 1.0, "reason": "self"},
        ),
        Event(
            tick=3,
            event_type="reputation_modified",
            actor_id="agent:auditor",
            payload={"target_agent_id": "agent:auditor", "delta": 1.0, "reason": "self"},
        ),
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=[],
        )
    )

    assert len(outcome.findings) == 2
    assert outcome.findings[0].evidence_refs != outcome.findings[1].evidence_refs
    assert [event.event_type for event in outcome.events].count("audit_flagged") == 2


def test_engine_runtime_auditor_emits_audit_events_and_unfreezes_after_duration(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-runtime-auditor",
            "ticks": 3,
            "governance": {
                "audit": {
                    "enabled": True,
                    "freeze_duration_ticks": 2,
                }
            },
            "agents": [
                {
                    "agent_id": "agent:auditor",
                    "name": "Auditor",
                    "internal": True,
                    "persona": "auditor",
                    "capabilities": ["audit"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    mock = MockLLMProvider(
        structured_responses={
            "Раунд (tick): 0\nТы: Auditor": {
                "actions": [
                    {
                        "type": "perform",
                        "description": "self-raise",
                        "justification": "test",
                    }
                ]
            },
            "self-raise": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "modify_reputation",
                        "args": {
                            "target_agent_id": "agent:auditor",
                            "delta": 1.0,
                            "reason": "self",
                        },
                    }
                ],
            },
        }
    )

    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=mock)
    state = asyncio.run(engine.run())

    events = [
        json.loads(line)
        for line in artifacts.events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_types = [event["event_type"] for event in events]
    assert "audit_flagged" in event_types
    assert "audit_case_opened" in event_types
    assert "audit_escalated" in event_types
    assert "reputation_frozen" in event_types
    assert "reputation_unfrozen" in event_types
    assert state.agents["agent:auditor"].reputation_frozen is False
