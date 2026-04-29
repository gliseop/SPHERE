"""Тесты правок Block 2: tick-based history, vote chain, семантический классификатор."""

from __future__ import annotations

import asyncio

from sphere_lc.auditor import RuntimeAuditor, _classify_contact_content
from sphere_lc.config import AuditRuntimeConfig
from sphere_lc.entities import EntityRecord, EntityRegistry
from sphere_lc.events import Event
from sphere_lc.ids import EntityKind
from sphere_lc.state import AgentState, WorldState


def _mk_state_with_external_pairs(pair_specs: list[tuple[str, str]]) -> WorldState:
    """Собирает state с парами internal↔external по спецификации.

    Args:
        pair_specs: Список ``(internal_id, external_id)``.

    Returns:
        Готовый ``WorldState``.
    """
    reg = EntityRegistry()
    state = WorldState(tick=0, registry=reg)
    seen_internal: set[str] = set()
    seen_external: set[str] = set()
    for internal_id, external_id in pair_specs:
        if internal_id not in seen_internal:
            reg.register(
                EntityRecord(
                    entity_id=internal_id,
                    kind=EntityKind.AGENT,
                    created_by=None,
                    created_tick=0,
                    meta={"name": internal_id},
                )
            )
            state.agents[internal_id] = AgentState(
                agent_id=internal_id,
                name=internal_id,
                internal=True,
                capabilities=["dao", "message"],
            )
            seen_internal.add(internal_id)
        if external_id not in seen_external:
            reg.register(
                EntityRecord(
                    entity_id=external_id,
                    kind=EntityKind.AGENT,
                    created_by=None,
                    created_tick=0,
                    meta={"name": external_id},
                )
            )
            state.agents[external_id] = AgentState(
                agent_id=external_id,
                name=external_id,
                internal=False,
                capabilities=["message"],
            )
            seen_external.add(external_id)
    return state


def _msg(*, tick: int, src: str, dst: str, text: str = "msg") -> Event:
    return Event(
        tick=tick,
        event_type="message_sent",
        actor_id=src,
        payload={"to_id": dst, "private": True, "content": text},
    )


def test_recall_synthetic_two_pairs_with_count_above_threshold_both_flagged() -> None:
    """Две пары с count>=4 в окне обязаны попадать в финдинги одновременно."""

    state = _mk_state_with_external_pairs(
        [("agent:off_1", "agent:contractor_a"), ("agent:off_2", "agent:contractor_b")]
    )
    state.tick = 5
    cfg = AuditRuntimeConfig(enabled=True, mode="rules", private_contact_window_ticks=5)
    auditor = RuntimeAuditor(cfg=cfg)

    recent_events: list[Event] = []
    for tick in range(1, 5):
        recent_events.append(_msg(tick=tick, src="agent:off_1", dst="agent:contractor_a"))
        recent_events.append(_msg(tick=tick, src="agent:off_2", dst="agent:contractor_b"))
    tick_events = [
        _msg(tick=5, src="agent:off_1", dst="agent:contractor_a"),
        _msg(tick=5, src="agent:off_2", dst="agent:contractor_b"),
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            pattern_events=recent_events,
        )
    )

    flagged_pairs = {
        (f.subject_agent_id, f.target_agent_id)
        for f in outcome.findings
        if f.mechanism.startswith("private_contact_frequency")
    }
    assert ("agent:off_1", "agent:contractor_a") in flagged_pairs
    assert ("agent:off_2", "agent:contractor_b") in flagged_pairs


def test_high_severity_private_contact_frequency_routes_to_collegial_review() -> None:
    """count>=5 при contact_frequency должно давать route_to_collegial_review (а не open_case)."""

    state = _mk_state_with_external_pairs(
        [("agent:off_1", "agent:contractor_a")]
    )
    state.tick = 5
    cfg = AuditRuntimeConfig(
        enabled=True,
        mode="rules",
        private_contact_window_ticks=5,
        collegial_review_enabled=True,
        min_confidence_to_review=0.65,
    )
    auditor = RuntimeAuditor(cfg=cfg)

    recent_events: list[Event] = []
    for tick in range(1, 5):
        recent_events.append(_msg(tick=tick, src="agent:off_1", dst="agent:contractor_a"))
        recent_events.append(_msg(tick=tick, src="agent:contractor_a", dst="agent:off_1"))
    tick_events = [
        _msg(tick=5, src="agent:off_1", dst="agent:contractor_a"),
        _msg(tick=5, src="agent:contractor_a", dst="agent:off_1"),
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            pattern_events=recent_events,
        )
    )

    contact_findings = [
        f for f in outcome.findings if f.mechanism.startswith("private_contact_frequency")
    ]
    assert contact_findings, "ожидается хотя бы один finding по частым контактам"
    high_finding = next((f for f in contact_findings if f.severity == "high"), None)
    assert high_finding is not None, f"ожидается severity=high, имеем {[f.severity for f in contact_findings]}"
    assert high_finding.recommended_action == "route_to_collegial_review", (
        f"high+private_contact_frequency должен идти в коллегиальное ревью, "
        f"а не {high_finding.recommended_action}"
    )


