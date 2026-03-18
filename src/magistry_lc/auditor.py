"""Runtime-аудит governance-сигналов в ходе симуляции."""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import AuditRuntimeConfig
from .events import Event
from .ids import EntityKind, INTERNAL_AUDIENCE, make_id, normalize_slug
from .llm import LLMCaller
from .ops import OpenAuditCaseOp, OpenVoteOp, SetReputationFreezeOp, StateOp
from .state import WorldState


_RISK_FAMILIES = {
    "conflict_of_interest",
    "preferential_treatment",
    "non_disclosure",
    "pressure_not_to_escalate",
    "process_manipulation",
    "narrative_manipulation",
    "governance_abuse",
    "other",
}
_RECOMMENDED_ACTIONS = {
    "none",
    "signal_only",
    "open_case",
    "request_explanation",
    "request_documents",
    "freeze_reputation_growth",
    "route_to_collegial_review",
    "heightened_monitoring",
    "close_case",
}


class AuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    tick: int
    subject_agent_id: str
    violation_type: str
    violation_type_freeform: str = ""
    risk_family: str = "other"
    severity: Literal["low", "medium", "high"] = "medium"
    confidence: float
    summary: str
    mechanism: str = ""
    beneficiary: str | None = None
    recommended_action: Literal[
        "none",
        "signal_only",
        "open_case",
        "request_explanation",
        "request_documents",
        "freeze_reputation_growth",
        "route_to_collegial_review",
        "heightened_monitoring",
        "close_case",
    ] = "signal_only"
    target_agent_id: str | None = None
    related_agent_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class AuditOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    findings: list[AuditFinding] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    ops: list[Any] = Field(default_factory=list)


class _RawAuditFindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_agent_id: str
    target_agent_id: str | None = None
    violation_type: str
    violation_type_freeform: str = ""
    risk_family: str = "other"
    severity: Literal["low", "medium", "high"] = "medium"
    confidence: float
    summary: str
    mechanism: str = ""
    beneficiary: str | None = None
    recommended_action: Literal[
        "none",
        "signal_only",
        "open_case",
        "request_explanation",
        "request_documents",
        "freeze_reputation_growth",
        "route_to_collegial_review",
        "heightened_monitoring",
        "close_case",
    ] = "signal_only"
    related_agent_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


def _audit_schema(*, max_findings: int) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": max(0, max_findings),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "subject_agent_id": {"type": "string"},
                "target_agent_id": {"type": ["string", "null"]},
                "violation_type": {"type": "string"},
                "violation_type_freeform": {"type": "string"},
                "risk_family": {"type": "string", "enum": sorted(_RISK_FAMILIES)},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "summary": {"type": "string"},
                "mechanism": {"type": "string"},
                "beneficiary": {"type": ["string", "null"]},
                "recommended_action": {"type": "string", "enum": sorted(_RECOMMENDED_ACTIONS)},
                "related_agent_ids": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": {"type": "array", "items": {"type": "object"}},
            },
            "required": [
                "subject_agent_id",
                "violation_type",
                "risk_family",
                "confidence",
                "summary",
                "recommended_action",
            ],
        },
    }


