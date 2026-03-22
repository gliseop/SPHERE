from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magistry_lc.auditor import RuntimeAuditor
from magistry_lc.config import AuditRuntimeConfig, ScenarioConfig
from magistry_lc.dao import DaoEngine
from magistry_lc.engine import RunArtifacts, WorldEngine
from magistry_lc.entities import EntityRecord, EntityRegistry
from magistry_lc.events import Event
from magistry_lc.ids import EntityKind
from magistry_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from magistry_lc.ops import ModifyReputationOp, OpenAuditCaseOp, OpenVoteOp, UpdateAuditCaseOp
from magistry_lc.state import AgentState, AuditCase, Vote, WorldState
from magistry_lc.tracing import TraceLog


def _mk_state() -> WorldState:
    reg = EntityRegistry()
    state = WorldState(tick=0, registry=reg)
    for aid, caps in (
        ("agent:auditor", ["message"]),
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


class _TickOneAuditorProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "\"current_tick_events\": [{\"tick\": 1" in user:
            return StructuredLLMResponse(
                data=[
                    {
                        "subject_agent_id": "agent:off_2",
                        "target_agent_id": "agent:off_1",
                        "violation_type": "nomination_after_private_contact",
                        "risk_family": "preferential_treatment",
                        "confidence": 0.9,
                        "summary": "Приватный контакт перед номинацией.",
                        "mechanism": "private contact + nomination",
                        "recommended_action": "freeze_reputation_growth",
                        "related_agent_ids": ["agent:off_1"],
                        "evidence_refs": [{"tick": 1, "event_type": "vote_opened"}],
                    }
                ],
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _ReviewAuditorProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        return StructuredLLMResponse(
            data=[
                {
                    "subject_agent_id": "agent:off_1",
                    "violation_type": "support_vote_after_private_contact",
                    "risk_family": "preferential_treatment",
                    "confidence": 0.91,
                    "summary": "Есть подозрение на координацию перед голосованием.",
                    "mechanism": "private contact + support vote",
                    "recommended_action": "route_to_collegial_review",
                    "related_agent_ids": ["agent:off_2"],
                    "evidence_refs": [{"tick": 2, "event_type": "vote_cast"}],
                }
            ],
            model="mock",
        )


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
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules"))

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
    assert [event.event_type for event in outcome.events] == ["audit_flagged"]
    assert [op.__class__.__name__ for op in outcome.ops] == ["OpenAuditCaseOp"]


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
            mode="rules",
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
    assert not any(event.event_type == "audit_escalated" for event in outcome.events)
    assert not any(op.__class__.__name__ == "SetReputationFreezeOp" for op in outcome.ops)
    assert any(op.__class__.__name__ == "OpenAuditCaseOp" for op in outcome.ops)


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
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules"))

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
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules", freeze_duration_ticks=2))

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
    assert [event.event_type for event in emitted] == ["audit_case_opened", "reputation_frozen"]


def test_runtime_auditor_keeps_repeated_same_tick_findings_distinct() -> None:
    state = _mk_state()
    state.tick = 3
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules"))

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


def test_runtime_auditor_llm_mode_opens_collegial_review(tmp_path: Path) -> None:
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
    auditor = RuntimeAuditor(
        cfg=AuditRuntimeConfig(enabled=True, mode="llm", collegial_review_enabled=True, review_jury_size=1),
        llm=LLMCaller(provider=_ReviewAuditorProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
    )
    outcome = asyncio.run(auditor.inspect_tick(state=state, tick_events=[], recent_events=[]))

    assert outcome.findings
    assert any(event.event_type == "audit_flagged" for event in outcome.events)
    assert any(event.event_type == "audit_escalated" for event in outcome.events)
    assert any(isinstance(op, OpenAuditCaseOp) for op in outcome.ops)
    assert any(isinstance(op, OpenVoteOp) and op.vote_type == "audit_review" for op in outcome.ops)


def test_runtime_auditor_flags_non_escalation_under_pressure() -> None:
    state = _mk_state()
    state.agents["agent:head"] = AgentState(
        agent_id="agent:head",
        name="Head",
        internal=True,
        capabilities=["message", "dao"],
    )
    state.registry.register(
        EntityRecord(
            entity_id="agent:head",
            kind=EntityKind.AGENT,
            created_by=None,
            created_tick=0,
            meta={"name": "Head"},
        )
    )
    state.tick = 2
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules", obligation_window_ticks=3))

    recent_events = [
        Event(
            tick=1,
            event_type="world_event",
            actor_id=None,
            payload={"description": "Обнаружен возможный конфликт интересов, требуется немедленно передать материалы аудиту и комиссии."},
            audience=["aud:internal"],
        )
    ]
    tick_events = [
        Event(
            tick=2,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={
                "to_id": "agent:head",
                "private": True,
                "text": "Предлагаю быстро согласовать пакет ответа и комментарий вместо эскалации в комиссию.",
            },
            audience=["agent:off_1", "agent:head"],
        )
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
        )
    )

    assert any(finding.violation_type == "non_escalation_under_pressure" for finding in outcome.findings)
    assert any(finding.evidence_refs for finding in outcome.findings)


def test_runtime_auditor_postprocess_normalizes_taxonomy_and_binds_evidence() -> None:
    state = _mk_state()
    state.tick = 3
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules"))

    recent_events = [
        Event(
            tick=2,
            event_type="world_event",
            actor_id=None,
            payload={"description": "Обнаружен конфликт интересов и сверху требуют ускорить публикацию отчёта, иначе будут санкции."},
            audience=["aud:internal"],
        )
    ]
    tick_events = [
        Event(
            tick=3,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={
                "to_id": "chan:public",
                "private": False,
                "text": "Конфликтов интересов не выявлено, все документы уже опубликованы, процесс полностью прозрачен.",
            },
            audience=["aud:public"],
        )
    ]
    finding = auditor._make_finding(
        tick=3,
        subject_agent_id="agent:off_1",
        target_agent_id="chan:public",
        violation_type="narrative_manipulation",
        violation_type_freeform="",
        risk_family="narrative_manipulation",
        severity="medium",
        confidence=0.74,
        summary="Публичное сообщение о полной прозрачности на фоне дедлайна и конфликта.",
        mechanism="public reassurance while the deadline pressure is increasing",
        recommended_action="request_explanation",
        evidence_refs=[],
    )

    processed = auditor._postprocess_finding(
        finding=finding,
        state=state,
        tick_events=tick_events,
        recent_events=recent_events,
        current_tick=3,
    )

    assert processed is not None
    assert processed.violation_type == "partial_disclosure_under_deadline_pressure"
    assert processed.evidence_refs


def test_runtime_auditor_updates_existing_case_instead_of_opening_duplicate() -> None:
    state = _mk_state()
    state.tick = 3
    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, mode="rules"))
    finding = auditor._make_finding(
        tick=3,
        subject_agent_id="agent:off_1",
        target_agent_id="agent:off_2",
        violation_type="support_vote_after_private_contact",
        risk_family="preferential_treatment",
        severity="medium",
        confidence=0.81,
        summary="Повторный эпизод координации перед голосованием.",
        mechanism="repeat support vote after private contact",
        recommended_action="open_case",
        evidence_refs=[{"tick": 3, "event_type": "vote_cast", "target_agent_id": "agent:off_2"}],
    )
    case_id = auditor._case_id_for_finding(finding)
    state.audit_cases[case_id] = AuditCase(
        case_id=case_id,
        finding_id="finding:existing",
        created_tick=1,
        subject_agent_id="agent:off_1",
        target_agent_id="agent:off_2",
        risk_family="preferential_treatment",
        violation_type="support_vote_after_private_contact",
        summary="Старый эпизод.",
        recommended_action="open_case",
        confidence=0.7,
        evidence_refs=[{"tick": 1, "event_type": "vote_cast", "target_agent_id": "agent:off_2"}],
        finding_ids=["finding:existing"],
        episode_count=1,
        updated_tick=1,
        last_finding_tick=1,
    )

    outcome = auditor._apply_policy(state=state, findings=[finding], current_tick=3, recent_events=[])

    assert any(isinstance(op, UpdateAuditCaseOp) and op.case_id == case_id for op in outcome.ops)
    assert not any(isinstance(op, OpenAuditCaseOp) and op.case_id == case_id for op in outcome.ops)


