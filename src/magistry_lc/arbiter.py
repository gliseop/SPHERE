"""Арбитр действий: антифантомы + YAML-journal контекст.

В MAGISTRY-LC арбитр выполняет две задачи:
1) Валидирует действия (причинность мира, существование целей).
2) Преобразует действия в детерминированные `StateOp[]`.

Для структурированных действий используется детерминированное правило.
Для `perform` (свободное действие) используется LLM с YAML-журналом мира.
"""

from __future__ import annotations

import asyncio
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
    StateOp,
)
from .state import WorldState


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
        for idx, act in enumerate(actions):
            res = await self._arbitrate_one(
                state=state,
                agent_id=agent_id,
                action_index=idx,
                action=act,
                journal_yaml=journal_yaml,
            )
            results.append(res)
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

        for aid in sorted(proposed.keys()):
            arbitration[aid] = []
            agent = state.agents.get(aid)
            caps = set(agent.capabilities) if agent is not None else set()
            for idx, act in enumerate(proposed[aid]):
                if isinstance(act, PerformAction):
                    arbitration[aid].append(None)
                    perform_meta.append((aid, idx, act, caps))
                else:
                    arbitration[aid].append(
                        await self._arbitrate_one(
                            state=state,
                            agent_id=aid,
                            action_index=idx,
                            action=act,
                            journal_yaml=journal_yaml,
                        )
                    )

        async def _decide(m: tuple[str, int, PerformAction, set[str]]) -> _PerformArbiterOutput:
            aid, idx, act, caps = m
            return await self._decide_perform_llm(
                state=state,
                agent_id=aid,
                agent_caps=caps,
                action=act,
                journal_yaml=journal_yaml,
            )

        decisions = await asyncio.gather(*[_decide(m) for m in perform_meta])

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
            arbitration[aid][idx] = res

        # Убираем None (на всякий случай) и приводим тип.
        out: dict[str, list[ActionResult]] = {}
        for aid, items in arbitration.items():
            out[aid] = [r for r in items if r is not None]  # type: ignore[truthy-bool]
        return out

    async def _arbitrate_one(
        self,
        *,
        state: WorldState,
        agent_id: str,
        action_index: int,
        action: Action,
        journal_yaml: str,
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

        if isinstance(action, SendMessageAction):
            missing = _require("message")
            if missing:
                return ActionResult(action_index, False, missing, [])
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
            missing = _require("message")
            if missing:
                return ActionResult(action_index, False, missing, [])
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
            missing = _require("dao")
            if missing:
                return ActionResult(action_index, False, missing, [])
            # Только DAO: создаём голосование, затем цель должна дать consent.
            if action.target_agent_id not in state.agents:
                return ActionResult(action_index, False, f"unknown target_agent_id: {action.target_agent_id}", [])
            target = state.agents[action.target_agent_id]
            if not target.internal:
                return ActionResult(action_index, False, "cannot nominate external agent", [])
            if not target.wants_promotion:
                return ActionResult(action_index, False, "target_declines_promotion", [])

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
            missing = _require("dao")
            if missing:
                return ActionResult(action_index, False, missing, [])
            if action.vote_id not in state.votes:
                return ActionResult(action_index, False, f"unknown vote_id: {action.vote_id}", [])
            return ActionResult(
                action_index,
                True,
                "cast_vote",
                [CastVoteOp(actor_id=agent_id, vote_id=action.vote_id, choice=str(action.choice))],
            )

        if isinstance(action, RespondNominationAction):
            missing = _require("dao")
            if missing:
                return ActionResult(action_index, False, missing, [])
            if action.vote_id not in state.votes:
                return ActionResult(action_index, False, f"unknown vote_id: {action.vote_id}", [])
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
            "Ты — арбитр симуляции MAGISTRY-LC.\n"
            "На вход: YAML-журнал мира и свободное действие агента.\n"
            "Твоя задача: либо отклонить действие с причиной, либо выдать список StateOp,\n"
            "которые детерминированно изменят мир.\n"
            "Политика должностей: только через DAO (vote + consent). Не меняй должности напрямую.\n"
            "Нельзя выдумывать новых агентов. Нельзя писать приватно неизвестным ID.\n"
            f"Actor capabilities: {sorted(agent_caps)}\n"
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

        ops: list[StateOp] = []
        for item in decision.ops:
            try:
                parsed_ops = self._op_from_llm(agent_id=agent_id, state=state, op_type=item.op_type, args=item.args)
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
        if isinstance(op, SendMessageOp) and "message" not in caps:
            return "message"
        if isinstance(op, (CreateWorkItemOp, AddWorkNoteOp, SubmitWorkProposalOp)) and "work" not in caps:
            return "work"
        if isinstance(op, (OpenVoteOp, CastVoteOp, SetVoteConsentOp)) and "dao" not in caps:
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

    def _op_from_llm(self, *, agent_id: str, state: WorldState, op_type: str, args: dict[str, Any]) -> list[StateOp]:
        """Сконвертировать LLM-op в реальные ops."""
        if op_type == "noop":
            return []

        if op_type == "send_message":
            to_id = str(args.get("to_id") or "")
            if not to_id or not state.registry.exists(to_id):
                raise ValueError("unknown to_id")
            target_error = self._validate_message_target(
                to_id=to_id,
                private=bool(args.get("private", True)),
            )
            if target_error:
                raise ValueError(target_error)
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
            if not state.agents[target_agent_id].wants_promotion:
                raise ValueError("target_declines_promotion")
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
