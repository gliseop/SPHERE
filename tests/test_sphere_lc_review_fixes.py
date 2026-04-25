from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sphere_lc.agent import AgentRunner, event_visible_to_agent
from sphere_lc.auditor import RuntimeAuditor
from sphere_lc.llm import MockLLMProvider, StructuredLLMResponse

from sphere_lc.actions import (
    ActionType,
    CastVoteAction,
    CreateWorkItemAction,
    NominatePositionChangeAction,
    PerformAction,
    RequestEntityAction,
    RespondNominationAction,
    SendMessageAction,
    SpawnAgentAction,
)
from sphere_lc.arbiter import Arbiter, _PerformArbiterOutput, _normalize_perform_op_type
from sphere_lc.config import (
    DEFAULT_LLM_MODEL,
    AuditRuntimeConfig,
    GovernanceConfig,
    LLMConfig,
    MemoryConfig,
    RuntimeConfig,
    ScenarioConfig,
)
from sphere_lc.dao import DaoEngine
from sphere_lc.cli import _cmd_run
from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.entities import EntityRecord, EntityRegistry
from sphere_lc.events import Event, EventLog
from sphere_lc.id_alloc import IdAllocator
from sphere_lc.ids import EntityKind, INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from sphere_lc.journal import WorldJournal
from sphere_lc.llm import LLMCaller
from sphere_lc.llm.caller import create_llm_provider
from sphere_lc.llm.providers import OpenAICompatibleProvider, create_provider
from sphere_lc.memory import AgentMemory, WorkingEntry
from sphere_lc.ops import CastVoteOp, CreateAgentOp, RecordNarrativeActionOp
from sphere_lc.ops import CloseAuditCaseOp, OpenAuditCaseOp, OpenVoteOp, UpdateAuditCaseOp
from sphere_lc.state import AgentState, ArtifactState, Vote, WorkItem, WorldState
from sphere_lc.tracing import TraceLog
from sphere_lc.worldgen import WorldGenerator


def _mk_state(*, off_1_caps: list[str], off_2_caps: list[str], off_2_wants_promotion: bool = True) -> WorldState:
    reg = EntityRegistry()
    state = WorldState(tick=0, registry=reg)

    # Channel for public messages.
    reg.register(EntityRecord(entity_id="chan:public", kind=EntityKind.CHANNEL, created_by=None, created_tick=0, meta={}))

    # Agents.
    for aid, name, caps, wants in (
        ("agent:off_1", "Off 1", off_1_caps, True),
        ("agent:off_2", "Off 2", off_2_caps, off_2_wants_promotion),
    ):
        reg.register(EntityRecord(entity_id=aid, kind=EntityKind.AGENT, created_by=None, created_tick=0, meta={"name": name}))
        state.agents[aid] = AgentState(
            agent_id=aid,
            name=name,
            internal=True,
            capabilities=list(caps),
            wants_promotion=bool(wants),
        )

    return state


def _mk_arbiter(tmp_path: Path, *, mock: MockLLMProvider) -> Arbiter:
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)
    gov = GovernanceConfig()
    return Arbiter(llm=llm, governance=gov, id_alloc=IdAllocator(), dao=DaoEngine(cfg=gov), temperature=0.0)


class _FailingPerformProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "- actor_id: agent:off_1" in user and not self._failed:
            self._failed = True
            raise RuntimeError("perform-llm-failure")
        return super().generate_structured(system, user, schema, temperature)