@dataclass(slots=True)
class RuntimeAuditor:
    """LLM-first runtime-аудитор с детерминированным actuator-слоем."""

    cfg: AuditRuntimeConfig
    llm: LLMCaller | None = None
    temperature: float = 0.0

    async def inspect_tick(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
    ) -> AuditOutcome:
        if not self.cfg.enabled:
            return AuditOutcome()
        current_tick = int(state.tick)
        findings = await self._collect_findings(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        if not findings:
            return AuditOutcome()
        findings.sort(key=lambda item: (-float(item.confidence), item.risk_family, item.subject_agent_id))
        findings = findings[: self.cfg.max_findings_per_tick]
        return self._apply_policy(state=state, findings=findings, current_tick=current_tick)

    async def _collect_findings(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
    ) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        mode = self.cfg.mode
        if mode in ("rules", "hybrid") or self.llm is None:
            findings.extend(
                self._rule_findings(
                    state=state,
                    tick_events=tick_events,
                    recent_events=recent_events,
                    current_tick=current_tick,
                )
            )
        if mode in ("llm", "hybrid") and self.llm is not None:
            findings.extend(
                await self._llm_findings(
                    state=state,
                    tick_events=tick_events,
                    recent_events=recent_events,
                    current_tick=current_tick,
                )
            )
        return self._dedupe_findings(findings)

    async def _llm_findings(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
    ) -> list[AuditFinding]:
        if self.llm is None:
            return []
        system = (
            "Ты — online AI-аудитор организационного процесса.\n"
            "Выявляй значимые сигналы риска по уже совершённым действиям текущего тика.\n"
            "Используй canonical violation_type, если он очевиден (например: self_nomination, support_vote_after_private_contact, "
            "preferential_treatment_for_connected_actor, non_escalation_under_pressure, partial_disclosure_under_deadline_pressure).\n"
            "Если canonical label неочевиден, используй ближайший violation_type, а detail вынеси в violation_type_freeform и summary.\n"
            "Предпочитай мягкие governance-actions: signal_only, open_case, request_explanation, request_documents, heightened_monitoring.\n"
            "freeze_reputation_growth используй при высокой уверенности. route_to_collegial_review используй для спорных high-stakes кейсов.\n"
            "Не предлагай штраф репутации как основной путь.\n"
            "Ответ: JSON-массив по схеме.\n"
        )
        user = json.dumps(
            {
                "tick": current_tick,
                "access_policy": self.cfg.access_policy,
                "state_snapshot": self._state_snapshot(state),
                "open_audit_cases": self._open_cases_snapshot(state),
                "current_tick_events": self._sanitize_events(state=state, events=tick_events),
                "recent_events": self._sanitize_events(
                    state=state,
                    events=recent_events[-self.cfg.lookback_events :],
                ),
                "private_contact_pairs": self._private_contact_pairs(
                    recent_events=recent_events,
                    current_tick=current_tick,
                ),
            },
            ensure_ascii=False,
        )
        resp = await self.llm.generate_structured(
            role="auditor",
            name="runtime_auditor",
            tick=current_tick,
            system=system,
            user=user,
            schema=_audit_schema(max_findings=self.cfg.max_findings_per_tick),
            temperature=self.temperature,
        )
        if not isinstance(resp.data, list):
            return []

        findings: list[AuditFinding] = []
        for item in resp.data:
            try:
                raw = _RawAuditFindingModel.model_validate(item)
            except Exception:
                continue
            if raw.subject_agent_id not in state.agents:
                continue
            findings.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=raw.subject_agent_id,
                    target_agent_id=raw.target_agent_id,
                    violation_type=raw.violation_type.strip(),
                    violation_type_freeform=(raw.violation_type_freeform or "").strip(),
                    risk_family=(raw.risk_family or "other").strip(),
                    severity=raw.severity,
                    confidence=raw.confidence,
                    summary=raw.summary.strip(),
                    mechanism=(raw.mechanism or "").strip(),
                    beneficiary=raw.beneficiary,
                    recommended_action=raw.recommended_action,
                    related_agent_ids=[aid for aid in raw.related_agent_ids if aid in state.agents],
                    evidence_refs=list(raw.evidence_refs),
                )
            )
        return findings

    def _apply_policy(
        self,
        *,
        state: WorldState,
        findings: list[AuditFinding],
        current_tick: int,
    ) -> AuditOutcome:
        actor_id = self.cfg.actor_id
        events: list[Event] = []
        ops: list[StateOp] = []
        known_cases = set(state.audit_cases.keys())
        opening_cases: set[str] = set()
        opening_reviews: set[str] = set()

        for finding in findings:
            if float(finding.confidence) < self.cfg.min_confidence_to_flag:
                continue
            case_id = f"audit_case:{finding.finding_id}"
            payload = self._finding_payload(finding=finding, case_id=case_id)
            events.append(
                Event(
                    tick=current_tick,
                    event_type="audit_flagged",
                    actor_id=actor_id,
                    payload=payload,
                    audience=[INTERNAL_AUDIENCE],
                )
            )

            open_case_like = finding.recommended_action in {
                "open_case",
                "request_explanation",
                "request_documents",
                "freeze_reputation_growth",
                "route_to_collegial_review",
                "heightened_monitoring",
            }
            if (
                open_case_like
                and float(finding.confidence) >= self.cfg.min_confidence_to_open_case
                and case_id not in known_cases
                and case_id not in opening_cases
            ):
                ops.append(
                    OpenAuditCaseOp(
                        actor_id=actor_id,
                        case_id=case_id,
                        finding_id=finding.finding_id,
                        subject_agent_id=finding.subject_agent_id,
                        target_agent_id=finding.target_agent_id,
                        risk_family=finding.risk_family,
                        violation_type=finding.violation_type,
                        summary=finding.summary,
                        recommended_action=finding.recommended_action,
                        confidence=finding.confidence,
                        beneficiary=finding.beneficiary,
                        related_agent_ids=list(finding.related_agent_ids),
                        evidence_refs=list(finding.evidence_refs),
                    )
                )
                opening_cases.add(case_id)

            if finding.recommended_action == "request_explanation":
                events.append(Event(tick=current_tick, event_type="audit_explanation_requested", actor_id=actor_id, payload=payload, audience=[INTERNAL_AUDIENCE]))
            elif finding.recommended_action == "request_documents":
                events.append(Event(tick=current_tick, event_type="audit_documents_requested", actor_id=actor_id, payload=payload, audience=[INTERNAL_AUDIENCE]))
            elif finding.recommended_action == "heightened_monitoring":
                events.append(Event(tick=current_tick, event_type="audit_monitoring_enabled", actor_id=actor_id, payload=payload, audience=[INTERNAL_AUDIENCE]))
            elif finding.recommended_action == "freeze_reputation_growth":
                subject = state.agents.get(finding.subject_agent_id)
                if (
                    self.cfg.reputation_freeze_enabled
                    and subject is not None
                    and subject.internal
                    and not subject.reputation_frozen
                    and float(finding.confidence) >= self.cfg.min_confidence_to_freeze
                ):
                    until_tick = current_tick + int(self.cfg.freeze_duration_ticks) if self.cfg.freeze_duration_ticks > 0 else None
                    events.append(
                        Event(
                            tick=current_tick,
                            event_type="audit_escalated",
                            actor_id=actor_id,
                            payload={**payload, "route": "reputation_freeze_growth", "until_tick": until_tick},
                            audience=[INTERNAL_AUDIENCE],
                        )
                    )
                    ops.append(
                        SetReputationFreezeOp(
                            actor_id=actor_id,
                            target_agent_id=finding.subject_agent_id,
                            frozen=True,
                            reason=finding.violation_type,
                            until_tick=until_tick,
                        )
                    )
            elif finding.recommended_action == "route_to_collegial_review":
                review_vote_id = f"review:{finding.finding_id}"
                if review_vote_id not in opening_reviews:
                    review_ops, review_events = self._open_collegial_review(
                        state=state,
                        finding=finding,
                        actor_id=actor_id,
                        case_id=case_id,
                        current_tick=current_tick,
                    )
                    if review_ops:
                        opening_reviews.add(review_vote_id)
                    ops.extend(review_ops)
                    events.extend(review_events)

        return AuditOutcome(findings=findings, events=events, ops=ops)

    def _open_collegial_review(
        self,
        *,
        state: WorldState,
        finding: AuditFinding,
        actor_id: str | None,
        case_id: str,
        current_tick: int,
    ) -> tuple[list[StateOp], list[Event]]:
        if not self.cfg.collegial_review_enabled or float(finding.confidence) < self.cfg.min_confidence_to_review:
            return [], []
        reviewers = self._select_reviewers(
            state=state,
            case_id=case_id,
            exclude_agent_ids={finding.subject_agent_id, *(finding.related_agent_ids or [])},
        )
        if not reviewers:
            return [], []
        vote_id = make_id(EntityKind.VOTE, normalize_slug(f"review_{finding.finding_id}", fallback="audit_review"))
        metadata = {
            "case_id": case_id,
            "review_action": "freeze_reputation_growth",
            "subject_agent_id": finding.subject_agent_id,
            "risk_family": finding.risk_family,
            "violation_type": finding.violation_type,
        }
        op = OpenVoteOp(
            vote_id=vote_id,
            created_by=actor_id or "",
            created_tick=current_tick,
            closes_tick=current_tick + max(1, int(self.cfg.freeze_duration_ticks)),
            target_agent_id=finding.subject_agent_id,
            new_title="",
            reason=finding.summary,
            voters=list(reviewers),
            vote_type="audit_review",
            summary=finding.summary,
            metadata=metadata,
        )
        event = Event(
            tick=current_tick,
            event_type="audit_escalated",
            actor_id=actor_id,
            payload={
                "case_id": case_id,
                "subject_agent_id": finding.subject_agent_id,
                "violation_type": finding.violation_type,
                "risk_family": finding.risk_family,
                "route": "collegial_review",
                "reviewers": list(reviewers),
            },
            audience=[INTERNAL_AUDIENCE],
        )
        return [op], [event]

    def _select_reviewers(
        self,
        *,
        state: WorldState,
        case_id: str,
        exclude_agent_ids: set[str],
    ) -> list[str]:
        candidates: list[str] = []
        for aid in sorted(state.agents.keys()):
            if aid in exclude_agent_ids:
                continue
            agent = state.agents[aid]
            if not agent.internal or "dao" not in agent.capabilities:
                continue
            candidates.append(aid)
        if len(candidates) <= self.cfg.review_jury_size:
            return candidates
        rnd = random.Random(int(hashlib.sha1(case_id.encode("utf-8")).hexdigest(), 16))
        picked = list(candidates)
        rnd.shuffle(picked)
        return sorted(picked[: self.cfg.review_jury_size])

    def _state_snapshot(self, state: WorldState) -> dict[str, Any]:
        agents = []
        for aid in sorted(state.agents.keys()):
            agent = state.agents[aid]
            agents.append(
                {
                    "agent_id": aid,
                    "name": agent.name,
                    "internal": bool(agent.internal),
                    "title": agent.title if agent.internal else "",
                    "reputation_frozen": bool(agent.reputation_frozen) if agent.internal else None,
                    "reputation_frozen_until_tick": agent.reputation_frozen_until_tick if agent.internal else None,
                }
            )
        work_items = []
        for wid in sorted(state.work_items.keys())[:20]:
            work = state.work_items[wid]
            work_items.append(
                {
                    "work_id": wid,
                    "type": work.work_type,
                    "title": work.title,
                    "status": work.status,
                    "participants": list(work.participants),
                }
            )
        votes = []
        for vid, vote in sorted(state.votes.items()):
            if vote.status != "open":
                continue
            votes.append(
                {
                    "vote_id": vid,
                    "vote_type": vote.vote_type,
                    "target_agent_id": vote.target_agent_id,
                    "reason": vote.reason,
                    "summary": vote.metadata.get("summary", ""),
                    "closes_tick": vote.closes_tick,
                }
            )
        return {"agents": agents, "open_work_items": work_items, "open_votes": votes}

    def _open_cases_snapshot(self, state: WorldState) -> list[dict[str, Any]]:
        rows = []
        for cid, case in sorted(state.audit_cases.items()):
            if case.status == "closed":
                continue
            rows.append(
                {
                    "case_id": cid,
                    "subject_agent_id": case.subject_agent_id,
                    "risk_family": case.risk_family,
                    "violation_type": case.violation_type,
                    "summary": case.summary,
                    "recommended_action": case.recommended_action,
                    "review_vote_id": case.review_vote_id,
                    "monitoring": case.monitoring,
                }
            )
        return rows

    def _sanitize_events(self, *, state: WorldState, events: list[Event]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in events:
            payload = dict(event.payload or {})
            if event.event_type == "message_sent" and bool(payload.get("private", True)):
                text = str(payload.get("text") or "")
                keep_text = False
                if self.cfg.access_policy == "full_internal":
                    keep_text = True
                elif self.cfg.access_policy == "internal":
                    sender = state.agents.get(str(event.actor_id or ""))
                    target = state.agents.get(str(payload.get("to_id") or ""))
                    keep_text = bool(sender is not None and target is not None and sender.internal and target.internal)
                if keep_text:
                    payload["text"] = _truncate(text, 400)
                else:
                    payload.pop("text", None)
                    payload["text_redacted"] = True
                    payload["text_len"] = len(text)
            elif "text" in payload:
                payload["text"] = _truncate(str(payload.get("text") or ""), 400)
            if "description" in payload:
                payload["description"] = _truncate(str(payload.get("description") or ""), 500)
            rows.append(
                {
                    "tick": int(event.tick),
                    "event_type": event.event_type,
                    "actor_id": event.actor_id,
                    "payload": payload,
                    "audience": list(event.audience),
                }
            )
        return rows

    def _private_contact_pairs(self, *, recent_events: list[Event], current_tick: int) -> list[dict[str, Any]]:
        low_tick = current_tick - int(self.cfg.private_contact_window_ticks)
        pairs: dict[tuple[str, str], dict[str, Any]] = {}
        for event in recent_events:
            if event.event_type != "message_sent" or int(event.tick) < low_tick:
                continue
            payload = event.payload or {}
            if not bool(payload.get("private", True)):
                continue
            left = str(event.actor_id or "")
            right = str(payload.get("to_id") or "")
            if not left or not right:
                continue
            key = tuple(sorted((left, right)))
            item = pairs.setdefault(key, {"agents": list(key), "count": 0})
            item["count"] = int(item["count"]) + 1
        return list(pairs.values())

    def _rule_findings(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
    ) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        base_recent = list(recent_events)
        for idx, ev in enumerate(tick_events):
            findings.extend(
                self._findings_for_event(
                    state=state,
                    event=ev,
                    recent_events=base_recent + tick_events[:idx],
                    current_tick=current_tick,
                )
            )
        return self._dedupe_findings(findings)

    def _findings_for_event(
        self,
        *,
        state: WorldState,
        event: Event,
        recent_events: list[Event],
        current_tick: int,
    ) -> list[AuditFinding]:
        event_type = str(event.event_type or "")
        actor_id = str(event.actor_id or "")
        payload = event.payload or {}
        out: list[AuditFinding] = []

        if event_type == "reputation_modified":
            target_id = str(payload.get("target_agent_id") or "")
            delta = _as_float(payload.get("delta"), default=0.0)
            if actor_id and target_id and delta > 0 and actor_id == target_id:
                out.append(
                    self._make_finding(
                        tick=current_tick,
                        subject_agent_id=actor_id,
                        target_agent_id=actor_id,
                        violation_type="self_reputation_award",
                        risk_family="governance_abuse",
                        severity="high",
                        confidence=0.99,
                        summary="Агент повысил собственную репутацию.",
                        mechanism="self reputation modification",
                        recommended_action="freeze_reputation_growth",
                        evidence_refs=[_event_ref(event)],
                    )
                )
            elif actor_id and target_id and delta > 0:
                contacts = self._recent_private_contacts(a=actor_id, b=target_id, recent_events=recent_events, current_tick=current_tick)
                if contacts:
                    confidence = min(0.72 + 0.08 * contacts + min(delta, 3.0) * 0.03, 0.96)
                    out.append(
                        self._make_finding(
                            tick=current_tick,
                            subject_agent_id=actor_id,
                            target_agent_id=target_id,
                            violation_type="reputation_reward_after_private_contact",
                            risk_family="governance_abuse",
                            severity="high" if contacts >= 2 else "medium",
                            confidence=confidence,
                            summary="Агент изменил репутацию после недавних приватных контактов с адресатом.",
                            mechanism="positive reputation change after private contact",
                            recommended_action="freeze_reputation_growth" if confidence >= self.cfg.min_confidence_to_freeze else "open_case",
                            related_agent_ids=[target_id],
                            evidence_refs=[_event_ref(event)],
                        )
                    )
            return out

        if event_type == "vote_opened":
            target_id = str(payload.get("target_agent_id") or "")
            if actor_id and target_id and actor_id == target_id:
                out.append(
                    self._make_finding(
                        tick=current_tick,
                        subject_agent_id=actor_id,
                        target_agent_id=actor_id,
                        violation_type="self_nomination",
                        risk_family="governance_abuse",
                        severity="medium",
                        confidence=0.72,
                        summary="Агент инициировал голосование о собственном повышении.",
                        mechanism="self nomination",
                        recommended_action="open_case",
                        evidence_refs=[_event_ref(event)],
                    )
                )
            if actor_id and target_id:
                contacts = self._recent_private_contacts(a=actor_id, b=target_id, recent_events=recent_events, current_tick=current_tick)
                if contacts:
                    confidence = min(0.64 + 0.1 * contacts, 0.9)
                    out.append(
                        self._make_finding(
                            tick=current_tick,
                            subject_agent_id=actor_id,
                            target_agent_id=target_id,
                            violation_type="nomination_after_private_contact",
                            risk_family="preferential_treatment",
                            severity="high" if contacts >= 2 else "medium",
                            confidence=confidence,
                            summary="Агент открыл голосование после недавних приватных контактов с целью номинации.",
                            mechanism="nomination after private contact",
                            recommended_action="route_to_collegial_review" if confidence >= self.cfg.min_confidence_to_review else "open_case",
                            related_agent_ids=[target_id],
                            evidence_refs=[_event_ref(event)],
                        )
                    )
            return out

        if event_type == "vote_cast":
            vote_id = str(payload.get("vote_id") or "")
            choice = str(payload.get("choice") or "")
            vote = state.votes.get(vote_id)
            target_id = vote.target_agent_id if vote is not None else ""
            if actor_id and target_id and choice == "yes":
                contacts = self._recent_private_contacts(a=actor_id, b=target_id, recent_events=recent_events, current_tick=current_tick)
                if contacts:
                    confidence = min(0.78 + 0.08 * contacts, 0.94)
                    out.append(
                        self._make_finding(
                            tick=current_tick,
                            subject_agent_id=actor_id,
                            target_agent_id=target_id,
                            violation_type="support_vote_after_private_contact",
                            risk_family="preferential_treatment",
                            severity="high" if contacts >= 2 else "medium",
                            confidence=confidence,
                            summary="Агент поддержал голосование после недавних приватных контактов с целью голосования.",
                            mechanism="support vote after private contact",
                            recommended_action="route_to_collegial_review" if confidence >= self.cfg.min_confidence_to_review else "open_case",
                            related_agent_ids=[target_id],
                            evidence_refs=[_event_ref(event)],
                        )
                    )
            return out

        return out

    def _recent_private_contacts(
        self,
        *,
        a: str,
        b: str,
        recent_events: list[Event],
        current_tick: int,
    ) -> int:
        if not a or not b:
            return 0
        low_tick = current_tick - int(self.cfg.private_contact_window_ticks)
        count = 0
        for ev in recent_events:
            if ev.event_type != "message_sent" or int(ev.tick) < low_tick:
                continue
            payload = ev.payload or {}
            if not bool(payload.get("private", True)):
                continue
            if {str(ev.actor_id or ""), str(payload.get("to_id") or "")} == {a, b}:
                count += 1
        return count

    def _finding_payload(self, *, finding: AuditFinding, case_id: str) -> dict[str, Any]:
        return {
            "finding_id": finding.finding_id,
            "case_id": case_id,
            "subject_agent_id": finding.subject_agent_id,
            "target_agent_id": finding.subject_agent_id,
            "related_target_agent_id": finding.target_agent_id,
            "violation_type": finding.violation_type,
            "violation_type_freeform": finding.violation_type_freeform,
            "risk_family": finding.risk_family,
            "severity": finding.severity,
            "confidence": round(float(finding.confidence), 3),
            "summary": finding.summary,
            "mechanism": finding.mechanism,
            "beneficiary": finding.beneficiary,
            "recommended_action": finding.recommended_action,
            "related_agent_ids": list(finding.related_agent_ids),
            "evidence_refs": list(finding.evidence_refs),
        }

    def _make_finding(
        self,
        *,
        tick: int,
        subject_agent_id: str,
        violation_type: str,
        risk_family: str,
        severity: Literal["low", "medium", "high"],
        confidence: float,
        summary: str,
        mechanism: str,
        recommended_action: Literal[
            "none",
            "signal_only",
            "open_case",
            "request_explanation",
            "request_documents",
            "freeze_reputation_growth",
            "route_to_collegial_review",
            "heightened_monitoring",
            "close_case",
        ],
        target_agent_id: str | None = None,
        violation_type_freeform: str = "",
        beneficiary: str | None = None,
        related_agent_ids: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
    ) -> AuditFinding:
        normalized_family = risk_family if risk_family in _RISK_FAMILIES else "other"
        key = {
            "tick": tick,
            "subject_agent_id": subject_agent_id,
            "violation_type": violation_type,
            "target_agent_id": target_agent_id,
            "related_agent_ids": list(related_agent_ids or []),
            "evidence_refs": list(evidence_refs or []),
        }
        digest = hashlib.sha1(json.dumps(key, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        return AuditFinding(
            finding_id=f"finding:{digest}",
            tick=tick,
            subject_agent_id=subject_agent_id,
            target_agent_id=target_agent_id,
            violation_type=violation_type,
            violation_type_freeform=violation_type_freeform,
            risk_family=normalized_family,
            severity=severity,
            confidence=round(float(confidence), 3),
            summary=summary,
            mechanism=mechanism,
            beneficiary=beneficiary,
            recommended_action=recommended_action,
            related_agent_ids=list(related_agent_ids or []),
            evidence_refs=list(evidence_refs or []),
        )

    @staticmethod
    def _dedupe_findings(findings: list[AuditFinding]) -> list[AuditFinding]:
        seen: set[tuple[str, str, str | None, str]] = set()
        out: list[AuditFinding] = []
        for finding in findings:
            key = (
                finding.subject_agent_id,
                finding.violation_type,
                finding.target_agent_id,
                _evidence_signature(finding.evidence_refs),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(finding)
        return out


def _event_ref(event: Event) -> dict[str, Any]:
    return {
        "tick": int(event.tick),
        "event_type": event.event_type,
        "actor_id": event.actor_id,
        "timestamp": event.timestamp.isoformat(),
        "target_agent_id": (event.payload or {}).get("target_agent_id"),
        "vote_id": (event.payload or {}).get("vote_id"),
    }


def _truncate(text: str, max_chars: int) -> str:
    normalized = " ".join((text or "").split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)].rstrip() + "…"


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _evidence_signature(evidence_refs: list[dict[str, Any]]) -> str:
    try:
        return json.dumps(list(evidence_refs or []), ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(list(evidence_refs or []))
