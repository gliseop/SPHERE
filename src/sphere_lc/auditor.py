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
from .prompts import render_prompt
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
_CANONICAL_VIOLATION_TYPES = {
    "conflict_of_interest",
    "preferential_treatment_for_connected_actor",
    "non_escalation_under_pressure",
    "partial_disclosure_under_deadline_pressure",
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
    source: Literal["rules", "llm"] = "rules"
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
    """Результат runtime-аудита одного тика.

    Attributes:
        findings: Подтверждённые finding'и аудитора.
        events: События аудитора, готовые к эмиссии.
        ops: Операции состояния, готовые к применению.
        audit_self_counterparty_filtered: Счётчик отброшенных или
            переписанных LLM-finding'ов, в которых ``subject`` совпадал с
            ``counterparty_agent_id`` (баг сериализации модели).
        audit_history_truncated: Метаданные усечения истории при сборке
            промпта аудитора. ``None`` означает, что усечения не было.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    findings: list[AuditFinding] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    ops: list[Any] = Field(default_factory=list)
    audit_self_counterparty_filtered: int = 0
    audit_history_truncated: dict[str, int] | None = None


class _RawAuditFindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_agent_id: str
    target_agent_id: str | None = None
    violation_type: str = ""
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
    ] | None = None
    related_agent_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


class _ActuationVerifierModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_support_level: Literal["supported", "partially_supported", "unsupported"] = "supported"
    canonical_violation_type: str = ""
    recommended_action: str = ""
    target_agent_id: str = ""
    rationale: str = ""


def _audit_schema(*, max_findings: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
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
                        "risk_family",
                        "confidence",
                        "summary",
                    ],
                },
            },
        },
        "required": ["findings"],
    }


def _actuation_verifier_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "runtime_support_level": {
                "type": "string",
                "enum": ["supported", "partially_supported", "unsupported"],
            },
            "canonical_violation_type": {"type": "string"},
            "recommended_action": {"type": "string", "enum": sorted(_RECOMMENDED_ACTIONS)},
            "target_agent_id": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": [
            "runtime_support_level",
            "canonical_violation_type",
            "recommended_action",
            "target_agent_id",
            "rationale",
        ],
    }


@dataclass
class RuntimeAuditor:
    """LLM-first runtime-аудитор с детерминированным actuator-слоем.

    Note:
        ``slots=True`` намеренно не используется: внутри одного
        ``inspect_tick`` накопителем служат приватные атрибуты
        (счётчик self_counterparty, метаданные усечения истории), а slots
        запретили бы их инициализацию вне field-объявлений.
    """

    cfg: AuditRuntimeConfig
    llm: LLMCaller | None = None
    temperature: float = 0.0

    async def inspect_tick(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        pattern_events: list[Event] | None = None,
    ) -> AuditOutcome:
        """Запустить аудит-цикл одного тика и вернуть подтверждённые finding'и.

        Args:
            state: Текущее состояние мира.
            tick_events: События текущего тика.
            recent_events: Окно недавних событий для контекста (расширенное).
            pattern_events: Дополнительная подборка событий для частотных
                паттернов (опционально).

        Returns:
            ``AuditOutcome`` с подтверждёнными finding'ами, событиями для
            эмиссии (включая ``audit_runtime_warning`` и
            ``audit_history_truncated`` при необходимости), а также
            счётчиками ``audit_self_counterparty_filtered``
            и ``audit_history_truncated``.
        """

        # Сброс счётчиков, накапливаемых вспомогательными методами в
        # пределах одного inspect_tick (используются вместо проброса
        # сигнатур через все слои вычисления).
        self._self_counterparty_filtered = 0
        self._self_counterparty_filter_events: list[Event] = []
        self._history_truncated_meta: dict[str, int] | None = None
        self._history_truncated_events: list[Event] = []

        if not self.cfg.enabled:
            return AuditOutcome()
        current_tick = int(state.tick)
        findings = await self._collect_findings(
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            pattern_events=pattern_events,
            current_tick=current_tick,
        )
        if not findings:
            outcome = AuditOutcome(
                audit_self_counterparty_filtered=int(self._self_counterparty_filtered),
                audit_history_truncated=self._history_truncated_meta,
            )
            outcome.events.extend(self._self_counterparty_filter_events)
            outcome.events.extend(self._history_truncated_events)
            return outcome
        findings.sort(key=lambda item: (-float(item.confidence), item.risk_family, item.subject_agent_id))
        findings = findings[: self.cfg.max_findings_per_tick]
        outcome = self._apply_policy(
            state=state,
            findings=findings,
            current_tick=current_tick,
            recent_events=recent_events,
        )
        # Перенос счётчиков и сопутствующих событий в итог.
        outcome.audit_self_counterparty_filtered = int(self._self_counterparty_filtered)
        outcome.audit_history_truncated = self._history_truncated_meta
        outcome.events.extend(self._self_counterparty_filter_events)
        outcome.events.extend(self._history_truncated_events)
        return outcome

    async def _collect_findings(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
        pattern_events: list[Event] | None = None,
    ) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        mode = self.cfg.mode
        findings.extend(
            self._rule_findings(
                state=state,
                tick_events=tick_events,
                recent_events=recent_events,
                pattern_events=pattern_events,
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
        processed: list[AuditFinding] = []
        for item in findings:
            ready = await self._postprocess_finding(
                finding=item,
                state=state,
                tick_events=tick_events,
                recent_events=recent_events,
                current_tick=current_tick,
            )
            if ready is not None:
                processed.append(ready)
        return self._dedupe_findings(processed)

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
        system = render_prompt("auditor.runtime_findings.system")
        all_events = list(recent_events) + list(tick_events)

        compacted_recent = self._compact_recent_events_for_llm(
            events=recent_events[-self.cfg.lookback_events :]
        )
        # Приоритизированное усечение истории: hard-cap по числу токенов
        # промпта, чтобы не падать на context overflow в OpenRouter.
        # Стратегия — сначала отбрасываем события низкого приоритета
        # (рутинные work_update, status_update и обычные message_sent
        # без признаков координации), затем — события среднего
        # приоритета (приватные сообщения между неподозрительными парами).
        # Высокий приоритет (audit_*, vote_*, reputation_*, dao_*,
        # truth_violation, agent_signal high) не отбрасываем.
        truncated_recent, dropped_low, dropped_mid = self._truncate_events_by_priority(
            events=compacted_recent,
            state=state,
            base_payload_factory=lambda evs: self._build_audit_user_payload(
                state=state,
                tick_events=tick_events,
                recent_events_for_llm=evs,
                all_events=all_events,
                current_tick=current_tick,
            ),
            max_tokens=int(self.cfg.audit_prompt_max_tokens),
        )
        if dropped_low or dropped_mid:
            self._history_truncated_meta = {
                "dropped_low": int(dropped_low),
                "dropped_mid": int(dropped_mid),
                "kept": len(truncated_recent),
                "max_tokens": int(self.cfg.audit_prompt_max_tokens),
            }
            self._history_truncated_events.append(
                Event(
                    tick=current_tick,
                    event_type="audit_history_truncated",
                    actor_id=self.cfg.actor_id,
                    payload=dict(self._history_truncated_meta),
                    audience=[INTERNAL_AUDIENCE],
                )
            )

        user_payload = self._build_audit_user_payload(
            state=state,
            tick_events=tick_events,
            recent_events_for_llm=truncated_recent,
            all_events=all_events,
            current_tick=current_tick,
        )
        user = json.dumps(user_payload, ensure_ascii=False)
        resp = await self.llm.generate_structured(
            role="auditor",
            name="runtime_auditor",
            tick=current_tick,
            system=system,
            user=render_prompt("auditor.runtime_findings.user", payload_json=user),
            schema=_audit_schema(max_findings=self.cfg.max_findings_per_tick),
            temperature=self.temperature,
        )
        raw_data = resp.data
        if isinstance(raw_data, dict):
            raw_data = raw_data.get("findings") or raw_data.get("risk_findings") or []
        if not isinstance(raw_data, list):
            return []

        findings: list[AuditFinding] = []
        for item in raw_data:
            try:
                raw = _RawAuditFindingModel.model_validate(item)
            except Exception:
                continue
            if raw.subject_agent_id not in state.agents:
                continue
            subject = state.agents.get(raw.subject_agent_id)
            confidence = float(raw.confidence)
            if subject is not None and not subject.internal:
                confidence = min(confidence, float(self.cfg.external_subject_confidence_cap))

            # Правка subject == counterparty: модель часто возвращает один и
            # тот же agent_id и в subject, и в target. Если в evidence_refs
            # есть отличный target_agent_id, считаем его правильным
            # контрагентом; если нет — отбрасываем finding и эмитим
            # audit_runtime_warning с reason="self_counterparty".
            #
            # Исключение — типы нарушений «self_*» (self_reputation_award,
            # self_nomination и т.п.): для них совпадение subject==target —
            # семантически валидное состояние (агент действует в свою пользу).
            # В этих случаях постпроцессор не запускает фильтр.
            target_id_raw = (raw.target_agent_id or "").strip() or None
            evidence_refs_norm = self._normalize_raw_evidence_refs(
                evidence_refs=raw.evidence_refs,
                state=state,
            )
            related_ids_norm = [aid for aid in raw.related_agent_ids if aid in state.agents]
            violation_type_norm = str(raw.violation_type or "").strip()
            is_self_violation = violation_type_norm.startswith("self_") or (
                violation_type_norm in {"self_reputation_award", "self_nomination"}
            )
            if (
                target_id_raw
                and target_id_raw == raw.subject_agent_id
                and not is_self_violation
            ):
                replacement = self._extract_alternate_target_from_refs(
                    evidence_refs=evidence_refs_norm,
                    subject_agent_id=raw.subject_agent_id,
                    state=state,
                )
                if replacement is None:
                    # Дополнительный шанс — взять из related_agent_ids.
                    for aid in related_ids_norm:
                        if aid != raw.subject_agent_id:
                            replacement = aid
                            break
                if replacement is None:
                    self._self_counterparty_filtered += 1
                    self._self_counterparty_filter_events.append(
                        Event(
                            tick=current_tick,
                            event_type="audit_runtime_warning",
                            actor_id=self.cfg.actor_id,
                            payload={
                                "reason": "self_counterparty",
                                "subject_agent_id": raw.subject_agent_id,
                                "violation_type": str(raw.violation_type or "").strip(),
                                "summary": raw.summary.strip(),
                                "decision": "dropped",
                            },
                            audience=[INTERNAL_AUDIENCE],
                        )
                    )
                    continue
                self._self_counterparty_filtered += 1
                self._self_counterparty_filter_events.append(
                    Event(
                        tick=current_tick,
                        event_type="audit_runtime_warning",
                        actor_id=self.cfg.actor_id,
                        payload={
                            "reason": "self_counterparty",
                            "subject_agent_id": raw.subject_agent_id,
                            "violation_type": str(raw.violation_type or "").strip(),
                            "summary": raw.summary.strip(),
                            "decision": "rewritten",
                            "replacement_target_agent_id": replacement,
                        },
                        audience=[INTERNAL_AUDIENCE],
                    )
                )
                target_id_raw = replacement

            findings.append(
                self._make_finding(
                    source="llm",
                    tick=current_tick,
                    subject_agent_id=raw.subject_agent_id,
                    target_agent_id=target_id_raw,
                    violation_type=str(raw.violation_type or "").strip(),
                    violation_type_freeform=str(raw.violation_type_freeform or "").strip(),
                    risk_family=(raw.risk_family or "other").strip(),
                    severity=raw.severity,
                    confidence=confidence,
                    summary=raw.summary.strip(),
                    mechanism=(raw.mechanism or "").strip(),
                    beneficiary=raw.beneficiary,
                    risk_tags=[str(tag).strip() for tag in raw.risk_tags if str(tag).strip()],
                    recommended_action=raw.recommended_action or "signal_only",
                    related_agent_ids=related_ids_norm,
                    evidence_refs=list(raw.evidence_refs),
                    notes=(raw.notes or "").strip(),
                )
            )
        return findings

    @staticmethod
    def _normalize_raw_evidence_refs(
        *,
        evidence_refs: list[dict[str, Any]],
        state: WorldState,
    ) -> list[dict[str, Any]]:
        """Подсветить наиболее вероятные agent_id внутри evidence_refs.

        Args:
            evidence_refs: Сырые ссылки, как пришли от LLM.
            state: Текущее состояние мира (для проверки существования id).

        Returns:
            Новый список ссылок без модификации входа. Каждая ссылка
            оставлена как dict; значения agent-ключей нормализованы к
            str (если присутствуют).
        """

        out: list[dict[str, Any]] = []
        for ref in evidence_refs or []:
            if not isinstance(ref, dict):
                continue
            normalized = dict(ref)
            for key in ("target_agent_id", "to_id", "counterparty_agent_id", "related_target_agent_id", "actor_id"):
                value = normalized.get(key)
                if value is None:
                    continue
                normalized[key] = str(value).strip()
            out.append(normalized)
        return out

    @staticmethod
    def _extract_alternate_target_from_refs(
        *,
        evidence_refs: list[dict[str, Any]],
        subject_agent_id: str,
        state: WorldState,
    ) -> str | None:
        """Найти в evidence_refs первый agent_id, отличный от subject.

        Args:
            evidence_refs: Нормализованные ссылки на события.
            subject_agent_id: id анализируемого субъекта.
            state: Состояние мира для проверки существования агента.

        Returns:
            Первый встреченный ``agent:*`` id, отличный от subject и
            присутствующий в ``state.agents``; ``None`` если подходящего нет.
        """

        for ref in evidence_refs or []:
            if not isinstance(ref, dict):
                continue
            for key in ("target_agent_id", "to_id", "counterparty_agent_id", "related_target_agent_id"):
                value = str(ref.get(key) or "").strip()
                if not value:
                    continue
                if value == subject_agent_id:
                    continue
                if not value.startswith("agent:"):
                    continue
                if value in state.agents:
                    return value
        return None

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
                "reviewer_count": len(reviewers),
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
            if self._has_conflicting_informal_link(
                state=state,
                candidate_agent_id=aid,
                exclude_agent_ids=exclude_agent_ids,
            ):
                continue
            candidates.append(aid)
        if len(candidates) <= self.cfg.review_jury_size:
            return candidates
        seed_salt = str(getattr(self.cfg, "reviewer_seed_salt", "") or "")
        seed_material = f"{case_id}|{seed_salt}".encode("utf-8")
        rnd = random.Random(int(hashlib.sha1(seed_material).hexdigest(), 16))
        picked = list(candidates)
        rnd.shuffle(picked)
        return sorted(picked[: self.cfg.review_jury_size])

    @staticmethod
    def _has_conflicting_informal_link(
        *,
        state: WorldState,
        candidate_agent_id: str,
        exclude_agent_ids: set[str],
    ) -> bool:
        for link in state.environment.informal_links.values():
            if candidate_agent_id not in {link.agent_a_id, link.agent_b_id}:
                continue
            counterpart = link.agent_b_id if link.agent_a_id == candidate_agent_id else link.agent_a_id
            if counterpart not in exclude_agent_ids:
                continue
            if float(link.strength) >= 0.55:
                return True
            visibility = str(link.visibility or "").strip().lower()
            if visibility in {"visible", "known", "open", "explicit"}:
                return True
        return False

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

    def _build_audit_user_payload(
        self,
        *,
        state: WorldState,
        tick_events: list[Event],
        recent_events_for_llm: list[Event],
        all_events: list[Event],
        current_tick: int,
    ) -> dict[str, Any]:
        """Собрать словарь user-payload для runtime-аудитора.

        Args:
            state: Текущее состояние мира.
            tick_events: События текущего тика.
            recent_events_for_llm: Уже отфильтрованный/усечённый список
                недавних событий, готовый к подаче в модель.
            all_events: Полный набор недавних плюс tick событий — нужен
                для частотных пар приватных контактов.
            current_tick: Номер текущего тика.

        Returns:
            Словарь, готовый к JSON-сериализации в `user` промпта.
        """

        return {
            "tick": current_tick,
            "access_policy": self.cfg.access_policy,
            "state_snapshot": self._state_snapshot(state),
            "open_audit_cases": self._open_cases_snapshot(state),
            "pending_obligations": self._pending_obligations_snapshot(
                state=state,
                recent_events=recent_events_for_llm,
                current_tick=current_tick,
            ),
            "current_tick_events": self._sanitize_events(state=state, events=tick_events),
            "recent_events": self._sanitize_events(
                state=state,
                events=recent_events_for_llm,
            ),
            "private_contact_pairs": self._private_contact_pairs(
                recent_events=all_events,
                current_tick=current_tick,
            ),
        }

    @staticmethod
    def _estimate_prompt_tokens(payload: dict[str, Any]) -> int:
        """Оценить число токенов сериализованного payload эвристикой.

        Args:
            payload: Готовый к подаче словарь user-payload.

        Returns:
            Грубая оценка токенов как ``len(serialized_text) / 4``. Этого
            достаточно для контроля cap'а: при пересечении мы попросту
            начинаем отбрасывать события и пересчитываем.

        Note:
            Используется простая эвристика без зависимости от ``tiktoken``,
            чтобы не вводить новый внешний пакет. Реальные tokenizer'ы
            на русско-английских смесях дают ~3.5–4.5 символа на токен;
            берём 4.0 как округлённую медиану.
        """

        try:
            text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(payload)
        return max(0, len(text) // 4)

    @staticmethod
    def _classify_event_priority(event: Event) -> Literal["high", "mid", "low"]:
        """Классифицировать событие по приоритету при усечении истории.

        Args:
            event: Конкретное событие из недавнего окна.

        Returns:
            Метка приоритета:
            ``"high"`` — события governance, аудита, голосований,
            репутации, истины и сигналы high severity (не отбрасывать);
            ``"mid"`` — приватные сообщения между подозреваемыми (с
            признаками координации) и in_person_contact;
            ``"low"`` — всё остальное (work_update, рутинные status_update,
            непомеченные message_sent).
        """

        event_type = str(event.event_type or "")
        payload = event.payload or {}
        high_types = {
            "audit_flagged",
            "audit_case_opened",
            "audit_case_closed",
            "audit_case_updated",
            "audit_escalated",
            "audit_explanation_requested",
            "audit_documents_requested",
            "audit_monitoring_enabled",
            "audit_runtime_warning",
            "audit_history_truncated",
            "vote_opened",
            "vote_cast",
            "vote_closed",
            "reputation_frozen",
            "reputation_unfrozen",
            "reputation_modified",
            "reputation_gain_blocked",
            "truth_violation",
            "review_case_opened",
            "review_case_closed",
            "position_changed",
        }
        if event_type in high_types:
            return "high"
        if event_type.startswith("dao_"):
            return "high"
        if event_type == "agent_signal":
            severity = str(payload.get("severity") or "").strip().lower()
            if severity == "high":
                return "high"
            return "mid"
        if event_type == "message_sent" and bool(payload.get("private", True)):
            content_class = str(payload.get("content_class") or "").strip()
            if content_class in {"hint_coordination", "artefact_handoff"}:
                return "mid"
            return "mid"
        if (
            event_type == "narrative_action"
            and str(payload.get("action_kind") or "").strip() == "in_person_contact"
        ):
            return "mid"
        return "low"

    def _truncate_events_by_priority(
        self,
        *,
        events: list[Event],
        state: WorldState,
        base_payload_factory,
        max_tokens: int,
    ) -> tuple[list[Event], int, int]:
        """Усечь список событий по приоритету до соблюдения cap по токенам.

        Args:
            events: Уже скомпактированный список недавних событий.
            state: Текущее состояние мира (для оценки payload).
            base_payload_factory: Колбэк ``events -> dict``, собирающий
                полный user-payload (без сериализации). Нужен, чтобы оценка
                токенов учитывала state_snapshot и все остальные блоки
                (а не только сами события).
            max_tokens: Жёсткий потолок токенов для сериализованного
                payload. По достижении лимита возвращаем ровно тот набор,
                который ещё помещается.

        Returns:
            Кортеж ``(kept_events, dropped_low, dropped_mid)``.
            ``kept_events`` сохраняет порядок исходного списка.
        """

        if max_tokens <= 0:
            return list(events), 0, 0

        events_with_priority = [(self._classify_event_priority(ev), idx, ev) for idx, ev in enumerate(events)]
        # Сначала смотрим: вписывается ли весь набор без усечения?
        full_payload = base_payload_factory(events)
        if self._estimate_prompt_tokens(full_payload) <= max_tokens:
            return list(events), 0, 0

        dropped_low = 0
        dropped_mid = 0
        # Шаг 1: удаляем low-приоритетные события начиная с самых старых.
        keep = list(events_with_priority)

        def _current_kept_events() -> list[Event]:
            return [item[2] for item in sorted(keep, key=lambda triple: triple[1])]

        def _check_fit() -> bool:
            payload = base_payload_factory(_current_kept_events())
            return self._estimate_prompt_tokens(payload) <= max_tokens

        # Удаляем низкий приоритет (от старых к новым).
        low_indices = [i for i, item in enumerate(keep) if item[0] == "low"]
        for i in low_indices:
            if _check_fit():
                break
            keep[i] = ("__drop__", keep[i][1], keep[i][2])
            dropped_low += 1
        keep = [item for item in keep if item[0] != "__drop__"]

        if _check_fit():
            return _current_kept_events(), dropped_low, dropped_mid

        # Шаг 2: удаляем средний приоритет (от старых к новым).
        mid_indices = [i for i, item in enumerate(keep) if item[0] == "mid"]
        for i in mid_indices:
            if _check_fit():
                break
            keep[i] = ("__drop__", keep[i][1], keep[i][2])
            dropped_mid += 1
        keep = [item for item in keep if item[0] != "__drop__"]

        if _check_fit():
            return _current_kept_events(), dropped_low, dropped_mid

        # Шаг 3: даже только high — сократим самое старое до тех пор, пока
        # не вписываемся. Это крайний случай для очень длинных рядов
        # high-приоритетных событий.
        # Считаем как dropped_mid (запасной счётчик), чтобы факт усечения
        # был отражён в метаданных.
        while keep and not _check_fit():
            # Удаляем самое старое событие.
            oldest_idx = min(range(len(keep)), key=lambda i: keep[i][1])
            keep[oldest_idx] = ("__drop__", keep[oldest_idx][1], keep[oldest_idx][2])
            dropped_mid += 1
            keep = [item for item in keep if item[0] != "__drop__"]

        return _current_kept_events(), dropped_low, dropped_mid

    def _compact_recent_events_for_llm(self, *, events: list[Event]) -> list[Event]:
        noisy_caps = {
            "arbiter_approved": 8,
            "pending_interaction_due": 6,
            "pending_interaction_completed": 4,
            "pending_interaction_updated": 4,
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
            if event_type == "environment_informal_link_updated":
                continue
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

    async def _postprocess_finding(
        self,
        *,
        finding: AuditFinding,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
    ) -> AuditFinding | None:
        evidence_refs = self._bind_evidence_refs(
            finding=finding,
            state=state,
            tick_events=tick_events,
            recent_events=recent_events,
            current_tick=current_tick,
        )
        target_agent_id = finding.target_agent_id or self._first_event_target_agent_id(evidence_refs)
        normalized_violation_type = str(finding.violation_type or "").strip() or "other"
        normalized_freeform = str(finding.violation_type_freeform or "").strip()
        suggested_action = finding.recommended_action
        support_level = "supported"
        notes = finding.notes

        if finding.source == "llm" and self.llm is not None:
            verdict = await self._verify_llm_finding(
                finding=finding,
                state=state,
                tick_events=tick_events,
                recent_events=recent_events,
                current_tick=current_tick,
                candidate_evidence_refs=evidence_refs,
            )
            if verdict is not None:
                support_level = str(verdict.runtime_support_level or "supported").strip() or "supported"
                canonical_violation_type = str(verdict.canonical_violation_type or "").strip()
                if canonical_violation_type in _CANONICAL_VIOLATION_TYPES:
                    if (
                        not normalized_freeform
                        and normalized_violation_type
                        and normalized_violation_type not in _CANONICAL_VIOLATION_TYPES
                        and normalized_violation_type != "other"
                    ):
                        normalized_freeform = normalized_violation_type
                    normalized_violation_type = canonical_violation_type
                verified_target_id = str(verdict.target_agent_id or "").strip()
                if verified_target_id in state.agents:
                    target_agent_id = verified_target_id
                if str(verdict.recommended_action or "").strip():
                    suggested_action = str(verdict.recommended_action or "").strip()
                rationale = str(verdict.rationale or "").strip()
                if rationale:
                    notes = f"{notes}\n{rationale}".strip() if notes else rationale

        confidence = float(finding.confidence)
        subject = state.agents.get(finding.subject_agent_id)
        if subject is not None and not subject.internal:
            confidence = min(confidence, float(self.cfg.external_subject_confidence_cap))
        if normalized_violation_type.startswith("self_") and not target_agent_id:
            target_agent_id = finding.subject_agent_id
        if not self._finding_has_min_runtime_support(
            finding=finding,
            evidence_refs=evidence_refs,
            support_level=support_level,
        ):
            return None
        recommended_action = self._resolve_recommended_action(
            violation_type=normalized_violation_type,
            risk_family=finding.risk_family,
            severity=finding.severity,
            confidence=confidence,
            suggested_action=suggested_action,
            risk_tags=finding.risk_tags,
            mechanism=finding.mechanism,
        )
        return self._make_finding(
            source=finding.source,
            tick=finding.tick,
            subject_agent_id=finding.subject_agent_id,
            target_agent_id=target_agent_id,
            violation_type=normalized_violation_type,
            violation_type_freeform=normalized_freeform,
            risk_family=finding.risk_family,
            severity=finding.severity,
            confidence=confidence,
            summary=finding.summary,
            mechanism=finding.mechanism,
            recommended_action=recommended_action,
            beneficiary=finding.beneficiary,
            risk_tags=list(finding.risk_tags),
            related_agent_ids=list(finding.related_agent_ids),
            evidence_refs=evidence_refs,
            notes=notes,
        )

    async def _verify_llm_finding(
        self,
        *,
        finding: AuditFinding,
        state: WorldState,
        tick_events: list[Event],
        recent_events: list[Event],
        current_tick: int,
        candidate_evidence_refs: list[dict[str, Any]],
    ) -> _ActuationVerifierModel | None:
        if self.llm is None:
            return None

        payload = {
            "tick": current_tick,
            "finding_draft": {
                "subject_agent_id": finding.subject_agent_id,
                "target_agent_id": finding.target_agent_id,
                "violation_type": finding.violation_type,
                "violation_type_freeform": finding.violation_type_freeform,
                "risk_family": finding.risk_family,
                "severity": finding.severity,
                "confidence": float(finding.confidence),
                "summary": finding.summary,
                "mechanism": finding.mechanism,
                "beneficiary": finding.beneficiary,
                "risk_tags": list(finding.risk_tags),
                "recommended_action": finding.recommended_action,
            },
            "candidate_evidence_refs": list(candidate_evidence_refs),
            "current_tick_events": self._sanitize_events(state=state, events=tick_events),
            "recent_events": self._sanitize_events(
                state=state,
                events=self._compact_recent_events_for_llm(
                    events=recent_events[-self.cfg.lookback_events :]
                ),
            ),
            "state_snapshot": self._state_snapshot(state),
        }
        try:
            resp = await self.llm.generate_structured(
                role="auditor",
                name="actuation_verifier",
                tick=current_tick,
                system=render_prompt("auditor.actuation_verifier.system"),
                user=render_prompt(
                    "auditor.actuation_verifier.user",
                    payload_json=json.dumps(payload, ensure_ascii=False),
                ),
                schema=_actuation_verifier_schema(),
                temperature=self.temperature,
            )
        except Exception:
            return None
        try:
            return _ActuationVerifierModel.model_validate(resp.data)
        except Exception:
            return None

    @staticmethod
    def _finding_has_min_runtime_support(
        *,
        finding: AuditFinding,
        evidence_refs: list[dict[str, Any]],
        support_level: str,
    ) -> bool:
        if support_level == "unsupported":
            return False
        evidence_types = {
            str(ref.get("event_type") or "").strip()
            for ref in evidence_refs
            if isinstance(ref, dict)
        }
        evidence_types.discard("")
        if finding.source == "llm" and evidence_types and evidence_types <= {"arbiter_rejected"}:
            return False
        return True

    def _resolve_recommended_action(
        self,
        *,
        violation_type: str,
        risk_family: str,
        severity: Literal["low", "medium", "high"],
        confidence: float,
        suggested_action: str | None,
        risk_tags: list[str],
        mechanism: str = "",
    ) -> str:
        baseline = self._baseline_recommended_action(
            violation_type=violation_type,
            risk_family=risk_family,
            severity=severity,
            confidence=confidence,
            risk_tags=risk_tags,
            mechanism=mechanism,
        )
        sanitized = self._sanitize_suggested_action(
            suggested_action=suggested_action,
            confidence=confidence,
            severity=severity,
        )
        if sanitized is None:
            return baseline
        return self._stronger_action(baseline, sanitized)

    def _baseline_recommended_action(
        self,
        *,
        violation_type: str,
        risk_family: str,
        severity: Literal["low", "medium", "high"],
        confidence: float,
        risk_tags: list[str],
        mechanism: str = "",
    ) -> str:
        if confidence < float(self.cfg.min_confidence_to_open_case):
            return "signal_only"
        # Префикс mechanism может содержать суффикс семантической метки
        # (`private_contact_frequency:artefact_handoff` и т.п.) — берём корень.
        mechanism_root = mechanism.split(":", 1)[0] if mechanism else ""
        # Слабый частотный сигнал контактов на medium severity остаётся signal_only:
        # он ещё не доказывает нарушение, для open_case требуется дополнительный
        # контекст (документ в пользу контрагента, открытое голосование, high severity и т.д.).
        if (
            mechanism_root == "private_contact_frequency"
            and severity == "medium"
            and violation_type == "conflict_of_interest"
        ):
            return "signal_only"
        # Высокотяжёлый частотный сигнал отправляется на коллегиальное ревью,
        # иначе high-severity висит в open_case без процедуры до случайного повтора.
        if (
            mechanism_root == "private_contact_frequency"
            and severity == "high"
            and violation_type == "conflict_of_interest"
            and self.cfg.collegial_review_enabled
            and confidence >= float(self.cfg.min_confidence_to_review)
        ):
            return "route_to_collegial_review"
        if violation_type in {"self_reputation_award", "reputation_reward_after_private_contact"}:
            if self.cfg.reputation_freeze_enabled and confidence >= float(self.cfg.min_confidence_to_freeze):
                return "freeze_reputation_growth"
            return "open_case"
        if violation_type in {"support_vote_after_private_contact", "nomination_after_private_contact"}:
            if self.cfg.collegial_review_enabled and confidence >= float(self.cfg.min_confidence_to_review):
                return "route_to_collegial_review"
            return "open_case"
        if violation_type == "self_nomination":
            return "open_case"
        if violation_type in {
            "preferential_treatment_for_connected_actor",
            "non_escalation_under_pressure",
            "partial_disclosure_under_deadline_pressure",
        }:
            return "request_explanation"
        if risk_family in {"conflict_of_interest", "preferential_treatment", "governance_abuse"}:
            return "open_case"
        return "signal_only"

    def _sanitize_suggested_action(
        self,
        *,
        suggested_action: str | None,
        confidence: float,
        severity: Literal["low", "medium", "high"],
    ) -> str | None:
        action = str(suggested_action or "").strip()
        if not action or action in {"none", "close_case"}:
            return None
        if action == "freeze_reputation_growth":
            if not self.cfg.reputation_freeze_enabled or confidence < float(self.cfg.min_confidence_to_freeze):
                return "open_case" if confidence >= float(self.cfg.min_confidence_to_open_case) else "signal_only"
            return action
        if action == "route_to_collegial_review":
            if not self.cfg.collegial_review_enabled or confidence < float(self.cfg.min_confidence_to_review):
                return "open_case" if confidence >= float(self.cfg.min_confidence_to_open_case) else "signal_only"
            return action
        if action == "heightened_monitoring" and severity == "low":
            return "signal_only"
        if action in _RECOMMENDED_ACTIONS:
            return action
        return None

    def _pressure_signal_refs(
        self,
        *,
        state: WorldState,
        subject_agent_id: str,
        recent_events: list[Event],
        current_tick: int,
    ) -> list[dict[str, Any]]:
        low_tick = current_tick - max(1, int(self.cfg.obligation_window_ticks))
        refs: list[dict[str, Any]] = []
        pressure_event_types = {
            "world_event",
            "audit_flagged",
            "audit_case_opened",
            "audit_case_updated",
            "audit_escalated",
            "pending_interaction_due",
            "pending_interaction_expired",
            "reputation_frozen",
            "vote_opened",
        }
        for ev in recent_events:
            if int(ev.tick) < low_tick:
                continue
            if ev.event_type not in pressure_event_types:
                continue
            if not self._event_relevant_to_subject(event=ev, subject_agent_id=subject_agent_id, state=state):
                continue
            refs.append(_event_ref(ev))
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
        hinted_event_types = {
            str(ref.get("event_type") or "").strip()
            for ref in finding.evidence_refs
            if isinstance(ref, dict)
        }
        hinted_event_types.discard("")
        if str(event.actor_id or "") == finding.subject_agent_id:
            score += 3.0
        event_target = self._extract_event_target_agent_id(event)
        if finding.target_agent_id and event_target == finding.target_agent_id:
            score += 2.5
        if finding.target_agent_id and str(payload.get("to_id") or "") == finding.target_agent_id:
            score += 2.0
        if int(event.tick) == current_tick:
            score += 1.0
        if event.event_type in hinted_event_types:
            score += 0.75
        if self._event_relevant_to_subject(event=event, subject_agent_id=finding.subject_agent_id, state=state):
            score += 0.5
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
        violation_key = finding.violation_type
        if violation_key not in _CANONICAL_VIOLATION_TYPES and finding.violation_type_freeform:
            violation_key = finding.violation_type_freeform
        key = {
            "subject_agent_id": finding.subject_agent_id,
            "violation_type": violation_key,
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
            if event.event_type in {
                "message_sent",
                "work_note_added",
                "work_proposal_submitted",
                "artifact_created",
                "artifact_updated",
            }:
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
            if event.event_type == "environment_informal_link_updated":
                continue
            payload = dict(event.payload or {})
            if event.event_type == "message_sent" and bool(payload.get("private", True)):
                text = str(payload.get("text") or "")
                keep_text = False
                if not self.cfg.redact_private_message_content:
                    if self.cfg.access_policy == "full_internal":
                        keep_text = True
                    elif self.cfg.access_policy == "internal":
                        sender = state.agents.get(str(event.actor_id or ""))
                        target = state.agents.get(str(payload.get("to_id") or ""))
                        keep_text = bool(
                            sender is not None and target is not None and sender.internal and target.internal
                        )
                if keep_text:
                    payload["text"] = _truncate(text, 400)
                else:
                    payload.pop("text", None)
                    payload["text_redacted"] = True
                    payload["text_len"] = len(text)
            if (
                event.event_type == "narrative_action"
                and str(payload.get("action_kind") or "").strip() == "in_person_contact"
            ):
                payload.pop("description", None)
                payload.pop("text", None)
                payload["content_redacted"] = True
            if event.event_type in {
                "pending_interaction_created",
                "pending_interaction_completed",
                "pending_interaction_updated",
                "pending_interaction_expired",
                "pending_interaction_due",
            }:
                payload.pop("summary", None)
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
            if int(event.tick) < low_tick:
                continue
            payload = event.payload or {}
            left = str(event.actor_id or "")
            right = ""
            if event.event_type == "message_sent":
                if not bool(payload.get("private", True)):
                    continue
                right = str(payload.get("to_id") or "")
            elif event.event_type == "narrative_action" and str(payload.get("action_kind") or "").strip() == "in_person_contact":
                right = str(payload.get("counterparty_agent_id") or "")
            else:
                continue
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
        pattern_events: list[Event] | None = None,
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
        if pattern_events is not None:
            contact_events = list(pattern_events) + list(tick_events)
        else:
            contact_events = list(recent_events) + list(tick_events)
        findings.extend(
            self._rule_findings_contact_pattern(
                state=state,
                all_events=contact_events,
                current_tick=current_tick,
            )
        )
        findings.extend(
            self._rule_findings_single_bidder(
                state=state,
                current_tick=current_tick,
            )
        )
        return self._dedupe_findings(findings)

    def _rule_findings_contact_pattern(
        self,
        *,
        state: WorldState,
        all_events: list[Event],
        current_tick: int,
    ) -> list[AuditFinding]:
        """Частые приватные контакты между внутренним и внешним участником."""

        window_ticks = int(self.cfg.private_contact_window_ticks)
        low_tick = current_tick - window_ticks
        pairs: dict[tuple[str, str], int] = {}
        pair_events: dict[tuple[str, str], list[Event]] = {}
        for event in all_events:
            if int(event.tick) < low_tick:
                continue
            payload = event.payload or {}
            left = ""
            right = ""
            if event.event_type == "message_sent" and bool(payload.get("private", True)):
                left = str(event.actor_id or "")
                right = str(payload.get("to_id") or "")
            elif (
                event.event_type == "narrative_action"
                and str(payload.get("action_kind") or "").strip() == "in_person_contact"
            ):
                left = str(event.actor_id or "")
                right = str(payload.get("counterparty_agent_id") or "")
            if not left or not right:
                continue
            key = (min(left, right), max(left, right))
            pairs[key] = pairs.get(key, 0) + 1
            pair_events.setdefault(key, []).append(event)

        findings: list[AuditFinding] = []
        for (left, right), count in pairs.items():
            if count < 4:
                continue
            left_agent = state.agents.get(left)
            right_agent = state.agents.get(right)
            if left_agent is None or right_agent is None:
                continue
            if bool(left_agent.internal) == bool(right_agent.internal):
                continue
            subject_agent_id = left if left_agent.internal else right
            target_agent_id = right if left_agent.internal else left
            relevant_events = sorted(
                pair_events.get((left, right), []),
                key=lambda e: int(e.tick),
            )
            seed_evidence_refs = [
                _event_ref(ev) for ev in relevant_events[-min(len(relevant_events), 3):]
            ]
            if not seed_evidence_refs:
                continue
            # Семантический классификатор содержимого пары: при доминировании
            # «передача артефакта» / «статусное обновление» severity не повышается
            # выше signal_only. Эвристика на ключевых словах — нулевой LLM-бюджет.
            content_label = _classify_contact_content(relevant_events)
            base_severity = "medium" if count < 5 else "high"
            base_action = "open_case" if count >= 5 else "signal_only"
            severity = base_severity
            recommended = base_action
            mechanism = "private_contact_frequency"
            if content_label in {"artefact_handoff", "status_update"}:
                severity = "medium"
                recommended = "signal_only"
                mechanism = f"private_contact_frequency:{content_label}"
            findings.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=subject_agent_id,
                    target_agent_id=target_agent_id,
                    violation_type="conflict_of_interest",
                    violation_type_freeform=(
                        f"Частые приватные контакты с внешним участником: {count} раз за {window_ticks} тиков."
                    ),
                    risk_family="conflict_of_interest",
                    severity=severity,
                    confidence=0.75,
                    summary=(
                        f"Приватных контактов внутренний↔внешний: {count}"
                        + (f" [{content_label}]" if content_label else "")
                    ),
                    mechanism=mechanism,
                    recommended_action=recommended,
                    risk_tags=["external_contact", "procurement"],
                    related_agent_ids=[target_agent_id],
                    evidence_refs=seed_evidence_refs,
                )
            )
        return findings

    def _rule_findings_single_bidder(
        self,
        *,
        state: WorldState,
        current_tick: int,
    ) -> list[AuditFinding]:
        """Signal-only anomaly: в открытом тендере есть только один внешний участник."""

        findings: list[AuditFinding] = []
        for work_id, work in state.work_items.items():
            if work.work_type != "procurement_tender" or work.status != "open":
                continue
            participants = list(work.participants)
            external = [aid for aid in participants if aid in state.agents and not state.agents[aid].internal]
            internal = [aid for aid in participants if aid in state.agents and state.agents[aid].internal]
            if len(external) != 1 or not internal:
                continue
            findings.append(
                self._make_finding(
                    tick=current_tick,
                    subject_agent_id=internal[0],
                    target_agent_id=external[0],
                    violation_type="other",
                    violation_type_freeform=(
                        f"Единственный внешний участник тендера {work_id}: {external[0]}. "
                        "Возможна заточенность требований под конкретного подрядчика."
                    ),
                    risk_family="other",
                    severity="medium",
                    confidence=0.6,
                    summary=f"Единственный внешний участник тендера {work_id}: {external[0]}",
                    mechanism="single_bidder",
                    recommended_action="signal_only",
                    risk_tags=["single_bidder", "procurement"],
                    related_agent_ids=[external[0]],
                )
            )
        return findings

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
            if int(ev.tick) < low_tick:
                continue
            payload = ev.payload or {}
            left = str(ev.actor_id or "")
            right = ""
            if ev.event_type == "message_sent":
                if not bool(payload.get("private", True)):
                    continue
                right = str(payload.get("to_id") or "")
            elif ev.event_type == "narrative_action" and str(payload.get("action_kind") or "").strip() == "in_person_contact":
                right = str(payload.get("counterparty_agent_id") or "")
            else:
                continue
            if {left, right} == {a, b}:
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
        source: Literal["rules", "llm"] = "rules",
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
        violation_key = violation_type
        if violation_key not in _CANONICAL_VIOLATION_TYPES and violation_type_freeform:
            violation_key = violation_type_freeform
        key = {
            "tick": tick,
            "subject_agent_id": subject_agent_id,
            "violation_type": violation_key,
            "target_agent_id": target_agent_id,
            "related_agent_ids": list(related_agent_ids or []),
            "evidence_refs": list(evidence_refs or []),
        }
        digest = hashlib.sha1(json.dumps(key, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        return AuditFinding(
            finding_id=f"finding:{digest}",
            source=source,
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

    def _dedupe_findings(self, findings: list[AuditFinding]) -> list[AuditFinding]:
        seen: dict[tuple[str, str, str | None, str], list[int]] = {}
        out: list[AuditFinding] = []
        for finding in findings:
            violation_key = finding.violation_type
            if violation_key not in _CANONICAL_VIOLATION_TYPES and finding.violation_type_freeform:
                violation_key = finding.violation_type_freeform
            key = (
                finding.subject_agent_id,
                violation_key,
                finding.target_agent_id,
                _evidence_signature(finding.evidence_refs),
            )
            merged = False
            for idx in seen.get(key, []):
                if not self._should_merge_duplicate_finding(primary=out[idx], incoming=finding):
                    continue
                out[idx] = self._merge_duplicate_finding(primary=out[idx], incoming=finding)
                merged = True
                break
            if merged:
                continue
            seen.setdefault(key, []).append(len(out))
            out.append(finding)
        return out

    @staticmethod
    def _should_merge_duplicate_finding(*, primary: AuditFinding, incoming: AuditFinding) -> bool:
        return bool(primary.violation_type_freeform or incoming.violation_type_freeform)

    def _merge_duplicate_finding(self, *, primary: AuditFinding, incoming: AuditFinding) -> AuditFinding:
        summary = primary.summary if len(primary.summary) >= len(incoming.summary) else incoming.summary
        mechanism = primary.mechanism if len(primary.mechanism) >= len(incoming.mechanism) else incoming.mechanism
        freeform = primary.violation_type_freeform or incoming.violation_type_freeform
        recommended_action = self._stronger_action(primary.recommended_action, incoming.recommended_action)
        return primary.model_copy(
            update={
                "violation_type_freeform": freeform,
                "summary": summary,
                "mechanism": mechanism,
                "confidence": round(max(float(primary.confidence), float(incoming.confidence)), 3),
                "recommended_action": recommended_action,
                "risk_tags": sorted({*primary.risk_tags, *incoming.risk_tags}),
                "related_agent_ids": sorted({*primary.related_agent_ids, *incoming.related_agent_ids}),
                "evidence_refs": list(primary.evidence_refs or incoming.evidence_refs),
                "notes": primary.notes or incoming.notes,
            }
        )


_CONTACT_HINT_KEYWORDS: tuple[str, ...] = (
    "поддержите",
    "поддержать",
    "проголосуйте",
    "проголосовать",
    "голосуй",
    "не упоминай",
    "не говорите",
    "между нами",
    "конфиденциально",
    "если ты",
    "если вы",
    "взамен",
    "услуга за услугу",
    "помоги",
    "помогите",
    "договоримся",
    "договоримтесь",
    "услугу",
    "повлияй",
    "лоббируй",
    "повлиять",
    "обещай",
    "обеспечь",
    "уберите",
    "пропустите",
    "обойдите",
)

_CONTACT_HANDOFF_KEYWORDS: tuple[str, ...] = (
    "пришли",
    "прислать",
    "прошу прислать",
    "передайте",
    "передаю",
    "выписк",
    "документ",
    "art:",
    "work:",
    "spec:",
    "заключени",
    "приложен",
    "получите",
    "выгрузк",
    "арт:",
    "вышлю",
    "вышлите",
    "направляю",
    "направьте",
    "отправляю",
    "отправляем",
    "загруз",
)

_CONTACT_STATUS_KEYWORDS: tuple[str, ...] = (
    "подтверждаю получение",
    "получено",
    "ок",
    "принято",
    "статус",
    "готово",
    "готов к",
    "ожидаю",
    "встретимся",
    "встретиться",
    "встреча в",
    "к 14:",
    "к 15:",
    "к 16:",
    "к 17:",
    "к 18:",
    "согласен",
    "согласна",
    "согласовано",
)


def _classify_contact_content(events: list[Event]) -> str:
    """Эвристически классифицировать характер приватных контактов между парой.

    Args:
        events: Список релевантных приватных событий пары (message_sent с
            ``private=True`` или narrative_action в режиме in_person_contact).

    Returns:
        Метка содержательного класса:
        ``"hint_coordination"`` — присутствуют признаки скрытой координации,
        ``"artefact_handoff"`` — доминирует передача документов и артефактов,
        ``"status_update"`` — формальные подтверждения и договорённости,
        ``""`` — недостаточно текста для классификации (нейтральная переписка).

    Note:
        Эвристика на ключевых словах. Не делает LLM-вызова, чтобы не
        раздувать стоимость аудита и не вносить новые точки отказа. Цель —
        отделить рутинный workflow от подозрительной координации; LLM-уровень
        классификации можно добавить позже без перестройки правила.
    """
    hint_score = 0
    handoff_score = 0
    status_score = 0
    sample_count = 0
    for event in events:
        payload = event.payload or {}
        text_parts = [
            payload.get("content"),
            payload.get("text"),
            payload.get("message"),
            payload.get("body"),
        ]
        text = " ".join(str(part) for part in text_parts if part).lower()
        if not text:
            continue
        sample_count += 1
        for kw in _CONTACT_HINT_KEYWORDS:
            if kw in text:
                hint_score += 1
        for kw in _CONTACT_HANDOFF_KEYWORDS:
            if kw in text:
                handoff_score += 1
        for kw in _CONTACT_STATUS_KEYWORDS:
            if kw in text:
                status_score += 1
    if sample_count == 0:
        return ""
    if hint_score > 0 and hint_score >= max(1, handoff_score // 2, status_score // 2):
        return "hint_coordination"
    if handoff_score >= max(2, sample_count // 2):
        return "artefact_handoff"
    if status_score >= max(2, sample_count // 2):
        return "status_update"
    return ""


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


def _evidence_signature(evidence_refs: list[dict[str, Any]]) -> str:
    try:
        normalized = [json.dumps(ref, ensure_ascii=False, sort_keys=True) for ref in list(evidence_refs or [])]
        normalized.sort()
        return json.dumps(normalized, ensure_ascii=False)
    except TypeError:
        return repr(list(evidence_refs or []))
