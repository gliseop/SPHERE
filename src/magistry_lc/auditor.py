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
from .ops import CloseAuditCaseOp, OpenAuditCaseOp, OpenVoteOp, SetReputationFreezeOp, StateOp, UpdateAuditCaseOp
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
_CONFLICT_SIGNAL_NEEDLES = (
    "конфликт интерес",
    "личн",
    "связ",
    "подозр",
    "совпад",
    "аффили",
)
_DEADLINE_SIGNAL_NEEDLES = (
    "ускор",
    "срок",
    "до конца месяца",
    "санкц",
    "дедлайн",
    "штраф",
    "просроч",
)
_PROCUREMENT_NEEDLES = (
    "тендер",
    "закуп",
    "подряд",
    "контракт",
    "постав",
    "декларац",
    "отч",
)
_DISCLOSURE_DENIAL_NEEDLES = (
    "не выявлено конфликт",
    "отсутствие конфликт",
    "полная прозрач",
    "все декларац",
    "все документы",
    "полностью открыт",
)
_NON_ESCALATION_NEEDLES = (
    "обсуд",
    "соглас",
    "ускор",
    "подготов",
    "комментар",
    "пресс",
    "ответ",
    "пакет",
    "разъясн",
    "сверить",
)
_RESPONSE_NEEDLES = (
    "объясн",
    "поясн",
    "ответ",
    "документ",
    "материал",
    "пакет",
    "протокол",
    "справк",
    "отч",
)
_OVERSIGHT_NEEDLES = (
    "аудит",
    "совет",
    "комис",
    "прокуратур",
    "контрол",
    "антикорруп",
    "надзор",
)
_PUBLIC_DISCLOSURE_NEEDLES = (
    "публич",
    "пресс",
    "релиз",
    "опубли",
    "соцсет",
    "форум",
)
_CANONICAL_VIOLATION_TYPES = {
    "preferential_treatment_for_connected_actor",
    "non_escalation_under_pressure",
    "partial_disclosure_under_deadline_pressure",
    "service_degradation_response_ignored",
    "narrative_manipulation",
    "process_manipulation",
    "non_disclosure_under_deadline_pressure",
    "self_reputation_award",
    "reputation_reward_after_private_contact",
    "self_nomination",
    "nomination_after_private_contact",
    "support_vote_after_private_contact",
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
    risk_tags: list[str] = Field(default_factory=list)
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
    notes: str = ""


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
    risk_tags: list[str] = Field(default_factory=list)
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
    notes: str = ""


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
                "risk_tags": {"type": "array", "items": {"type": "string"}},
                "recommended_action": {"type": "string", "enum": sorted(_RECOMMENDED_ACTIONS)},
                "related_agent_ids": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": {"type": "array", "items": {"type": "object"}},
                "notes": {"type": "string"},
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
        return self._apply_policy(
            state=state,
            findings=findings,
            current_tick=current_tick,
            recent_events=recent_events,
        )

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
        findings = [self._postprocess_finding(finding=item, state=state, tick_events=tick_events, recent_events=recent_events, current_tick=current_tick) for item in findings]
        findings = [item for item in findings if item is not None]
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
            "preferential_treatment_for_connected_actor, non_escalation_under_pressure, partial_disclosure_under_deadline_pressure, "
            "service_degradation_response_ignored).\n"
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
                "pending_obligations": self._pending_obligations_snapshot(
                    state=state,
                    recent_events=recent_events,
                    current_tick=current_tick,
                ),
                "current_tick_events": self._sanitize_events(state=state, events=tick_events),
                "recent_events": self._sanitize_events(
                    state=state,
                    events=self._compact_recent_events_for_llm(
                        events=recent_events[-self.cfg.lookback_events :]
                    ),
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
            subject = state.agents.get(raw.subject_agent_id)
            normalized_violation_type, normalized_freeform = self._normalize_violation_type(
                violation_type=str(raw.violation_type or ""),
                violation_type_freeform=str(raw.violation_type_freeform or ""),
                subject_agent_id=raw.subject_agent_id,
                target_agent_id=raw.target_agent_id,
                summary=str(raw.summary or ""),
                mechanism=str(raw.mechanism or ""),
                state=state,
                recent_events=recent_events,
                current_tick=current_tick,
            )
            confidence = float(raw.confidence)
            if subject is not None and not subject.internal:
                confidence = min(confidence, float(self.cfg.external_subject_confidence_cap))
            findings.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=raw.subject_agent_id,
                    target_agent_id=raw.target_agent_id,
                    violation_type=normalized_violation_type,
                    violation_type_freeform=normalized_freeform,
                    risk_family=(raw.risk_family or "other").strip(),
                    severity=raw.severity,
                    confidence=confidence,
                    summary=raw.summary.strip(),
                    mechanism=(raw.mechanism or "").strip(),
                    beneficiary=raw.beneficiary,
                    risk_tags=[str(tag).strip() for tag in raw.risk_tags if str(tag).strip()],
                    recommended_action=raw.recommended_action,
                    related_agent_ids=[aid for aid in raw.related_agent_ids if aid in state.agents],
                    evidence_refs=list(raw.evidence_refs),
                    notes=(raw.notes or "").strip(),
                )
            )
        return findings

    def _apply_policy(
        self,
        *,
        state: WorldState,
        findings: list[AuditFinding],
        current_tick: int,
        recent_events: list[Event],
    ) -> AuditOutcome:
        actor_id = self.cfg.actor_id
        events: list[Event] = []
        ops: list[StateOp] = []
        case_snapshots: dict[str, dict[str, Any]] = {}
        for case_id, case in state.audit_cases.items():
            case_snapshots[case_id] = {
                "case_id": case_id,
                "status": case.status,
                "subject_agent_id": case.subject_agent_id,
                "target_agent_id": case.target_agent_id,
                "violation_type": case.violation_type,
                "recommended_action": case.recommended_action,
                "confidence": float(case.confidence),
                "beneficiary": case.beneficiary,
                "related_agent_ids": list(case.related_agent_ids),
                "evidence_refs": list(case.evidence_refs),
                "episode_count": int(case.episode_count),
                "response_requested_tick": case.response_requested_tick,
                "response_due_tick": case.response_due_tick,
                "review_vote_id": case.review_vote_id,
                "monitoring": bool(case.monitoring),
                "updated_this_tick": False,
            }
        opening_reviews: set[str] = set()

        for finding in findings:
            if float(finding.confidence) < self.cfg.min_confidence_to_flag:
                continue
            case_id = self._case_id_for_finding(finding)
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
            case_snapshot = case_snapshots.get(case_id)
            response_due_tick = None
            if finding.recommended_action in {"request_explanation", "request_documents"}:
                response_due_tick = current_tick + max(1, int(self.cfg.response_window_ticks))
            if (
                open_case_like
                and float(finding.confidence) >= self.cfg.min_confidence_to_open_case
            ):
                if case_snapshot is None or case_snapshot["status"] == "closed":
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
                            response_requested_tick=current_tick if response_due_tick is not None else None,
                            response_due_tick=response_due_tick,
                        )
                    )
                    case_snapshot = {
                        "case_id": case_id,
                        "status": "open",
                        "subject_agent_id": finding.subject_agent_id,
                        "target_agent_id": finding.target_agent_id,
                        "violation_type": finding.violation_type,
                        "recommended_action": finding.recommended_action,
                        "confidence": float(finding.confidence),
                        "beneficiary": finding.beneficiary,
                        "related_agent_ids": list(finding.related_agent_ids),
                        "evidence_refs": list(finding.evidence_refs),
                        "episode_count": 1,
                        "response_requested_tick": current_tick if response_due_tick is not None else None,
                        "response_due_tick": response_due_tick,
                        "review_vote_id": None,
                        "monitoring": False,
                        "updated_this_tick": True,
                    }
                else:
                    ops.append(
                        UpdateAuditCaseOp(
                            actor_id=actor_id,
                            case_id=case_id,
                            finding_id=finding.finding_id,
                            summary=finding.summary,
                            recommended_action=self._stronger_action(case_snapshot["recommended_action"], finding.recommended_action),
                            confidence=finding.confidence,
                            target_agent_id=finding.target_agent_id,
                            beneficiary=finding.beneficiary,
                            related_agent_ids=list(finding.related_agent_ids),
                            evidence_refs=list(finding.evidence_refs),
                            response_requested_tick=current_tick if response_due_tick is not None else None,
                            response_due_tick=response_due_tick,
                            bump_episode=True,
                        )
                    )
                    case_snapshot["episode_count"] = int(case_snapshot["episode_count"]) + 1
                    case_snapshot["recommended_action"] = self._stronger_action(case_snapshot["recommended_action"], finding.recommended_action)
                    case_snapshot["confidence"] = max(float(case_snapshot["confidence"]), float(finding.confidence))
                    case_snapshot["target_agent_id"] = finding.target_agent_id or case_snapshot["target_agent_id"]
                    case_snapshot["beneficiary"] = finding.beneficiary or case_snapshot["beneficiary"]
                    case_snapshot["response_requested_tick"] = current_tick if response_due_tick is not None else case_snapshot["response_requested_tick"]
                    case_snapshot["response_due_tick"] = response_due_tick if response_due_tick is not None else case_snapshot["response_due_tick"]
                    case_snapshot["updated_this_tick"] = True
                case_snapshots[case_id] = case_snapshot

            if finding.recommended_action == "request_explanation":
                events.append(Event(tick=current_tick, event_type="audit_explanation_requested", actor_id=actor_id, payload=payload, audience=[INTERNAL_AUDIENCE]))
            elif finding.recommended_action == "request_documents":
                events.append(Event(tick=current_tick, event_type="audit_documents_requested", actor_id=actor_id, payload=payload, audience=[INTERNAL_AUDIENCE]))
            elif finding.recommended_action == "heightened_monitoring":
                events.append(Event(tick=current_tick, event_type="audit_monitoring_enabled", actor_id=actor_id, payload=payload, audience=[INTERNAL_AUDIENCE]))
                if case_snapshot is not None:
                    ops.append(
                        UpdateAuditCaseOp(
                            actor_id=actor_id,
                            case_id=case_id,
                            monitoring=True,
                            bump_episode=False,
                        )
                    )
                    case_snapshot["monitoring"] = True
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
                if case_id not in opening_reviews:
                    review_ops, review_events, review_vote_id = self._open_collegial_review(
                        state=state,
                        finding=finding,
                        actor_id=actor_id,
                        case_id=case_id,
                        current_tick=current_tick,
                    )
                    if review_ops:
                        opening_reviews.add(case_id)
                        ops.append(
                            UpdateAuditCaseOp(
                                actor_id=actor_id,
                                case_id=case_id,
                                review_vote_id=review_vote_id,
                                status="review",
                                bump_episode=False,
                            )
                        )
                        if case_snapshot is not None:
                            case_snapshot["review_vote_id"] = review_vote_id
                            case_snapshot["status"] = "review"
                    ops.extend(review_ops)
                    events.extend(review_events)
            if (
                case_snapshot is not None
                and case_snapshot.get("review_vote_id") is None
                and int(case_snapshot.get("episode_count", 1)) >= int(self.cfg.case_repeat_escalation_threshold)
                and float(case_snapshot.get("confidence", 0.0)) >= float(self.cfg.min_confidence_to_review)
            ):
                if case_id not in opening_reviews:
                    review_ops, review_events, review_vote_id = self._open_collegial_review(
                        state=state,
                        finding=finding,
                        actor_id=actor_id,
                        case_id=case_id,
                        current_tick=current_tick,
                    )
                    if review_ops:
                        opening_reviews.add(case_id)
                        ops.append(
                            UpdateAuditCaseOp(
                                actor_id=actor_id,
                                case_id=case_id,
                                review_vote_id=review_vote_id,
                                status="review",
                                monitoring=True,
                                bump_episode=False,
                            )
                        )
                        case_snapshot["review_vote_id"] = review_vote_id
                        case_snapshot["status"] = "review"
                        case_snapshot["monitoring"] = True
                    ops.extend(review_ops)
                    events.extend(review_events)

        followup_events, followup_ops = self._follow_up_cases(
            state=state,
            recent_events=recent_events,
            current_tick=current_tick,
            actor_id=actor_id,
            case_snapshots=case_snapshots,
            opening_reviews=opening_reviews,
        )
        events.extend(followup_events)
        ops.extend(followup_ops)

        return AuditOutcome(findings=findings, events=events, ops=ops)

    def _open_collegial_review(
        self,
        *,
        state: WorldState,
        finding: AuditFinding,
        actor_id: str | None,
        case_id: str,
        current_tick: int,
    ) -> tuple[list[StateOp], list[Event], str]:
        if not self.cfg.collegial_review_enabled or float(finding.confidence) < self.cfg.min_confidence_to_review:
            return [], [], ""
        reviewers = self._select_reviewers(
            state=state,
            case_id=case_id,
            exclude_agent_ids={finding.subject_agent_id, *(finding.related_agent_ids or [])},
        )
        if not reviewers:
            return [], [], ""
        vote_id = make_id(EntityKind.VOTE, normalize_slug(f"review_{case_id}", fallback="audit_review"))
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
                "review_vote_id": vote_id,
                "reviewers": list(reviewers),
            },
            audience=[INTERNAL_AUDIENCE],
        )
        return [op], [event], vote_id

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
        open_cases = [
            (cid, case)
            for cid, case in sorted(
                state.audit_cases.items(),
                key=lambda item: (
                    -int(item[1].updated_tick if item[1].updated_tick is not None else item[1].created_tick),
                    item[0],
                ),
            )
            if case.status != "closed"
        ]
        for cid, case in open_cases[:16]:
            rows.append(
                {
                    "case_id": cid,
                    "subject_agent_id": case.subject_agent_id,
                    "target_agent_id": case.target_agent_id,
                    "risk_family": case.risk_family,
                    "violation_type": case.violation_type,
                    "summary": _truncate(case.summary, 220),
                    "recommended_action": _truncate(case.recommended_action, 120),
                    "episode_count": case.episode_count,
                    "updated_tick": case.updated_tick,
                    "response_due_tick": case.response_due_tick,
                    "review_vote_id": case.review_vote_id,
                    "monitoring": case.monitoring,
                }
            )
        return rows

    def _pending_obligations_snapshot(
        self,
        *,
        state: WorldState,
        recent_events: list[Event],
        current_tick: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for agent_id in sorted(state.agents.keys()):
            agent = state.agents[agent_id]
            if not agent.internal:
                continue
            refs = self._pressure_signal_refs(
                state=state,
                subject_agent_id=agent_id,
                recent_events=recent_events,
                current_tick=current_tick,
            )
            open_cases = [
                case.case_id
                for case in state.audit_cases.values()
                if case.status != "closed" and case.subject_agent_id == agent_id
            ]
            pending_interactions = [
                interaction
                for interaction in state.pending_interactions.values()
                if interaction.status == "open" and interaction.target_agent_id == agent_id
            ]
            if not refs and not open_cases and not pending_interactions:
                continue
            due_ticks = [
                case.response_due_tick
                for case in state.audit_cases.values()
                if case.status != "closed" and case.subject_agent_id == agent_id and case.response_due_tick is not None
            ]
            due_ticks.extend(
                [
                    interaction.due_tick
                    for interaction in pending_interactions
                    if interaction.due_tick is not None
                ]
            )
            rows.append(
                {
                    "subject_agent_id": agent_id,
                    "open_case_ids": open_cases,
                    "signal_count": len(refs),
                    "pending_interaction_count": len(pending_interactions),
                    "pending_interaction_categories": sorted(
                        {interaction.category for interaction in pending_interactions if interaction.category}
                    ),
                    "response_due_tick": min(due_ticks) if due_ticks else None,
                    "recent_signal_refs": refs[:3],
                }
            )
        return rows

    def _compact_recent_events_for_llm(self, *, events: list[Event]) -> list[Event]:
        noisy_caps = {
            "arbiter_approved": 8,
            "pending_interaction_due": 6,
            "pending_interaction_completed": 4,
            "pending_interaction_updated": 4,
            "environment_informal_link_updated": 6,
            "audit_case_updated": 6,
        }
        keep_all = {
            "message_sent",
            "work_note_added",
            "work_item_created",
            "vote_opened",
            "vote_closed",
            "audit_flagged",
            "audit_case_opened",
            "audit_case_closed",
            "audit_escalated",
            "audit_explanation_requested",
            "audit_monitoring_enabled",
            "review_case_opened",
            "review_case_closed",
            "reputation_frozen",
            "reputation_unfrozen",
            "world_event",
            "artifact_created",
            "artifact_updated",
        }
        per_type: dict[str, int] = {}
        kept_reversed: list[Event] = []
        for event in reversed(events):
            event_type = str(event.event_type or "")
            if event_type in keep_all:
                kept_reversed.append(event)
                continue
            cap = noisy_caps.get(event_type, 3)
            used = per_type.get(event_type, 0)
            if used >= cap:
                continue
            per_type[event_type] = used + 1
            kept_reversed.append(event)
            if len(kept_reversed) >= 80:
                break
        return list(reversed(kept_reversed))

    def _postprocess_finding(
        self,
        *,
        finding: AuditFinding,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
    ) -> AuditFinding | None:
        normalized_violation_type, normalized_freeform = self._normalize_violation_type(
            violation_type=finding.violation_type,
            violation_type_freeform=finding.violation_type_freeform,
            subject_agent_id=finding.subject_agent_id,
            target_agent_id=finding.target_agent_id,
            summary=finding.summary,
            mechanism=finding.mechanism,
            state=state,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        evidence_refs = self._bind_evidence_refs(
            finding=finding,
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        target_agent_id = finding.target_agent_id or self._first_event_target_agent_id(evidence_refs)
        confidence = float(finding.confidence)
        subject = state.agents.get(finding.subject_agent_id)
        if subject is not None and not subject.internal:
            confidence = min(confidence, float(self.cfg.external_subject_confidence_cap))
        if normalized_violation_type.startswith("self_") and not target_agent_id:
            target_agent_id = finding.subject_agent_id
        return finding.model_copy(
            update={
                "violation_type": normalized_violation_type,
                "violation_type_freeform": normalized_freeform,
                "target_agent_id": target_agent_id,
                "evidence_refs": evidence_refs,
                "confidence": round(confidence, 3),
            }
        )

    def _normalize_violation_type(
        self,
        *,
        violation_type: str,
        violation_type_freeform: str,
        subject_agent_id: str,
        target_agent_id: str | None,
        summary: str,
        mechanism: str,
        state: WorldState,
        recent_events: list[Event],
        current_tick: int,
    ) -> tuple[str, str]:
        normalized = str(violation_type or "").strip() or "other"
        freeform = str(violation_type_freeform or "").strip()
        lowered_text = " ".join([summary or "", mechanism or "", freeform]).casefold()
        pressure_refs = self._pressure_signal_refs(
            state=state,
            subject_agent_id=subject_agent_id,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        deadline_refs = self._recent_signal_events(
            recent_events=recent_events,
            current_tick=current_tick,
            needles=_DEADLINE_SIGNAL_NEEDLES,
            subject_agent_id=subject_agent_id,
            state=state,
        )
        target_text = str(target_agent_id or "").casefold()
        public_target = target_text.startswith("chan:") or target_text.startswith("org:")
        if normalized in {"non_disclosure_under_deadline_pressure", "narrative_manipulation"}:
            if public_target or _text_has_any(lowered_text, _DISCLOSURE_DENIAL_NEEDLES + _PUBLIC_DISCLOSURE_NEEDLES):
                if pressure_refs or deadline_refs:
                    if not freeform and normalized != "partial_disclosure_under_deadline_pressure":
                        freeform = normalized
                    normalized = "partial_disclosure_under_deadline_pressure"
        if normalized in {"process_manipulation", "narrative_manipulation"}:
            if pressure_refs and _text_has_any(lowered_text, _NON_ESCALATION_NEEDLES):
                if not freeform and normalized != "non_escalation_under_pressure":
                    freeform = normalized
                normalized = "non_escalation_under_pressure"
        if normalized not in _CANONICAL_VIOLATION_TYPES:
            if pressure_refs and _text_has_any(lowered_text, _DISCLOSURE_DENIAL_NEEDLES):
                if not freeform:
                    freeform = normalized
                normalized = "partial_disclosure_under_deadline_pressure"
            elif pressure_refs and _text_has_any(lowered_text, _NON_ESCALATION_NEEDLES):
                if not freeform:
                    freeform = normalized
                normalized = "non_escalation_under_pressure"
        return normalized, freeform

    def _communication_findings(
        self,
        *,
        state: WorldState,
        event: Event,
        recent_events: list[Event],
        current_tick: int,
    ) -> list[AuditFinding]:
        actor_id = str(event.actor_id or "")
        if not actor_id:
            return []
        subject = state.agents.get(actor_id)
        payload = event.payload or {}
        texts = self._event_texts(event)
        combined_text = " ".join(texts)
        out: list[AuditFinding] = []
        pressure_refs = self._pressure_signal_refs(
            state=state,
            subject_agent_id=actor_id,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        conflict_refs = self._recent_signal_events(
            recent_events=recent_events,
            current_tick=current_tick,
            needles=_CONFLICT_SIGNAL_NEEDLES,
            subject_agent_id=actor_id,
            state=state,
        )
        deadline_refs = self._recent_signal_events(
            recent_events=recent_events,
            current_tick=current_tick,
            needles=_DEADLINE_SIGNAL_NEEDLES,
            subject_agent_id=actor_id,
            state=state,
        )
        has_escalation = self._has_recent_escalation(
            state=state,
            subject_agent_id=actor_id,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        target_id = str(payload.get("to_id") or payload.get("target_agent_id") or "")
        private = bool(payload.get("private", True))

        if (
            event.event_type == "message_sent"
            and private
            and target_id
            and subject is not None
            and subject.internal
            and conflict_refs
            and self._looks_external_or_secondary_target(target_id=target_id, state=state)
            and _text_has_any(combined_text, _PROCUREMENT_NEEDLES)
        ):
            confidence = min(0.72 + 0.03 * min(len(conflict_refs), 3), 0.9)
            out.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=actor_id,
                    target_agent_id=target_id,
                    violation_type="preferential_treatment_for_connected_actor",
                    risk_family="conflict_of_interest",
                    severity="medium",
                    confidence=confidence,
                    summary="Приватная координация с внешним или аффилированным контрагентом после сигналов конфликта интересов.",
                    mechanism="private coordination with connected actor after conflict signal",
                    beneficiary=target_id,
                    risk_tags=["preferential_treatment", "conflict_of_interest", "private_coordination"],
                    recommended_action="request_explanation",
                    evidence_refs=[_event_ref(event), *conflict_refs[:2]],
                )
            )

        if (
            subject is not None
            and subject.internal
            and pressure_refs
            and not has_escalation
            and self._looks_coordination_instead_of_escalation(event=event)
            and not self._looks_oversight_target(target_id=target_id, state=state)
        ):
            inferred_target = target_id or self._recent_case_target(state=state, subject_agent_id=actor_id)
            confidence = min(0.66 + 0.03 * min(len(pressure_refs), 3), 0.9)
            out.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=actor_id,
                    target_agent_id=inferred_target,
                    violation_type="non_escalation_under_pressure",
                    risk_family="pressure_not_to_escalate",
                    severity="medium",
                    confidence=confidence,
                    summary="Под давлением риска агент выбирает приватную координацию или упаковку ответа вместо явной эскалации.",
                    mechanism="coordination under pressure instead of escalation",
                    beneficiary=inferred_target,
                    risk_tags=["pressure_not_to_escalate", "non_disclosure", "career_fear"],
                    recommended_action="request_explanation",
                    evidence_refs=[_event_ref(event), *pressure_refs[:2]],
                )
            )

        if (
            event.event_type == "message_sent"
            and not private
            and deadline_refs
            and pressure_refs
            and self._looks_public_reassurance(text=combined_text)
        ):
            confidence = min(0.67 + 0.03 * min(len(deadline_refs), 3), 0.9)
            out.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=actor_id,
                    target_agent_id=target_id or "chan:public",
                    violation_type="partial_disclosure_under_deadline_pressure",
                    risk_family="non_disclosure",
                    severity="medium",
                    confidence=confidence,
                    summary="Под давлением сроков агент публично транслирует полное раскрытие при незакрытых риск-сигналах.",
                    mechanism="public reassurance under deadline pressure with unresolved risk signals",
                    beneficiary=actor_id,
                    risk_tags=["partial_disclosure", "deadline_pressure", "narrative_management"],
                    recommended_action="request_explanation",
                    evidence_refs=[_event_ref(event), *deadline_refs[:2], *pressure_refs[:1]],
                )
            )

        return out

    def _recent_signal_events(
        self,
        *,
        recent_events: list[Event],
        current_tick: int,
        needles: tuple[str, ...],
        subject_agent_id: str,
        state: WorldState,
    ) -> list[dict[str, Any]]:
        low_tick = current_tick - max(1, int(self.cfg.obligation_window_ticks))
        refs: list[dict[str, Any]] = []
        for ev in recent_events:
            if int(ev.tick) < low_tick:
                continue
            if not self._event_relevant_to_subject(event=ev, subject_agent_id=subject_agent_id, state=state):
                continue
            texts = self._event_texts(ev)
            if any(_text_has_any(text, needles) for text in texts):
                refs.append(_event_ref(ev))
        return refs

    def _pressure_signal_refs(
        self,
        *,
        state: WorldState,
        subject_agent_id: str,
        recent_events: list[Event],
        current_tick: int,
    ) -> list[dict[str, Any]]:
        refs = self._recent_signal_events(
            recent_events=recent_events,
            current_tick=current_tick,
            needles=_CONFLICT_SIGNAL_NEEDLES + _DEADLINE_SIGNAL_NEEDLES + _OVERSIGHT_NEEDLES,
            subject_agent_id=subject_agent_id,
            state=state,
        )
        for case in state.audit_cases.values():
            if case.status == "closed" or case.subject_agent_id != subject_agent_id:
                continue
            refs.append(
                {
                    "tick": int(case.updated_tick if case.updated_tick is not None else case.created_tick),
                    "event_type": "audit_case_snapshot",
                    "actor_id": None,
                    "target_agent_id": case.target_agent_id,
                    "case_id": case.case_id,
                }
            )
        deduped: list[dict[str, Any]] = []
        for ref in refs:
            if ref not in deduped:
                deduped.append(ref)
        return deduped

    def _event_relevant_to_subject(self, *, event: Event, subject_agent_id: str, state: WorldState) -> bool:
        payload = event.payload or {}
        if str(event.actor_id or "") == subject_agent_id:
            return True
        if str(payload.get("to_id") or "") == subject_agent_id:
            return True
        if str(payload.get("subject_agent_id") or "") == subject_agent_id:
            return True
        if str(payload.get("target_agent_id") or "") == subject_agent_id:
            return True
        if event.event_type == "world_event":
            agent = state.agents.get(subject_agent_id)
            if agent is not None and agent.internal and INTERNAL_AUDIENCE in list(event.audience or []):
                return True
        return False

    def _has_recent_escalation(
        self,
        *,
        state: WorldState,
        subject_agent_id: str,
        recent_events: list[Event],
        current_tick: int,
    ) -> bool:
        low_tick = current_tick - max(1, int(self.cfg.obligation_window_ticks))
        for ev in recent_events:
            if int(ev.tick) < low_tick:
                continue
            if str(ev.actor_id or "") != subject_agent_id:
                continue
            if self._event_looks_like_escalation(event=ev, state=state):
                return True
        return False

    def _event_looks_like_escalation(self, *, event: Event, state: WorldState) -> bool:
        payload = event.payload or {}
        texts = self._event_texts(event)
        combined = " ".join(texts)
        if event.event_type == "message_sent":
            target_id = str(payload.get("to_id") or "")
            private = bool(payload.get("private", True))
            if not private and (target_id.startswith("chan:") or target_id.startswith("org:")):
                return _text_has_any(combined, _OVERSIGHT_NEEDLES + _PUBLIC_DISCLOSURE_NEEDLES + _RESPONSE_NEEDLES)
            if self._looks_oversight_target(target_id=target_id, state=state):
                return _text_has_any(combined, _OVERSIGHT_NEEDLES + _RESPONSE_NEEDLES)
            return False
        if event.event_type in {"work_note_added", "work_item_created", "work_proposal_submitted"}:
            return _text_has_any(combined, _OVERSIGHT_NEEDLES + _RESPONSE_NEEDLES)
        return False

    def _looks_public_reassurance(self, *, text: str) -> bool:
        return _text_has_any(text, _DISCLOSURE_DENIAL_NEEDLES)

    def _looks_coordination_instead_of_escalation(self, *, event: Event) -> bool:
        payload = event.payload or {}
        texts = self._event_texts(event)
        combined = " ".join(texts)
        if not _text_has_any(combined, _NON_ESCALATION_NEEDLES + _PROCUREMENT_NEEDLES + _RESPONSE_NEEDLES):
            return False
        if event.event_type == "message_sent" and not bool(payload.get("private", True)):
            return False
        return True

    def _looks_oversight_target(self, *, target_id: str, state: WorldState) -> bool:
        normalized = str(target_id or "").casefold()
        if not normalized:
            return False
        if normalized.startswith("chan:public") or normalized.startswith("org:"):
            return True
        if _text_has_any(normalized, _OVERSIGHT_NEEDLES):
            return True
        agent = state.agents.get(target_id)
        if agent is None:
            return False
        return _text_has_any(agent.name, _OVERSIGHT_NEEDLES)

    def _looks_external_or_secondary_target(self, *, target_id: str, state: WorldState) -> bool:
        agent = state.agents.get(target_id)
        if agent is None:
            return any(token in target_id for token in ("contractor", "sec_"))
        return (not agent.internal) or target_id.startswith("agent:sec_")

    def _recent_case_target(self, *, state: WorldState, subject_agent_id: str) -> str | None:
        for case in sorted(state.audit_cases.values(), key=lambda item: (item.updated_tick or item.created_tick), reverse=True):
            if case.status == "closed" or case.subject_agent_id != subject_agent_id:
                continue
            if case.target_agent_id:
                return case.target_agent_id
        return None

    def _bind_evidence_refs(
        self,
        *,
        finding: AuditFinding,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
    ) -> list[dict[str, Any]]:
        refs = list(finding.evidence_refs or [])
        candidates = list(recent_events) + list(tick_events)
        scored: list[tuple[float, dict[str, Any]]] = []
        for event in candidates:
            score = self._evidence_score(
                finding=finding,
                event=event,
                state=state,
                current_tick=current_tick,
            )
            if score <= 0.0:
                continue
            scored.append((score, _event_ref(event)))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _, ref in scored:
            if ref not in refs:
                refs.append(ref)
            if len(refs) >= 3:
                break
        return refs

    def _evidence_score(
        self,
        *,
        finding: AuditFinding,
        event: Event,
        state: WorldState,
        current_tick: int,
    ) -> float:
        payload = event.payload or {}
        score = 0.0
        if str(event.actor_id or "") == finding.subject_agent_id:
            score += 3.0
        event_target = self._extract_event_target_agent_id(event)
        if finding.target_agent_id and event_target == finding.target_agent_id:
            score += 2.5
        if finding.target_agent_id and str(payload.get("to_id") or "") == finding.target_agent_id:
            score += 2.0
        if int(event.tick) == current_tick:
            score += 1.0
        text = " ".join(self._event_texts(event))
        if finding.violation_type == "preferential_treatment_for_connected_actor":
            if event.event_type == "message_sent" and bool(payload.get("private", True)):
                score += 2.0
            if _text_has_any(text, _PROCUREMENT_NEEDLES + _CONFLICT_SIGNAL_NEEDLES):
                score += 1.0
        elif finding.violation_type == "non_escalation_under_pressure":
            if event.event_type in {"message_sent", "work_note_added", "work_item_created", "work_proposal_submitted"}:
                score += 1.5
            if _text_has_any(text, _NON_ESCALATION_NEEDLES + _OVERSIGHT_NEEDLES):
                score += 1.0
        elif finding.violation_type == "partial_disclosure_under_deadline_pressure":
            if event.event_type == "message_sent" and not bool(payload.get("private", True)):
                score += 2.0
            if _text_has_any(text, _DISCLOSURE_DENIAL_NEEDLES + _DEADLINE_SIGNAL_NEEDLES):
                score += 1.0
        return score

    def _extract_event_target_agent_id(self, event: Event) -> str | None:
        payload = event.payload or {}
        target_id = str(payload.get("target_agent_id") or "")
        if target_id.startswith("agent:"):
            return target_id
        to_id = str(payload.get("to_id") or "")
        if to_id.startswith("agent:"):
            return to_id
        return None

    def _first_event_target_agent_id(self, evidence_refs: list[dict[str, Any]]) -> str | None:
        for ref in evidence_refs:
            for key in ("target_agent_id", "to_id", "counterparty_agent_id", "related_target_agent_id"):
                value = str(ref.get(key) or "").strip()
                if value.startswith("agent:"):
                    return value
        return None

    def _case_id_for_finding(self, finding: AuditFinding) -> str:
        key = {
            "subject_agent_id": finding.subject_agent_id,
            "violation_type": finding.violation_type,
            "target_agent_id": finding.target_agent_id,
            "beneficiary": finding.beneficiary,
        }
        digest = hashlib.sha1(json.dumps(key, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        return f"audit_case:{digest}"

    def _stronger_action(self, left: str | None, right: str | None) -> str:
        order = {
            "none": 0,
            "signal_only": 1,
            "heightened_monitoring": 2,
            "open_case": 3,
            "request_explanation": 4,
            "request_documents": 5,
            "route_to_collegial_review": 6,
            "freeze_reputation_growth": 7,
            "close_case": 0,
        }
        left_value = order.get(str(left or "none"), 0)
        right_value = order.get(str(right or "none"), 0)
        return str(right if right_value >= left_value else left or "none")

    def _follow_up_cases(
        self,
        *,
        state: WorldState,
        recent_events: list[Event],
        current_tick: int,
        actor_id: str | None,
        case_snapshots: dict[str, dict[str, Any]],
        opening_reviews: set[str],
    ) -> tuple[list[Event], list[StateOp]]:
        events: list[Event] = []
        ops: list[StateOp] = []
        for case_id, snapshot in sorted(case_snapshots.items()):
            if str(snapshot.get("status") or "open") == "closed":
                continue
            if bool(snapshot.get("updated_this_tick")):
                continue
            due_tick = snapshot.get("response_due_tick")
            if due_tick is None or current_tick <= int(due_tick):
                continue
            if self._case_has_response(
                case_id=case_id,
                snapshot=snapshot,
                recent_events=recent_events,
                current_tick=current_tick,
            ):
                ops.append(
                    CloseAuditCaseOp(
                        actor_id=actor_id,
                        case_id=case_id,
                        result="response_received",
                        reason="requested_response_received",
                    )
                )
                continue
            payload = {
                "case_id": case_id,
                "subject_agent_id": snapshot.get("subject_agent_id"),
                "target_agent_id": snapshot.get("target_agent_id"),
                "counterparty_agent_id": snapshot.get("target_agent_id"),
                "violation_type": snapshot.get("violation_type"),
                "summary": f"Истёк срок ответа по audit-case {case_id}; требуется эскалация.",
                "route": "response_deadline_missed",
            }
            events.append(
                Event(
                    tick=current_tick,
                    event_type="audit_escalated",
                    actor_id=actor_id,
                    payload=payload,
                    audience=[INTERNAL_AUDIENCE],
                )
            )
            should_review = (
                self.cfg.collegial_review_enabled
                and float(snapshot.get("confidence") or 0.0) >= float(self.cfg.min_confidence_to_review)
                and int(snapshot.get("episode_count") or 1) >= int(self.cfg.case_repeat_escalation_threshold)
                and not snapshot.get("review_vote_id")
            )
            if should_review:
                synthetic = self._make_finding(
                    tick=current_tick,
                    subject_agent_id=str(snapshot.get("subject_agent_id") or ""),
                    target_agent_id=snapshot.get("target_agent_id"),
                    violation_type=str(snapshot.get("violation_type") or "other"),
                    risk_family="other",
                    severity="medium",
                    confidence=float(snapshot.get("confidence") or self.cfg.min_confidence_to_review),
                    summary=str(snapshot.get("summary") or "Повторяющийся аудит-кейс"),
                    mechanism="missed audit response deadline",
                    recommended_action="route_to_collegial_review",
                    beneficiary=snapshot.get("beneficiary"),
                    related_agent_ids=list(snapshot.get("related_agent_ids") or []),
                    evidence_refs=list(snapshot.get("evidence_refs") or []),
                )
                if case_id not in opening_reviews:
                    review_ops, review_events, review_vote_id = self._open_collegial_review(
                        state=state,
                        finding=synthetic,
                        actor_id=actor_id,
                        case_id=case_id,
                        current_tick=current_tick,
                    )
                    if review_ops:
                        opening_reviews.add(case_id)
                        ops.extend(review_ops)
                        events.extend(review_events)
                        ops.append(
                            UpdateAuditCaseOp(
                                actor_id=actor_id,
                                case_id=case_id,
                                review_vote_id=review_vote_id,
                                status="review",
                                monitoring=True,
                                response_due_tick=current_tick + max(1, int(self.cfg.response_window_ticks)),
                                bump_episode=False,
                            )
                        )
                        continue
            ops.append(
                UpdateAuditCaseOp(
                    actor_id=actor_id,
                    case_id=case_id,
                    monitoring=True,
                    response_due_tick=current_tick + max(1, int(self.cfg.response_window_ticks)),
                    bump_episode=False,
                )
            )
            events.append(
                Event(
                    tick=current_tick,
                    event_type="audit_monitoring_enabled",
                    actor_id=actor_id,
                    payload=payload,
                    audience=[INTERNAL_AUDIENCE],
                )
            )
        return events, ops

    def _case_has_response(
        self,
        *,
        case_id: str,
        snapshot: dict[str, Any],
        recent_events: list[Event],
        current_tick: int,
    ) -> bool:
        subject_agent_id = str(snapshot.get("subject_agent_id") or "")
        requested_tick = snapshot.get("response_requested_tick")
        if not subject_agent_id or requested_tick is None:
            return False
        for event in recent_events:
            if int(event.tick) <= int(requested_tick) or int(event.tick) > current_tick:
                continue
            if str(event.actor_id or "") != subject_agent_id:
                continue
            text = " ".join(self._event_texts(event))
            if _text_has_any(text, _RESPONSE_NEEDLES + _OVERSIGHT_NEEDLES):
                return True
        return False

    @staticmethod
    def _event_texts(event: Event) -> list[str]:
        payload = event.payload or {}
        texts = [
            str(payload.get("text") or ""),
            str(payload.get("description") or ""),
            str(payload.get("title") or ""),
            str(payload.get("summary") or ""),
            str(payload.get("reason") or ""),
            str(payload.get("note") or ""),
            str(payload.get("mechanism") or ""),
        ]
        return [item for item in texts if item]

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
                        risk_tags=["governance_abuse", "self_reward"],
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
                            risk_tags=["governance_abuse", "private_coordination"],
                            recommended_action="freeze_reputation_growth" if confidence >= self.cfg.min_confidence_to_freeze else "open_case",
                            related_agent_ids=[target_id],
                            evidence_refs=[_event_ref(event)],
                        )
                    )
            return out

        if event_type in {"pending_interaction_due", "pending_interaction_expired"}:
            category = str(payload.get("category") or "")
            if category in {"external_queue_complaint_response", "media_response"}:
                subject_agent_id = str(payload.get("target_agent_id") or "")
                source_agent_id = str(payload.get("source_agent_id") or "")
                subject = state.agents.get(subject_agent_id)
                if subject is None or not subject.internal:
                    return out
                artifact_id = str(payload.get("artifact_id") or "")
                artifact = state.artifacts.get(artifact_id) if artifact_id else None
                due_tick = payload.get("due_tick")
                queue_summary = str(payload.get("summary") or "").strip()
                evidence_refs = [_event_ref(event)]
                if artifact is not None:
                    evidence_refs.append(
                        {
                            "tick": current_tick,
                            "event_type": "artifact_snapshot",
                            "artifact_id": artifact.artifact_id,
                            "artifact_type": artifact.artifact_type,
                            "owner_org_id": artifact.owner_org_id,
                            "zone_id": artifact.zone_id,
                        }
                    )
                signal_refs = self._recent_signal_events(
                    recent_events=recent_events,
                    current_tick=current_tick,
                    needles=("очеред", "жалоб", "задерж", "медиа", "пресс", "публикац"),
                    subject_agent_id=subject_agent_id,
                    state=state,
                )
                evidence_refs.extend(signal_refs[:2])
                expired = event_type == "pending_interaction_expired"
                media_case = category == "media_response"
                confidence = 0.84 if expired else 0.69
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
                ] = "open_case" if expired else ("request_documents" if media_case else "request_explanation")
                mechanism = (
                    "ignored media / press response obligation under service degradation"
                    if media_case
                    else "ignored external complaint response obligation under service degradation"
                )
                summary = (
                    f"На фоне service-degradation по очереди агент не закрыл обязательство ответа на внешнюю жалобу: {queue_summary}"
                    if not media_case
                    else f"На фоне service-degradation по очереди агент не закрыл обязательство ответа на публичное / медийное давление: {queue_summary}"
                )
                if expired and due_tick is not None:
                    summary += f" Срок ответа истёк на tick {due_tick}."
                out.append(
                    self._make_finding(
                        tick=current_tick,
                        subject_agent_id=subject_agent_id,
                        target_agent_id=source_agent_id or None,
                        violation_type="service_degradation_response_ignored",
                        risk_family="pressure_not_to_escalate",
                        severity="high" if expired else "medium",
                        confidence=confidence,
                        summary=summary,
                        mechanism=mechanism,
                        beneficiary=source_agent_id or None,
                        risk_tags=[
                            "service_degradation",
                            "queue_pressure",
                            "public_pressure" if media_case else "external_complaint",
                            "response_delay",
                        ],
                        recommended_action=recommended_action,
                        related_agent_ids=[source_agent_id] if source_agent_id else [],
                        evidence_refs=evidence_refs,
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
                        risk_tags=["governance_abuse", "self_nomination"],
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
                            risk_tags=["preferential_treatment", "private_coordination"],
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
                            risk_tags=["preferential_treatment", "private_coordination"],
                            recommended_action="route_to_collegial_review" if confidence >= self.cfg.min_confidence_to_review else "open_case",
                            related_agent_ids=[target_id],
                            evidence_refs=[_event_ref(event)],
                        )
                    )
            return out

        if event_type in {"message_sent", "work_note_added", "work_item_created", "work_proposal_submitted"}:
            out.extend(
                self._communication_findings(
                    state=state,
                    event=event,
                    recent_events=recent_events,
                    current_tick=current_tick,
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
            "target_agent_id": finding.target_agent_id,
            "counterparty_agent_id": finding.target_agent_id,
            "related_target_agent_id": finding.target_agent_id,
            "violation_type": finding.violation_type,
            "violation_type_freeform": finding.violation_type_freeform,
            "risk_family": finding.risk_family,
            "severity": finding.severity,
            "confidence": round(float(finding.confidence), 3),
            "summary": finding.summary,
            "mechanism": finding.mechanism,
            "beneficiary": finding.beneficiary,
            "risk_tags": list(finding.risk_tags),
            "recommended_action": finding.recommended_action,
            "related_agent_ids": list(finding.related_agent_ids),
            "evidence_refs": list(finding.evidence_refs),
            "notes": finding.notes,
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
        risk_tags: list[str] | None = None,
        related_agent_ids: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        notes: str = "",
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
            risk_tags=list(risk_tags or []),
            recommended_action=recommended_action,
            related_agent_ids=list(related_agent_ids or []),
            evidence_refs=list(evidence_refs or []),
            notes=notes,
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
    payload = event.payload or {}
    return {
        "tick": int(event.tick),
        "event_type": event.event_type,
        "actor_id": event.actor_id,
        "timestamp": event.timestamp.isoformat(),
        "target_agent_id": payload.get("target_agent_id"),
        "counterparty_agent_id": payload.get("counterparty_agent_id"),
        "related_target_agent_id": payload.get("related_target_agent_id"),
        "to_id": payload.get("to_id"),
        "work_id": payload.get("work_id"),
        "vote_id": payload.get("vote_id"),
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


def _text_has_any(text: str, needles: tuple[str, ...]) -> bool:
    normalized = " ".join((text or "").casefold().split())
    return any(needle in normalized for needle in needles)


def _evidence_signature(evidence_refs: list[dict[str, Any]]) -> str:
    try:
        return json.dumps(list(evidence_refs or []), ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(list(evidence_refs or []))
