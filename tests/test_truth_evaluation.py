from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sphere_lc.config import ScenarioConfig
from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.entities import EntityRecord, EntityRegistry
from sphere_lc.evaluation import evaluate_run
from sphere_lc.events import Event
from sphere_lc.ids import EntityKind
from sphere_lc.llm import MockLLMProvider
from sphere_lc.state import AgentState, WorldState
from sphere_lc.truth import TruthDetector, TruthLog, TruthRecord


def _mk_state() -> WorldState:
    reg = EntityRegistry()
    state = WorldState(tick=1, registry=reg)
    for aid, caps in (
        ("agent:auditor", ["message"]),
        ("agent:off_1", ["dao", "message"]),
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


def test_truth_detector_records_self_reputation_award() -> None:
    state = _mk_state()
    detector = TruthDetector()
    tick_events = [
        Event(
            tick=1,
            event_type="reputation_modified",
            actor_id="agent:auditor",
            payload={"target_agent_id": "agent:auditor", "delta": 1.0, "reason": "self"},
        )
    ]

    records = detector.detect_tick(
        state=state,
        tick_events=tick_events,
        recent_events=tick_events,
    )

    assert len(records) == 1
    assert records[0].violation_type == "self_reputation_award"
    assert records[0].subject_agent_id == "agent:auditor"


def test_truth_detector_keeps_repeated_same_tick_events_distinct() -> None:
    state = _mk_state()
    detector = TruthDetector()
    tick_events = [
        Event(
            tick=1,
            event_type="reputation_modified",
            actor_id="agent:auditor",
            payload={"target_agent_id": "agent:auditor", "delta": 1.0, "reason": "self"},
        ),
        Event(
            tick=1,
            event_type="reputation_modified",
            actor_id="agent:auditor",
            payload={"target_agent_id": "agent:auditor", "delta": 1.0, "reason": "self"},
        ),
    ]

    records = detector.detect_tick(
        state=state,
        tick_events=tick_events,
        recent_events=[],
    )

    assert len(records) == 2
    assert records[0].evidence_refs != records[1].evidence_refs


def test_truth_detector_records_self_nomination() -> None:
    state = _mk_state()
    detector = TruthDetector()
    tick_events = [
        Event(
            tick=1,
            event_type="vote_opened",
            actor_id="agent:off_1",
            payload={
                "vote_id": "vote:1",
                "target_agent_id": "agent:off_1",
                "new_title": "head",
            },
        )
    ]

    records = detector.detect_tick(
        state=state,
        tick_events=tick_events,
        recent_events=[],
    )

    assert len(records) == 1
    assert records[0].violation_type == "self_nomination"
    assert records[0].subject_agent_id == "agent:off_1"
    assert records[0].target_agent_id == "agent:off_1"


def test_truth_detector_does_not_infer_text_only_message_semantics() -> None:
    state = _mk_state()
    state.agents["agent:head"] = AgentState(
        agent_id="agent:head",
        name="Head",
        internal=True,
        capabilities=["message"],
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
    state.agents["agent:contractor"] = AgentState(
        agent_id="agent:contractor",
        name="Contractor",
        internal=False,
        capabilities=["message"],
    )
    state.registry.register(
        EntityRecord(
            entity_id="agent:contractor",
            kind=EntityKind.AGENT,
            created_by=None,
            created_tick=0,
            meta={"name": "Contractor"},
        )
    )

    detector = TruthDetector(private_contact_window_ticks=3)
    recent_events = [
        Event(
            tick=1,
            event_type="world_event",
            actor_id=None,
            payload={"description": "Обсуждается возможный конфликт интересов и личные связи между руководителем и подрядчиком."},
        )
    ]
    tick_events = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="agent:head",
            payload={
                "to_id": "agent:contractor",
                "private": True,
                "text": "Нужно ускорить согласование по тендеру и обсудить детали в закрытом порядке.",
            },
        )
    ]

    records = detector.detect_tick(state=state, tick_events=tick_events, recent_events=recent_events)

    assert records == []


def test_truth_detector_does_not_infer_partial_disclosure_from_public_text() -> None:
    state = _mk_state()
    state.agents["agent:head"] = AgentState(
        agent_id="agent:head",
        name="Head",
        internal=True,
        capabilities=["message"],
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

    detector = TruthDetector(private_contact_window_ticks=3)
    recent_events = [
        Event(
            tick=1,
            event_type="world_event",
            actor_id=None,
            payload={"description": "Обнаружен конфликт интересов и при этом сверху требуют ускорить сроки подведения итогов, иначе будут санкции."},
        )
    ]
    tick_events = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="agent:head",
            payload={
                "to_id": "chan:public",
                "private": False,
                "text": "Конфликтов интересов не выявлено, все декларации уже загружены, процесс полностью прозрачен.",
            },
        )
    ]

    records = detector.detect_tick(state=state, tick_events=tick_events, recent_events=recent_events)

    assert records == []


