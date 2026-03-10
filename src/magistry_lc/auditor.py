"""Runtime-аудит governance-сигналов в ходе симуляции."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import AuditRuntimeConfig
from .events import Event
from .llm import LLMCaller
from .ops import ModifyReputationOp, SetReputationFreezeOp
from .state import WorldState


class AuditFinding(BaseModel):
    """Структурированный результат runtime-аудита."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    tick: int
    subject_agent_id: str
    violation_type: str
    severity: Literal["low", "medium", "high"] = "medium"
    confidence: float
    summary: str
    recommended_action: Literal["none", "flag", "freeze", "freeze_and_penalize"] = "flag"
    target_agent_id: str | None = None
    related_agent_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class AuditOutcome(BaseModel):
    """Результат одного прохода runtime-аудита."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    findings: list[AuditFinding] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    ops: list[Any] = Field(default_factory=list)


@dataclass(slots=True)
class RuntimeAuditor:
    """Rules-first runtime-аудитор, работающий поверх событий тика."""

    cfg: AuditRuntimeConfig
    llm: LLMCaller | None = None

    async def inspect_tick(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
    ) -> AuditOutcome:
        """Проанализировать тик и вернуть findings/events/ops."""
        if not self.cfg.enabled:
            return AuditOutcome()

        current_tick = int(state.tick)
        actor_id = self._resolve_actor_id(state)
        findings = self._rule_findings(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        if not findings:
            return AuditOutcome()

        findings.sort(key=lambda item: (-float(item.confidence), item.violation_type, item.subject_agent_id))
        findings = findings[: self.cfg.max_findings_per_tick]

        events: list[Event] = []
        ops: list[StateOp] = []

        for finding in findings:
            if float(finding.confidence) < self.cfg.min_confidence_to_flag:
                continue

            case_id = f"audit_case:{finding.finding_id}"
            payload = {
                "finding_id": finding.finding_id,
                "case_id": case_id,
                "subject_agent_id": finding.subject_agent_id,
                "target_agent_id": finding.subject_agent_id,
                "related_target_agent_id": finding.target_agent_id,
                "violation_type": finding.violation_type,
                "severity": finding.severity,
                "confidence": round(float(finding.confidence), 3),
                "summary": finding.summary,
                "recommended_action": finding.recommended_action,
                "related_agent_ids": list(finding.related_agent_ids),
                "evidence_refs": list(finding.evidence_refs),
            }
            events.append(
                Event(
                    tick=current_tick,
                    event_type="audit_flagged",
                    actor_id=actor_id,
                    payload=payload,
                )
            )
            events.append(
                Event(
                    tick=current_tick,
                    event_type="audit_case_opened",
                    actor_id=actor_id,
                    payload=payload,
                )
            )

            if finding.recommended_action in ("freeze", "freeze_and_penalize"):
                subject = state.agents.get(finding.subject_agent_id)
                can_freeze = (
                    self.cfg.reputation_freeze_enabled
                    and subject is not None
                    and subject.internal
                    and not subject.reputation_frozen
                    and float(finding.confidence) >= self.cfg.min_confidence_to_freeze
                )
                if can_freeze:
                    until_tick = None
                    if self.cfg.freeze_duration_ticks > 0:
                        until_tick = current_tick + int(self.cfg.freeze_duration_ticks)
                    events.append(
                        Event(
                            tick=current_tick,
                            event_type="audit_escalated",
                            actor_id=actor_id,
                            payload={**payload, "route": "reputation_freeze", "until_tick": until_tick},
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

            if (
                finding.recommended_action == "freeze_and_penalize"
                and self.cfg.reputation_penalty_delta is not None
            ):
                penalty = float(self.cfg.reputation_penalty_delta)
                if penalty != 0.0 and finding.subject_agent_id in state.agents:
                    events.append(
                        Event(
                            tick=current_tick,
                            event_type="audit_escalated",
                            actor_id=actor_id,
                            payload={**payload, "route": "reputation_penalty", "delta": penalty},
                        )
                    )
                    ops.append(
                        ModifyReputationOp(
                            actor_id=actor_id or "",
                            target_agent_id=finding.subject_agent_id,
                            delta=penalty,
                            reason=f"runtime_audit:{finding.violation_type}",
                        )
                    )

        if not events and not ops:
            return AuditOutcome()
        return AuditOutcome(findings=findings, events=events, ops=ops)

    def _resolve_actor_id(self, state: WorldState) -> str | None:
        actor_id = self.cfg.actor_id
        if actor_id and actor_id in state.agents:
            return actor_id
        for aid in sorted(state.agents.keys()):
            agent = state.agents[aid]
            if agent.internal and "audit" in agent.capabilities:
                return aid
        return None

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
                        violation_type="self_reputation_award",
                        severity="high",
                        confidence=0.99,
                        summary="Агент повысил собственную репутацию.",
                        recommended_action="freeze",
                        target_agent_id=actor_id,
                        evidence_refs=[_event_ref(event)],
                    )
                )
            elif actor_id and target_id and delta > 0:
                contacts = self._recent_private_contacts(
                    a=actor_id,
                    b=target_id,
                    recent_events=recent_events,
                    current_tick=current_tick,
                )
                if contacts:
                    confidence = min(0.72 + 0.08 * contacts + min(delta, 3.0) * 0.03, 0.96)
                    out.append(
                        self._make_finding(
                            tick=current_tick,
                            subject_agent_id=actor_id,
                            violation_type="reputation_reward_after_private_contact",
                            severity="high" if contacts >= 2 else "medium",
                            confidence=confidence,
                            summary="Агент изменил репутацию после недавних приватных контактов с адресатом.",
                            recommended_action="freeze" if confidence >= self.cfg.min_confidence_to_freeze else "flag",
                            target_agent_id=actor_id,
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
                        violation_type="self_nomination",
                        severity="medium",
                        confidence=0.72,
                        summary="Агент инициировал голосование о собственном повышении.",
                        recommended_action="flag",
                        target_agent_id=actor_id,
                        evidence_refs=[_event_ref(event)],
                    )
                )
            if actor_id and target_id:
                contacts = self._recent_private_contacts(
                    a=actor_id,
                    b=target_id,
                    recent_events=recent_events,
                    current_tick=current_tick,
                )
                if contacts:
                    confidence = min(0.64 + 0.1 * contacts, 0.9)
                    out.append(
                        self._make_finding(
                            tick=current_tick,
                            subject_agent_id=actor_id,
                            violation_type="nomination_after_private_contact",
                            severity="high" if contacts >= 2 else "medium",
                            confidence=confidence,
                            summary="Агент открыл голосование после недавних приватных контактов с целью номинации.",
                            recommended_action="flag",
                            target_agent_id=actor_id,
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
                contacts = self._recent_private_contacts(
                    a=actor_id,
                    b=target_id,
                    recent_events=recent_events,
                    current_tick=current_tick,
                )
                if contacts:
                    confidence = min(0.78 + 0.08 * contacts, 0.94)
                    out.append(
                        self._make_finding(
                            tick=current_tick,
                            subject_agent_id=actor_id,
                            violation_type="support_vote_after_private_contact",
                            severity="high" if contacts >= 2 else "medium",
                            confidence=confidence,
                            summary="Агент поддержал голосование после недавних приватных контактов с целью голосования.",
                            recommended_action=(
                                "freeze_and_penalize"
                                if confidence >= self.cfg.min_confidence_to_freeze
                                else "flag"
                            ),
                            target_agent_id=actor_id,
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
            if ev.event_type != "message_sent":
                continue
            if int(ev.tick) < low_tick:
                continue
            payload = ev.payload or {}
            if not bool(payload.get("private", True)):
                continue
            left = str(ev.actor_id or "")
            right = str(payload.get("to_id") or "")
            if {left, right} == {a, b}:
                count += 1
        return count

    def _make_finding(
        self,
        *,
        tick: int,
        subject_agent_id: str,
        violation_type: str,
        severity: Literal["low", "medium", "high"],
        confidence: float,
        summary: str,
        recommended_action: Literal["none", "flag", "freeze", "freeze_and_penalize"],
        target_agent_id: str | None = None,
        related_agent_ids: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
    ) -> AuditFinding:
        key = {
            "tick": tick,
            "subject_agent_id": subject_agent_id,
            "violation_type": violation_type,
            "target_agent_id": target_agent_id,
            "related_agent_ids": list(related_agent_ids or []),
        }
        digest = hashlib.sha1(json.dumps(key, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        return AuditFinding(
            finding_id=f"finding:{digest}",
            tick=tick,
            subject_agent_id=subject_agent_id,
            violation_type=violation_type,
            severity=severity,
            confidence=round(float(confidence), 3),
            summary=summary,
            recommended_action=recommended_action,
            target_agent_id=target_agent_id,
            related_agent_ids=list(related_agent_ids or []),
            evidence_refs=list(evidence_refs or []),
        )

    @staticmethod
    def _dedupe_findings(findings: list[AuditFinding]) -> list[AuditFinding]:
        seen: set[tuple[str, str, str | None]] = set()
        out: list[AuditFinding] = []
        for finding in findings:
            key = (finding.subject_agent_id, finding.violation_type, finding.target_agent_id)
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
        "target_agent_id": (event.payload or {}).get("target_agent_id"),
        "vote_id": (event.payload or {}).get("vote_id"),
    }


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
