"""Внутренний словарь действий и схемы agent/runtime-контрактов.

Когнитивный агент больше не выбирает typed actions напрямую. Его основной
контракт — свободное текстовое описание хода (`proposal`), которое затем
переводится арбитром во внутренние `StateOp`. Typed `Action` сохранены как
внутренний/legacy-слой совместимости и как vocabulary исполнительного runtime.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    """Тип действия."""

    PERFORM = "perform"
    SEND_MESSAGE = "send_message"
    PUBLISH = "publish"
    CREATE_WORK_ITEM = "create_work_item"
    ADD_WORK_NOTE = "add_work_note"
    SUBMIT_WORK_PROPOSAL = "submit_work_proposal"
    NOMINATE_POSITION_CHANGE = "nominate_position_change"
    CAST_VOTE = "cast_vote"
    RESPOND_NOMINATION = "respond_nomination"
    REQUEST_ENTITY = "request_entity"
    SPAWN_AGENT = "spawn_agent"
    NOOP = "noop"


class VoteChoice(StrEnum):
    """Выбор в голосовании."""

    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"


SPAWN_AGENT_ALLOWED_CAPABILITIES: tuple[str, ...] = ("message", "work")


class _BaseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    justification: str = ""


class PerformAction(_BaseAction):
    """Свободное действие: описание + опциональная цель.

    Это аналог `perform_action` из текущего движка, но с антифантом-правилом:
    `target_id` должен быть либо пустым, либо существовать в EntityRegistry.
    """

    type: Literal[ActionType.PERFORM]
    description: str
    target_id: str = ""


class SendMessageAction(_BaseAction):
    type: Literal[ActionType.SEND_MESSAGE]
    to_id: str
    text: str
    private: bool = True


class PublishAction(_BaseAction):
    type: Literal[ActionType.PUBLISH]
    channel_id: str
    text: str


class CreateWorkItemAction(_BaseAction):
    type: Literal[ActionType.CREATE_WORK_ITEM]
    work_type: str
    title: str
    description: str = ""
    participants: list[str] = Field(default_factory=list)


class AddWorkNoteAction(_BaseAction):
    type: Literal[ActionType.ADD_WORK_NOTE]
    work_id: str
    text: str


class SubmitWorkProposalAction(_BaseAction):
    type: Literal[ActionType.SUBMIT_WORK_PROPOSAL]
    work_id: str
    text: str


class NominatePositionChangeAction(_BaseAction):
    type: Literal[ActionType.NOMINATE_POSITION_CHANGE]
    target_agent_id: str
    new_title: str
    reason: str = ""


class CastVoteAction(_BaseAction):
    type: Literal[ActionType.CAST_VOTE]
    vote_id: str
    choice: VoteChoice


class RespondNominationAction(_BaseAction):
    type: Literal[ActionType.RESPOND_NOMINATION]
    vote_id: str
    accept: bool


class RequestEntityAction(_BaseAction):
    type: Literal[ActionType.REQUEST_ENTITY]
    kind: Literal["org", "chan"]
    slug: str
    description: str = ""


class SpawnAgentAction(_BaseAction):
    type: Literal[ActionType.SPAWN_AGENT]
    slug: str
    name: str
    internal: bool
    persona_hint: str
    org_id: str = ""
    zone_id: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["message", "work"])


class NoopAction(_BaseAction):
    type: Literal[ActionType.NOOP]


Action = Annotated[
    PerformAction
    | SendMessageAction
    | PublishAction
    | CreateWorkItemAction
    | AddWorkNoteAction
    | SubmitWorkProposalAction
    | NominatePositionChangeAction
    | CastVoteAction
    | RespondNominationAction
    | RequestEntityAction
    | SpawnAgentAction
    | NoopAction,
    Field(discriminator="type"),
]


def agent_turn_json_schema(*, max_chars: int = 4000) -> dict[str, Any]:
    """JSON-схема для свободного turn proposal когнитивного агента.

    Агент описывает ход одним свободным текстовым предложением/абзацем без
    typed action menu. Арбитр затем сам выделяет из этого текста формальные
    последствия для мира.
    """

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "proposal": {
                "type": "string",
                "maxLength": max(1, int(max_chars)),
            }
        },
        "required": ["proposal"],
    }