class _RetryingMaterializationProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.perform_calls = 0

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "- actor_id: agent:off_1" in user:
            self.perform_calls += 1
            if "PREVIOUS ATTEMPT:" in user:
                return StructuredLLMResponse(
                    data={
                        "approved": True,
                        "reason": "materialized_after_retry",
                        "ops": [
                            {
                                "op_type": "send_message",
                                "args": {"to_id": "agent:off_2", "text": "Нужно вынести вопрос на обсуждение.", "private": True},
                            }
                        ],
                    },
                    model="mock",
                )
            return StructuredLLMResponse(
                data={"approved": True, "reason": "approved", "ops": []},
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _IdleProposalProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.perform_calls = 0

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "- actor_id: agent:off_1" in user:
            self.perform_calls += 1
            return StructuredLLMResponse(
                data={"approved": True, "reason": "noop_proposal", "ops": []},
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _StillEmptyMaterializationProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.perform_calls = 0

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "- actor_id: agent:off_1" in user:
            self.perform_calls += 1
            return StructuredLLMResponse(
                data={"approved": True, "reason": "approved_but_empty", "ops": []},
                model="mock",
        )
        return super().generate_structured(system, user, schema, temperature)


class _ObservationOnlyProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.perform_calls = 0
        self.observation_checks = 0

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "является ли шаг observation-only" in system:
            self.observation_checks += 1
            return StructuredLLMResponse(
                data={"observation_only": True, "rationale": "read-only"},
                model="mock",
            )
        if "- actor_id: agent:off_1" in user:
            self.perform_calls += 1
            return StructuredLLMResponse(
                data={
                    "approved": False,
                    "reason": "Просмотр существующего документа не создаёт изменения состояния мира.",
                    "ops": [],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _MixedObservationProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "ACTOR: agent:off_1" in user and "PROPOSAL:" in user:
            return StructuredLLMResponse(
                data={"steps": ["Сначала просмотрю art:brief и сверю детали.", "Затем напишу agent:off_2 короткое сообщение."]},
                model="mock",
            )
        if "является ли шаг observation-only" in system and "просмотрю art:brief" in user:
            return StructuredLLMResponse(data={"observation_only": True, "rationale": "read-only"}, model="mock")
        if "- actor_id: agent:off_1" in user and "просмотрю art:brief" in user:
            return StructuredLLMResponse(
                data={"approved": False, "reason": "read-only review", "ops": []},
                model="mock",
            )
        if "- actor_id: agent:off_1" in user and "напишу agent:off_2" in user:
            return StructuredLLMResponse(
                data={
                    "approved": True,
                    "reason": "approved",
                    "ops": [
                        {
                            "op_type": "send_message",
                            "args": {"to_id": "agent:off_2", "private": True, "text": "Коротко сообщаю итог сверки."},
                        }
                    ],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _StepwisePerformProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "ACTOR: agent:off_1" in user and "PROPOSAL:" in user:
            return StructuredLLMResponse(
                data={
                    "steps": [
                        "Сначала перейду в соседний кабинет, чтобы оказаться рядом с коллегой.",
                        "После этого лично сообщу коллеге итог и зафиксирую короткое сообщение.",
                    ]
                },
                model="mock",
            )
        if "- actor_id: agent:off_1" in user and "перейду в соседний кабинет" in user:
            return StructuredLLMResponse(
                data={
                    "approved": True,
                    "reason": "move_first",
                    "ops": [
                        {
                            "op_type": "narrative_action",
                            "args": {
                                "description": "Перешёл в кабинет коллеги.",
                                "zone_id": "zone:room_b",
                            },
                        }
                    ],
                },
                model="mock",
            )
        if "- actor_id: agent:off_1" in user and "лично сообщу коллеге итог" in user:
            return StructuredLLMResponse(
                data={
                    "approved": True,
                    "reason": "message_after_move",
                    "ops": [
                        {
                            "op_type": "send_message",
                            "args": {
                                "to_id": "agent:off_2",
                                "text": "Теперь можно обсудить итог лично.",
                                "private": True,
                            },
                        }
                    ],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _DocumentGroundingProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "document-layer materialization" in system:
            return StructuredLLMResponse(
                data={
                    "supported_level": "unsupported",
                    "rewritten_text": "Запросил у коллег подтверждение и ожидаю письменные пояснения по расхождениям.",
                    "rationale": "candidate note утверждает уже полученный результат, которого в мире пока нет",
                },
                model="mock",
            )
        if "- actor_id: agent:off_1" in user:
            return StructuredLLMResponse(
                data={
                    "approved": True,
                    "reason": "approved",
                    "ops": [
                        {
                            "op_type": "add_work_note",
                            "args": {
                                "work_id": "work:case",
                                "text": "Получил подтверждение от коллег и учёл письменные пояснения в деле.",
                            },
                        }
                    ],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _FailingProposeProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Off 1" in user and not self._failed:
            self._failed = True
            raise RuntimeError("agent-llm-failure")
        return super().generate_structured(system, user, schema, temperature)


def test_arbiter_allows_basic_communication_without_message_capability(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=[], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = SendMessageAction(
        type=ActionType.SEND_MESSAGE,
        to_id="agent:off_2",
        text="hi",
        private=True,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is True
    assert res[0].reason == "send_message"


def test_arbiter_rejects_private_message_to_non_agent_target(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = SendMessageAction(
        type=ActionType.SEND_MESSAGE,
        to_id="chan:public",
        text="hi",
        private=True,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "private_message_requires_agent_target" in res[0].reason


def test_arbiter_allows_private_message_across_known_zones(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=[], off_2_caps=[])
    state.agents["agent:off_1"].zone_id = "zone:left"
    state.agents["agent:off_2"].zone_id = "zone:right"
    state.registry.register(
        EntityRecord(entity_id="zone:left", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    state.registry.register(
        EntityRecord(entity_id="zone:right", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = SendMessageAction(
        type=ActionType.SEND_MESSAGE,
        to_id="agent:off_2",
        text="hi",
        private=True,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is True
    assert res[0].reason == "send_message"


def test_narrative_action_with_zone_updates_actor_location() -> None:
    state = _mk_state(off_1_caps=["message", "work"], off_2_caps=["message"])
    state.agents["agent:off_1"].zone_id = "zone:left"
    state.registry.register(
        EntityRecord(entity_id="zone:left", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    state.registry.register(
        EntityRecord(entity_id="zone:right", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )

    op = RecordNarrativeActionOp(
        actor_id="agent:off_1",
        description="Перешёл в соседний кабинет.",
        action_kind="move",
        zone_id="zone:right",
    )
    events = op.apply(state)

    assert state.agents["agent:off_1"].zone_id == "zone:right"
    assert events[0].event_type == "narrative_action"
    assert events[0].payload["previous_zone_id"] == "zone:left"
    assert events[0].payload["relocated"] is True


def test_arbiter_allows_in_person_contact_after_same_turn_relocation(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message", "work"], off_2_caps=["message"])
    state.agents["agent:off_1"].zone_id = "zone:left"
    state.agents["agent:off_2"].zone_id = "zone:right"
    state.registry.register(
        EntityRecord(entity_id="zone:left", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    state.registry.register(
        EntityRecord(entity_id="zone:right", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    decision = _PerformArbiterOutput.model_validate(
        {
            "approved": True,
            "reason": "approved",
            "ops": [
                {
                    "op_type": "narrative_action",
                    "args": {
                        "description": "Сначала зайду в кабинет Off 2.",
                        "action_kind": "move",
                        "zone_id": "zone:right",
                    },
                },
                {
                    "op_type": "in_person_contact",
                    "args": {
                        "target_agent_id": "agent:off_2",
                        "summary": "Нужно обсудить вопрос лично.",
                    },
                },
            ],
        }
    )
    res, _, _ = asyncio.run(
        arbiter._convert_perform_decision(
            state=state,
            agent_id="agent:off_1",
            proposal="Сначала приду в кабинет, потом поговорю лично.",
            step_target_id="",
            agent_caps={"message"},
            action_index=0,
            decision=decision,
        )
    )

    assert res.approved is True
    assert len(res.ops) == 2
    scratch_state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    scratch_state.agents["agent:off_1"].zone_id = "zone:left"
    scratch_state.agents["agent:off_2"].zone_id = "zone:right"
    scratch_state.registry.register(
        EntityRecord(entity_id="zone:left", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    scratch_state.registry.register(
        EntityRecord(entity_id="zone:right", kind=EntityKind.ZONE, created_by=None, created_tick=0)
    )
    for op in res.ops:
        op.apply(scratch_state)
    assert scratch_state.agents["agent:off_1"].zone_id == "zone:right"


def test_arbiter_fills_side_effect_agent_ids_from_context(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    decision = _PerformArbiterOutput.model_validate(
        {
            "approved": True,
            "reason": "approved",
            "ops": [
                {
                    "op_type": "send_message",
                    "args": {
                        "to_id": "agent:off_2",
                        "private": True,
                        "text": "Нужно коротко сверить детали.",
                    },
                },
                {
                    "op_type": "upsert_informal_link",
                    "source": "side_effect",
                    "args": {
                        "link_type": "coordination",
                        "strength_delta": 0.1,
                    },
                },
                {
                    "op_type": "upsert_pending_interaction",
                    "source": "side_effect",
                    "args": {
                        "summary": "Жду ответ по деталям.",
                    },
                },
            ],
        }
    )

    res, _, _ = asyncio.run(
        arbiter._convert_perform_decision(
            state=state,
            agent_id="agent:off_1",
            proposal="Напишу agent:off_2 и зафиксирую, что жду от него ответ.",
            step_target_id="",
            agent_caps={"message"},
            action_index=0,
            decision=decision,
        )
    )

    assert res.approved is True
    assert [op.__class__.__name__ for op in res.ops] == [
        "SendMessageOp",
        "UpsertInformalLinkOp",
        "UpsertPendingInteractionOp",
    ]


def test_arbiter_recovers_canonical_agent_id_from_step_text(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    decision = _PerformArbiterOutput.model_validate(
        {
            "approved": True,
            "reason": "approved",
            "ops": [
                {
                    "op_type": "send_message",
                    "args": {
                        "to_id": "contractor",
                        "private": True,
                        "text": "Нужна короткая сверка по срокам.",
                    },
                }
            ],
        }
    )

    res, _, _ = asyncio.run(
        arbiter._convert_perform_decision(
            state=state,
            agent_id="agent:off_1",
            proposal="Напишу agent:off_2 и уточню сроки.",
            step_target_id="",
            agent_caps={"message"},
            action_index=0,
            decision=decision,
        )
    )

    assert res.approved is True
    assert [op.__class__.__name__ for op in res.ops] == ["SendMessageOp"]
    assert res.ops[0].to_id == "agent:off_2"


def test_send_message_prefers_context_over_full_step_multi_mention(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.agents["agent:off_3"] = AgentState(
        agent_id="agent:off_3",
        name="Off 3",
        internal=True,
        capabilities=["message"],
    )
    state.registry.register(
        EntityRecord(entity_id="agent:off_3", kind=EntityKind.AGENT, created_by=None, created_tick=0, meta={"name": "Off 3"})
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    decision = _PerformArbiterOutput.model_validate(
        {
            "approved": True,
            "reason": "approved",
            "ops": [
                {
                    "op_type": "send_message",
                    "args": {
                        "to_id": "",
                        "private": True,
                        "text": "Сначала напишу коллеге по итогам шага.",
                    },
                }
            ],
        }
    )

    res, _, _ = asyncio.run(
        arbiter._convert_perform_decision(
            state=state,
            agent_id="agent:off_1",
            proposal="Сначала напишу agent:off_2, а затем отдельно сообщу agent:off_3, если понадобится.",
            step_target_id="agent:off_2",
            agent_caps={"message"},
            action_index=0,
            decision=decision,
        )
    )

    assert res.approved is True
    assert [op.__class__.__name__ for op in res.ops] == ["SendMessageOp"]
    assert res.ops[0].to_id == "agent:off_2"


def test_arbiter_resolves_exact_display_name_from_args(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    decision = _PerformArbiterOutput.model_validate(
        {
            "approved": True,
            "reason": "approved",
            "ops": [
                {
                    "op_type": "send_message",
                    "args": {
                        "to_id": "Off 2",
                        "private": True,
                        "text": "Нужна короткая сверка.",
                    },
                }
            ],
        }
    )

    res, _, _ = asyncio.run(
        arbiter._convert_perform_decision(
            state=state,
            agent_id="agent:off_1",
            proposal="Напишу коллеге Off 2 и уточню детали.",
            step_target_id="",
            agent_caps={"message"},
            action_index=0,
            decision=decision,
        )
    )

    assert res.approved is True
    assert res.ops[0].to_id == "agent:off_2"


def test_arbiter_returns_ambiguous_to_id_for_duplicate_display_name(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.agents["agent:off_3"] = AgentState(
        agent_id="agent:off_3",
        name="Off 2",
        internal=True,
        capabilities=["message"],
    )
    state.registry.register(
        EntityRecord(entity_id="agent:off_3", kind=EntityKind.AGENT, created_by=None, created_tick=0, meta={"name": "Off 2"})
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    decision = _PerformArbiterOutput.model_validate(
        {
            "approved": True,
            "reason": "approved",
            "ops": [
                {
                    "op_type": "send_message",
                    "args": {
                        "to_id": "Off 2",
                        "private": True,
                        "text": "Нужна короткая сверка.",
                    },
                }
            ],
        }
    )

    res, _, _ = asyncio.run(
        arbiter._convert_perform_decision(
            state=state,
            agent_id="agent:off_1",
            proposal="Напишу Off 2 и уточню детали.",
            step_target_id="",
            agent_caps={"message"},
            action_index=0,
            decision=decision,
        )
    )

    assert res.approved is False
    assert "ambiguous_to_id" in res.reason


def test_arbiter_observation_only_step_is_approved_noop(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    provider = _ObservationOnlyProvider()
    arbiter = _mk_arbiter(tmp_path, mock=provider)

    act = PerformAction(
        type=ActionType.PERFORM,
        description="Просмотрю art:brief и сверю его с текущей перепиской.",
        target_id="",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert res[0].reason == "observation_only"
    assert res[0].ops == []
    assert provider.observation_checks >= 1


def test_arbiter_keeps_materialized_followup_after_observation_step(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.artifacts["art:brief"] = ArtifactState(
        artifact_id="art:brief",
        artifact_type="brief",
        title="Brief",
        summary="Краткая справка",
        owner_org_id=None,
        zone_id=None,
    )
    state.registry.register(
        EntityRecord(entity_id="art:brief", kind=EntityKind.ARTIFACT, created_by=None, created_tick=0, meta={"title": "Brief"})
    )
    arbiter = _mk_arbiter(tmp_path, mock=_MixedObservationProvider())

    act = PerformAction(
        type=ActionType.PERFORM,
        description="Сначала просмотрю art:brief и сверю детали, затем напишу agent:off_2 короткое сообщение.",
        target_id="",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert [op.__class__.__name__ for op in res[0].ops] == ["SendMessageOp"]


def test_arbiter_rejects_public_message_to_non_channel_target(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message", "work"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = SendMessageAction(
        type=ActionType.SEND_MESSAGE,
        to_id="agent:off_2",
        text="hi",
        private=False,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "public_message_requires_chan_or_org_target" in res[0].reason


def test_arbiter_blocks_nomination_when_target_declines_promotion(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"], off_2_wants_promotion=False)
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = NominatePositionChangeAction(
        type=ActionType.NOMINATE_POSITION_CHANGE,
        target_agent_id="agent:off_2",
        new_title="начальник",
        reason="test",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "target_declines_promotion" in res[0].reason


def test_arbiter_blocks_self_nomination_by_default(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = NominatePositionChangeAction(
        type=ActionType.NOMINATE_POSITION_CHANGE,
        target_agent_id="agent:off_1",
        new_title="начальник",
        reason="test",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert res[0].reason == "self_nomination_disabled"


def test_arbiter_rejects_work_item_with_unknown_participant(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["work"], off_2_caps=["work"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = CreateWorkItemAction(
        type=ActionType.CREATE_WORK_ITEM,
        work_type="task",
        title="Task",
        description="",
        participants=["agent:ghost"],
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "unknown_participant_agent_id" in res[0].reason


def test_arbiter_allows_similarly_worded_open_work_item_without_lexical_dedup(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["work"], off_2_caps=["work"])
    state.work_items["work:existing"] = WorkItem(
        work_id="work:existing",
        work_type="procurement_review",
        title="Подписание договора с ООО СтройГарант",
        description="",
        participants=["agent:off_1"],
    )
    state.registry.register(
        EntityRecord(
            entity_id="work:existing",
            kind=EntityKind.WORK_ITEM,
            created_by=None,
            created_tick=0,
            meta={"work_type": "procurement_review", "title": "Подписание договора с ООО СтройГарант"},
        )
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = CreateWorkItemAction(
        type=ActionType.CREATE_WORK_ITEM,
        work_type="procurement_review",
        title="Подготовка и подписание договора с ООО СтройГарант",
        description="",
        participants=["agent:off_1"],
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is True


def test_arbiter_rejects_vote_from_non_voter(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_2"],
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = CastVoteAction(
        type=ActionType.CAST_VOTE,
        vote_id="vote:1",
        choice="yes",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "agent_is_not_eligible_voter" in res[0].reason


def test_cli_module_invocation_executes_main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    src_dir = project_root / "src"
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{current_pythonpath}" if current_pythonpath else str(src_dir)
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "sphere_lc.cli", "--help"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0
    assert "SPHERE-LC" in result.stdout


def test_arbiter_rejects_target_self_vote_by_default(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_2",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_1",
        new_title="lead",
        voters=["agent:off_1", "agent:off_2"],
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = CastVoteAction(
        type=ActionType.CAST_VOTE,
        vote_id="vote:1",
        choice="yes",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert res[0].reason == "target_self_vote_disabled"


def test_arbiter_rejects_nomination_response_from_non_target(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1", "agent:off_2"],
    )
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = RespondNominationAction(
        type=ActionType.RESPOND_NOMINATION,
        vote_id="vote:1",
        accept=True,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "only_target_can_respond" in res[0].reason


def test_arbiter_allows_nomination_response_without_dao_capability(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["message", "work"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    open_vote = NominatePositionChangeAction(
        type=ActionType.NOMINATE_POSITION_CHANGE,
        target_agent_id="agent:off_2",
        new_title="lead",
        reason="test",
        justification="",
    )
    opened = asyncio.run(
        arbiter.arbitrate_actions(
            state=state,
            agent_id="agent:off_1",
            actions=[open_vote],
        )
    )
    assert opened[0].approved is True
    for op in opened[0].ops:
        op.apply(state)

    respond = RespondNominationAction(
        type=ActionType.RESPOND_NOMINATION,
        vote_id="vote:0_1",
        accept=True,
        justification="",
    )
    accepted = asyncio.run(
        arbiter.arbitrate_actions(
            state=state,
            agent_id="agent:off_2",
            actions=[respond],
        )
    )
    assert accepted[0].approved is True
    assert accepted[0].reason == "respond_nomination"


def test_arbiter_rejects_second_open_vote_for_same_target_in_tick(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    actions = [
        NominatePositionChangeAction(
            type=ActionType.NOMINATE_POSITION_CHANGE,
            target_agent_id="agent:off_2",
            new_title="lead",
            reason="first",
            justification="",
        ),
        NominatePositionChangeAction(
            type=ActionType.NOMINATE_POSITION_CHANGE,
            target_agent_id="agent:off_2",
            new_title="lead",
            reason="second",
            justification="",
        ),
    ]
    out = asyncio.run(
        arbiter.arbitrate_tick(
            state=state,
            proposed={"agent:off_1": actions},
            journal_yaml=state.journal_yaml(),
        )
    )
    assert out["agent:off_1"][0].approved is True
    assert out["agent:off_1"][1].approved is False
    assert "open_vote_already_exists_for_target" in out["agent:off_1"][1].reason


def test_arbiter_deduplicates_request_entity_in_same_tick(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    out = asyncio.run(
        arbiter.arbitrate_tick(
            state=state,
            proposed={
                "agent:off_1": [
                    RequestEntityAction(
                        type=ActionType.REQUEST_ENTITY,
                        kind="chan",
                        slug="shared",
                        description="",
                        justification="",
                    )
                ],
                "agent:off_2": [
                    RequestEntityAction(
                        type=ActionType.REQUEST_ENTITY,
                        kind="chan",
                        slug="shared",
                        description="",
                        justification="",
                    )
                ],
            },
            journal_yaml=state.journal_yaml(),
        )
    )

    assert out["agent:off_1"][0].approved is True
    assert [type(op).__name__ for op in out["agent:off_1"][0].ops] == ["CreateEntityOp"]
    assert out["agent:off_2"][0].approved is True
    assert out["agent:off_2"][0].reason == "entity_already_exists"
    assert out["agent:off_2"][0].ops == []


def test_request_entity_requires_internal_actor_by_default(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.agents["agent:off_2"].internal = False
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    out = asyncio.run(
        arbiter.arbitrate_actions(
            state=state,
            agent_id="agent:off_2",
            actions=[
                RequestEntityAction(
                    type=ActionType.REQUEST_ENTITY,
                    kind="chan",
                    slug="external_public",
                    description="external request",
                    justification="",
                )
            ],
        )
    )

    assert out[0].approved is False
    assert out[0].reason == "request_entity_requires_internal_actor"


def test_request_entity_accepts_typed_channel_slug_without_double_prefix(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    out = asyncio.run(
        arbiter.arbitrate_actions(
            state=state,
            agent_id="agent:off_1",
            actions=[
                RequestEntityAction(
                    type=ActionType.REQUEST_ENTITY,
                    kind="chan",
                    slug="chan:procurement_confidential",
                    description="private procurement coordination",
                    justification="",
                )
            ],
        )
    )

    assert out[0].approved is True
    assert out[0].ops[0].entity_id == "chan:procurement_confidential"


def test_arbiter_open_vote_excludes_target_from_voters_by_default(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())

    act = NominatePositionChangeAction(
        type=ActionType.NOMINATE_POSITION_CHANGE,
        target_agent_id="agent:off_2",
        new_title="lead",
        reason="normal vote",
        justification="",
    )
    out = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert out[0].approved is True
    open_vote_op = out[0].ops[0]
    assert open_vote_op.voters == ["agent:off_1"]


def test_perform_invalid_op_is_rejected_not_silently_skipped(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    mock = MockLLMProvider(
        structured_responses={
            "test-perform": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "send_message",
                        "args": {"to_id": "agent:off_2", "text": "hi", "private": True},
                    },
                    {"op_type": "unsupported_op", "args": {}},
                ],
            }
        }
    )
    arbiter = _mk_arbiter(tmp_path, mock=mock)

    act = PerformAction(type=ActionType.PERFORM, description="test-perform", target_id="", justification="")
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert "perform_op_invalid" in res[0].reason


def test_perform_rejection_does_not_consume_id_allocator(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=[], off_2_caps=["work"])
    mock = MockLLMProvider(
        structured_responses={
            "blocked-task": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "create_work_item",
                        "args": {"work_type": "task", "title": "A"},
                    }
                ],
            },
            "valid-task": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "create_work_item",
                        "args": {"work_type": "task", "title": "B"},
                    }
                ],
            },
        }
    )
    arbiter = _mk_arbiter(tmp_path, mock=mock)

    blocked = PerformAction(type=ActionType.PERFORM, description="blocked-task", target_id="", justification="")
    rejected = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[blocked]))
    assert rejected[0].approved is False
    assert rejected[0].reason == "missing_capability:work"

    valid = PerformAction(type=ActionType.PERFORM, description="valid-task", target_id="", justification="")
    approved = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_2", actions=[valid]))
    assert approved[0].approved is True
    assert approved[0].ops
    assert approved[0].ops[0].work_id == "work:0_1"


def test_perform_vote_alias_maps_to_cast_vote(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_2",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
    )
    mock = MockLLMProvider(
        structured_responses={
            "vote-alias": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "vote",
                        "args": {"vote_id": "vote:1", "choice": "yes"},
                    }
                ],
            }
        }
    )
    arbiter = _mk_arbiter(tmp_path, mock=mock)

    act = PerformAction(type=ActionType.PERFORM, description="vote-alias", target_id="", justification="")
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert res[0].ops
    assert res[0].ops[0].__class__.__name__ == "CastVoteOp"


def test_perform_message_alias_maps_to_send_message(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    mock = MockLLMProvider(
        structured_responses={
            "message-alias": {
                "approved": True,
                "reason": "ok",
                "ops": [
                    {
                        "op_type": "message",
                        "args": {"to_id": "agent:off_2", "text": "Нужно обсудить детали.", "private": True},
                    }
                ],
            }
        }
    )
    arbiter = _mk_arbiter(tmp_path, mock=mock)

    act = PerformAction(type=ActionType.PERFORM, description="message-alias", target_id="", justification="")
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert res[0].ops
    assert res[0].ops[0].__class__.__name__ == "SendMessageOp"


def test_arbiter_retries_substantive_proposal_after_empty_materialization(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    provider = _RetryingMaterializationProvider()
    arbiter = _mk_arbiter(tmp_path, mock=provider)

    act = PerformAction(
        type=ActionType.PERFORM,
        description="Сначала коротко обсужу вопрос с коллегой и вынесу его на рабочее обсуждение.",
        target_id="",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert provider.perform_calls == 2
    assert res[0].approved is True
    assert res[0].ops
    assert res[0].ops[0].__class__.__name__ == "SendMessageOp"


def test_arbiter_does_not_retry_idle_human_noop_proposal(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    provider = _IdleProposalProvider()
    arbiter = _mk_arbiter(tmp_path, mock=provider)

    act = PerformAction(
        type=ActionType.PERFORM,
        description="Пока не предпринимаю новых шагов и просто наблюдаю за развитием ситуации.",
        target_id="",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert provider.perform_calls == 1
    assert res[0].approved is True
    assert res[0].ops == []


def test_arbiter_normalizes_legacy_perform_payload_shape(tmp_path: Path) -> None:
    class _LegacyPerformShapeProvider(MockLLMProvider):
        def generate_structured(
            self,
            system: str,
            user: str,
            schema: dict,
            temperature: float = 0.0,
        ):
            if "- actor_id: agent:off_1" in user:
                return StructuredLLMResponse(
                    data={
                        "approved": True,
                        "reason": None,
                        "ops": [
                            {
                                "op_type": "add_work_note",
                                "target_id": "work:case",
                                "params": {
                                    "note": "Краткая фиксация по делу.",
                                    "author_id": "",
                                },
                            },
                            {
                                "op_type": "submit_work_proposal",
                                "target_id": "work:case",
                                "args": {
                                    "content": "Предлагаю учесть дефекты в графике.",
                                    "author_id": "",
                                },
                            },
                            {
                                "op_type": "send_message",
                                "target_id": "agent:off_2",
                                "args": {
                                    "message": "Нужно коротко сверить позицию по делу.",
                                    "is_private": True,
                                },
                            },
                        ],
                        "side_effects": [
                            {
                                "op_type": "information_signal",
                                "params": {
                                    "description": "В отделе назревает внутреннее обсуждение спорного вопроса."
                                },
                            },
                            {
                                "op_type": "upsert_informal_link",
                                "args": {
                                    "agent_a_id": "agent:off_1",
                                    "agent_b_id": "agent:off_2",
                                    "link_type": "coordination",
                                    "strength_delta": 0.1,
                                    "source": "",
                                },
                            }
                        ],
                    },
                    model="mock",
                )
            return super().generate_structured(system, user, schema, temperature)

    state = _mk_state(off_1_caps=["message", "work"], off_2_caps=["message"])
    state.work_items["work:case"] = WorkItem(
        work_id="work:case",
        work_type="case",
        title="Case",
    )
    state.registry.register(
        EntityRecord(
            entity_id="work:case",
            kind=EntityKind.WORK_ITEM,
            created_by=None,
            created_tick=0,
            meta={"work_type": "case", "title": "Case"},
        )
    )
    arbiter = _mk_arbiter(tmp_path, mock=_LegacyPerformShapeProvider())
    act = PerformAction(
        type=ActionType.PERFORM,
        description="Сначала лично напишу коллеге, а затем зафиксирую общий сигнал для среды.",
        target_id="",
        justification="",
    )

    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    op_names = [op.__class__.__name__ for op in res[0].ops]
    assert "AddWorkNoteOp" in op_names
    assert "SubmitWorkProposalOp" in op_names
    assert "SendMessageOp" in op_names
    assert "AddInformationSignalOp" in op_names
    assert "UpsertInformalLinkOp" in op_names

    note_op = next(op for op in res[0].ops if op.__class__.__name__ == "AddWorkNoteOp")
    proposal_op = next(op for op in res[0].ops if op.__class__.__name__ == "SubmitWorkProposalOp")
    link_op = next(op for op in res[0].ops if op.__class__.__name__ == "UpsertInformalLinkOp")

    assert note_op.text == "Краткая фиксация по делу."
    assert proposal_op.text == "Предлагаю учесть дефекты в графике."
    assert link_op.source == "agent:off_1"


def test_arbiter_keeps_main_step_when_side_effect_is_invalid(tmp_path: Path) -> None:
    class _SideEffectFailureProvider(MockLLMProvider):
        def generate_structured(
            self,
            system: str,
            user: str,
            schema: dict,
            temperature: float = 0.0,
        ):
            if "- actor_id: agent:off_1" in user:
                return StructuredLLMResponse(
                    data={
                        "approved": True,
                        "reason": "ok",
                        "ops": [
                            {
                                "op_type": "add_work_note",
                                "target_id": "work:case",
                                "args": {"note_text": "Главная заметка остаётся валидной."},
                            }
                        ],
                        "side_effects": [
                            {
                                "op_type": "send_message",
                                "args": {
                                    "text": "Это побочный эффект без явного адресата.",
                                    "private": True,
                                },
                            }
                        ],
                    },
                    model="mock",
                )
            return super().generate_structured(system, user, schema, temperature)

    state = _mk_state(off_1_caps=["message", "work"], off_2_caps=["message"])
    state.work_items["work:case"] = WorkItem(
        work_id="work:case",
        work_type="case",
        title="Case",
    )
    state.registry.register(
        EntityRecord(
            entity_id="work:case",
            kind=EntityKind.WORK_ITEM,
            created_by=None,
            created_tick=0,
            meta={"work_type": "case", "title": "Case"},
        )
    )
    arbiter = _mk_arbiter(tmp_path, mock=_SideEffectFailureProvider())
    act = PerformAction(
        type=ActionType.PERFORM,
        description="Добавлю в дело короткую заметку и отдельно отмечу общий сигнал.",
        target_id="",
        justification="",
    )

    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert [op.__class__.__name__ for op in res[0].ops] == ["AddWorkNoteOp"]
    assert res[0].ops[0].text == "Главная заметка остаётся валидной."


def test_arbiter_normalizes_real_model_alias_op_types() -> None:
    assert _normalize_perform_op_type("record_narrative_action") == "narrative_action"
    assert _normalize_perform_op_type("add_narrative_action") == "narrative_action"
    assert _normalize_perform_op_type("create_pending_interaction") == "upsert_pending_interaction"
    assert _normalize_perform_op_type("record_narrative_action_op") == "narrative_action"
    assert _normalize_perform_op_type("information_signal") == "add_information_signal"
    assert _normalize_perform_op_type("private_contact") == "in_person_contact"


def test_arbiter_rejects_substantive_proposal_when_retry_still_empty(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    provider = _StillEmptyMaterializationProvider()
    arbiter = _mk_arbiter(tmp_path, mock=provider)

    act = PerformAction(
        type=ActionType.PERFORM,
        description="Обсужу вопрос с коллегой и отправлю ему конкретное сообщение по процессу.",
        target_id="",
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert provider.perform_calls >= 2
    assert res[0].approved is False
    assert res[0].reason == "proposal_not_materialized_after_retry"


def test_arbiter_decomposes_multi_step_proposal_into_ordered_ops(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.registry.register(EntityRecord(entity_id="zone:room_a", kind=EntityKind.ZONE, created_by=None, created_tick=0, meta={"title": "Room A"}))
    state.registry.register(EntityRecord(entity_id="zone:room_b", kind=EntityKind.ZONE, created_by=None, created_tick=0, meta={"title": "Room B"}))
    state.agents["agent:off_1"].zone_id = "zone:room_a"
    state.agents["agent:off_2"].zone_id = "zone:room_b"

    arbiter = _mk_arbiter(tmp_path, mock=_StepwisePerformProvider())
    act = PerformAction(
        type=ActionType.PERFORM,
        description="Сначала приду к коллеге, а потом лично сообщу ему итог.",
        target_id="agent:off_2",
        justification="",
    )

    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert [op.__class__.__name__ for op in res[0].ops] == ["RecordNarrativeActionOp", "SendMessageOp"]


def test_arbiter_rewrites_unsupported_document_note_via_grounding_verifier(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["work"], off_2_caps=["work"])
    state.registry.register(
        EntityRecord(
            entity_id="work:case",
            kind=EntityKind.WORK_ITEM,
            created_by=None,
            created_tick=0,
            meta={"title": "Case"},
        )
    )
    state.work_items["work:case"] = WorkItem(work_id="work:case", work_type="case", title="Case")

    arbiter = _mk_arbiter(tmp_path, mock=_DocumentGroundingProvider())
    act = PerformAction(
        type=ActionType.PERFORM,
        description="Сначала попрошу коллег подтвердить расхождения, затем зафиксирую это в деле.",
        target_id="",
        justification="",
    )

    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))

    assert res[0].approved is True
    assert res[0].ops
    assert res[0].ops[0].__class__.__name__ == "AddWorkNoteOp"
    assert res[0].ops[0].text == "Запросил у коллег подтверждение и ожидаю письменные пояснения по расхождениям."


def test_arbiter_rejects_temporally_backdated_message(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.tick = 5
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    arbiter.runtime.start_date = date(2026, 3, 9)
    arbiter.runtime.tick_duration_days = 1

    act = SendMessageAction(
        type=ActionType.SEND_MESSAGE,
        to_id="agent:off_2",
        text="Встреча подтверждена на 2026-03-09.",
        private=True,
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is False
    assert res[0].reason == "temporal_date_before_current_tick:2026-03-09"


def test_arbiter_allows_human_readable_runtime_spawn_name_without_role_gate(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["spawn"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=MockLLMProvider())
    arbiter.runtime.allow_runtime_spawn = True
    arbiter.runtime.max_agents = 5

    act = SpawnAgentAction(
        type=ActionType.SPAWN_AGENT,
        slug="witness",
        name="Свидетель А",
        internal=False,
        persona_hint="Знает детали сделки.",
        capabilities=["message"],
        justification="",
    )
    res = asyncio.run(arbiter.arbitrate_actions(state=state, agent_id="agent:off_1", actions=[act]))
    assert res[0].approved is True
    assert res[0].ops


def test_dao_eligible_voters_filters_by_dao_capability() -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["message"])
    dao = DaoEngine(cfg=GovernanceConfig())
    voters = dao.eligible_voters(state)
    assert voters == ["agent:off_1"]


def test_dao_passes_vote_with_consent_and_non_self_nomination() -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=1,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
        votes={"agent:off_1": "yes"},
        target_consented=True,
    )
    state.tick = 1
    dao = DaoEngine(cfg=GovernanceConfig())

    ops = dao.close_votes(state)
    close_ops = [op for op in ops if op.__class__.__name__ == "CloseVoteOp"]
    position_ops = [op for op in ops if op.__class__.__name__ == "ChangePositionOp"]

    assert close_ops
    assert close_ops[0].result == "passed"
    assert close_ops[0].reason == "threshold_passed"
    assert position_ops


def test_dao_cancels_vote_for_frozen_target_with_reason() -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.agents["agent:off_2"].reputation_frozen = True
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=1,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
        votes={"agent:off_1": "yes"},
        target_consented=True,
    )
    state.tick = 1
    dao = DaoEngine(cfg=GovernanceConfig())

    ops = dao.close_votes(state)
    close_ops = [op for op in ops if op.__class__.__name__ == "CloseVoteOp"]

    assert close_ops
    assert close_ops[0].result == "canceled"
    assert close_ops[0].reason == "reputation_frozen"


def test_arbiter_continues_when_perform_llm_fails(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    arbiter = _mk_arbiter(tmp_path, mock=_FailingPerformProvider())

    proposed = {
        "agent:off_1": [PerformAction(type=ActionType.PERFORM, description="perform", target_id="", justification="")],
        "agent:off_2": [
            SendMessageAction(
                type=ActionType.SEND_MESSAGE,
                to_id="agent:off_1",
                text="ok",
                private=True,
                justification="",
            )
        ],
    }
    out = asyncio.run(arbiter.arbitrate_tick(state=state, proposed=proposed, journal_yaml=state.journal_yaml()))
    assert out["agent:off_1"][0].approved is False
    assert "arbiter_llm_error" in out["agent:off_1"][0].reason
    assert out["agent:off_2"][0].approved is True


def test_worldgen_does_not_receive_private_message_text(tmp_path: Path) -> None:
    mock = MockLLMProvider()
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)
    wg = WorldGenerator(llm=llm, temperature=0.0)

    private_msg = Event(
        tick=0,
        event_type="message_sent",
        actor_id="agent:off_1",
        payload={"to_id": "agent:off_2", "private": True, "text": "SECRET"},
        audience=["agent:off_1", "agent:off_2"],
    )
    asyncio.run(wg.generate(tick=0, recent_events=[private_msg], language="ru"))

    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in trace_text

    # И дополнительно: worldgen получает только internal/public события.
    spans = [json.loads(line) for line in trace_text.splitlines() if line.strip()]
    assert spans
    user_payload = spans[-1]["user"]
    assert INTERNAL_AUDIENCE not in user_payload  # worldgen input uses normalized json, not audience tokens
    assert PUBLIC_AUDIENCE not in user_payload


def test_create_agent_op_emits_initial_reputation_snapshot() -> None:
    state = WorldState(tick=3, registry=EntityRegistry())

    events = CreateAgentOp(
        entity_id="agent:spawned",
        name="Spawned",
        internal=True,
        persona_hint="helper",
        capabilities=["message"],
        created_by="agent:spawner",
        created_tick=3,
    ).apply(state)

    assert [event.event_type for event in events] == ["entity_created", "reputation_snapshot"]
    assert events[1].payload["target_agent_id"] == "agent:spawned"
    assert events[1].payload["score"] == 0.0
    assert events[1].payload["internal"] is True
    assert events[1].payload["title"] == "специалист"


def test_engine_init_uses_agent_initial_reputation(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "init-reputation",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "capabilities": ["message"],
                    "initial_reputation": 7.0,
                }
            ],
            "world": {},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())

    state = engine._init_state(event_log=EventLog(artifacts.events_path))

    assert state.agents["agent:off_1"].reputation == 7.0
    snapshots = [
        event
        for event in EventLog(artifacts.events_path).iter_events()
        if event.event_type == "reputation_snapshot"
    ]
    assert snapshots[0].payload["score"] == 7.0


def test_engine_governance_rewards_use_system_actor(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "reward-cycle-system-actor",
            "ticks": 1,
            "governance": {
                "audit": {
                    "enabled": True,
                    "actor_id": "agent:auditor",
                }
            },
            "agents": [
                {
                    "agent_id": "agent:auditor",
                    "name": "Auditor",
                    "internal": True,
                    "capabilities": ["work"],
                }
            ],
            "world": {},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)

    reward_events = engine._apply_reputation_consequences(
        state=state,
        tick_events=[
            Event(
                tick=0,
                event_type="vote_target_consented",
                actor_id="agent:auditor",
                payload={"vote_id": "vote:test", "accept": True},
            )
        ],
        event_log=event_log,
    )

    assert len(reward_events) == 1
    assert reward_events[0].event_type == "reputation_modified"
    assert reward_events[0].actor_id is None

    auditor = RuntimeAuditor(cfg=AuditRuntimeConfig(enabled=True, actor_id="agent:auditor"))
    outcome = asyncio.run(
        auditor.inspect_tick(
            state=state,
            tick_events=reward_events,
            recent_events=[],
        )
    )
    assert not outcome.findings


def test_engine_work_proposals_do_not_grant_reputation(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "no-reward-for-work-proposal",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "capabilities": ["work"],
                }
            ],
            "world": {},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)

    reward_events = engine._apply_reputation_consequences(
        state=state,
        tick_events=[
            Event(
                tick=0,
                event_type="work_proposal_submitted",
                actor_id="agent:off_1",
                payload={"work_id": "work:test"},
            )
        ],
        event_log=event_log,
    )

    assert reward_events == []


def test_engine_init_rejects_initial_work_item_with_unknown_participant(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "init-phantom-work-item",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "capabilities": ["work"],
                }
            ],
            "world": {
                "work_items": [
                    {
                        "work_id": "work:init",
                        "work_type": "task",
                        "title": "Task",
                        "participants": ["agent:ghost"],
                    }
                ]
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())

    with pytest.raises(ValueError, match="Participant agent not found"):
        engine._init_state(event_log=EventLog(artifacts.events_path))


def test_engine_memory_summarization_runs_in_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "parallel-memory-summarization",
            "ticks": 1,
            "runtime": {
                "parallel_workers": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:off_2",
                    "name": "Off 2",
                    "internal": True,
                    "capabilities": ["message"],
                },
            ],
            "world": {},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)
    llm = LLMCaller(provider=MockLLMProvider(), trace=TraceLog(artifacts.trace_path))

    active = 0
    max_active = 0

    async def _fake_summarize(self, *, llm, language, cfg, tick, temperature) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(AgentMemory, "maybe_summarize_working", _fake_summarize)

    asyncio.run(
        engine._update_agent_memory(
            state=state,
            tick_events=[],
            llm=llm,
            embedder=None,
            embed_cache={},
            event_log=event_log,
        )
    )

    assert max_active >= 2


def test_engine_ignores_idempotent_runtime_audit_op_failures(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "idempotent-runtime-audit-ops",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "capabilities": ["message", "dao"],
                },
                {
                    "agent_id": "agent:off_2",
                    "name": "Off 2",
                    "internal": True,
                    "capabilities": ["message", "dao"],
                },
            ],
            "world": {},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)

    ops = [
        OpenAuditCaseOp(
            actor_id=None,
            case_id="audit_case:test",
            finding_id="finding:test",
            subject_agent_id="agent:off_1",
            risk_family="conflict_of_interest",
            violation_type="preferential_treatment_for_connected_actor",
            summary="first",
            recommended_action="review",
            confidence=0.8,
        ),
        OpenAuditCaseOp(
            actor_id=None,
            case_id="audit_case:test",
            finding_id="finding:test_2",
            subject_agent_id="agent:off_1",
            risk_family="conflict_of_interest",
            violation_type="preferential_treatment_for_connected_actor",
            summary="duplicate",
            recommended_action="review",
            confidence=0.8,
        ),
        UpdateAuditCaseOp(
            actor_id=None,
            case_id="audit_case:test",
            summary="updated",
        ),
        CloseAuditCaseOp(
            actor_id=None,
            case_id="audit_case:test",
            result="confirmed",
            reason="done",
        ),
        UpdateAuditCaseOp(
            actor_id=None,
            case_id="audit_case:test",
            summary="late-update",
        ),
        OpenVoteOp(
            vote_id="vote:test",
            created_by="agent:off_1",
            created_tick=0,
            closes_tick=1,
            target_agent_id="agent:off_2",
            new_title="",
            reason="review",
            voters=["agent:off_1"],
            vote_type="audit_review",
        ),
        OpenVoteOp(
            vote_id="vote:test",
            created_by="agent:off_1",
            created_tick=0,
            closes_tick=1,
            target_agent_id="agent:off_2",
            new_title="",
            reason="duplicate-review",
            voters=["agent:off_1"],
            vote_type="audit_review",
        ),
    ]

    events = engine._apply_ops(state=state, ops=ops, event_log=event_log, origin="runtime_audit")

    assert not any(event.event_type == "arbiter_op_failed" for event in events)
    assert any(event.event_type == "audit_case_opened" for event in events)
    assert any(event.event_type == "audit_case_updated" for event in events)
    assert any(event.event_type == "audit_case_closed" for event in events)
    assert sum(1 for event in events if event.event_type == "vote_opened") == 1


def test_cast_vote_anonymizes_audit_review_choice_and_actor() -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:review_1"] = Vote(
        vote_id="vote:review_1",
        vote_type="audit_review",
        created_by="agent:off_2",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="",
        reason="review",
        voters=["agent:off_1"],
    )

    events = CastVoteOp(actor_id="agent:off_1", vote_id="vote:review_1", choice="yes").apply(state)

    vote = state.votes["vote:review_1"]
    assert vote.votes == {}
    assert vote.anon_vote_counts == {"yes": 1}
    assert vote.anon_voters_cast == {"agent:off_1"}
    assert events[0].actor_id is None
    assert events[0].payload == {"vote_id": "vote:review_1"}
    assert events[0].audience == ["aud:internal"]

    with pytest.raises(ValueError, match="already voted"):
        CastVoteOp(actor_id="agent:off_1", vote_id="vote:review_1", choice="yes").apply(state)


def test_journal_and_state_hide_audit_review_ballots() -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])
    state.votes["vote:review_1"] = Vote(
        vote_id="vote:review_1",
        vote_type="audit_review",
        created_by="agent:off_2",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="",
        reason="review",
        voters=["agent:off_1"],
        anon_vote_counts={"yes": 1},
        anon_voters_cast={"agent:off_1"},
    )
    state.votes["vote:position_1"] = Vote(
        vote_id="vote:position_1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
        votes={"agent:off_1": "yes"},
    )

    journal_votes = {item["id"]: item["votes"] for item in state.journal_dict()["votes"]}

    assert journal_votes["vote:review_1"] == {}
    assert journal_votes["vote:position_1"] == {"agent:off_1": "yes"}
    assert WorldJournal._vote_entry(state, "vote:review_1")["votes"] == {}
    assert WorldJournal._vote_entry(state, "vote:position_1")["votes"] == {"agent:off_1": "yes"}


def test_audit_review_vote_opening_is_visible_only_to_reviewers() -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["dao"])

    events = OpenVoteOp(
        vote_id="vote:review_1",
        created_by="agent:off_2",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="",
        reason="review",
        voters=["agent:off_1"],
        vote_type="audit_review",
        metadata={"case_id": "audit_case:review_1"},
    ).apply(state)

    vote_opened = next(event for event in events if event.event_type == "vote_opened")
    review_opened = next(event for event in events if event.event_type == "review_case_opened")

    assert vote_opened.audience == ["agent:off_1"]
    assert event_visible_to_agent(vote_opened, "agent:off_1", internal=True)
    assert not event_visible_to_agent(vote_opened, "agent:off_2", internal=True)
    assert review_opened.audience == ["aud:internal"]
    assert event_visible_to_agent(review_opened, "agent:off_2", internal=True)
    assert "voters" not in review_opened.payload


def test_create_llm_provider_uses_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("sphere_lc.llm.caller._load_dotenv_if_available", lambda: None)
    monkeypatch.setattr("sphere_lc.llm.caller.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENAI_PROVIDER_ORDER", raising=False)
    _ = create_llm_provider(LLMConfig(model=DEFAULT_LLM_MODEL, base_url=None))
    assert captured["base_url"] == "https://example.test/v1"
    assert captured["provider_order"] is None


def test_create_llm_provider_uses_env_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("sphere_lc.llm.caller.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "Groq, OpenAI,Groq")

    _ = create_llm_provider(LLMConfig(model=DEFAULT_LLM_MODEL, base_url=None))

    assert captured["provider_order"] == ["Groq", "OpenAI"]


def test_create_provider_uses_env_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("sphere_lc.llm.providers.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "Groq, OpenAI,Groq")

    _ = create_provider(mock=False, model=DEFAULT_LLM_MODEL)

    assert captured["provider_order"] == ["Groq", "OpenAI"]


def test_create_llm_provider_loads_dotenv_from_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("sphere_lc.llm.caller.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENAI_PROVIDER_ORDER", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=dotenv-test-key\nOPENAI_BASE_URL=https://dotenv.example/v1\n",
        encoding="utf-8",
    )

    _ = create_llm_provider(LLMConfig(model=DEFAULT_LLM_MODEL, base_url=None))

    assert captured["api_key"] == "dotenv-test-key"
    assert captured["base_url"] == "https://dotenv.example/v1"


def test_openai_provider_keeps_zero_temperature_for_standard_backends() -> None:
    provider = SimpleNamespace(_base_url="https://api.openai.com/v1", _model=DEFAULT_LLM_MODEL)

    assert OpenAICompatibleProvider._effective_temperature(provider, 0.0) == 0.0
    assert OpenAICompatibleProvider._effective_temperature(provider, 0.35) == 0.35


def test_openai_provider_clamps_zero_temperature_only_for_minimax() -> None:
    provider = SimpleNamespace(_base_url="https://api.minimax.chat/v1", _model="MiniMax-Text-01")

    assert OpenAICompatibleProvider._effective_temperature(provider, 0.0) == 0.01


def test_openai_provider_only_sets_provider_order_for_openrouter() -> None:
    standard = OpenAICompatibleProvider(
        model=DEFAULT_LLM_MODEL,
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        provider_order=["Groq"],
    )
    routed = OpenAICompatibleProvider(
        model=DEFAULT_LLM_MODEL,
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        provider_order=["Groq"],
    )

    assert standard._extra_body is None
    assert routed._extra_body == {
        "provider": {"order": ["Groq"], "allow_fallbacks": True}
    }


def test_openai_provider_uses_30s_timeout_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPHERE_LLM_REQUEST_TIMEOUT_S", raising=False)
    monkeypatch.delenv("SPHERE_LLM_CALL_DEADLINE_S", raising=False)
    provider = OpenAICompatibleProvider(
        model=DEFAULT_LLM_MODEL,
        api_key="test-key",
        base_url="https://api.openai.com/v1",
    )

    assert provider._client_kwargs["timeout"] == 30.0


def test_openai_provider_caps_attempt_timeout_by_remaining_budget() -> None:
    provider = OpenAICompatibleProvider(
        model=DEFAULT_LLM_MODEL,
        api_key="test-key",
        base_url="https://api.openai.com/v1",
    )
    provider._request_timeout_s = 30.0
    provider._call_deadline_s = 1.0

    captured: dict[str, object] = {}

    class _FakeResponse:
        choices = [SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)

    class _FakeChat:
        class _Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _FakeResponse()

        completions = _Completions()

    provider._get_client = lambda: SimpleNamespace(chat=_FakeChat())  # type: ignore[method-assign]

    result = provider.generate("sys", "usr", 0.0)

    assert result.text == "ok"
    assert captured["timeout"] == pytest.approx(1.0, rel=0.01)


def test_openai_provider_stops_retrying_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatibleProvider(
        model=DEFAULT_LLM_MODEL,
        api_key="test-key",
        base_url="https://api.openai.com/v1",
    )
    provider._request_timeout_s = 30.0
    provider._call_deadline_s = 1.0
    provider._max_retries = 2
    provider._retry_base_delay_s = 0.1
    provider._retry_max_delay_s = 0.1

    calls = {"count": 0}

    class APITimeoutError(Exception):
        pass

    class _FakeChat:
        class _Completions:
            def create(self, **kwargs):
                calls["count"] += 1
                raise APITimeoutError("timeout")

        completions = _Completions()

    provider._get_client = lambda: SimpleNamespace(chat=_FakeChat())  # type: ignore[method-assign]

    monkeypatch.setattr("time.sleep", lambda _: None)

    with pytest.raises(TimeoutError, match="timed out"):
        provider.generate("sys", "usr", 0.0)

    assert calls["count"] == 1


def test_governance_config_rejects_auto_position_policy() -> None:
    with pytest.raises(ValidationError, match="position_policy='auto'"):
        GovernanceConfig(position_policy="auto")


def test_audit_runtime_config_accepts_llm_and_hybrid_modes() -> None:
    assert AuditRuntimeConfig(mode="hybrid").mode == "hybrid"
    assert AuditRuntimeConfig(mode="llm").mode == "llm"


def test_runtime_config_rejects_zero_worldgen_interval() -> None:
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(
            {
                "version": 1,
                "title": "bad-worldgen-interval",
                "ticks": 1,
                "runtime": {"enable_worldgen": True, "worldgen_every_ticks": 0},
                "agents": [],
                "world": {},
            }
        )


def test_memory_summarizes_working_buffer(tmp_path: Path) -> None:
    mem = AgentMemory(agent_id="agent:off_1")
    cfg = MemoryConfig(working_max_entries=2, working_summarize_batch=2, working_summary_min_overflow=1)

    for t in range(4):
        mem.add_working(tick=t, text=f"e{t}")

    mock = MockLLMProvider(responses={"Обнови сводку рабочей памяти агента.": "- one\n- two\n"})
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)

    asyncio.run(
        mem.maybe_summarize_working(
            llm=llm,
            language="ru",
            cfg=cfg,
            tick=10,
            temperature=0.0,
        )
    )

    assert mem.summary == "- one - two"
    assert len(mem.working) == 2


def test_memory_summarization_failure_preserves_working_buffer(tmp_path: Path) -> None:
    class _FailingSummaryProvider(MockLLMProvider):
        def generate(self, system: str, user: str, temperature: float = 0.0):
            raise RuntimeError("boom")

    mem = AgentMemory(agent_id="agent:off_1")
    cfg = MemoryConfig(working_max_entries=2, working_summarize_batch=2, working_summary_min_overflow=1)

    for t in range(4):
        mem.add_working(tick=t, text=f"e{t}")

    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=_FailingSummaryProvider(), trace=trace)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            mem.maybe_summarize_working(
                llm=llm,
                language="ru",
                cfg=cfg,
                tick=10,
                temperature=0.0,
            )
        )

    assert [entry.text for entry in mem.working] == ["e0", "e1", "e2", "e3"]


def test_memory_skips_summary_when_overflow_below_threshold(tmp_path: Path) -> None:
    mem = AgentMemory(agent_id="agent:off_1")
    cfg = MemoryConfig(working_max_entries=4, working_summarize_batch=2, working_summary_min_overflow=3)

    for t in range(6):
        mem.add_working(tick=t, text=f"e{t}")

    mock = MockLLMProvider(responses={"Обнови сводку рабочей памяти агента.": "- should not happen\n"})
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)

    asyncio.run(
        mem.maybe_summarize_working(
            llm=llm,
            language="ru",
            cfg=cfg,
            tick=10,
            temperature=0.0,
        )
    )

    assert mem.summary == ""
    assert len(mem.working) == 6


def test_memory_compacts_repeated_working_entries() -> None:
    mem = AgentMemory(agent_id="agent:off_1")
    batch = [
        WorkingEntry(tick=1, text="Неформальная связь обновлена: agent:off_1 — agent:off_2 [private_contact] (сила 0.20)"),
        WorkingEntry(tick=1, text="Неформальная связь обновлена: agent:off_1 — agent:off_2 [private_contact] (сила 0.45)"),
        WorkingEntry(tick=2, text="Публичное сообщение agent:off_1 -> chan:public: тест"),
    ]

    lines = mem._compact_working_batch(batch)

    assert lines[0].startswith("- (t1, x2) Неформальная связь обновлена:")
    assert lines[1] == "- (t2) Публичное сообщение agent:off_1 -> chan:public: тест"


def test_memory_config_uses_real_embeddings_by_default() -> None:
    cfg = MemoryConfig()

    assert cfg.embeddings_mock is False


def test_memory_config_validates_new_render_limits() -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(working_render_max_chars=0)

    with pytest.raises(ValidationError):
        MemoryConfig(summary_context_fraction=1.5)


def test_render_memory_query_keeps_informal_link_strength(tmp_path: Path) -> None:
    class _CaptureEmbedder:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            return [[0.1, 0.2, 0.3] for _ in texts]

    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
        embedder=_CaptureEmbedder(),
    )
    event = Event(
        tick=1,
        event_type="environment_informal_link_updated",
        actor_id="agent:off_1",
        payload={
            "agent_a_id": "agent:off_1",
            "agent_b_id": "agent:off_2",
            "link_type": "private_contact",
            "strength": 0.42,
        },
        audience=[INTERNAL_AUDIENCE],
    )

    asyncio.run(
        runner._render_memory(
            agent=state.agents["agent:off_1"],
            state=state,
            visible_events=[event],
        )
    )

    query_text = runner.embedder.calls[0][0]
    assert "0.42" in query_text
    assert "<num>" not in query_text


def test_update_agent_memory_persists_pending_and_informal_link_events(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "title": "memory-event-thresholds",
            "ticks": 1,
            "agents": [
                {"agent_id": "agent:off_1", "name": "Off 1", "internal": True, "capabilities": ["message"]},
                {"agent_id": "agent:off_2", "name": "Off 2", "internal": True, "capabilities": ["message"]},
            ],
            "world": {},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)
    llm = LLMCaller(provider=MockLLMProvider(), trace=TraceLog(artifacts.trace_path))
    tick_events = [
        Event(
            tick=1,
            event_type="pending_interaction_completed",
            actor_id="agent:off_1",
            payload={
                "interaction_id": "pending:1",
                "target_agent_id": "agent:off_1",
                "source_agent_id": "agent:off_2",
                "category": "reply",
                "summary": "Подготовить ответ по закупке",
            },
            audience=[INTERNAL_AUDIENCE],
        ),
        Event(
            tick=1,
            event_type="environment_informal_link_updated",
            actor_id="agent:off_1",
            payload={
                "agent_a_id": "agent:off_1",
                "agent_b_id": "agent:off_2",
                "link_type": "private_contact",
                "strength": 0.55,
            },
            audience=[INTERNAL_AUDIENCE],
        ),
    ]

    asyncio.run(
        engine._update_agent_memory(
            state=state,
            tick_events=tick_events,
            llm=llm,
            embedder=None,
            embed_cache={},
            event_log=event_log,
        )
    )

    off_1_docs = state.agents["agent:off_1"].memory.docs
    off_2_docs = state.agents["agent:off_2"].memory.docs

    assert any(doc.kind == "result" and "Обязательство выполнено" in doc.text for doc in off_1_docs)
    assert any(doc.kind == "observation" and "Обязательство выполнено" in doc.text for doc in off_2_docs)
    assert any("Неформальная связь обновлена" in doc.text for doc in off_1_docs)
    assert any("Неформальная связь обновлена" in doc.text for doc in off_2_docs)


def test_agent_prompt_exposes_respond_nomination_without_dao_capability(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["message", "work"])
    state.votes["vote:1"] = Vote(
        vote_id="vote:1",
        vote_type="position_change",
        created_by="agent:off_1",
        created_tick=0,
        closes_tick=2,
        target_agent_id="agent:off_2",
        new_title="lead",
        voters=["agent:off_1"],
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=state.agents["agent:off_2"],
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Голосования в ходу: vote:1" in prompt
    assert "Верни только JSON с одним полем `reply`." in prompt
    assert "если тебя выдвинули, ты можешь прямо согласиться или отказаться" in prompt
    assert "respond_nomination (vote_id, accept: true/false)" not in prompt


def test_agent_prompt_policy_injects_extra_rules_and_examples(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["dao"], off_2_caps=["message", "work"])
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(
            agent_prompt={
                "extra_rules": [
                    "Если пишешь конкретному участнику, не оформляй этот шаг как публикацию в канале.",
                ],
                "extra_good_examples": [
                    "Сначала напишу agent:off_1 лично, а отдельным шагом опубликую позицию в chan:public.",
                ],
            }
        ),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=state.agents["agent:off_2"],
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Практические ориентиры:" in prompt
    assert "не оформляй этот шаг как публикацию в канале" in prompt
    assert "Сначала напишу agent:off_1 лично, а отдельным шагом опубликую позицию в chan:public." in prompt


def test_scenario_config_accepts_declarative_policy_blocks() -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "policy-config",
            "ticks": 1,
            "runtime": {
                "agent_prompt": {
                    "extra_rules": ["Не смешивай `agent:*` и `chan:*` в одной цели."],
                }
            },
            "agents": [],
            "world": {},
        }
    )

    assert cfg.runtime.agent_prompt.extra_rules == ["Не смешивай `agent:*` и `chan:*` в одной цели."]


def test_langgraph_world_graph_supports_checkpoint_path(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")

    from sphere_lc.entities import EntityRegistry
    from sphere_lc.graphs import build_world_graph

    async def _gather(gs: dict) -> dict:
        return {"proposed": {}}

    async def _apply(gs: dict) -> dict:
        return {"tick_events": []}

    app = build_world_graph(
        gather_actions_node=_gather,
        apply_actions_node=_apply,
        debug=False,
        checkpoint_path=tmp_path / "langgraph.sqlite",
    )

    state = WorldState(tick=0, registry=EntityRegistry())
    out = asyncio.run(app.ainvoke({"world": state, "events_history": []}))
    assert isinstance(out, dict)


def test_world_engine_runs_with_langgraph_enabled(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")

    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "langgraph-run",
            "ticks": 1,
            "runtime": {"use_langgraph": True},
            "agents": [
                {"agent_id": "agent:off_1", "name": "Off 1", "internal": True, "persona": "test"},
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    state = asyncio.run(engine.run())

    assert state.tick == 0
    assert artifacts.events_path.exists()
    assert "arbiter_llm_error" not in artifacts.events_path.read_text(encoding="utf-8")


def test_cli_run_defaults_to_results_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        (
            "version: 1\n"
            "title: cli-run\n"
            "ticks: 1\n"
            "agents:\n"
            "  - agent_id: agent:off_1\n"
            "    name: Off 1\n"
            "    internal: true\n"
            "world: {}\n"
        ),
        encoding="utf-8",
    )

    captured: dict[str, Path] = {}

    class _FakeEngine:
        def __init__(self, *, cfg, artifacts, provider_override=None) -> None:
            captured["out_dir"] = artifacts.out_dir

        async def run(self) -> None:
            return None

    class _FakeDatetime:
        @classmethod
        def now(cls):  # noqa: D401
            class _Now:
                @staticmethod
                def strftime(fmt: str) -> str:
                    return "20260310_120000"

            return _Now()

    monkeypatch.setattr("sphere_lc.cli.WorldEngine", _FakeEngine)
    monkeypatch.setattr("sphere_lc.cli.datetime", _FakeDatetime)

    args = argparse.Namespace(
        scenario=str(scenario_path),
        out=None,
        ticks=None,
        enrich_personas=False,
        persona_enrich_mode=None,
    )

    _cmd_run(args)

    assert captured["out_dir"] == Path("results") / "20260310_120000"


def test_world_journal_tracks_history_and_caps() -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    state.agents["agent:off_1"].org_id = "org:city"
    state.agents["agent:off_1"].zone_id = "zone:left"
    state.registry.register(EntityRecord(entity_id="org:city", kind=EntityKind.ORG, created_by=None, created_tick=0))
    state.registry.register(EntityRecord(entity_id="zone:left", kind=EntityKind.ZONE, created_by=None, created_tick=0))
    journal = WorldJournal.from_state(
        state=state,
        store_max_work_items=3,
        store_max_votes=3,
        history_max_entries=5,
    )

    ev1 = Event(
        tick=0,
        event_type="arbiter_approved",
        actor_id="agent:off_1",
        payload={"action_index": 0, "reason": "ok", "action": "noop", "ops": []},
        audience=[INTERNAL_AUDIENCE],
    )
    ev2 = Event(
        tick=0,
        event_type="message_sent",
        actor_id="agent:off_1",
        payload={"to_id": "agent:off_2", "private": True, "text": "hi"},
        audience=["agent:off_1", "agent:off_2"],
    )
    journal.apply_events(state=state, events=[ev1, ev2])

    d = journal.to_dict()
    assert len(d["history"]) == 2
    assert d["history"][0]["type"] == "arbiter_approved"
    assert d["history"][1]["type"] == "message_sent"
    agent_entry = next(item for item in d["agents"] if item["id"] == "agent:off_1")
    assert agent_entry["zone_id"] == "zone:left"
    assert agent_entry["org_id"] == "org:city"

    for i in range(5):
        wid = f"work:w{i}"
        state.work_items[wid] = WorkItem(work_id=wid, work_type="t", title=f"T{i}")
        journal.apply_events(
            state=state,
            events=[
                Event(
                    tick=0,
                    event_type="work_item_created",
                    actor_id="agent:off_1",
                    payload={"work_id": wid, "work_type": "t", "title": f"T{i}", "description": "", "participants": []},
                    audience=[INTERNAL_AUDIENCE],
                )
            ],
        )
    assert len(journal.work_items) <= 3


def test_world_journal_redacts_private_messages_and_tracks_world_events() -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
    journal = WorldJournal.from_state(state=state, history_max_entries=10)

    journal.apply_events(
        state=state,
        events=[
            Event(
                tick=0,
                event_type="message_sent",
                actor_id="agent:off_1",
                payload={"to_id": "agent:off_2", "private": True, "text": "SECRET"},
                audience=["agent:off_1", "agent:off_2"],
            ),
            Event(
                tick=0,
                event_type="world_event",
                actor_id=None,
                payload={"description": "Storm"},
                audience=[INTERNAL_AUDIENCE],
            ),
        ],
    )

    yaml_text = journal.to_yaml()
    assert "SECRET" not in yaml_text

    d = journal.to_dict()
    assert any(e.get("type") == "world_event" for e in d["history"])


def test_engine_redacts_private_message_text_in_arbiter_approved(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-private-redaction",
            "ticks": 1,
            "runtime": {"max_actions_per_turn": 1},
                "agents": [
                    {
                        "agent_id": "agent:off_1",
                        "name": "Off 1",
                        "internal": True,
                        "persona": "test",
                        "capabilities": ["message"],
                    },
                    {
                        "agent_id": "agent:off_2",
                        "name": "Off 2",
                        "internal": True,
                        "persona": "test",
                        "capabilities": ["message"],
                    },
                ],
                "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
            }
        )
    mock = MockLLMProvider(
        structured_responses={
            "Off 1": [
                {
                    "type": "send_message",
                    "to_id": "agent:off_2",
                    "text": "SECRET",
                    "private": True,
                    "justification": "test",
                }
            ],
            "Off 2": [{"type": "noop", "justification": ""}],
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=mock)
    asyncio.run(engine.run())

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved = [
        e for e in events
        if e.get("event_type") == "arbiter_approved" and e.get("actor_id") == "agent:off_1"
    ]
    assert approved
    action_repr = str(approved[0].get("payload", {}).get("action", ""))
    assert "SECRET" not in action_repr
    assert "<redacted>" in action_repr

    private_msgs = [
        e for e in events
        if e.get("event_type") == "message_sent"
        and e.get("actor_id") == "agent:off_1"
        and bool(e.get("payload", {}).get("private", True))
    ]
    assert private_msgs
    assert private_msgs[0].get("payload", {}).get("text") == "SECRET"


def test_engine_survives_single_agent_llm_failure(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-llm-failsafe",
            "ticks": 1,
            "runtime": {"max_actions_per_turn": 1},
                "agents": [
                    {
                        "agent_id": "agent:off_1",
                        "name": "Off 1",
                        "internal": True,
                        "persona": "test",
                        "capabilities": ["message"],
                    },
                    {
                        "agent_id": "agent:off_2",
                        "name": "Off 2",
                        "internal": True,
                        "persona": "test",
                        "capabilities": ["message"],
                    },
                ],
                "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
            }
        )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_FailingProposeProvider())
    state = asyncio.run(engine.run())

    assert state.tick == 0
    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("event_type") == "agent_llm_error" for e in events)


def test_composer_normalizes_or_falls_back_on_invalid_ids(tmp_path: Path) -> None:
    from sphere_lc.composer import WorldComposer

    mock = MockLLMProvider(
        structured_responses={
            "compose-bad-ids": {
                "title": "t",
                "description": "d",
                "agents": [
                    {"agent_id": "", "name": "A", "internal": True, "persona": "p"},
                    {"agent_id": "off_1", "name": "B", "internal": True, "persona": "p"},
                    {"agent_id": "agent:off_1", "name": "C", "internal": True, "persona": "p"},
                ],
                "world": {
                    "channels": [{"channel_id": "public", "title": "public"}],
                    "orgs": [{"org_id": "", "title": "o"}],
                    "work_items": [
                        {
                            "work_id": "",
                            "work_type": "task",
                            "title": "w",
                            "description": "",
                            "participants": ["off_1", "agent:off_1", ""],
                        }
                    ],
                },
            }
        }
    )

    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=mock, trace=trace)
    composer = WorldComposer(llm=llm, temperature=0.0, generate_personas=False)
    cfg = asyncio.run(composer.compose(description="compose-bad-ids", ticks=1, language="ru"))

    agent_ids = [a.agent_id for a in cfg.agents]
    assert len(agent_ids) == len(set(agent_ids))
    assert all(aid.startswith("agent:") for aid in agent_ids)

    assert all(ch.channel_id.startswith("chan:") for ch in cfg.world.channels)
    assert all(o.org_id.startswith("org:") for o in cfg.world.orgs)
    assert all(w.work_id.startswith("work:") for w in cfg.world.work_items)

    known_agents = set(agent_ids)
    for w in cfg.world.work_items:
        assert all(pid in known_agents for pid in w.participants)
