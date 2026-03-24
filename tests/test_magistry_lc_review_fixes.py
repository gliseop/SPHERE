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

from magistry_lc.agent import AgentRunner
from magistry_lc.auditor import RuntimeAuditor
from magistry_lc.llm import MockLLMProvider

from magistry_lc.actions import (
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
from magistry_lc.arbiter import Arbiter
from magistry_lc.config import (
    AuditRuntimeConfig,
    GovernanceConfig,
    LLMConfig,
    MemoryConfig,
    RuntimeConfig,
    ScenarioConfig,
)
from magistry_lc.dao import DaoEngine
from magistry_lc.cli import _cmd_run
from magistry_lc.engine import RunArtifacts, WorldEngine
from magistry_lc.entities import EntityRecord, EntityRegistry
from magistry_lc.events import Event, EventLog
from magistry_lc.id_alloc import IdAllocator
from magistry_lc.ids import EntityKind, INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from magistry_lc.journal import WorldJournal
from magistry_lc.llm import LLMCaller
from magistry_lc.llm.caller import create_llm_provider
from magistry_lc.llm.providers import OpenAICompatibleProvider, create_provider
from magistry_lc.memory import AgentMemory, WorkingEntry
from magistry_lc.ops import CreateAgentOp
from magistry_lc.ops import CloseAuditCaseOp, OpenAuditCaseOp, OpenVoteOp, UpdateAuditCaseOp
from magistry_lc.state import AgentState, Vote, WorkItem, WorldState
from magistry_lc.tracing import TraceLog
from magistry_lc.worldgen import WorldGenerator


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


def test_arbiter_enforces_message_capability(tmp_path: Path) -> None:
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
    assert res[0].approved is False
    assert "missing_capability:message" in res[0].reason


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


def test_arbiter_rejects_public_message_to_non_channel_target(tmp_path: Path) -> None:
    state = _mk_state(off_1_caps=["message"], off_2_caps=["message"])
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


def test_arbiter_rejects_duplicate_open_work_item(tmp_path: Path) -> None:
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
    assert res[0].approved is False
    assert res[0].reason == "duplicate_open_work_item:work:existing"


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
        [sys.executable, "-m", "magistry_lc.cli", "--help"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0
    assert "MAGISTRY-LC" in result.stdout


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


def test_arbiter_rejects_role_based_runtime_spawn_name(tmp_path: Path) -> None:
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
    assert res[0].approved is False
    assert res[0].reason == "spawn_name_is_role_alias"


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


def test_create_llm_provider_uses_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("magistry_lc.llm.caller._load_dotenv_if_available", lambda: None)
    monkeypatch.setattr("magistry_lc.llm.caller.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENAI_PROVIDER_ORDER", raising=False)
    _ = create_llm_provider(LLMConfig(model="gpt-4o-mini", base_url=None))
    assert captured["base_url"] == "https://example.test/v1"
    assert captured["provider_order"] is None


def test_create_llm_provider_uses_env_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("magistry_lc.llm.caller.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "Groq, OpenAI,Groq")

    _ = create_llm_provider(LLMConfig(model="gpt-4o-mini", base_url=None))

    assert captured["provider_order"] == ["Groq", "OpenAI"]


def test_create_provider_uses_env_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("magistry_lc.llm.providers.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "Groq, OpenAI,Groq")

    _ = create_provider(mock=False, model="gpt-4o-mini")

    assert captured["provider_order"] == ["Groq", "OpenAI"]


def test_create_llm_provider_loads_dotenv_from_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _FakeProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("magistry_lc.llm.caller.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("OPENAI_PROVIDER_ORDER", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=dotenv-test-key\nOPENAI_BASE_URL=https://dotenv.example/v1\n",
        encoding="utf-8",
    )

    _ = create_llm_provider(LLMConfig(model="gpt-4o-mini", base_url=None))

    assert captured["api_key"] == "dotenv-test-key"
    assert captured["base_url"] == "https://dotenv.example/v1"


def test_openai_provider_keeps_zero_temperature_for_standard_backends() -> None:
    provider = SimpleNamespace(_base_url="https://api.openai.com/v1", _model="gpt-4o-mini")

    assert OpenAICompatibleProvider._effective_temperature(provider, 0.0) == 0.0
    assert OpenAICompatibleProvider._effective_temperature(provider, 0.35) == 0.35


def test_openai_provider_clamps_zero_temperature_only_for_minimax() -> None:
    provider = SimpleNamespace(_base_url="https://api.minimax.chat/v1", _model="MiniMax-Text-01")

    assert OpenAICompatibleProvider._effective_temperature(provider, 0.0) == 0.01


def test_openai_provider_only_sets_provider_order_for_openrouter() -> None:
    standard = OpenAICompatibleProvider(
        model="gpt-4o-mini",
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        provider_order=["Groq"],
    )
    routed = OpenAICompatibleProvider(
        model="gpt-4o-mini",
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        provider_order=["Groq"],
    )

    assert standard._extra_body is None
    assert routed._extra_body == {
        "provider": {"order": ["Groq"], "allow_fallbacks": True}
    }


def test_openai_provider_uses_30s_timeout_by_default() -> None:
    provider = OpenAICompatibleProvider(
        model="gpt-4o-mini",
        api_key="test-key",
        base_url="https://api.openai.com/v1",
    )

    assert provider._client_kwargs["timeout"] == 30.0


def test_openai_provider_caps_attempt_timeout_by_remaining_budget() -> None:
    provider = OpenAICompatibleProvider(
        model="gpt-4o-mini",
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
        model="gpt-4o-mini",
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
        WorkingEntry(tick=1, text="environment_informal_link_updated: {'a': 1}"),
        WorkingEntry(tick=1, text="environment_informal_link_updated: {'a': 2}"),
        WorkingEntry(tick=2, text="Публичное сообщение agent:off_1 -> chan:public: тест"),
    ]

    lines = mem._compact_working_batch(batch)

    assert lines[0].startswith("- (t1, x2) environment_informal_link_updated:")
    assert lines[1] == "- (t2) Публичное сообщение agent:off_1 -> chan:public: тест"


def test_memory_config_uses_real_embeddings_by_default() -> None:
    cfg = MemoryConfig()

    assert cfg.embeddings_mock is False


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

    assert "Open votes: vote:1" in prompt
    assert "respond_nomination (vote_id, accept: true/false)" in prompt


def test_langgraph_world_graph_supports_checkpoint_path(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")

    from magistry_lc.entities import EntityRegistry
    from magistry_lc.graphs import build_world_graph

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

    monkeypatch.setattr("magistry_lc.cli.WorldEngine", _FakeEngine)
    monkeypatch.setattr("magistry_lc.cli.datetime", _FakeDatetime)

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
    from magistry_lc.composer import WorldComposer

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
    cfg = asyncio.run(composer.compose(description="compose-bad-ids", ticks=1, seed=1, language="ru"))

    agent_ids = [a.agent_id for a in cfg.agents]
    assert len(agent_ids) == len(set(agent_ids))
    assert all(aid.startswith("agent:") for aid in agent_ids)

    assert all(ch.channel_id.startswith("chan:") for ch in cfg.world.channels)
    assert all(o.org_id.startswith("org:") for o in cfg.world.orgs)
    assert all(w.work_id.startswith("work:") for w in cfg.world.work_items)

    known_agents = set(agent_ids)
    for w in cfg.world.work_items:
        assert all(pid in known_agents for pid in w.participants)
