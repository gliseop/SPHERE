from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sphere_lc.config import ScenarioConfig
from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.entities import EntityRecord, EntityRegistry
from sphere_lc.evaluation import augment_evaluation_with_semantic_judge, evaluate_run
from sphere_lc.events import Event
from sphere_lc.fidelity import FidelitySummary, augment_fidelity_with_semantic_judge
from sphere_lc.ids import EntityKind
from sphere_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from sphere_lc.state import AgentState, Vote, WorldState
from sphere_lc.tracing import TraceLog
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


class _SemanticRealismCounterProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.last_user = ""

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        self.last_user = user
        if '"unknown_to_id_count": 2' in user and '"dependency_missing_count": 2' in user:
            return StructuredLLMResponse(
                data={
                    "findings": [
                        {
                            "category": "followup_gap",
                            "severity": "high",
                            "summary": "Симуляция теряет коммуникацию и внешнее давление из-за повторяющихся identity/dependency failures.",
                            "evidence_refs": [
                                {"tick": 0, "event_type": "arbiter_rejected", "actor_id": "agent:off_1"},
                                {"tick": 1, "event_type": "worldgen_artifact_dependency_missing"},
                            ],
                        }
                    ]
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"findings": []}, model="mock")


class _EvaluationSemanticJudgeProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "post-hoc judge соответствия runtime-аудита и truth-layer" in system:
            return StructuredLLMResponse(
                data={
                    "semantic_matches": [{"truth_index": 0, "signal_index": 0}],
                    "case_matches": [{"truth_case_index": 0, "signal_case_index": 0}],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


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


def test_truth_detector_counts_in_person_contact_for_vote_pattern() -> None:
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
    detector = TruthDetector(private_contact_window_ticks=3)
    recent_events = [
        Event(
            tick=1,
            event_type="narrative_action",
            actor_id="agent:off_1",
            payload={
                "action_kind": "in_person_contact",
                "description": "Личный разговор перед голосованием.",
                "counterparty_agent_id": "agent:off_2",
            },
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

    records = detector.detect_tick(state=state, tick_events=tick_events, recent_events=recent_events)

    assert records
    assert records[0].violation_type == "support_vote_after_private_contact"


def test_truth_detector_detect_contact_patterns_includes_current_tick_events() -> None:
    state = _mk_state()
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
            meta={"name": "agent:contractor"},
        )
    )
    detector = TruthDetector(private_contact_window_ticks=3)
    all_events = [
        Event(
            tick=2,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={"to_id": "agent:contractor", "private": True, "text": "one"},
        ),
        Event(
            tick=3,
            event_type="message_sent",
            actor_id="agent:contractor",
            payload={"to_id": "agent:off_1", "private": True, "text": "two"},
        ),
        Event(
            tick=4,
            event_type="message_sent",
            actor_id="agent:off_1",
            payload={"to_id": "agent:contractor", "private": True, "text": "three"},
        ),
        Event(
            tick=5,
            event_type="message_sent",
            actor_id="agent:contractor",
            payload={"to_id": "agent:off_1", "private": True, "text": "four"},
        ),
    ]

    records = detector.detect_contact_patterns(
        state=state,
        all_events=all_events,
        tick=5,
    )

    assert records
    assert records[0].violation_type == "conflict_of_interest"
    assert records[0].target_agent_id == "agent:contractor"


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
    summary = asyncio.run(
        augment_evaluation_with_semantic_judge(
            summary=summary,
            llm=LLMCaller(provider=_EvaluationSemanticJudgeProvider(), trace=TraceLog(tmp_path / "trace_semantic.jsonl")),
            events_path=tmp_path / "events.jsonl",
            truth_path=tmp_path / "truth.jsonl",
            truth_freeform_path=None,
            scenario_description="equivalent semantic label test",
        )
    )

    assert summary.true_positive == 0
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
    summary = asyncio.run(
        augment_evaluation_with_semantic_judge(
            summary=summary,
            llm=LLMCaller(provider=_EvaluationSemanticJudgeProvider(), trace=TraceLog(tmp_path / "trace_case.jsonl")),
            events_path=tmp_path / "events.jsonl",
            truth_path=tmp_path / "truth.jsonl",
            truth_freeform_path=tmp_path / "truth_freeform.jsonl",
            scenario_description="freeform truth semantic/case evaluation test",
        )
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
            "Сегодняшний рабочий день: 0.\nТы — Off 1.": {
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
            "Сегодняшний рабочий день: 0.\nТы — Off 2.": {"actions": [{"type": "noop", "justification": ""}]},
            "Сегодняшний рабочий день: 1.\nТы — Off 1.": {"actions": [{"type": "noop", "justification": ""}]},
            "Сегодняшний рабочий день: 1.\nТы — Off 2.": {
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
            "Сегодняшний рабочий день: 2.\nТы — Off 1.": {"actions": [{"type": "noop", "justification": ""}]},
            "Сегодняшний рабочий день: 2.\nТы — Off 2.": {
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


def test_fidelity_semantic_judge_augments_summary(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tick": 0,
                        "event_type": "work_note_added",
                        "actor_id": "agent:off_1",
                        "payload": {"work_id": "work:1", "text": "Замечаний не выявлено."},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 0,
                        "event_type": "message_sent",
                        "actor_id": "agent:off_1",
                        "payload": {"to_id": "chan:oversight", "private": False, "text": "Проверка завершена."},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = FidelitySummary(by_metric={})
    llm = LLMCaller(
        provider=MockLLMProvider(
            structured_responses={
                '"scenario_description": "test semantic realism"': {
                    "findings": [
                        {
                            "category": "documentary_overclaim",
                            "severity": "high",
                            "summary": "Документ утверждает финальный результат без достаточного мирового следа.",
                            "evidence_refs": [{"tick": 0, "event_type": "work_note_added", "actor_id": "agent:off_1"}],
                        }
                    ]
                }
            }
        ),
        trace=TraceLog(tmp_path / "trace.jsonl"),
    )

    augmented = asyncio.run(
        augment_fidelity_with_semantic_judge(
            summary=summary,
            llm=llm,
            events_path=events_path,
            scenario_description="test semantic realism",
        )
    )

    assert augmented.semantic_realism_findings_total == 1
    assert augmented.semantic_realism_by_category["documentary_overclaim"] == 1
    assert augmented.semantic_realism_findings[0]["category"] == "documentary_overclaim"


def test_fidelity_semantic_judge_uses_structural_stall_counters(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tick": 0,
                        "event_type": "arbiter_rejected",
                        "actor_id": "agent:off_1",
                        "payload": {"reason": "perform_op_invalid:send_message:ValueError:unknown to_id"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 1,
                        "event_type": "arbiter_rejected",
                        "actor_id": "agent:off_1",
                        "payload": {"reason": "perform_op_invalid:send_message:ValueError:unknown to_id"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 1,
                        "event_type": "worldgen_artifact_dependency_missing",
                        "actor_id": None,
                        "payload": {"artifact_id": "art:1", "missing_dependencies": ["org:gazette"]},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 2,
                        "event_type": "worldgen_artifact_dependency_missing",
                        "actor_id": None,
                        "payload": {"artifact_id": "art:2", "missing_dependencies": ["org:gazette"]},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = FidelitySummary(by_metric={})
    provider = _SemanticRealismCounterProvider()
    llm = LLMCaller(provider=provider, trace=TraceLog(tmp_path / "trace.jsonl"))

    augmented = asyncio.run(
        augment_fidelity_with_semantic_judge(
            summary=summary,
            llm=llm,
            events_path=events_path,
            scenario_description="test structural stall realism",
        )
    )

    assert '"unknown_to_id_count": 2' in provider.last_user
    assert '"dependency_missing_count": 2' in provider.last_user
    assert augmented.semantic_realism_findings_total == 1


def test_fidelity_semantic_judge_does_not_penalize_observation_only(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tick": 0,
                        "event_type": "arbiter_approved",
                        "actor_id": "agent:off_1",
                        "payload": {"reason": "observation_only", "action_type": "perform"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tick": 1,
                        "event_type": "arbiter_approved",
                        "actor_id": "agent:off_1",
                        "payload": {"reason": "observation_only", "action_type": "perform"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = FidelitySummary(by_metric={})
    llm = LLMCaller(
        provider=MockLLMProvider(structured_responses={'"scenario_description": "test observation realism"': {"findings": []}}),
        trace=TraceLog(tmp_path / "trace.jsonl"),
    )

    augmented = asyncio.run(
        augment_fidelity_with_semantic_judge(
            summary=summary,
            llm=llm,
            events_path=events_path,
            scenario_description="test observation realism",
        )
    )

    assert augmented.semantic_realism_findings_total == 0