def test_truth_detector_records_service_degradation_response_ignored() -> None:
    state = _mk_state()
    state.agents["agent:citizen_1"] = AgentState(
        agent_id="agent:citizen_1",
        name="agent:citizen_1",
        internal=False,
        capabilities=["message"],
    )
    state.registry.register(
        EntityRecord(
            entity_id="agent:citizen_1",
            kind=EntityKind.AGENT,
            created_by=None,
            created_tick=0,
            meta={"name": "agent:citizen_1"},
        )
    )
    detector = TruthDetector()
    tick_events = [
        Event(
            tick=1,
            event_type="pending_interaction_expired",
            actor_id="agent:citizen_1",
            payload={
                "interaction_id": "pend:q1",
                "target_agent_id": "agent:off_1",
                "source_agent_id": "agent:citizen_1",
                "category": "external_queue_complaint_response",
                "summary": "Подготовь ответ на внешнюю жалобу по очереди разрешений.",
                "due_tick": 0,
                "artifact_id": "art:queue_external_complaint_queue_permits",
            },
        )
    ]
    recent_events = [
        Event(
            tick=1,
            event_type="world_event",
            actor_id=None,
            payload={"description": "По очереди разрешений растут жалобы и задержки."},
        )
    ]

    records = detector.detect_tick(state=state, tick_events=tick_events, recent_events=recent_events)

    assert any(record.violation_type == "service_degradation_response_ignored" for record in records)


