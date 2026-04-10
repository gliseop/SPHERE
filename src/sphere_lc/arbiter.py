"""Арбитр действий: антифантомы + YAML-journal контекст.

В SPHERE-LC арбитр выполняет две задачи:
1) Валидирует действия (причинность мира, существование целей).
2) Преобразует действия в детерминированные `StateOp[]`.

Для структурированных действий используется детерминированное правило.
Для `perform` (свободное действие) используется LLM с YAML-журналом мира.
"""

from __future__ import annotations

import asyncio
import copy
import re
from datetime import date, timedelta
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ConfigDict

from .actions import (
    Action,
    ActionType,
    SPAWN_AGENT_ALLOWED_CAPABILITIES,
    CastVoteAction,
    CreateWorkItemAction,
    NoopAction,
    PerformAction,
    PublishAction,
    RequestEntityAction,
    RespondNominationAction,
    SendMessageAction,
    SpawnAgentAction,
    SubmitWorkProposalAction,
    AddWorkNoteAction,
    NominatePositionChangeAction,
)
from .config import GovernanceConfig, RuntimeConfig
from .dao import DaoEngine
from .id_alloc import IdAllocator
from .ids import EntityKind, ensure_kind, make_id, normalize_slug, parse_typed_id
from .llm import LLMCaller
from .ops import (
    AddInformationSignalOp,
    AddWorkNoteOp,
    CastVoteOp,
    CreateAgentOp,
    CreateArtifactOp,
    CreateEntityOp,
    CreateWorkItemOp,
    ModifyReputationOp,
    OpenVoteOp,
    RecordNarrativeActionOp,
    ResolvePendingInteractionOp,
    SendMessageOp,
    SetVoteConsentOp,
    SubmitWorkProposalOp,
    UpdateArtifactOp,
    UpsertInformalLinkOp,
    UpsertPendingInteractionOp,
    StateOp,
)
from .prompts import render_prompt
from .state import WorldState
from .utils import (
    looks_like_machine_name,
    normalize_agent_display_name,
    normalize_whitespace,
)


_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DOTTED_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_CAMEL_TO_SNAKE_RE = re.compile(r"(?<!^)(?=[A-Z])")
_TYPED_ID_RE = re.compile(r"\b[a-z]+:[A-Za-z0-9][A-Za-z0-9_.-]*\b")


def _extract_dates(text: str) -> list[date]:
    out: list[date] = []
    for match in _ISO_DATE_RE.findall(text or ""):
        try:
            out.append(date.fromisoformat(match))
        except ValueError:
            continue
    for match in _DOTTED_DATE_RE.findall(text or ""):
        try:
            day, month, year = match.split(".")
            out.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return out


def _normalize_perform_op_type(op_type: str) -> str:
    raw = (op_type or "").strip()
    if not raw:
        return raw
    explicit = {
        "SendMessageOp": "send_message",
        "message": "send_message",
        "PublishOp": "send_message",
        "CreateWorkOp": "create_work_item",
        "CreateWorkItemOp": "create_work_item",
        "AddWorkNoteOp": "add_work_note",
        "SubmitWorkProposalOp": "submit_work_proposal",
        "CreateEntityOp": "create_entity",
        "OpenVoteOp": "open_vote",
        "CastVoteOp": "cast_vote",
        "SetVoteConsentOp": "respond_nomination",
        "RespondNominationOp": "respond_nomination",
        "ModifyReputationOp": "modify_reputation",
        "CreateArtifactOp": "create_artifact",
        "UpdateArtifactOp": "update_artifact",
        "RecordNarrativeActionOp": "narrative_action",
        "record_narrative_action": "narrative_action",
        "add_narrative_action": "narrative_action",
        "private_contact": "in_person_contact",
        "PrivateContactOp": "in_person_contact",
        "InPersonContactOp": "in_person_contact",
        "UpsertInformalLinkOp": "upsert_informal_link",
        "information_signal": "add_information_signal",
        "AddInformationSignalOp": "add_information_signal",
        "UpsertPendingInteractionOp": "upsert_pending_interaction",
        "create_pending_interaction": "upsert_pending_interaction",
        "ResolvePendingInteractionOp": "resolve_pending_interaction",
        "NoopOp": "noop",
    }
    if raw in explicit:
        return explicit[raw]
    if raw.endswith("_op"):
        raw = raw[:-3]
        if raw in explicit:
            return explicit[raw]
    if raw.endswith("Op"):
        raw = raw[:-2]
    if raw.islower():
        return raw
    normalized = _CAMEL_TO_SNAKE_RE.sub("_", raw).lower()
    return normalized


@dataclass(slots=True)
class ActionResult:
    """Результат арбитража одного действия."""

    action_index: int
    approved: bool
    reason: str
    ops: list[StateOp]


class _PerformOpModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_type: str
    args: dict[str, Any]
    source: str = "ops"


class _PerformArbiterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str = ""
    ops: list[_PerformOpModel] = []


class _PerformPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[str] = []


class _DocumentGroundingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_level: str = "supported"
    rewritten_text: str = ""
    rationale: str = ""


class _ObservationVerifierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_only: bool = False
    rationale: str = ""


@dataclass(slots=True)
class _ResolvedContextualId:
    status: str
    value: str = ""


def _normalize_perform_op_args(op_type: str, args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)

    def _move(target: str, *sources: str) -> None:
        if target in normalized and normalized.get(target) not in (None, ""):
            return
        for source in sources:
            if source in normalized and normalized.get(source) not in (None, ""):
                normalized[target] = normalized[source]
                return

    if op_type == "send_message":
        _move("to_id", "to_id", "to_agent_id", "channel_id", "target_agent_id")
        _move("text", "text", "message", "content", "description")
        _move("private", "private", "is_private")
        if "private" not in normalized and "channel_id" in normalized:
            normalized["private"] = False
    elif op_type == "in_person_contact":
        _move("target_agent_id", "target_agent_id", "to_id", "to_agent_id", "agent_b_id", "counterparty_agent_id")
        _move("summary", "summary", "description", "text", "content", "message")
        _move("zone_id", "zone_id", "location_zone_id")
    elif op_type in {"add_work_note", "submit_work_proposal"}:
        _move("text", "text", "note", "note_text", "content", "description", "summary")
    elif op_type == "upsert_pending_interaction":
        _move("target_agent_id", "target_agent_id", "responder_agent_id", "recipient_agent_id", "to_id")
        _move("source_agent_id", "source_agent_id", "initiator_agent_id", "initiator_id", "actor_id")
        _move("summary", "summary", "description", "text")
        _move("due_offset_ticks", "due_offset_ticks", "due_in_ticks")
    elif op_type == "upsert_informal_link":
        _move("agent_a_id", "agent_a_id", "source_agent_id", "actor_id", "from_id")
        _move("agent_b_id", "agent_b_id", "target_agent_id", "to_id", "recipient_agent_id", "counterparty_agent_id")
    elif op_type == "add_information_signal":
        _move("signal", "signal", "description", "text", "message")
    elif op_type == "create_artifact":
        _move("summary", "summary", "description", "text")
    elif op_type == "narrative_action":
        _move("description", "description", "summary", "text", "content")

    for noisy_key in (
        "from_agent_id",
        "from_id",
        "agent_id",
        "initiator_id",
        "initiator_agent_id",
        "responder_agent_id",
        "recipient_agent_id",
        "to_agent_id",
        "channel_id",
        "message",
        "content",
        "note",
        "note_text",
        "description" if op_type != "narrative_action" and op_type != "add_information_signal" and op_type != "create_artifact" else "",
        "summary" if op_type in {"add_work_note", "submit_work_proposal"} else "",
        "is_private",
        "params",
    ):
        if noisy_key:
            normalized.pop(noisy_key, None)

    return normalized