def test_runtime_auditor_escalates_overdue_case_without_response() -> None:
    state = _mk_state()
    state.tick = 5
    auditor = RuntimeAuditor(
        cfg=AuditRuntimeConfig(
            enabled=True,
            mode="rules",
            collegial_review_enabled=False,
            response_window_ticks=2,
            case_repeat_escalation_threshold=2,
        )
    )
    state.audit_cases["audit_case:late"] = AuditCase(
        case_id="audit_case:late",
        finding_id="finding:late",
        created_tick=1,
        subject_agent_id="agent:off_1",
        target_agent_id="agent:off_2",
        risk_family="preferential_treatment",
        violation_type="support_vote_after_private_contact",
        summary="Ожидается объяснение по приватной координации.",
        recommended_action="request_explanation",
        confidence=0.82,
        finding_ids=["finding:late"],
        episode_count=1,
        updated_tick=2,
        last_finding_tick=2,
        response_requested_tick=2,
        response_due_tick=4,
    )

    outcome = auditor._apply_policy(state=state, findings=[], current_tick=5, recent_events=[])

    assert any(event.event_type == "audit_escalated" for event in outcome.events)
    assert any(isinstance(op, UpdateAuditCaseOp) for op in outcome.ops)


def test_dao_closes_audit_review_and_freezes_subject() -> None:
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
    state.tick = 3
    state.audit_cases["audit_case:f1"] = AuditCase(
        case_id="audit_case:f1",
        finding_id="finding:f1",
        created_tick=1,
        subject_agent_id="agent:off_1",
        risk_family="preferential_treatment",
        violation_type="support_vote_after_private_contact",
        summary="summary",
        recommended_action="route_to_collegial_review",
        confidence=0.9,
    )
    state.votes["vote:review_1"] = Vote(
        vote_id="vote:review_1",
        vote_type="audit_review",
        created_by="",
        created_tick=1,
        closes_tick=3,
        target_agent_id="agent:off_1",
        new_title="",
        reason="summary",
        voters=["agent:off_2", "agent:off_3"],
        votes={"agent:off_2": "yes", "agent:off_3": "yes"},
        metadata={
            "case_id": "audit_case:f1",
            "review_action": "freeze_reputation_growth",
            "subject_agent_id": "agent:off_1",
        },
    )
    dao = DaoEngine(cfg=ScenarioConfig().governance)
    ops = dao.close_votes(state)
    emitted = []
    for op in ops:
        emitted.extend(op.apply(state))

    event_types = [event.event_type for event in emitted]
    assert "vote_closed" in event_types
    assert "review_case_closed" in event_types
    assert "audit_case_closed" in event_types
    assert "reputation_frozen" in event_types


