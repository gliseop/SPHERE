"""Арбитр действий: антифантомы + YAML-journal контекст.

В SPHERE-LC арбитр выполняет две задачи:
1) Валидирует действия (причинность мира, существование целей).
2) Преобразует действия в детерминированные `StateOp[]`.

Для структурированных действий используется детерминированное правило.
Для `perform` (свободное действие) используется LLM с YAML-журналом мира.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from dataclasses import dataclass, field
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
)


_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DOTTED_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_CAMEL_TO_SNAKE_RE = re.compile(r"(?<!^)(?=[A-Z])")


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
        "UpsertInformalLinkOp": "upsert_informal_link",
        "AddInformationSignalOp": "add_information_signal",
        "UpsertPendingInteractionOp": "upsert_pending_interaction",
        "ResolvePendingInteractionOp": "resolve_pending_interaction",
        "NoopOp": "noop",
    }
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


class _PerformArbiterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str = ""
    ops: list[_PerformOpModel] = []


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
    elif op_type == "upsert_pending_interaction":
        _move("target_agent_id", "target_agent_id", "responder_agent_id", "recipient_agent_id")
        _move("source_agent_id", "source_agent_id", "initiator_agent_id", "initiator_id", "actor_id")
        _move("summary", "summary", "description", "text")
        _move("due_offset_ticks", "due_offset_ticks", "due_in_ticks")
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
        "description" if op_type != "narrative_action" and op_type != "add_information_signal" and op_type != "create_artifact" else "",
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

    raw_ops: list[Any] = []
    ops_value = raw.get("ops")
    if isinstance(ops_value, list):
        raw_ops.extend(ops_value)
    side_effects_value = raw.get("side_effects")
    if isinstance(side_effects_value, list):
        raw_ops.extend(side_effects_value)

    normalized_ops: list[dict[str, Any]] = []
    for item in raw_ops:
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
        normalized_ops.append(
            {
                "op_type": op_type,
                "args": _normalize_perform_op_args(op_type, args),
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

        async def _decide(m: tuple[str, int, PerformAction, set[str]]) -> _PerformArbiterOutput:
            aid, idx, act, caps = m
            return await self._decide_perform_llm(
                state=state,
                agent_id=aid,
                agent_caps=caps,
                action=act,
                journal_yaml=journal_yaml,
            )

        raw_decisions = await asyncio.gather(*[_decide(m) for m in perform_meta], return_exceptions=True)
        decisions: list[_PerformArbiterOutput] = []
        for item in raw_decisions:
            if isinstance(item, Exception):
                decisions.append(
                    _PerformArbiterOutput(
                        approved=False,
                        reason=f"arbiter_llm_error:{item.__class__.__name__}:{item}",
                        ops=[],
                    )
                )
            else:
                decisions.append(item)

        # Конвертация perform-решений → StateOp делается строго детерминированно.
        for meta, decision in zip(perform_meta, decisions, strict=True):
            aid, idx, act, caps = meta
            res = self._convert_perform_decision(
                state=state,
                agent_id=aid,
                agent_caps=caps,
                action_index=idx,
                decision=decision,
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
            contact_error = self._validate_private_contact_feasibility(
                state=state,
                from_id=agent_id,
                to_id=action.to_id,
                private=bool(action.private),
            )
            if contact_error:
                return ActionResult(action_index, False, contact_error, [])
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
        if self._should_retry_unmaterialized_proposal(proposal=proposal, decision=retried):
            return _PerformArbiterOutput(
                approved=False,
                reason="proposal_not_materialized_after_retry",
                ops=[],
            )
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
        if str(decision.reason or "").strip() in {"noop", "noop_proposal"}:
            return False
        return True

    def _convert_perform_decision(
        self,
        *,
        state: WorldState,
        agent_id: str,
        agent_caps: set[str],
        action_index: int,
        decision: _PerformArbiterOutput,
    ) -> ActionResult:
        if not decision.approved:
            return ActionResult(action_index, False, decision.reason or "rejected", [])

        scratch_alloc = IdAllocator(counters=dict(self.id_alloc.counters or {}))
        ops: list[StateOp] = []
        for item in decision.ops:
            try:
                parsed_ops = self._op_from_llm(
                    agent_id=agent_id,
                    state=state,
                    op_type=item.op_type,
                    args=item.args,
                    id_alloc=scratch_alloc,
                )
            except Exception as exc:
                return ActionResult(
                    action_index,
                    False,
                    f"perform_op_invalid:{item.op_type}:{exc.__class__.__name__}:{exc}",
                    [],
                )
            for op in parsed_ops:
                missing = self._missing_capability_for_op(op, agent_caps)
                if missing:
                    return ActionResult(action_index, False, f"missing_capability:{missing}", [])
                ops.append(op)

        self.id_alloc.counters = dict(scratch_alloc.counters or {})
        return ActionResult(action_index, True, decision.reason or "approved", ops)

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
        decision = await self._decide_perform_llm(
            state=state,
            agent_id=agent_id,
            agent_caps=agent_caps,
            action=action,
            journal_yaml=journal_yaml,
        )
        return self._convert_perform_decision(
            state=state,
            agent_id=agent_id,
            agent_caps=agent_caps,
            action_index=action_index,
            decision=decision,
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
    def _validate_private_contact_feasibility(
        *,
        state: WorldState,
        from_id: str,
        to_id: str,
        private: bool,
    ) -> str | None:
        if not private:
            return None
        sender = state.agents.get(from_id)
        recipient = state.agents.get(to_id)
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

    def _op_from_llm(
        self,
        *,
        agent_id: str,
        state: WorldState,
        op_type: str,
        args: dict[str, Any],
        id_alloc: IdAllocator | None = None,
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
            to_id = str(args.get("to_id") or "")
            if not to_id or not state.registry.exists(to_id):
                raise ValueError("unknown to_id")
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("text") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            target_error = self._validate_message_target(
                to_id=to_id,
                private=bool(args.get("private", True)),
            )
            if target_error:
                raise ValueError(target_error)
            contact_error = self._validate_private_contact_feasibility(
                state=state,
                from_id=agent_id,
                to_id=to_id,
                private=bool(args.get("private", True)),
            )
            if contact_error:
                raise ValueError(contact_error)
            return [
                SendMessageOp(
                    from_id=agent_id,
                    to_id=to_id,
                    text=str(args.get("text") or ""),
                    private=bool(args.get("private", True)),
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
            work_id = str(args.get("work_id") or "")
            if work_id not in state.work_items:
                raise ValueError("unknown work_id")
            temporal_error = self._validate_temporal_texts(
                current_tick=state.tick,
                texts=[str(args.get("text") or "")],
            )
            if temporal_error:
                raise ValueError(temporal_error)
            return [AddWorkNoteOp(actor_id=agent_id, work_id=work_id, text=str(args.get("text") or ""))]

        if op_type == "submit_work_proposal":
            work_id = str(args.get("work_id") or "")
            if work_id not in state.work_items:
                raise ValueError("unknown work_id")
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
            witnesses_raw = args.get("witnesses") or []
            witnesses = [str(w) for w in witnesses_raw if str(w) in state.agents]
            return [
                RecordNarrativeActionOp(
                    actor_id=agent_id,
                    description=description,
                    action_kind=str(args.get("action_kind") or "general"),
                    zone_id=zone_id,
                    witnesses=witnesses or None,
                )
            ]

        if op_type == "upsert_informal_link":
            agent_a = str(args.get("agent_a_id") or "")
            agent_b = str(args.get("agent_b_id") or "")
            if agent_a not in state.agents:
                raise ValueError(f"unknown agent_a_id: {agent_a}")
            if agent_b not in state.agents:
                raise ValueError(f"unknown agent_b_id: {agent_b}")
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
                    source=str(args.get("source") or "arbiter_side_effect"),
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
            target_agent_id = str(args.get("target_agent_id") or "")
            if target_agent_id not in state.agents:
                raise ValueError(f"unknown target_agent_id: {target_agent_id}")
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
