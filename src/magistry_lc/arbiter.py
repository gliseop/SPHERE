"""Арбитр действий: антифантомы + YAML-journal контекст.

В MAGISTRY-LC арбитр выполняет две задачи:
1) Валидирует действия (причинность мира, существование целей).
2) Преобразует действия в детерминированные `StateOp[]`.

Для структурированных действий используется детерминированное правило.
Для `perform` (свободное действие) используется LLM с YAML-журналом мира.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from .actions import (
    Action,
    ActionType,
    CastVoteAction,
    CreateWorkItemAction,
    NoopAction,
    PerformAction,
    PublishAction,
    RequestEntityAction,
    RespondNominationAction,
    SendMessageAction,
    SubmitWorkProposalAction,
    AddWorkNoteAction,
    NominatePositionChangeAction,
)
from .config import GovernanceConfig
from .dao import DaoEngine
from .entities import EntityRecord
from .id_alloc import IdAllocator
from .ids import EntityKind, ensure_kind, make_id, parse_typed_id
from .llm import LLMCaller
from .ops import (
    AddWorkNoteOp,
    CastVoteOp,
    CreateEntityOp,
    CreateWorkItemOp,
    ModifyReputationOp,
    OpenVoteOp,
    SendMessageOp,
    SetVoteConsentOp,
    SubmitWorkProposalOp,
)
from .state import WorldState


@dataclass(slots=True)
class ActionResult:
    """Результат арбитража одного действия."""

    action_index: int
    approved: bool
    reason: str
    ops: list[object]


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
    temperature: float = 0.0

    async def arbitrate_actions(
        self,
        *,
        state: WorldState,
        agent_id: str,
        actions: list[Action],
    ) -> list[ActionResult]:
        """Преобразовать Action[] в список результатов с ops."""
        results: list[ActionResult] = []
        for idx, act in enumerate(actions):
            res = await self._arbitrate_one(state=state, agent_id=agent_id, action_index=idx, action=act)
            results.append(res)
        return results

    async def _arbitrate_one(
        self,
        *,
        state: WorldState,
        agent_id: str,
        action_index: int,
        action: Action,
    ) -> ActionResult:
        # Structured actions: deterministic translation + anti-phantoms.
        if isinstance(action, NoopAction):
            return ActionResult(action_index, True, "noop", [])

        if isinstance(action, SendMessageAction):
            if not state.registry.exists(action.to_id):
                return ActionResult(action_index, False, f"unknown to_id: {action.to_id}", [])
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
            if action.work_id not in state.work_items:
                return ActionResult(action_index, False, f"unknown work_id: {action.work_id}", [])
            return ActionResult(
                action_index,
                True,
                "add_work_note",
                [AddWorkNoteOp(actor_id=agent_id, work_id=action.work_id, text=action.text)],
            )

        if isinstance(action, SubmitWorkProposalAction):
            if action.work_id not in state.work_items:
                return ActionResult(action_index, False, f"unknown work_id: {action.work_id}", [])
            return ActionResult(
                action_index,
                True,
                "submit_work_proposal",
                [SubmitWorkProposalOp(actor_id=agent_id, work_id=action.work_id, text=action.text)],
            )

        if isinstance(action, RequestEntityAction):
            kind = EntityKind.ORG if action.kind == "org" else EntityKind.CHANNEL
            eid = make_id(kind, action.slug)
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

        if isinstance(action, NominatePositionChangeAction):
            # Только DAO: создаём голосование, затем цель должна дать consent.
            if action.target_agent_id not in state.agents:
                return ActionResult(action_index, False, f"unknown target_agent_id: {action.target_agent_id}", [])
            target = state.agents[action.target_agent_id]
            if not target.internal:
                return ActionResult(action_index, False, "cannot nominate external agent", [])

            vote_id = self.id_alloc.next_id(EntityKind.VOTE, tick=state.tick)
            voters = self.dao.eligible_voters(state)
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
            if action.vote_id not in state.votes:
                return ActionResult(action_index, False, f"unknown vote_id: {action.vote_id}", [])
            return ActionResult(
                action_index,
                True,
                "cast_vote",
                [CastVoteOp(actor_id=agent_id, vote_id=action.vote_id, choice=str(action.choice))],
            )

        if isinstance(action, RespondNominationAction):
            if action.vote_id not in state.votes:
                return ActionResult(action_index, False, f"unknown vote_id: {action.vote_id}", [])
            return ActionResult(
                action_index,
                True,
                "respond_nomination",
                [SetVoteConsentOp(actor_id=agent_id, vote_id=action.vote_id, accept=bool(action.accept))],
            )

        if isinstance(action, PerformAction):
            return await self._arbitrate_perform(state=state, agent_id=agent_id, action_index=action_index, action=action)

        return ActionResult(action_index, False, f"unsupported action: {action.type}", [])

    async def _arbitrate_perform(
        self,
        *,
        state: WorldState,
        agent_id: str,
        action_index: int,
        action: PerformAction,
    ) -> ActionResult:
        # Антифантом: если задан target_id — он должен существовать.
        target_id = action.target_id.strip()
        if target_id and not state.registry.exists(target_id):
            return ActionResult(action_index, False, f"unknown target_id: {target_id}", [])

        system = (
            "Ты — арбитр симуляции MAGISTRY-LC.\n"
            "На вход: YAML-журнал мира и свободное действие агента.\n"
            "Твоя задача: либо отклонить действие с причиной, либо выдать список StateOp,\n"
            "которые детерминированно изменят мир.\n"
            f"Политика должностей: только через DAO (vote + consent). Не меняй должности напрямую.\n"
            "Нельзя выдумывать новых агентов. Нельзя писать приватно неизвестным ID.\n"
            "Ответ: только JSON по схеме.\n"
        )
        user = (
            "YAML JOURNAL:\n"
            f"{state.journal_yaml()}\n\n"
            f"ACTION:\n"
            f"- actor_id: {agent_id}\n"
            f"- description: {action.description}\n"
            f"- target_id: {target_id}\n"
        )
        schema = _perform_output_schema()
        resp = await self.llm.generate_structured(
            role="arbiter",
            name="perform",
            tick=state.tick,
            system=system,
            user=user,
            schema=schema,
            temperature=self.temperature,
        )
        try:
            parsed = _PerformArbiterOutput.model_validate(resp.data)
        except Exception as exc:
            return ActionResult(action_index, False, f"arbiter_parse_error:{exc}", [])
        if not parsed.approved:
            return ActionResult(action_index, False, parsed.reason or "rejected", [])

        ops: list[object] = []
        for item in parsed.ops:
            try:
                ops.extend(self._op_from_llm(agent_id=agent_id, state=state, op_type=item.op_type, args=item.args))
            except Exception:
                continue

        return ActionResult(action_index, True, parsed.reason or "approved", ops)

    def _op_from_llm(self, *, agent_id: str, state: WorldState, op_type: str, args: dict[str, Any]) -> list[object]:
        """Сконвертировать LLM-op в реальные ops."""
        if op_type == "noop":
            return []

        if op_type == "send_message":
            to_id = str(args.get("to_id") or "")
            if not to_id or not state.registry.exists(to_id):
                raise ValueError("unknown to_id")
            return [
                SendMessageOp(
                    from_id=agent_id,
                    to_id=to_id,
                    text=str(args.get("text") or ""),
                    private=bool(args.get("private", True)),
                )
            ]

        if op_type == "create_work_item":
            wid = self.id_alloc.next_id(EntityKind.WORK_ITEM, tick=state.tick)
            participants = [str(x) for x in (args.get("participants") or [])]
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
            return [AddWorkNoteOp(actor_id=agent_id, work_id=work_id, text=str(args.get("text") or ""))]

        if op_type == "submit_work_proposal":
            work_id = str(args.get("work_id") or "")
            if work_id not in state.work_items:
                raise ValueError("unknown work_id")
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
            if target_agent_id not in state.agents:
                raise ValueError("unknown target_agent_id")
            vote_id = self.id_alloc.next_id(EntityKind.VOTE, tick=state.tick)
            voters = self.dao.eligible_voters(state)
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
            return [CastVoteOp(actor_id=agent_id, vote_id=vote_id, choice=str(args.get("choice") or "abstain"))]

        if op_type == "respond_nomination":
            vote_id = str(args.get("vote_id") or "")
            if vote_id not in state.votes:
                raise ValueError("unknown vote_id")
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