def test_engine_runtime_auditor_emits_audit_events_and_unfreezes_after_duration(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-runtime-auditor",
            "ticks": 5,
            "governance": {
                "audit": {
                    "enabled": True,
                    "mode": "llm",
                    "freeze_duration_ticks": 2,
                }
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "off1",
                    "capabilities": ["message", "dao"],
                },
                {
                    "agent_id": "agent:off_2",
                    "name": "Off 2",
                    "internal": True,
                    "persona": "off2",
                    "capabilities": ["message", "dao"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    mock = _TickOneAuditorProvider(
        structured_responses={
            "Раунд (tick): 0\nТы: Off 1": {
                "actions": [
                    {
                        "type": "send_message",
                        "to_id": "agent:off_2",
                        "text": "secret",
                        "private": True,
                        "justification": "seed private contact",
                    }
                ]
            },
            "Раунд (tick): 0\nТы: Off 2": {"actions": [{"type": "noop", "justification": ""}]},
            "Раунд (tick): 1\nТы: Off 1": {"actions": [{"type": "noop", "justification": ""}]},
            "Раунд (tick): 1\nТы: Off 2": {
                "actions": [
                    {
                        "type": "nominate_position_change",
                        "target_agent_id": "agent:off_1",
                        "new_title": "lead",
                        "reason": "promote",
                        "justification": "",
                    }
                ]
            },
            "Раунд (tick): 2\nТы: Off 1": {"actions": [{"type": "noop", "justification": ""}]},
            "Раунд (tick): 2\nТы: Off 2": {
                "actions": [
                    {
                        "type": "cast_vote",
                        "vote_id": "vote:1_1",
                        "choice": "yes",
                        "justification": "",
                    }
                ]
            },
            "Раунд (tick): 3\nТы: Off 1": {"actions": [{"type": "noop", "justification": ""}]},
            "Раунд (tick): 3\nТы: Off 2": {"actions": [{"type": "noop", "justification": ""}]},
            "Раунд (tick): 4\nТы: Off 1": {"actions": [{"type": "noop", "justification": ""}]},
            "Раунд (tick): 4\nТы: Off 2": {"actions": [{"type": "noop", "justification": ""}]},
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
    assert state.agents["agent:off_2"].reputation_frozen is False
