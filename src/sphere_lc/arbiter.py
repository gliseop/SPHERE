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
    AddWorkNoteOp,
    CastVoteOp,
    CreateAgentOp,
    CreateEntityOp,
    CreateWorkItemOp,
    ModifyReputationOp,
    OpenVoteOp,
    SendMessageOp,
    SetVoteConsentOp,
    SubmitWorkProposalOp,
    StateOp,
)
from .state import WorldState
from .utils import (
    looks_like_machine_name,
    looks_like_role_label,
    normalize_agent_display_name,
)


_WORK_TOKEN_RE = re.compile(r"[^A-Za-zА-Яа-я0-9_]+")
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DOTTED_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_CAMEL_TO_SNAKE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _work_tokens(text: str) -> set[str]:
    return {t for t in _WORK_TOKEN_RE.split((text or "").casefold()) if t}


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
        """Найти похожее открытое дело, если оно уже существует.

        Это подавляет бюрократические дубли, когда агенты многократно
        создают почти одинаковые work items вместо работы в уже открытом деле.
        """
        new_title_tokens = _work_tokens(title)
        new_work_type = (work_type or "").strip().casefold()
        new_title_norm = " ".join(sorted(new_title_tokens))
        if not new_title_tokens:
            return None

        best_work_id: str | None = None
        best_score = 0.0
        for wid, work in state.work_items.items():
            if (work.status or "open") != "open":
                continue
            existing_tokens = _work_tokens(work.title)
            if not existing_tokens:
                continue
            existing_norm = " ".join(sorted(existing_tokens))
            if new_title_norm == existing_norm:
                return wid
            if new_work_type and (work.work_type or "").strip().casefold() != new_work_type:
                continue
            intersection = new_title_tokens & existing_tokens
            if not intersection:
                continue
            containment = len(intersection) / max(1, min(len(new_title_tokens), len(existing_tokens)))
            jaccard = len(intersection) / max(1, len(new_title_tokens | existing_tokens))
            score = containment + jaccard
            if containment >= 0.8 and jaccard >= 0.5 and score > best_score:
                best_score = score
                best_work_id = wid
        return best_work_id

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
        if looks_like_role_label(name):
            return "spawn_name_is_role_alias"
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

        system = (
            "Ты — арбитр симуляции SPHERE-LC.\n"
            "На вход: YAML-журнал мира и свободное действие агента.\n"
            "Твоя задача: либо отклонить действие с причиной, либо выдать список StateOp,\n"
            "которые детерминированно изменят мир.\n"
            "Политика должностей: только через DAO (vote + consent). Не меняй должности напрямую.\n"
            "Нельзя выдумывать новых агентов. Нельзя писать приватно неизвестным ID.\n"
            "Базовая коммуникация агента не требует отдельного capability: send_message можно использовать как естественное действие,\n"
            "но оно всё равно подчиняется физике мира, typed-id и пространственным ограничениям.\n"
            f"Actor capabilities: {sorted(agent_caps)}\n"
            "ВАЖНО: в op_type используй только snake_case-значения из JSON-схемы, а не Python-классы вроде SendMessageOp.\n"
            "Ответ: только JSON по схеме.\n"
        )
        user = (
            "YAML JOURNAL:\n"
            f"{journal_yaml}\n\n"
            "ACTION:\n"
            f"- actor_id: {agent_id}\n"
            f"- description: {action.description}\n"
            f"- target_id: {target_id}\n"
        )

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
            return _PerformArbiterOutput.model_validate(resp.data)
        except Exception as exc:
            return _PerformArbiterOutput(approved=False, reason=f"arbiter_parse_error:{exc}", ops=[])

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
        if isinstance(op, (CreateWorkItemOp, AddWorkNoteOp, SubmitWorkProposalOp)) and "work" not in caps:
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

        raise ValueError(f"unsupported op_type: {op_type}")