def test_classifier_artefact_handoff_downgrades_severity() -> None:
    """При доминировании передачи документов severity не выше signal_only."""

    state = _mk_state_with_external_pairs(
        [("agent:off_1", "agent:contractor_a")]
    )
    state.tick = 5
    cfg = AuditRuntimeConfig(enabled=True, mode="rules", private_contact_window_ticks=5)
    auditor = RuntimeAuditor(cfg=cfg)

    recent_events: list[Event] = []
    for tick in range(1, 5):
        recent_events.append(
            _msg(
                tick=tick,
                src="agent:off_1",
                dst="agent:contractor_a",
                text=f"Прошу прислать выписку art:7_5 и spec:00{tick}",
            )
        )
        recent_events.append(
            _msg(
                tick=tick,
                src="agent:contractor_a",
                dst="agent:off_1",
                text=f"Передаю документ work:A-00{tick}",
            )
        )
    tick_events = [
        _msg(
            tick=5,
            src="agent:off_1",
            dst="agent:contractor_a",
            text="Передайте выгрузку spec:005",
        ),
        _msg(
            tick=5,
            src="agent:contractor_a",
            dst="agent:off_1",
            text="Направляю выписку work:A-005",
        ),
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            pattern_events=recent_events,
        )
    )

    contact_findings = [
        f for f in outcome.findings if f.mechanism.startswith("private_contact_frequency")
    ]
    assert contact_findings
    finding = contact_findings[0]
    assert finding.mechanism == "private_contact_frequency:artefact_handoff"
    assert finding.severity == "medium"
    assert finding.recommended_action == "signal_only"


def test_classifier_hint_coordination_keeps_high_severity() -> None:
    """Слова-маркеры скрытой координации не позволяют downgrade severity."""

    state = _mk_state_with_external_pairs(
        [("agent:off_1", "agent:contractor_a")]
    )
    state.tick = 5
    cfg = AuditRuntimeConfig(
        enabled=True,
        mode="rules",
        private_contact_window_ticks=5,
        collegial_review_enabled=True,
        min_confidence_to_review=0.65,
    )
    auditor = RuntimeAuditor(cfg=cfg)

    coordination_text = "Поддержите меня в голосовании, не упоминайте обстоятельства, услуга за услугу"
    recent_events: list[Event] = []
    for tick in range(1, 5):
        recent_events.append(
            _msg(
                tick=tick,
                src="agent:off_1",
                dst="agent:contractor_a",
                text=coordination_text,
            )
        )
        recent_events.append(
            _msg(
                tick=tick,
                src="agent:contractor_a",
                dst="agent:off_1",
                text="Поддержу, повлияй на коллег",
            )
        )
    tick_events = [
        _msg(
            tick=5,
            src="agent:off_1",
            dst="agent:contractor_a",
            text=coordination_text,
        ),
        _msg(
            tick=5,
            src="agent:contractor_a",
            dst="agent:off_1",
            text="Поддержу, обещай поддержку",
        ),
    ]

    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            pattern_events=recent_events,
        )
    )

    contact_findings = [
        f for f in outcome.findings if f.mechanism.startswith("private_contact_frequency")
    ]
    assert contact_findings
    high_finding = next((f for f in contact_findings if f.severity == "high"), None)
    assert high_finding is not None
    assert high_finding.recommended_action == "route_to_collegial_review"
    assert ":artefact_handoff" not in high_finding.mechanism
    assert ":status_update" not in high_finding.mechanism


def test_classify_contact_content_three_classes_distinct() -> None:
    """Эвристика классификатора отличает три класса содержимого."""

    handoff = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="a",
            payload={
                "content": "Прошу прислать выписку art:7_5 и spec:001",
                "private": True,
            },
        ),
        Event(
            tick=2,
            event_type="message_sent",
            actor_id="a",
            payload={
                "content": "Передаю документ work:A-001 и заключение",
                "private": True,
            },
        ),
    ]
    status = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="a",
            payload={"content": "Подтверждаю получение, готов к встрече", "private": True},
        ),
        Event(
            tick=2,
            event_type="message_sent",
            actor_id="a",
            payload={"content": "Согласовано, ожидаю", "private": True},
        ),
    ]
    hint = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="a",
            payload={
                "content": "Поддержите меня в голосовании, не упоминайте обстоятельства",
                "private": True,
            },
        ),
    ]
    neutral = [
        Event(
            tick=1,
            event_type="message_sent",
            actor_id="a",
            payload={"content": "Привет", "private": True},
        ),
    ]

    assert _classify_contact_content(handoff) == "artefact_handoff"
    assert _classify_contact_content(status) == "status_update"
    assert _classify_contact_content(hint) == "hint_coordination"
    assert _classify_contact_content(neutral) == ""