def _normalize_perform_llm_output(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, Any] = {
        "approved": bool(raw.get("approved", False)),
        "reason": str(raw.get("reason") or ""),
        "ops": [],
    }

    normalized_ops: list[dict[str, Any]] = []
    ops_value = raw.get("ops")
    if isinstance(ops_value, list):
        for item in ops_value:
            if not isinstance(item, dict):
                continue
            raw_type = str(item.get("op_type") or item.get("type") or "").strip()
            op_type = _normalize_perform_op_type(raw_type)
            if not op_type:
                continue
            raw_args = item.get("args")
            if isinstance(raw_args, dict):
                args = dict(raw_args)
            elif isinstance(item.get("params"), dict):
                args = dict(item.get("params") or {})
            else:
                args = {
                    key: value
                    for key, value in item.items()
                    if key not in {"op_type", "type", "args", "params"}
                }
            raw_target_id = str(item.get("target_id") or "").strip()
            if op_type == "send_message" and raw_target_id and "to_id" not in args:
                args["to_id"] = raw_target_id
            if op_type == "in_person_contact" and raw_target_id and "target_agent_id" not in args:
                args["target_agent_id"] = raw_target_id
            if op_type in {"add_work_note", "submit_work_proposal"} and raw_target_id and "work_id" not in args:
                args["work_id"] = raw_target_id
            normalized_ops.append(
                {
                    "op_type": op_type,
                    "args": _normalize_perform_op_args(op_type, args),
                    "source": "ops",
                }
            )

    side_effects_value = raw.get("side_effects")
    if isinstance(side_effects_value, list):
        for item in side_effects_value:
            if not isinstance(item, dict):
                continue
            raw_type = str(item.get("op_type") or item.get("type") or "").strip()
            op_type = _normalize_perform_op_type(raw_type)
            if not op_type:
                continue
            raw_args = item.get("args")
            if isinstance(raw_args, dict):
                args = dict(raw_args)
            elif isinstance(item.get("params"), dict):
                args = dict(item.get("params") or {})
            else:
                args = {
                    key: value
                    for key, value in item.items()
                    if key not in {"op_type", "type", "args", "params"}
                }
            raw_target_id = str(item.get("target_id") or "").strip()
            if op_type == "send_message" and raw_target_id and "to_id" not in args:
                args["to_id"] = raw_target_id
            if op_type == "in_person_contact" and raw_target_id and "target_agent_id" not in args:
                args["target_agent_id"] = raw_target_id
            if op_type in {"add_work_note", "submit_work_proposal"} and raw_target_id and "work_id" not in args:
                args["work_id"] = raw_target_id
            normalized_ops.append(
                {
                    "op_type": op_type,
                    "args": _normalize_perform_op_args(op_type, args),
                    "source": "side_effect",
                }
            )

    normalized["ops"] = normalized_ops
    return normalized


def _perform_output_schema() -> dict[str, Any]:
    """JSON schema для результата арбитража perform."""

    def op(const: str, args_props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "op_type": {"const": const},
                "args": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": args_props,
                    "required": required,
                },
            },
            "required": ["op_type", "args"],
        }

    ops_one_of = [
        op(
            "send_message",
            {
                "to_id": {"type": "string"},
                "text": {"type": "string"},
                "private": {"type": "boolean"},
            },
            ["to_id", "text"],
        ),
        op(
            "in_person_contact",
            {
                "target_agent_id": {"type": "string"},
                "summary": {"type": "string"},
                "zone_id": {"type": "string"},
            },
            ["target_agent_id", "summary"],
        ),
        op(
            "create_work_item",
            {
                "work_type": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "participants": {"type": "array", "items": {"type": "string"}},
            },
            ["work_type", "title"],
        ),
        op(
            "add_work_note",
            {"work_id": {"type": "string"}, "text": {"type": "string"}},
            ["work_id", "text"],
        ),
        op(
            "submit_work_proposal",
            {"work_id": {"type": "string"}, "text": {"type": "string"}},
            ["work_id", "text"],
        ),
        op(
            "create_entity",
            {
                "kind": {"type": "string", "enum": ["org", "chan"]},
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            ["kind", "slug"],
        ),
        op(
            "open_vote",
            {
                "target_agent_id": {"type": "string"},
                "new_title": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["target_agent_id", "new_title"],
        ),
        op(
            "cast_vote",
            {
                "vote_id": {"type": "string"},
                "choice": {"type": "string", "enum": ["yes", "no", "abstain"]},
            },
            ["vote_id", "choice"],
        ),
        op(
            "respond_nomination",
            {"vote_id": {"type": "string"}, "accept": {"type": "boolean"}},
            ["vote_id", "accept"],
        ),
        op(
            "modify_reputation",
            {
                "target_agent_id": {"type": "string"},
                "delta": {"type": "number"},
                "reason": {"type": "string"},
            },
            ["target_agent_id", "delta"],
        ),
        op(
            "create_artifact",
            {
                "artifact_type": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "related_work_id": {"type": "string"},
                "visibility": {"type": "string", "enum": ["internal", "public"]},
            },
            ["artifact_type", "title"],
        ),
        op(
            "update_artifact",
            {
                "artifact_id": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "status": {"type": "string"},
                "visibility": {"type": "string"},
            },
            ["artifact_id"],
        ),
        op(
            "narrative_action",
            {
                "description": {"type": "string"},
                "action_kind": {"type": "string"},
                "zone_id": {"type": "string"},
                "witnesses": {"type": "array", "items": {"type": "string"}},
            },
            ["description"],
        ),
        op(
            "upsert_informal_link",
            {
                "agent_a_id": {"type": "string"},
                "agent_b_id": {"type": "string"},
                "link_type": {"type": "string"},
                "strength_delta": {"type": "number"},
                "visibility": {"type": "string"},
                "source": {"type": "string"},
            },
            ["agent_a_id", "agent_b_id", "link_type"],
        ),
        op(
            "add_information_signal",
            {"signal": {"type": "string"}},
            ["signal"],
        ),
        op(
            "upsert_pending_interaction",
            {
                "target_agent_id": {"type": "string"},
                "source_agent_id": {"type": "string"},
                "category": {"type": "string"},
                "summary": {"type": "string"},
                "due_offset_ticks": {"type": "integer"},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            ["target_agent_id", "category", "summary"],
        ),
        op(
            "resolve_pending_interaction",
            {
                "interaction_id": {"type": "string"},
                "status": {"type": "string", "enum": ["completed", "expired"]},
                "reason": {"type": "string"},
            },
            ["interaction_id", "status"],
        ),
        op("noop", {}, []),
    ]

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "approved": {"type": "boolean"},
            "reason": {"type": "string"},
            "ops": {"type": "array", "items": {"oneOf": ops_one_of}},
        },
        "required": ["approved", "ops"],
    }


def _observation_verifier_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "observation_only": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["observation_only"],
    }


def _perform_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "steps": {
                "type": "array",
                "maxItems": 4,
                "items": {"type": "string"},
            }
        },
        "required": ["steps"],
    }


def _document_grounding_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "supported_level": {"type": "string"},
            "rewritten_text": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["supported_level", "rewritten_text", "rationale"],
    }