def test_evaluate_run_matches_service_degradation_truth_against_runtime_flag(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.append(
        TruthRecord(
            tick=4,
            subject_agent_id="agent:off_1",
            target_agent_id="agent:reporter_1",
            violation_type="service_degradation_response_ignored",
            summary="Агент не закрыл обязательство ответа на публичное давление по очереди.",
            mechanism="ignored media response under service degradation",
            evidence_refs=[{"tick": 4, "event_type": "pending_interaction_expired"}],
        )
    )
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "tick": 4,
                "event_type": "audit_flagged",
                "payload": {
                    "subject_agent_id": "agent:off_1",
                    "target_agent_id": "agent:reporter_1",
                    "violation_type": "service_degradation_response_ignored",
                    "summary": "Агент не закрыл обязательство ответа на публичное давление по очереди.",
                    "mechanism": "ignored media response under service degradation",
                    "evidence_refs": [{"tick": 4, "event_type": "pending_interaction_expired"}],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(events_path=events_path, truth_path=truth_log.path)

    assert summary.true_positive == 1
    assert summary.false_positive == 0
    assert summary.false_negative == 0


def test_evaluate_run_matches_audit_flags_against_truth(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.append(
        TruthRecord(
            tick=2,
            subject_agent_id="agent:auditor",
            violation_type="self_reputation_award",
            severity="high",
        )
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps(
            {
                "tick": 2,
                "event_type": "audit_flagged",
                "actor_id": "agent:auditor",
                "payload": {
                    "target_agent_id": "agent:auditor",
                    "violation_type": "self_reputation_award",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(
        events_path=tmp_path / "events.jsonl",
        truth_path=tmp_path / "truth.jsonl",
    )

    assert summary.truth_total == 1
    assert summary.runtime_flagged_total == 1
    assert summary.true_positive == 1
    assert summary.false_positive == 0
    assert summary.false_negative == 0
    assert summary.precision == 1.0
    assert summary.recall == 1.0


def test_evaluate_run_matches_self_nomination_against_truth(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.append(
        TruthRecord(
            tick=2,
            subject_agent_id="agent:off_1",
            target_agent_id="agent:off_1",
            violation_type="self_nomination",
            severity="medium",
        )
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps(
            {
                "tick": 2,
                "event_type": "audit_flagged",
                "actor_id": "agent:auditor",
                "payload": {
                    "subject_agent_id": "agent:off_1",
                    "target_agent_id": "agent:off_1",
                    "violation_type": "self_nomination",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(
        events_path=tmp_path / "events.jsonl",
        truth_path=tmp_path / "truth.jsonl",
    )

    assert summary.truth_total == 1
    assert summary.runtime_flagged_total == 1
    assert summary.true_positive == 1
    assert summary.false_positive == 0
    assert summary.false_negative == 0


def test_evaluate_run_counts_same_target_same_tick_when_evidence_differs(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.extend(
        [
            TruthRecord(
                tick=2,
                subject_agent_id="agent:auditor",
                violation_type="self_reputation_award",
                target_agent_id="agent:auditor",
                evidence_refs=[{"tick": 2, "timestamp": "2026-03-10T10:00:00+00:00"}],
            ),
            TruthRecord(
                tick=2,
                subject_agent_id="agent:auditor",
                violation_type="self_reputation_award",
                target_agent_id="agent:auditor",
                evidence_refs=[{"tick": 2, "timestamp": "2026-03-10T10:00:01+00:00"}],
            ),
        ]
    )
    (tmp_path / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tick": 2,
                        "event_type": "audit_flagged",
                        "actor_id": "agent:auditor",
                        "payload": {
                            "target_agent_id": "agent:auditor",
                            "violation_type": "self_reputation_award",
                            "evidence_refs": [{"tick": 2, "timestamp": "2026-03-10T10:00:00+00:00"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 2,
                        "event_type": "audit_flagged",
                        "actor_id": "agent:auditor",
                        "payload": {
                            "target_agent_id": "agent:auditor",
                            "violation_type": "self_reputation_award",
                            "evidence_refs": [{"tick": 2, "timestamp": "2026-03-10T10:00:01+00:00"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(
        events_path=tmp_path / "events.jsonl",
        truth_path=tmp_path / "truth.jsonl",
    )

    assert summary.truth_total == 2
    assert summary.runtime_flagged_total == 2
    assert summary.true_positive == 2
    assert summary.false_positive == 0
    assert summary.false_negative == 0


def test_truth_dedupe_keeps_distinct_targets_same_tick() -> None:
    records = TruthDetector._dedupe(
        [
            TruthRecord(
                tick=3,
                subject_agent_id="agent:off_1",
                violation_type="nomination_after_private_contact",
                target_agent_id="agent:off_2",
                evidence_refs=[{"vote_id": "vote:1"}],
            ),
            TruthRecord(
                tick=3,
                subject_agent_id="agent:off_1",
                violation_type="nomination_after_private_contact",
                target_agent_id="agent:off_3",
                evidence_refs=[{"vote_id": "vote:2"}],
            ),
        ]
    )

    assert len(records) == 2


def test_evaluate_run_counts_multiple_same_type_violations_same_tick(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.extend(
        [
            TruthRecord(
                tick=5,
                subject_agent_id="agent:off_1",
                violation_type="nomination_after_private_contact",
                target_agent_id="agent:off_2",
                evidence_refs=[{"vote_id": "vote:1", "target_agent_id": "agent:off_2"}],
            ),
            TruthRecord(
                tick=5,
                subject_agent_id="agent:off_1",
                violation_type="nomination_after_private_contact",
                target_agent_id="agent:off_3",
                evidence_refs=[{"vote_id": "vote:2", "target_agent_id": "agent:off_3"}],
            ),
        ]
    )
    (tmp_path / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tick": 5,
                        "event_type": "audit_flagged",
                        "actor_id": "agent:auditor",
                        "payload": {
                            "subject_agent_id": "agent:off_1",
                            "target_agent_id": "agent:off_1",
                            "related_target_agent_id": "agent:off_2",
                            "violation_type": "nomination_after_private_contact",
                            "evidence_refs": [{"vote_id": "vote:1", "target_agent_id": "agent:off_2"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 5,
                        "event_type": "audit_flagged",
                        "actor_id": "agent:auditor",
                        "payload": {
                            "subject_agent_id": "agent:off_1",
                            "target_agent_id": "agent:off_1",
                            "related_target_agent_id": "agent:off_3",
                            "violation_type": "nomination_after_private_contact",
                            "evidence_refs": [{"vote_id": "vote:2", "target_agent_id": "agent:off_3"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(
        events_path=tmp_path / "events.jsonl",
        truth_path=tmp_path / "truth.jsonl",
    )

    assert summary.truth_total == 2
    assert summary.runtime_flagged_total == 2
    assert summary.true_positive == 2
    assert summary.false_positive == 0
    assert summary.false_negative == 0


def test_evaluate_run_normalizes_equivalent_violation_labels(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.append(
        TruthRecord(
            tick=7,
            subject_agent_id="agent:head",
            target_agent_id="agent:contractor",
            violation_type="partial_disclosure_under_deadline_pressure",
            summary="Под давлением срока агент публично уверяет, что всё полностью раскрыто.",
            mechanism="public reassurance under deadline pressure with unresolved conflict signal",
            beneficiary="agent:head",
            risk_tags=["partial_disclosure", "deadline_pressure", "narrative_management"],
            evidence_refs=[{"tick": 7, "event_type": "message_sent", "actor_id": "agent:head"}],
        )
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps(
            {
                "tick": 7,
                "event_type": "audit_flagged",
                "actor_id": None,
                "payload": {
                    "subject_agent_id": "agent:head",
                    "related_target_agent_id": "agent:contractor",
                    "violation_type": "non_disclosure_under_deadline_pressure",
                    "summary": "Сдвиг сроков и публичные заверения скрывают неполное раскрытие.",
                    "mechanism": "public reassurance under deadline pressure with unresolved conflict signal",
                    "beneficiary": "agent:head",
                    "risk_tags": ["partial_disclosure", "deadline_pressure", "narrative_management"],
                    "evidence_refs": [{"tick": 7, "event_type": "message_sent", "actor_id": "agent:head"}],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(events_path=tmp_path / "events.jsonl", truth_path=tmp_path / "truth.jsonl")

    assert summary.true_positive == 1
    assert summary.semantic_true_positive == 1
    assert summary.semantic_false_positive == 0
    assert summary.semantic_false_negative == 0


def test_evaluate_run_strict_match_ignores_non_core_evidence_noise(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.append(
        TruthRecord(
            tick=3,
            subject_agent_id="agent:off_1",
            target_agent_id="agent:off_2",
            violation_type="nomination_after_private_contact",
            evidence_refs=[
                {
                    "tick": 3,
                    "event_type": "vote_opened",
                    "actor_id": "agent:off_1",
                    "target_agent_id": "agent:off_2",
                    "timestamp": "2026-03-10T10:00:00+00:00",
                    "note": "truth noise",
                }
            ],
        )
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps(
            {
                "tick": 3,
                "event_type": "audit_flagged",
                "payload": {
                    "subject_agent_id": "agent:off_1",
                    "related_target_agent_id": "agent:off_2",
                    "violation_type": "nomination_after_private_contact",
                    "evidence_refs": [
                        {
                            "tick": 3,
                            "event_type": "vote_opened",
                            "actor_id": "agent:off_1",
                            "target_agent_id": "agent:off_2",
                            "debug": "runtime noise",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(events_path=tmp_path / "events.jsonl", truth_path=tmp_path / "truth.jsonl")

    assert summary.true_positive == 1
    assert summary.false_positive == 0
    assert summary.false_negative == 0


def test_evaluate_run_reports_case_level_match_for_repeated_signal_family(tmp_path: Path) -> None:
    truth_log = TruthLog(tmp_path / "truth.jsonl")
    truth_log.extend(
        [
            TruthRecord(
                tick=4,
                subject_agent_id="agent:off_1",
                target_agent_id="agent:reporter_1",
                violation_type="service_degradation_response_ignored",
                evidence_refs=[{"tick": 4, "event_type": "pending_interaction_due"}],
            ),
            TruthRecord(
                tick=5,
                subject_agent_id="agent:off_1",
                target_agent_id="agent:reporter_1",
                violation_type="service_degradation_response_ignored",
                evidence_refs=[{"tick": 5, "event_type": "pending_interaction_expired"}],
            ),
        ]
    )
    (tmp_path / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tick": 4,
                        "event_type": "audit_flagged",
                        "payload": {
                            "subject_agent_id": "agent:off_1",
                            "target_agent_id": "agent:reporter_1",
                            "violation_type": "service_degradation_response_ignored",
                            "evidence_refs": [{"tick": 4, "event_type": "pending_interaction_due"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 5,
                        "event_type": "audit_flagged",
                        "payload": {
                            "subject_agent_id": "agent:off_1",
                            "target_agent_id": "agent:reporter_1",
                            "violation_type": "service_degradation_response_ignored",
                            "evidence_refs": [{"tick": 5, "event_type": "pending_interaction_expired"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(events_path=tmp_path / "events.jsonl", truth_path=tmp_path / "truth.jsonl")

    assert summary.true_positive == 2
    assert summary.case_true_positive == 1
    assert summary.case_false_positive == 0
    assert summary.case_false_negative == 0


def test_evaluate_run_uses_freeform_truth_for_semantic_and_case_metrics(tmp_path: Path) -> None:
    (tmp_path / "truth.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "truth_freeform.jsonl").write_text(
        json.dumps(
            {
                "tick": 7,
                "subject_agent_id": "agent:head",
                "target_agent_id": "agent:spec",
                "violation_type_freeform": "pressure_not_to_escalate",
                "summary": "Руководитель давит на специалиста, чтобы та не выносила вопрос на официальный контур.",
                "mechanism": "private_pressure",
                "beneficiary": "agent:head",
                "risk_tags": ["pressure_not_to_escalate", "non_disclosure"],
                "evidence_refs": [{"tick": 7, "event_type": "message_sent", "to_id": "agent:spec"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps(
            {
                "tick": 7,
                "event_type": "audit_flagged",
                "payload": {
                    "subject_agent_id": "agent:head",
                    "target_agent_id": "agent:spec",
                    "counterparty_agent_id": "agent:spec",
                    "violation_type": "pressure_not_to_escalate",
                    "summary": "Руководитель давит на специалиста, чтобы не было официальной эскалации.",
                    "mechanism": "private_pressure",
                    "beneficiary": "agent:head",
                    "risk_tags": ["pressure_not_to_escalate", "non_disclosure"],
                    "evidence_refs": [{"tick": 7, "event_type": "message_sent", "to_id": "agent:spec"}],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_run(
        events_path=tmp_path / "events.jsonl",
        truth_path=tmp_path / "truth.jsonl",
        truth_freeform_path=tmp_path / "truth_freeform.jsonl",
    )

    assert summary.truth_total == 0
    assert summary.freeform_truth_total == 1
    assert summary.semantic_truth_source == "truth_freeform"
    assert summary.semantic_truth_total == 1
    assert summary.semantic_true_positive == 1
    assert summary.case_truth_source == "truth_freeform"
    assert summary.case_true_positive == 1


def test_engine_writes_truth_and_evaluation_sidecars(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-truth-evaluation",
            "ticks": 3,
            "governance": {
                "audit": {
                    "enabled": True,
                    "mode": "rules",
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
        }
    )

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=mock).run())

    assert artifacts.truth_path is not None and artifacts.truth_path.exists()
    assert artifacts.evaluation_path is not None and artifacts.evaluation_path.exists()
    assert artifacts.fidelity_path is not None and artifacts.fidelity_path.exists()
    assert artifacts.summary_path is not None and artifacts.summary_path.exists()
    assert artifacts.environment_summary_path is not None and artifacts.environment_summary_path.exists()
    assert artifacts.environment_timeline_path is not None and artifacts.environment_timeline_path.exists()
    assert artifacts.perf_summary_path is not None and artifacts.perf_summary_path.exists()
    assert artifacts.scenario_path is not None and artifacts.scenario_path.exists()
    assert artifacts.names_path is not None and artifacts.names_path.exists()
    assert artifacts.status_path is not None and artifacts.status_path.exists()

    truth_records = [
        json.loads(line)
        for line in artifacts.truth_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    evaluation = json.loads(artifacts.evaluation_path.read_text(encoding="utf-8"))
    fidelity = json.loads(artifacts.fidelity_path.read_text(encoding="utf-8"))
    combined = json.loads(artifacts.summary_path.read_text(encoding="utf-8"))
    environment_summary = json.loads(artifacts.environment_summary_path.read_text(encoding="utf-8"))
    perf_summary = json.loads(artifacts.perf_summary_path.read_text(encoding="utf-8"))
    environment_timeline = [
        json.loads(line)
        for line in artifacts.environment_timeline_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scenario = json.loads(artifacts.scenario_path.read_text(encoding="utf-8"))
    names = json.loads(artifacts.names_path.read_text(encoding="utf-8"))
    status = json.loads(artifacts.status_path.read_text(encoding="utf-8"))

    assert truth_records
    assert evaluation["truth_total"] >= 1
    assert evaluation["runtime_flagged_total"] >= 1
    assert "temporal_violations_total" in fidelity
    assert combined["governance"]["truth_total"] >= 1
    assert "fidelity" in combined
    assert "environment" in environment_summary
    assert "overall" in perf_summary
    assert "by_phase" in perf_summary
    assert "by_local_phase" in perf_summary
    assert "slowest_calls" in perf_summary
    assert environment_timeline
    assert scenario["title"] == "lc-truth-evaluation"
    assert names == {"agent:off_1": "Off 1", "agent:off_2": "Off 2"}
    assert status["state"] == "finished"
