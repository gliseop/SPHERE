"""Структурированные действия агента (Action[]).

MAGISTRY-LC стремится держать интерфейс агента строгим и проверяемым:
- агент возвращает JSON-массив Action;
- engine валидирует Action по схеме и по EntityRegistry (антифантомы);
- затем Arbiter переводит Action -> StateOp[].
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


def actions_json_schema(*, max_actions: int) -> dict[str, Any]:
    """JSON-схема для `Action[]` (для structured output).

    Args:
        max_actions: Лимит действий за ход.

    Returns:
        JSON schema.
    """
    # Схема намеренно простая и без $ref: это повышает совместимость
    # со strict json_schema на разных провайдерах.
    base_props = {
        "type": {"type": "string"},
        "justification": {"type": "string"},
    }

    def obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {**base_props, **properties},
            "required": required,
        }

    one_of = [
        obj(
            {
                "type": {"const": ActionType.PERFORM},
                "description": {"type": "string"},
                "target_id": {"type": "string"},
            },
            ["type", "description"],
        ),
        obj(
            {
                "type": {"const": ActionType.SEND_MESSAGE},
                "to_id": {"type": "string"},
                "text": {"type": "string"},
                "private": {"type": "boolean"},
            },
            ["type", "to_id", "text"],
        ),
        obj(
            {
                "type": {"const": ActionType.PUBLISH},
                "channel_id": {"type": "string"},
                "text": {"type": "string"},
            },
            ["type", "channel_id", "text"],
        ),
        obj(
            {
                "type": {"const": ActionType.CREATE_WORK_ITEM},
                "work_type": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "participants": {"type": "array", "items": {"type": "string"}},
            },
            ["type", "work_type", "title"],
        ),
        obj(
            {
                "type": {"const": ActionType.ADD_WORK_NOTE},
                "work_id": {"type": "string"},
                "text": {"type": "string"},
            },
            ["type", "work_id", "text"],
        ),
        obj(
            {
                "type": {"const": ActionType.SUBMIT_WORK_PROPOSAL},
                "work_id": {"type": "string"},
                "text": {"type": "string"},
            },
            ["type", "work_id", "text"],
        ),
        obj(
            {
                "type": {"const": ActionType.NOMINATE_POSITION_CHANGE},
                "target_agent_id": {"type": "string"},
                "new_title": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["type", "target_agent_id", "new_title"],
        ),
        obj(
            {
                "type": {"const": ActionType.CAST_VOTE},
                "vote_id": {"type": "string"},
                "choice": {"type": "string", "enum": list(VoteChoice)},
            },
            ["type", "vote_id", "choice"],
        ),
        obj(
            {
                "type": {"const": ActionType.RESPOND_NOMINATION},
                "vote_id": {"type": "string"},
                "accept": {"type": "boolean"},
            },
            ["type", "vote_id", "accept"],
        ),
        obj(
            {
                "type": {"const": ActionType.REQUEST_ENTITY},
                "kind": {"type": "string", "enum": ["org", "chan"]},
                "slug": {"type": "string"},
                "description": {"type": "string"},
            },
            ["type", "kind", "slug"],
        ),
        obj(
            {
                "type": {"const": ActionType.SPAWN_AGENT},
                "slug": {"type": "string"},
                "name": {"type": "string"},
                "internal": {"type": "boolean"},
                "persona_hint": {"type": "string"},
                "org_id": {"type": "string"},
                "zone_id": {"type": "string"},
                "capabilities": {"type": "array", "items": {"type": "string"}},
            },
            ["type", "slug", "name", "internal", "persona_hint"],
        ),
        obj({"type": {"const": ActionType.NOOP}}, ["type"]),
    ]

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "actions": {
                "type": "array",
                "maxItems": max_actions,
                "items": {"oneOf": one_of},
            },
        },
        "required": ["actions"],
    }