@dataclass(slots=True)
class Arbiter:
    """Гибридный арбитр: deterministic для structured + LLM для perform."""

    llm: LLMCaller
    governance: GovernanceConfig
    id_alloc: IdAllocator
    dao: DaoEngine
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    temperature: float = 0.0

    async def arbitrate_actions(
        self,
        *,
        state: WorldState,
        agent_id: str,
        actions: list[Action],
        journal_yaml: str | None = None,
    ) -> list[ActionResult]:
        """Преобразовать Action[] в список результатов с ops."""
        journal_yaml = journal_yaml or state.journal_yaml()
        results: list[ActionResult] = []
        spawn_used = False
        for idx, act in enumerate(actions):
            res = await self._arbitrate_one(
                state=state,
                agent_id=agent_id,
                action_index=idx,
                action=act,
                journal_yaml=journal_yaml,
                spawn_used=spawn_used,
            )
            results.append(res)
            if isinstance(act, SpawnAgentAction) and res.approved:
                spawn_used = True
        return results

    async def arbitrate_tick(
        self,
        *,
        state: WorldState,
        proposed: dict[str, list[Action]],
        journal_yaml: str,
    ) -> dict[str, list[ActionResult]]:
        """Арбитраж всех действий тика с параллельным LLM только для `perform`.

        Важно: ID-аллокатор используется только на детерминированной фазе
        (конвертация LLM-ops в StateOp), чтобы не терять воспроизводимость.
        """
        arbitration: dict[str, list[ActionResult | None]] = {}
        perform_meta: list[tuple[str, int, PerformAction, set[str]]] = []
        reserved_vote_targets: set[str] = {
            vote.target_agent_id
            for vote in state.votes.values()
            if vote.status == "open"
        }
        reserved_entity_ids: set[str] = set(state.registry.list_ids())

        def _reserve_open_vote_target(res: ActionResult) -> ActionResult:
            if not res.approved:
                return res
            for op in res.ops:
                if not isinstance(op, OpenVoteOp):
                    continue
                if op.target_agent_id in reserved_vote_targets:
                    return ActionResult(
                        res.action_index,
                        False,
                        f"open_vote_already_exists_for_target:{op.target_agent_id}",
                        [],
                    )
                reserved_vote_targets.add(op.target_agent_id)
            return res

        def _reserve_created_entities(res: ActionResult) -> ActionResult:
            if not res.approved or not res.ops:
                return res

            filtered_ops: list[StateOp] = []
            reason = res.reason
            for op in res.ops:
                if isinstance(op, CreateEntityOp):
                    if op.entity_id in reserved_entity_ids:
                        reason = "entity_already_exists"
                        continue
                    reserved_entity_ids.add(op.entity_id)
                    filtered_ops.append(op)
                    continue

                if isinstance(op, CreateAgentOp):
                    if op.entity_id in reserved_entity_ids:
                        return ActionResult(
                            res.action_index,
                            False,
                            f"agent_id_conflict:{op.entity_id}",
                            [],
                        )
                    reserved_entity_ids.add(op.entity_id)
                    filtered_ops.append(op)
                    continue

                filtered_ops.append(op)

            return ActionResult(res.action_index, True, reason, filtered_ops)

        def _reserve_result(res: ActionResult) -> ActionResult:
            return _reserve_created_entities(_reserve_open_vote_target(res))

        for aid in sorted(proposed.keys()):
            arbitration[aid] = []
            agent = state.agents.get(aid)
            caps = set(agent.capabilities) if agent is not None else set()
            spawn_used = False
            for idx, act in enumerate(proposed[aid]):
                if isinstance(act, PerformAction):
                    arbitration[aid].append(None)
                    perform_meta.append((aid, idx, act, caps))
                else:
                    res = await self._arbitrate_one(
                        state=state,
                        agent_id=aid,
                        action_index=idx,
                        action=act,
                        journal_yaml=journal_yaml,
                        spawn_used=spawn_used,
                    )
                    reserved_res = _reserve_result(res)
                    arbitration[aid].append(reserved_res)
                    if isinstance(act, SpawnAgentAction) and reserved_res.approved:
                        spawn_used = True

        # Perform-actions проходят через единый pipeline decomposition/materialization/grounding.
        for aid, idx, act, caps in perform_meta:
            try:
                res = await self._arbitrate_perform(
                    state=state,
                    agent_id=aid,
                    agent_caps=caps,
                    action_index=idx,
                    action=act,
                    journal_yaml=journal_yaml,
                )
            except Exception as exc:
                res = ActionResult(
                    idx,
                    False,
                    f"arbiter_llm_error:{exc.__class__.__name__}:{exc}",
                    [],
                )
            arbitration[aid][idx] = _reserve_result(res)

        # Убираем None (на всякий случай) и приводим тип.
        out: dict[str, list[ActionResult]] = {}
        for aid, items in arbitration.items():
            out[aid] = [r for r in items if r is not None]  # type: ignore[truthy-bool]
        return out

    @staticmethod
    def _sanitize_spawn_capabilities(capabilities: list[str], *, internal: bool) -> list[str]:
        allowed = set(SPAWN_AGENT_ALLOWED_CAPABILITIES)
        out: list[str] = []
        seen: set[str] = set()
        for cap in capabilities:
            cap = str(cap or "").strip()
            if cap not in allowed or cap in seen:
                continue
            seen.add(cap)
            out.append(cap)
        if out:
            return out
        return ["message", "work"] if internal else ["message"]

    @staticmethod
    def _find_duplicate_open_work_item(
        *,
        state: WorldState,
        work_type: str,
        title: str,
    ) -> str | None:
        """Lexical duplicate suppression intentionally disabled."""
        return None

    def _validate_temporal_texts(self, *, current_tick: int, texts: list[str]) -> str | None:
        simulated_date = self.runtime.simulated_date(current_tick)
        if simulated_date is None:
            return None

        low = simulated_date - timedelta(days=int(self.runtime.temporal_past_slack_days))
        high = simulated_date + timedelta(days=int(self.runtime.temporal_future_horizon_days))
        for text in texts:
            for value in _extract_dates(text):
                if value < low:
                    return f"temporal_date_before_current_tick:{value.isoformat()}"
                if value > high:
                    return f"temporal_date_out_of_range:{value.isoformat()}"
        return None

    def _validate_action_temporal_window(self, *, state: WorldState, action: Action) -> str | None:
        dump = action.model_dump(mode="python")
        texts = [str(value) for value in dump.values() if isinstance(value, str)]
        return self._validate_temporal_texts(current_tick=state.tick, texts=texts)

    @staticmethod
    def _validate_spawn_display_name(name: str) -> str | None:
        if not name:
            return "spawn_requires_name"
        if looks_like_machine_name(name):
            return "spawn_name_not_human_readable"
        return None

    async def _arbitrate_one(
        self,
        *,
        state: WorldState,
        agent_id: str,
        action_index: int,
        action: Action,
        journal_yaml: str,
        spawn_used: bool = False,
    ) -> ActionResult:
        agent = state.agents.get(agent_id)
        if agent is None:
            return ActionResult(action_index, False, f"unknown agent_id: {agent_id}", [])

        def _require(cap: str) -> str | None:
            if cap in agent.capabilities:
                return None
            return f"missing_capability:{cap}"

        # Structured actions: deterministic translation + anti-phantoms.
        if isinstance(action, NoopAction):
            return ActionResult(action_index, True, "noop", [])

        temporal_error = self._validate_action_temporal_window(state=state, action=action)
        if temporal_error:
            return ActionResult(action_index, False, temporal_error, [])

        if isinstance(action, SendMessageAction):
            if not state.registry.exists(action.to_id):
                return ActionResult(action_index, False, f"unknown to_id: {action.to_id}", [])
            target_error = self._validate_message_target(to_id=action.to_id, private=bool(action.private))
            if target_error:
                return ActionResult(action_index, False, target_error, [])
            return ActionResult(
                action_index,
                True,
                "send_message",
                [
                    SendMessageOp(
                        from_id=agent_id,
                        to_id=action.to_id,
                        text=action.text,
                        private=bool(action.private),
                    )
                ],
            )

        if isinstance(action, PublishAction):
            if not state.registry.exists(action.channel_id):
                return ActionResult(action_index, False, f"unknown channel_id: {action.channel_id}", [])
            return ActionResult(
                action_index,
                True,
                "publish",
                [
                    SendMessageOp(
                        from_id=agent_id,
                        to_id=action.channel_id,
                        text=action.text,
                        private=False,
                    )
                ],
            )

        if isinstance(action, CreateWorkItemAction):
            missing = _require("work")
            if missing:
                return ActionResult(action_index, False, missing, [])
            participants_error = self._validate_work_participants(
                state=state,
                participants=action.participants,
            )
            if participants_error:
                return ActionResult(action_index, False, participants_error, [])
            duplicate_work_id = self._find_duplicate_open_work_item(
                state=state,
                work_type=action.work_type,
                title=action.title,
            )
            if duplicate_work_id is not None:
                return ActionResult(
                    action_index,
                    False,
                    f"duplicate_open_work_item:{duplicate_work_id}",
                    [],
                )
            wid = self.id_alloc.next_id(EntityKind.WORK_ITEM, tick=state.tick)
            return ActionResult(
                action_index,
                True,
                "create_work_item",
                [
                    CreateWorkItemOp(
                        created_by=agent_id,
                        work_id=wid,
                        work_type=action.work_type,
                        title=action.title,
                        description=action.description,
                        participants=action.participants,
                    )
                ],
            )

        if isinstance(action, AddWorkNoteAction):
            missing = _require("work")
            if missing:
                return ActionResult(action_index, False, missing, [])
            if action.work_id not in state.work_items:
                return ActionResult(action_index, False, f"unknown work_id: {action.work_id}", [])
            return ActionResult(
                action_index,
                True,
                "add_work_note",
                [AddWorkNoteOp(actor_id=agent_id, work_id=action.work_id, text=action.text)],
            )

        if isinstance(action, SubmitWorkProposalAction):
            missing = _require("work")
            if missing:
                return ActionResult(action_index, False, missing, [])
            if action.work_id not in state.work_items:
                return ActionResult(action_index, False, f"unknown work_id: {action.work_id}", [])
            return ActionResult(
                action_index,
                True,
                "submit_work_proposal",
                [SubmitWorkProposalOp(actor_id=agent_id, work_id=action.work_id, text=action.text)],
            )

        if isinstance(action, RequestEntityAction):
            if self.runtime.request_entity_internal_only and not agent.internal:
                return ActionResult(action_index, False, "request_entity_requires_internal_actor", [])
            kind = EntityKind.ORG if action.kind == "org" else EntityKind.CHANNEL
            slug = action.slug
            if ":" in slug:
                try:
                    parsed = parse_typed_id(slug)
                except ValueError:
                    return ActionResult(action_index, False, f"invalid_request_entity_slug:{slug}", [])
                if parsed.kind != kind:
                    return ActionResult(action_index, False, f"request_entity_kind_mismatch:{slug}", [])
                slug = parsed.slug
            eid = make_id(kind, slug)
            if state.registry.exists(eid):
                return ActionResult(action_index, True, "entity_already_exists", [])
            return ActionResult(
                action_index,
                True,
                "create_entity",
                [
                    CreateEntityOp(
                        entity_id=eid,
                        kind=kind,
                        created_by=agent_id,
                        created_tick=state.tick,
                        meta={"title": "", "description": action.description},
                    )
                ],
            )

        if isinstance(action, SpawnAgentAction):
            missing = _require("spawn")
            if missing:
                return ActionResult(action_index, False, missing, [])
            if not self.runtime.allow_runtime_spawn:
                return ActionResult(action_index, False, "runtime_spawn_disabled", [])
            if spawn_used:
                return ActionResult(action_index, False, "spawn_limit_per_tick_exceeded", [])
            if len(state.agents) >= self.runtime.max_agents:
                return ActionResult(action_index, False, "max_agents_reached", [])
            name = normalize_agent_display_name(action.name, fallback=action.slug)
            name_error = self._validate_spawn_display_name(name)
            if name_error:
                return ActionResult(action_index, False, name_error, [])
            slug = normalize_slug(action.slug, fallback=name or "spawned")
            entity_id = make_id(EntityKind.AGENT, slug)
            if state.registry.exists(entity_id) or entity_id in state.agents:
                return ActionResult(action_index, False, f"agent_id_conflict:{entity_id}", [])
            persona_hint = (action.persona_hint or "").strip()
            if not persona_hint:
                return ActionResult(action_index, False, "spawn_requires_persona_hint", [])
            org_id = str(getattr(action, "org_id", "") or "").strip() or None
            zone_id = str(getattr(action, "zone_id", "") or "").strip() or None
            if org_id:
                try:
                    ensure_kind(org_id, EntityKind.ORG)
                except ValueError:
                    return ActionResult(action_index, False, f"invalid_org_id:{org_id}", [])
                if not state.registry.exists(org_id):
                    return ActionResult(action_index, False, f"unknown_org_id:{org_id}", [])
            if zone_id:
                try:
                    ensure_kind(zone_id, EntityKind.ZONE)
                except ValueError:
                    return ActionResult(action_index, False, f"invalid_zone_id:{zone_id}", [])
                if not state.registry.exists(zone_id):
                    return ActionResult(action_index, False, f"unknown_zone_id:{zone_id}", [])
            capabilities = self._sanitize_spawn_capabilities(
                list(action.capabilities or []),
                internal=bool(action.internal),
            )
            return ActionResult(
                action_index,
                True,
                "spawn_agent",
                [
                    CreateAgentOp(
                        entity_id=entity_id,
                        name=name,
                        internal=bool(action.internal),
                        persona_hint=persona_hint,
                        capabilities=capabilities,
                        org_id=org_id,
                        zone_id=zone_id,
                        spawn_source="agent_spawn",
                        created_by=agent_id,
                        created_tick=state.tick,
                    )
                ],
            )

        if isinstance(action, NominatePositionChangeAction):
            missing = _require("dao")
            if missing:
                return ActionResult(action_index, False, missing, [])
            target_error = self._validate_open_vote_target(
                state=state,
                actor_id=agent_id,
                target_agent_id=action.target_agent_id,
            )
            if target_error:
                return ActionResult(action_index, False, target_error, [])

            vote_id = self.id_alloc.next_id(EntityKind.VOTE, tick=state.tick)
            excluded = {action.target_agent_id} if not self.governance.allow_target_self_vote else set()
            voters = self.dao.eligible_voters(state, exclude_agent_ids=excluded)
            closes_tick = state.tick + self.governance.vote_duration_ticks
            return ActionResult(
                action_index,
                True,
                "open_vote",
                [
                    OpenVoteOp(
                        vote_id=vote_id,
                        created_by=agent_id,
                        created_tick=state.tick,
                        closes_tick=closes_tick,
                        target_agent_id=action.target_agent_id,
                        new_title=action.new_title,
                        reason=action.reason,
                        voters=voters,
                    )
                ],
            )

        if isinstance(action, CastVoteAction):
            missing = _require("dao")
            if missing:
                return ActionResult(action_index, False, missing, [])
            if action.vote_id not in state.votes:
                return ActionResult(action_index, False, f"unknown vote_id: {action.vote_id}", [])
            vote = state.votes[action.vote_id]
            if not self.governance.allow_target_self_vote and vote.target_agent_id == agent_id:
                return ActionResult(action_index, False, "target_self_vote_disabled", [])
            if agent_id not in vote.voters:
                return ActionResult(action_index, False, f"agent_is_not_eligible_voter:{agent_id}", [])
            return ActionResult(
                action_index,
                True,
                "cast_vote",
                [CastVoteOp(actor_id=agent_id, vote_id=action.vote_id, choice=str(action.choice))],
            )

        if isinstance(action, RespondNominationAction):
            if action.vote_id not in state.votes:
                return ActionResult(action_index, False, f"unknown vote_id: {action.vote_id}", [])
            vote = state.votes[action.vote_id]
            if vote.target_agent_id != agent_id:
                return ActionResult(action_index, False, f"only_target_can_respond:{action.vote_id}", [])
            return ActionResult(
                action_index,
                True,
                "respond_nomination",
                [SetVoteConsentOp(actor_id=agent_id, vote_id=action.vote_id, accept=bool(action.accept))],
            )

        if isinstance(action, PerformAction):
            return await self._arbitrate_perform(
                state=state,
                agent_id=agent_id,
                agent_caps=set(agent.capabilities),
                action_index=action_index,
                action=action,
                journal_yaml=journal_yaml,
            )

        return ActionResult(action_index, False, f"unsupported action: {action.type}", [])

    async def _decide_perform_llm(
        self,
        *,
        state: WorldState,
        agent_id: str,
        agent_caps: set[str],
        action: PerformAction,
        journal_yaml: str,
    ) -> _PerformArbiterOutput:
        """Вызвать LLM для `perform` и вернуть structured-решение без конвертации в ops."""
        target_id = action.target_id.strip()
        if target_id and not state.registry.exists(target_id):
            return _PerformArbiterOutput(approved=False, reason=f"unknown target_id: {target_id}", ops=[])

        proposal = action.description.strip()
        system = render_prompt("arbiter.perform.system", agent_caps=sorted(agent_caps))
        user = self._perform_llm_user_prompt(
            journal_yaml=journal_yaml,
            agent_id=agent_id,
            proposal=proposal,
            target_id=target_id,
        )

        decision = await self._call_perform_llm_once(
            state=state,
            system=system,
            user=user,
        )
        if not decision.approved and await self._verify_observation_only(
            state=state,
            agent_id=agent_id,
            proposal=proposal,
            journal_yaml=journal_yaml,
            previous_decision=decision,
        ):
            return _PerformArbiterOutput(approved=True, reason="observation_only", ops=[])
        if not self._should_retry_unmaterialized_proposal(proposal=proposal, decision=decision):
            return decision

        retry_system = f"{system}\n{render_prompt('arbiter.perform.retry_suffix')}"
        retry_user = self._perform_llm_user_prompt(
            journal_yaml=journal_yaml,
            agent_id=agent_id,
            proposal=proposal,
            target_id=target_id,
            previous_decision=decision,
        )
        retried = await self._call_perform_llm_once(
            state=state,
            system=retry_system,
            user=retry_user,
        )
        if not retried.approved and await self._verify_observation_only(
            state=state,
            agent_id=agent_id,
            proposal=proposal,
            journal_yaml=journal_yaml,
            previous_decision=retried,
        ):
            return _PerformArbiterOutput(approved=True, reason="observation_only", ops=[])
        if self._should_retry_unmaterialized_proposal(proposal=proposal, decision=retried):
            if await self._verify_observation_only(
                state=state,
                agent_id=agent_id,
                proposal=proposal,
                journal_yaml=journal_yaml,
                previous_decision=retried,
            ):
                return _PerformArbiterOutput(approved=True, reason="observation_only", ops=[])
            return _PerformArbiterOutput(approved=False, reason="proposal_not_materialized_after_retry", ops=[])
        return retried

    def _perform_llm_user_prompt(
        self,
        *,
        journal_yaml: str,
        agent_id: str,
        proposal: str,
        target_id: str,
        previous_decision: _PerformArbiterOutput | None = None,
    ) -> str:
        if previous_decision is None:
            return render_prompt(
                "arbiter.perform.user",
                journal_yaml=journal_yaml,
                agent_id=agent_id,
                proposal=proposal,
                target_id=target_id,
            )
        return render_prompt(
            "arbiter.perform.user_with_previous",
            journal_yaml=journal_yaml,
            agent_id=agent_id,
            proposal=proposal,
            target_id=target_id,
            approved=previous_decision.approved,
            reason=previous_decision.reason,
            ops_count=len(previous_decision.ops),
        )

    async def _plan_perform_steps(
        self,
        *,
        state: WorldState,
        agent_id: str,
        proposal: str,
        journal_yaml: str,
    ) -> list[str]:
        """Разбить составной proposal на упорядоченные шаги через отдельный LLM-pass."""

        raw_proposal = str(proposal or "").strip()
        if not raw_proposal:
            return []
        try:
            resp = await self.llm.generate_structured(
                role="arbiter",
                name="perform_plan",
                tick=state.tick,
                system=render_prompt("arbiter.perform.plan.system"),
                user=render_prompt(
                    "arbiter.perform.plan.user",
                    journal_yaml=journal_yaml,
                    agent_id=agent_id,
                    proposal=raw_proposal,
                ),
                schema=_perform_plan_schema(),
                temperature=self.temperature,
            )
            parsed = _PerformPlanOutput.model_validate(resp.data)
        except Exception:
            return [raw_proposal]

        steps: list[str] = []
        seen: set[str] = set()
        for item in parsed.steps:
            step = str(item or "").strip()
            if not step:
                continue
            key = step.casefold()
            if key in seen:
                continue
            seen.add(key)
            steps.append(step)
        return steps or [raw_proposal]

    @staticmethod
    def _ops_summary_for_prompt(ops: list[StateOp]) -> str:
        """Коротко описать уже materialized ops для verifier/judge промптов."""

        if not ops:
            return "(нет materialized ops до этого места)"
        lines: list[str] = []
        for op in ops[-8:]:
            if isinstance(op, SendMessageOp):
                lines.append(f"- send_message -> {op.to_id} (private={op.private})")
            elif isinstance(op, RecordNarrativeActionOp):
                if op.action_kind == "in_person_contact" and op.counterparty_agent_id:
                    lines.append(f"- in_person_contact -> {op.counterparty_agent_id}")
                else:
                    lines.append(f"- narrative_action -> {op.description}")
            elif isinstance(op, AddWorkNoteOp):
                lines.append(f"- add_work_note -> {op.work_id}")
            elif isinstance(op, SubmitWorkProposalOp):
                lines.append(f"- submit_work_proposal -> {op.work_id}")
            elif isinstance(op, CreateArtifactOp):
                lines.append(f"- create_artifact -> {op.artifact_id}")
            else:
                lines.append(f"- {op.__class__.__name__}")
        return "\n".join(lines)

    async def _ground_documentary_op(
        self,
        *,
        state: WorldState,
        agent_id: str,
        proposal: str,
        op: StateOp,
        prior_ops: list[StateOp],
    ) -> StateOp:
        """Проверить, не перепрыгивает ли документарный op через фактически случившиеся события."""

        if isinstance(op, AddWorkNoteOp):
            op_kind = "work_note"
            anchor = op.work_id
            candidate_text = op.text
        elif isinstance(op, SubmitWorkProposalOp):
            op_kind = "work_proposal"
            anchor = op.work_id
            candidate_text = op.text
        else:
            return op

        if not str(candidate_text or "").strip():
            return op

        try:
            resp = await self.llm.generate_structured(
                role="arbiter",
                name="document_grounding",
                tick=state.tick,
                system=render_prompt("arbiter.document_grounding.system"),
                user=render_prompt(
                    "arbiter.document_grounding.user",
                    journal_yaml=state.journal_yaml(),
                    agent_id=agent_id,
                    proposal=proposal,
                    op_kind=op_kind,
                    anchor_id=anchor,
                    candidate_text=candidate_text,
                    prior_ops_summary=self._ops_summary_for_prompt(prior_ops),
                ),
                schema=_document_grounding_schema(),
                temperature=self.temperature,
            )
            verdict = _DocumentGroundingOutput.model_validate(resp.data)
        except Exception:
            return op

        level = str(verdict.supported_level or "").strip().casefold()
        rewritten_text = str(verdict.rewritten_text or "").strip()
        if level not in {"partially_supported", "unsupported"} or not rewritten_text:
            return op
        return replace(op, text=rewritten_text)

    async def _call_perform_llm_once(
        self,
        *,
        state: WorldState,
        system: str,
        user: str,
    ) -> _PerformArbiterOutput:
        resp = await self.llm.generate_structured(
            role="arbiter",
            name="perform",
            tick=state.tick,
            system=system,
            user=user,
            schema=_perform_output_schema(),
            temperature=self.temperature,
        )
        try:
            return _PerformArbiterOutput.model_validate(_normalize_perform_llm_output(resp.data))
        except Exception as exc:
            return _PerformArbiterOutput(approved=False, reason=f"arbiter_parse_error:{exc}", ops=[])

    def _should_retry_unmaterialized_proposal(
        self,
        *,
        proposal: str,
        decision: _PerformArbiterOutput,
    ) -> bool:
        if not str(proposal or "").strip():
            return False
        if not decision.approved or decision.ops:
            return False
        if str(decision.reason or "").strip() in {"noop", "noop_proposal", "observation_only"}:
            return False
        return True

    async def _verify_observation_only(
        self,
        *,
        state: WorldState,
        agent_id: str,
        proposal: str,
        journal_yaml: str,
        previous_decision: _PerformArbiterOutput,
    ) -> bool:
        """Проверить отдельным LLM-pass, является ли шаг чистым наблюдением."""

        try:
            resp = await self.llm.generate_structured(
                role="arbiter",
                name="observation_verifier",
                tick=state.tick,
                system=render_prompt("arbiter.observation_verifier.system"),
                user=render_prompt(
                    "arbiter.observation_verifier.user",
                    journal_yaml=journal_yaml,
                    agent_id=agent_id,
                    proposal=proposal,
                    approved=previous_decision.approved,
                    reason=previous_decision.reason,
                    ops_count=len(previous_decision.ops),
                ),
                schema=_observation_verifier_schema(),
                temperature=self.temperature,
            )
            verdict = _ObservationVerifierOutput.model_validate(resp.data)
        except Exception:
            return False
        return bool(verdict.observation_only)

    async def _convert_perform_decision(
        self,
        *,
        state: WorldState,
        agent_id: str,
        action_index: int,
        proposal: str,
        step_target_id: str,
        agent_caps: set[str],
        decision: _PerformArbiterOutput,
        scratch_state: WorldState | None = None,
        scratch_alloc: IdAllocator | None = None,
        commit_ids: bool = True,
    ) -> tuple[ActionResult, WorldState, IdAllocator]:
        work_state = scratch_state or copy.deepcopy(state)
        allocator = scratch_alloc or IdAllocator(counters=dict(self.id_alloc.counters or {}))
        if not decision.approved:
            return ActionResult(action_index, False, decision.reason or "rejected", []), work_state, allocator

        ops: list[StateOp] = []
        context_target_agent_id = self._resolve_contextual_id(
            state=work_state,
            raw_id="",
            proposal_text=proposal,
            allowed_kinds={EntityKind.AGENT},
            action_target_id=step_target_id,
            exclude_ids={agent_id},
        )
        for item in decision.ops:
            is_side_effect = str(getattr(item, "source", "") or "").strip().casefold() == "side_effect"
            try:
                parsed_ops = self._op_from_llm(
                    agent_id=agent_id,
                    state=work_state,
                    op_type=item.op_type,
                    args=item.args,
                    id_alloc=allocator,
                    proposal_text=proposal,
                    action_target_id=step_target_id,
                    fallback_target_agent_id=context_target_agent_id,
                )
            except Exception as exc:
                if is_side_effect and ops:
                    continue
                return ActionResult(
                    action_index,
                    False,
                    f"perform_op_invalid:{item.op_type}:{exc.__class__.__name__}:{exc}",
                    [],
                ), work_state, allocator
            for op in parsed_ops:
                missing = self._missing_capability_for_op(op, agent_caps)
                if missing:
                    if is_side_effect and ops:
                        continue
                    return ActionResult(action_index, False, f"missing_capability:{missing}", []), work_state, allocator
                op = await self._ground_documentary_op(
                    state=work_state,
                    agent_id=agent_id,
                    proposal=proposal,
                    op=op,
                    prior_ops=ops,
                )
                try:
                    op.apply(work_state)
                except Exception as exc:
                    if is_side_effect and ops:
                        continue
                    return ActionResult(
                        action_index,
                        False,
                        f"perform_op_invalid:{item.op_type}:{exc.__class__.__name__}:{exc}",
                        [],
                    ), work_state, allocator
                ops.append(op)
                if isinstance(op, SendMessageOp) and op.to_id in work_state.agents:
                    context_target_agent_id = op.to_id
                elif isinstance(op, UpsertPendingInteractionOp):
                    context_target_agent_id = op.target_agent_id
                elif isinstance(op, UpsertInformalLinkOp):
                    context_target_agent_id = op.agent_b_id if op.agent_a_id == agent_id else op.agent_a_id
                elif isinstance(op, RecordNarrativeActionOp) and op.counterparty_agent_id:
                    context_target_agent_id = op.counterparty_agent_id

        if commit_ids:
            self.id_alloc.counters = dict(allocator.counters or {})
        return ActionResult(action_index, True, decision.reason or "approved", ops), work_state, allocator

    async def _arbitrate_perform(
        self,
        *,
        state: WorldState,
        agent_id: str,
        agent_caps: set[str],
        action_index: int,
        action: PerformAction,
        journal_yaml: str,
    ) -> ActionResult:
        steps = await self._plan_perform_steps(
            state=state,
            agent_id=agent_id,
            proposal=action.description,
            journal_yaml=journal_yaml,
        )
        if not steps:
            steps = [action.description]

        scratch_state = copy.deepcopy(state)
        scratch_alloc = IdAllocator(counters=dict(self.id_alloc.counters or {}))
        aggregated_ops: list[StateOp] = []
        reasons: list[str] = []

        for step in steps:
            step_action = PerformAction(
                type=action.type,
                description=step,
                target_id=action.target_id,
                justification=action.justification,
            )
            decision = await self._decide_perform_llm(
                state=scratch_state,
                agent_id=agent_id,
                agent_caps=agent_caps,
                action=step_action,
                journal_yaml=scratch_state.journal_yaml(),
            )
            step_result, scratch_state, scratch_alloc = await self._convert_perform_decision(
                state=state,
                agent_id=agent_id,
                agent_caps=agent_caps,
                action_index=action_index,
                proposal=step,
                step_target_id=step_action.target_id,
                decision=decision,
                scratch_state=scratch_state,
                scratch_alloc=scratch_alloc,
                commit_ids=False,
            )
            if not step_result.approved:
                return step_result
            aggregated_ops.extend(step_result.ops)
            if step_result.reason and step_result.reason != "approved":
                reasons.append(step_result.reason)

        self.id_alloc.counters = dict(scratch_alloc.counters or {})
        return ActionResult(
            action_index,
            True,
            " | ".join(reasons) if reasons else "approved",
            aggregated_ops,
        )

    @staticmethod
    def _missing_capability_for_op(op: StateOp, caps: set[str]) -> str | None:
        """Вернуть недостающую capability для op (или None)."""
        if isinstance(op, (CreateWorkItemOp, AddWorkNoteOp, SubmitWorkProposalOp, CreateArtifactOp, UpdateArtifactOp)) and "work" not in caps:
            return "work"
        if isinstance(op, (OpenVoteOp, CastVoteOp)) and "dao" not in caps:
            return "dao"
        if isinstance(op, ModifyReputationOp) and "audit" not in caps:
            return "audit"
        return None

    @staticmethod
    def _validate_message_target(*, to_id: str, private: bool) -> str | None:
        """Проверить совместимость private-флага и типа цели сообщения."""
        target_kind = parse_typed_id(to_id).kind
        if private and target_kind != EntityKind.AGENT:
            return f"private_message_requires_agent_target:{to_id}"
        if not private and target_kind not in (EntityKind.CHANNEL, EntityKind.ORG):
            return f"public_message_requires_chan_or_org_target:{to_id}"
        return None

    @staticmethod
    def _extract_existing_typed_ids(
        *,
        state: WorldState,
        text: str,
        allowed_kinds: set[EntityKind],
        exclude_ids: set[str] | None = None,
    ) -> list[str]:
        """Вытащить уже существующие typed-id нужных kind из текста шага."""
        out: list[str] = []
        excluded = exclude_ids or set()
        for match in _TYPED_ID_RE.findall(text or ""):
            if match in excluded or not state.registry.exists(match):
                continue
            try:
                parsed = parse_typed_id(match)
            except ValueError:
                continue
            if parsed.kind not in allowed_kinds or match in out:
                continue
            out.append(match)
        return out

    @staticmethod
    def _normalize_identity_text(text: str) -> str:
        normalized = normalize_whitespace(text or "")
        normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
        return normalize_whitespace(normalized).casefold()

    @classmethod
    def _display_name_candidates(
        cls,
        *,
        state: WorldState,
        allowed_kinds: set[EntityKind],
        exclude_ids: set[str],
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for entity_id in sorted(state.registry.list_ids()):
            if entity_id in exclude_ids:
                continue
            try:
                parsed = parse_typed_id(entity_id)
            except ValueError:
                continue
            if parsed.kind not in allowed_kinds:
                continue
            display_name = ""
            if parsed.kind == EntityKind.AGENT:
                agent = state.agents.get(entity_id)
                if agent is not None:
                    display_name = agent.name
                else:
                    record = state.registry.get(entity_id)
                    display_name = str(record.meta.get("name") or "") if record is not None else ""
            elif parsed.kind == EntityKind.WORK_ITEM:
                work = state.work_items.get(entity_id)
                if work is not None:
                    display_name = work.title
                else:
                    record = state.registry.get(entity_id)
                    display_name = str(record.meta.get("title") or "") if record is not None else ""
            elif parsed.kind == EntityKind.ARTIFACT:
                artifact = state.artifacts.get(entity_id)
                if artifact is not None:
                    display_name = artifact.title
                else:
                    record = state.registry.get(entity_id)
                    display_name = str(record.meta.get("title") or "") if record is not None else ""
            else:
                record = state.registry.get(entity_id)
                if record is not None:
                    display_name = str(record.meta.get("title") or record.meta.get("name") or "")
            normalized_name = cls._normalize_identity_text(display_name)
            if normalized_name:
                candidates.append((entity_id, normalized_name))
        return candidates

    @classmethod
    def _match_exact_display_names(
        cls,
        *,
        state: WorldState,
        text: str,
        allowed_kinds: set[EntityKind],
        exclude_ids: set[str],
        in_full_text: bool,
    ) -> list[str]:
        normalized_text = cls._normalize_identity_text(text)
        if not normalized_text:
            return []
        matches: list[str] = []
        for entity_id, normalized_name in cls._display_name_candidates(
            state=state,
            allowed_kinds=allowed_kinds,
            exclude_ids=exclude_ids,
        ):
            matched = normalized_name in normalized_text if in_full_text else normalized_text == normalized_name
            if matched and entity_id not in matches:
                matches.append(entity_id)
        return matches

    @classmethod
    def _resolve_contextual_id(
        cls,
        *,
        state: WorldState,
        raw_id: str,
        proposal_text: str,
        allowed_kinds: set[EntityKind],
        action_target_id: str = "",
        fallback_id: str = "",
        exclude_ids: set[str] | None = None,
        prefer_context: bool = False,
    ) -> _ResolvedContextualId:
        """Разрешить typed-id через локальный контекст шага без fuzzy matching."""

        excluded = exclude_ids or set()

        def _valid(candidate: str) -> str | None:
            value = str(candidate or "").strip()
            if not value or value in excluded or not state.registry.exists(value):
                return None
            try:
                parsed = parse_typed_id(value)
            except ValueError:
                return None
            if parsed.kind not in allowed_kinds:
                return None
            return value

        def _resolved(candidate: str) -> _ResolvedContextualId:
            return _ResolvedContextualId(status="resolved", value=candidate)

        def _try_context() -> _ResolvedContextualId | None:
            for candidate in (action_target_id, fallback_id):
                resolved = _valid(candidate)
                if resolved is not None:
                    return _resolved(resolved)
            return None

        direct = _valid(raw_id)
        if direct is not None:
            return _resolved(direct)

        if prefer_context:
            contextual = _try_context()
            if contextual is not None:
                return contextual

        extracted = cls._extract_existing_typed_ids(
            state=state,
            text=proposal_text,
            allowed_kinds=allowed_kinds,
            exclude_ids=excluded,
        )
        if len(extracted) == 1:
            return _resolved(extracted[0])
        if len(extracted) > 1:
            return _ResolvedContextualId(status="ambiguous")

        raw_name_matches = cls._match_exact_display_names(
            state=state,
            text=str(raw_id or ""),
            allowed_kinds=allowed_kinds,
            exclude_ids=excluded,
            in_full_text=False,
        )
        if len(raw_name_matches) == 1:
            return _resolved(raw_name_matches[0])
        if len(raw_name_matches) > 1:
            return _ResolvedContextualId(status="ambiguous")

        text_name_matches = cls._match_exact_display_names(
            state=state,
            text=proposal_text,
            allowed_kinds=allowed_kinds,
            exclude_ids=excluded,
            in_full_text=True,
        )
        if len(text_name_matches) == 1:
            return _resolved(text_name_matches[0])
        if len(text_name_matches) > 1:
            return _ResolvedContextualId(status="ambiguous")

        contextual = _try_context()
        if contextual is not None:
            return contextual
        return _ResolvedContextualId(status="unknown", value=str(raw_id or "").strip())

    @staticmethod
    def _validate_in_person_contact_feasibility(
        *,
        state: WorldState,
        from_id: str,
        target_agent_id: str,
    ) -> str | None:
        sender = state.agents.get(from_id)
        recipient = state.agents.get(target_agent_id)
        if sender is None or recipient is None:
            return None
        if sender.zone_id and recipient.zone_id and sender.zone_id != recipient.zone_id:
            return f"private_contact_requires_shared_zone:{sender.zone_id}!={recipient.zone_id}"
        return None

    @staticmethod
    def _validate_work_participants(*, state: WorldState, participants: list[str]) -> str | None:
        """Проверить участников work-item до формирования op."""
        for participant_id in participants:
            try:
                ensure_kind(participant_id, EntityKind.AGENT)
            except ValueError:
                return f"participant_must_be_agent:{participant_id}"
            if participant_id not in state.agents:
                return f"unknown_participant_agent_id:{participant_id}"
        return None

    def _validate_open_vote_target(
        self,
        *,
        state: WorldState,
        actor_id: str,
        target_agent_id: str,
    ) -> str | None:
        """Проверить цель голосования за смену должности."""
        target = state.agents.get(target_agent_id)
        if target is None:
            return f"unknown target_agent_id: {target_agent_id}"
        if not target.internal:
            return "cannot nominate external agent"
        if actor_id == target_agent_id and not self.governance.allow_self_nomination:
            return "self_nomination_disabled"
        if not target.wants_promotion:
            return "target_declines_promotion"
        for vote in state.votes.values():
            if vote.status == "open" and vote.target_agent_id == target_agent_id:
                return f"open_vote_already_exists_for_target:{target_agent_id}"
        return None

    def _resolve_id_or_raise(
        self,
        *,
        state: WorldState,
        raw_id: str,
        proposal_text: str,
        allowed_kinds: set[EntityKind],
        unknown_reason: str,
        ambiguous_reason: str,
        action_target_id: str = "",
        fallback_id: str = "",
        exclude_ids: set[str] | None = None,
        prefer_context: bool = False,
    ) -> str:
        resolution = self._resolve_contextual_id(
            state=state,
            raw_id=raw_id,
            proposal_text=proposal_text,
            allowed_kinds=allowed_kinds,
            action_target_id=action_target_id,
            fallback_id=fallback_id,
            exclude_ids=exclude_ids,
            prefer_context=prefer_context,
        )
        if resolution.status == "resolved" and resolution.value:
            return resolution.value
        if resolution.status == "ambiguous":
            raise ValueError(ambiguous_reason)
        raise ValueError(unknown_reason)

    def _op_from_llm(
        self,
        *,
        agent_id: str,
        state: WorldState,
        op_type: str,
        args: dict[str, Any],
        id_alloc: IdAllocator | None = None,
        proposal_text: str = "",
        action_target_id: str = "",
        fallback_target_agent_id: str = "",
    ) -> list[StateOp]:
        """Сконвертировать LLM-op в реальные ops."""
        allocator = id_alloc or self.id_alloc
        op_type = _normalize_perform_op_type(op_type)
        if op_type == "vote":
            if args.get("vote_id") or args.get("choice"):
                op_type = "cast_vote"
            elif args.get("target_agent_id") or args.get("new_title"):
                op_type = "open_vote"
        if op_type == "noop":
            return []

        if op_type == "send_message":
            private = bool(args.get("private", True))
            to_id = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("to_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.AGENT} if private else {EntityKind.CHANNEL, EntityKind.ORG},
                unknown_reason="unknown to_id",
                ambiguous_reason="ambiguous_to_id",
                action_target_id=action_target_id,
                fallback_id=fallback_target_agent_id,
                prefer_context=True,
            )
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("text") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            target_error = self._validate_message_target(
                to_id=to_id,
                private=private,
            )
            if target_error:
                raise ValueError(target_error)
            return [
                SendMessageOp(
                    from_id=agent_id,
                    to_id=to_id,
                    text=str(args.get("text") or ""),
                    private=private,
                )
            ]

        if op_type == "in_person_contact":
            target_agent_id = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("target_agent_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.AGENT},
                unknown_reason="unknown target_agent_id",
                ambiguous_reason="ambiguous_target_agent_id",
                action_target_id=action_target_id,
                fallback_id=fallback_target_agent_id,
                exclude_ids={agent_id},
            )
            contact_error = self._validate_in_person_contact_feasibility(
                state=state,
                from_id=agent_id,
                target_agent_id=target_agent_id,
            )
            if contact_error:
                raise ValueError(contact_error)
            summary = str(args.get("summary") or proposal_text or "").strip()
            if not summary:
                raise ValueError("in_person_contact requires summary")
            zone_id = str(args.get("zone_id") or "").strip() or None
            if zone_id and not state.registry.exists(zone_id):
                zone_id = None
            actor = state.agents.get(agent_id)
            if zone_id is None and actor is not None:
                zone_id = actor.zone_id
            counterparty = state.agents.get(target_agent_id)
            description = (
                f"Провёл личный разговор с {counterparty.name}. {summary}"
                if counterparty is not None
                else summary
            )
            return [
                RecordNarrativeActionOp(
                    actor_id=agent_id,
                    description=description,
                    action_kind="in_person_contact",
                    zone_id=zone_id,
                    counterparty_agent_id=target_agent_id,
                )
            ]

        if op_type == "create_work_item":
            wid = allocator.next_id(EntityKind.WORK_ITEM, tick=state.tick)
            participants = [str(x) for x in (args.get("participants") or [])]
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("title") or ""), str(args.get("description") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            participants_error = self._validate_work_participants(
                state=state,
                participants=participants,
            )
            if participants_error:
                raise ValueError(participants_error)
            duplicate_work_id = self._find_duplicate_open_work_item(
                state=state,
                work_type=str(args.get("work_type") or ""),
                title=str(args.get("title") or ""),
            )
            if duplicate_work_id is not None:
                raise ValueError(f"duplicate_open_work_item:{duplicate_work_id}")
            return [
                CreateWorkItemOp(
                    created_by=agent_id,
                    work_id=wid,
                    work_type=str(args.get("work_type") or ""),
                    title=str(args.get("title") or ""),
                    description=str(args.get("description") or ""),
                    participants=participants,
                )
            ]

        if op_type == "add_work_note":
            work_id = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("work_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.WORK_ITEM},
                unknown_reason="unknown work_id",
                ambiguous_reason="ambiguous_work_id",
                action_target_id=action_target_id,
            )
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("text") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            return [AddWorkNoteOp(actor_id=agent_id, work_id=work_id, text=str(args.get("text") or ""))]

        if op_type == "submit_work_proposal":
            work_id = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("work_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.WORK_ITEM},
                unknown_reason="unknown work_id",
                ambiguous_reason="ambiguous_work_id",
                action_target_id=action_target_id,
            )
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("text") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            return [SubmitWorkProposalOp(actor_id=agent_id, work_id=work_id, text=str(args.get("text") or ""))]

        if op_type == "create_entity":
            kind_raw = str(args.get("kind") or "")
            slug = str(args.get("slug") or "")
            kind = EntityKind.ORG if kind_raw == "org" else EntityKind.CHANNEL
            eid = make_id(kind, slug)
            if state.registry.exists(eid):
                return []
            return [
                CreateEntityOp(
                    entity_id=eid,
                    kind=kind,
                    created_by=agent_id,
                    created_tick=state.tick,
                    meta={"title": str(args.get("title") or ""), "description": str(args.get("description") or "")},
                )
            ]

        if op_type == "open_vote":
            target_agent_id = str(args.get("target_agent_id") or "")
            target_error = self._validate_open_vote_target(
                state=state,
                actor_id=agent_id,
                target_agent_id=target_agent_id,
            )
            if target_error:
                raise ValueError(target_error)
            vote_id = allocator.next_id(EntityKind.VOTE, tick=state.tick)
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("new_title") or ""), str(args.get("reason") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            excluded = {target_agent_id} if not self.governance.allow_target_self_vote else set()
            voters = self.dao.eligible_voters(state, exclude_agent_ids=excluded)
            closes_tick = state.tick + self.governance.vote_duration_ticks
            return [
                OpenVoteOp(
                    vote_id=vote_id,
                    created_by=agent_id,
                    created_tick=state.tick,
                    closes_tick=closes_tick,
                    target_agent_id=target_agent_id,
                    new_title=str(args.get("new_title") or ""),
                    reason=str(args.get("reason") or ""),
                    voters=voters,
                )
            ]

        if op_type == "cast_vote":
            vote_id = str(args.get("vote_id") or "")
            if vote_id not in state.votes:
                raise ValueError("unknown vote_id")
            vote = state.votes[vote_id]
            if not self.governance.allow_target_self_vote and vote.target_agent_id == agent_id:
                raise ValueError("target_self_vote_disabled")
            if agent_id not in vote.voters:
                raise ValueError(f"agent_is_not_eligible_voter:{agent_id}")
            return [CastVoteOp(actor_id=agent_id, vote_id=vote_id, choice=str(args.get("choice") or "abstain"))]

        if op_type == "respond_nomination":
            vote_id = str(args.get("vote_id") or "")
            if vote_id not in state.votes:
                raise ValueError("unknown vote_id")
            vote = state.votes[vote_id]
            if vote.target_agent_id != agent_id:
                raise ValueError(f"only_target_can_respond:{vote_id}")
            return [SetVoteConsentOp(actor_id=agent_id, vote_id=vote_id, accept=bool(args.get("accept")))]

        if op_type == "modify_reputation":
            target_agent_id = str(args.get("target_agent_id") or "")
            if target_agent_id not in state.agents:
                raise ValueError("unknown target_agent_id")
            delta = float(args.get("delta") or 0.0)
            return [
                ModifyReputationOp(
                    actor_id=agent_id,
                    target_agent_id=target_agent_id,
                    delta=delta,
                    reason=str(args.get("reason") or ""),
                )
            ]

        if op_type == "create_artifact":
            artifact_type = str(args.get("artifact_type") or "document")
            title = str(args.get("title") or "")
            if not title:
                raise ValueError("create_artifact requires title")
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[title, str(args.get("summary") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            related_work_id = str(args.get("related_work_id") or "").strip() or None
            if related_work_id and related_work_id not in state.work_items:
                raise ValueError(f"unknown related_work_id: {related_work_id}")
            artifact_id = allocator.next_id(EntityKind.ARTIFACT, tick=state.tick)
            agent_state = state.agents.get(agent_id)
            owner_org_id = agent_state.org_id if agent_state else None
            zone_id = agent_state.zone_id if agent_state else None
            visibility = str(args.get("visibility") or "internal")
            if visibility not in {"internal", "public"}:
                visibility = "internal"
            return [
                CreateArtifactOp(
                    created_by=agent_id,
                    artifact_id=artifact_id,
                    artifact_type=artifact_type,
                    title=title,
                    summary=str(args.get("summary") or ""),
                    owner_org_id=owner_org_id,
                    zone_id=zone_id,
                    related_work_id=related_work_id,
                    visibility=visibility,
                )
            ]

        if op_type == "update_artifact":
            artifact_id = str(args.get("artifact_id") or "")
            if artifact_id not in state.artifacts:
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            return [
                UpdateArtifactOp(
                    actor_id=agent_id,
                    artifact_id=artifact_id,
                    title=str(args.get("title") or "") or None,
                    summary=str(args.get("summary") or "") or None,
                    status=str(args.get("status") or "") or None,
                    visibility=str(args.get("visibility") or "") or None,
                )
            ]

        if op_type == "narrative_action":
            description = str(args.get("description") or "")
            if not description:
                raise ValueError("narrative_action requires description")
            zone_id = str(args.get("zone_id") or "").strip() or None
            if zone_id and not state.registry.exists(zone_id):
                zone_id = None
            counterparty_resolution = self._resolve_contextual_id(
                state=state,
                raw_id=str(args.get("counterparty_agent_id") or args.get("target_agent_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.AGENT},
                action_target_id=action_target_id,
                fallback_id=fallback_target_agent_id,
                exclude_ids={agent_id},
                prefer_context=True,
            )
            if counterparty_resolution.status == "ambiguous":
                raise ValueError("ambiguous_target_agent_id")
            counterparty_agent_id = counterparty_resolution.value
            witnesses_raw = args.get("witnesses") or []
            witnesses = [str(w) for w in witnesses_raw if str(w) in state.agents]
            return [
                RecordNarrativeActionOp(
                    actor_id=agent_id,
                    description=description,
                    action_kind=str(args.get("action_kind") or "general"),
                    zone_id=zone_id,
                    counterparty_agent_id=counterparty_agent_id or None,
                    witnesses=witnesses or None,
                )
            ]

        if op_type == "upsert_informal_link":
            agent_a = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("agent_a_id") or ""),
                proposal_text="",
                allowed_kinds={EntityKind.AGENT},
                unknown_reason="unknown agent_a_id: ",
                ambiguous_reason="ambiguous_agent_a_id",
                fallback_id=agent_id,
            )
            agent_b = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("agent_b_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.AGENT},
                unknown_reason="unknown agent_b_id: ",
                ambiguous_reason="ambiguous_agent_b_id",
                action_target_id=action_target_id,
                fallback_id=fallback_target_agent_id,
                exclude_ids={agent_a or agent_id},
                prefer_context=True,
            )
            if agent_a == agent_b:
                raise ValueError("informal link requires two different agents")
            link_type = str(args.get("link_type") or "").strip()
            if not link_type:
                raise ValueError("upsert_informal_link requires link_type")
            strength_delta = float(args.get("strength_delta") or 0.1)
            strength_delta = max(-0.5, min(0.5, strength_delta))
            return [
                UpsertInformalLinkOp(
                    actor_id=agent_id,
                    agent_a_id=agent_a,
                    agent_b_id=agent_b,
                    link_type=link_type,
                    strength_delta=strength_delta,
                    visibility=str(args.get("visibility") or "latent") or "latent",
                    source=str(args.get("source") or "").strip() or agent_id,
                )
            ]

        if op_type == "add_information_signal":
            signal = str(args.get("signal") or "").strip()
            if not signal:
                raise ValueError("add_information_signal requires signal text")
            return [
                AddInformationSignalOp(
                    actor_id=agent_id,
                    signal=signal,
                )
            ]

        if op_type == "upsert_pending_interaction":
            target_agent_id = self._resolve_id_or_raise(
                state=state,
                raw_id=str(args.get("target_agent_id") or ""),
                proposal_text=proposal_text,
                allowed_kinds={EntityKind.AGENT},
                unknown_reason="unknown target_agent_id",
                ambiguous_reason="ambiguous_target_agent_id",
                action_target_id=action_target_id,
                fallback_id=fallback_target_agent_id,
                exclude_ids={agent_id},
                prefer_context=True,
            )
            source_agent_id = str(args.get("source_agent_id") or "").strip() or agent_id
            if source_agent_id not in state.agents:
                source_agent_id = agent_id
            category = str(args.get("category") or "follow_up").strip()
            summary = str(args.get("summary") or "").strip()
            if not summary:
                raise ValueError("upsert_pending_interaction requires summary")
            due_offset = int(args.get("due_offset_ticks") or 2)
            due_offset = max(1, min(20, due_offset))
            priority = str(args.get("priority") or "normal")
            if priority not in {"low", "normal", "high"}:
                priority = "normal"
            interaction_id = f"pi_t{state.tick}_{agent_id}_{allocator.next_id(EntityKind.WORK_ITEM, tick=state.tick)}"
            return [
                UpsertPendingInteractionOp(
                    actor_id=agent_id,
                    interaction_id=interaction_id,
                    target_agent_id=target_agent_id,
                    source_agent_id=source_agent_id,
                    category=category,
                    summary=summary,
                    due_tick=state.tick + due_offset,
                    priority=priority,
                )
            ]

        if op_type == "resolve_pending_interaction":
            interaction_id = str(args.get("interaction_id") or "")
            if interaction_id not in state.pending_interactions:
                raise ValueError(f"unknown interaction_id: {interaction_id}")
            resolve_status = str(args.get("status") or "completed")
            if resolve_status not in {"completed", "expired"}:
                resolve_status = "completed"
            return [
                ResolvePendingInteractionOp(
                    actor_id=agent_id,
                    interaction_id=interaction_id,
                    status=resolve_status,
                    reason=str(args.get("reason") or ""),
                )
            ]

        raise ValueError(f"unsupported op_type: {op_type}")
