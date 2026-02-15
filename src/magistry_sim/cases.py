"""Ядро делопроизводства: дела, предложения, конечный автомат."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CaseSchema(BaseModel):
    """Схема конечного автомата для типа дела.

    Определяет допустимые стадии и правила переходов.
    """

    model_config = {"extra": "forbid"}

    stages: list[str]
    initial_stage: str
    auto_transitions: dict[str, str] = Field(default_factory=dict)
    conditional_transitions: dict[str, tuple[str, str]] = Field(
        default_factory=dict
    )
    action_transitions: dict[str, tuple[str, str]] = Field(
        default_factory=dict
    )
    terminal_stages: list[str] = Field(default_factory=list)
    open_capability: str
    propose_capability: str
    resolve_capability: str


class Proposal(BaseModel):
    """Предложение (отклик) по делу."""

    model_config = {"extra": "forbid"}

    id: str
    case_id: str
    author_id: str
    content: str
    submitted_at: int


class Note(BaseModel):
    """Публичная запись в деле."""

    model_config = {"extra": "forbid"}

    id: str
    case_id: str
    author_id: str
    content: str
    created_at: int


class Vote(BaseModel):
    """Голос присяжного по делу трибунала."""

    model_config = {"extra": "forbid"}

    voter_id: str
    case_id: str
    verdict: str
    reasoning: str
    round: int


class Case(BaseModel):
    """Организационный процесс (дело)."""

    model_config = {"extra": "forbid"}

    id: str
    case_type: str
    title: str
    description: str
    owner_id: str
    stage: str
    params: str = ""
    proposals: list[Proposal] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    votes: list[Vote] = Field(default_factory=list)
    created_at: int = 0
    deadline_round: int | None = None
    closed_at: int | None = None
    decision: str | None = None
    justification: str | None = None


CASE_REGISTRY: dict[str, CaseSchema] = {
    "procurement": CaseSchema(
        stages=["open", "collecting", "evaluation", "closed"],
        initial_stage="open",
        auto_transitions={"open": "collecting"},
        conditional_transitions={
            "collecting": ("evaluation", "deadline_expired"),
        },
        action_transitions={
            "evaluation": ("closed", "resolve_case"),
        },
        terminal_stages=["closed"],
        open_capability="open_case:procurement",
        propose_capability="submit_proposal:procurement",
        resolve_capability="resolve_case:procurement",
    ),
    "hiring": CaseSchema(
        stages=["open", "screening", "decision", "closed"],
        initial_stage="open",
        auto_transitions={},
        conditional_transitions={
            "open": ("screening", "has_proposals"),
        },
        action_transitions={
            "screening": ("decision", "resolve_case"),
            "decision": ("closed", "resolve_case"),
        },
        terminal_stages=["closed"],
        open_capability="open_case:hiring",
        propose_capability="submit_proposal:hiring",
        resolve_capability="resolve_case:hiring",
    ),
    "budget": CaseSchema(
        stages=["draft", "review", "closed"],
        initial_stage="draft",
        auto_transitions={},
        conditional_transitions={
            "draft": ("review", "has_proposals"),
        },
        action_transitions={
            "review": ("closed", "resolve_case"),
        },
        terminal_stages=["closed"],
        open_capability="open_case:budget",
        propose_capability="submit_proposal:budget",
        resolve_capability="resolve_case:budget",
    ),
    "investigation": CaseSchema(
        stages=["filed", "observation", "frozen", "tribunal", "verdict"],
        initial_stage="filed",
        auto_transitions={},
        conditional_transitions={
            "tribunal": ("verdict", "quorum_reached"),
        },
        action_transitions={
            "filed": ("observation", "file_report"),
            "filed|frozen": ("frozen", "file_report"),
            "filed|tribunal": ("tribunal", "file_report"),
        },
        terminal_stages=["observation", "frozen", "verdict"],
        open_capability="file_report:investigation",
        propose_capability="file_report:investigation",
        resolve_capability="file_report:investigation",
    ),
}


def validate_transition(
    case_type: str, current_stage: str, target_stage: str, action: str
) -> bool:
    """Проверить допустимость перехода в конечном автомате.

    Args:
        case_type: Тип дела.
        current_stage: Текущая стадия.
        target_stage: Целевая стадия.
        action: Действие, инициирующее переход.

    Returns:
        True, если переход допустим.
    """
    schema = CASE_REGISTRY.get(case_type)
    if schema is None:
        return False

    if current_stage not in schema.stages:
        return False
    if target_stage not in schema.stages:
        return False

    auto = schema.auto_transitions.get(current_stage)
    if auto == target_stage:
        return True

    cond = schema.conditional_transitions.get(current_stage)
    if cond and cond[0] == target_stage:
        return True

    act = schema.action_transitions.get(current_stage)
    if act and act[0] == target_stage and act[1] == action:
        return True

    key = f"{current_stage}|{target_stage}"
    act2 = schema.action_transitions.get(key)
    if act2 and act2[0] == target_stage and act2[1] == action:
        return True

    return False


def check_condition(
    condition: str,
    case: Case,
    current_round: int,
    quorum_size: int = 3,
) -> bool:
    """Проверить условие для условного перехода.

    Args:
        condition: Имя условия (deadline_expired, has_proposals, quorum_reached).
        case: Дело.
        current_round: Текущий раунд.
        quorum_size: Требуемое число голосов для кворума трибунала.

    Returns:
        True, если условие выполнено.
    """
    if condition == "deadline_expired":
        return (
            case.deadline_round is not None
            and current_round >= case.deadline_round
        )
    if condition == "has_proposals":
        return len(case.proposals) > 0
    if condition == "quorum_reached":
        required_votes = max(1, quorum_size)
        return len(case.votes) >= required_votes
    return False


def apply_transition(case: Case, target_stage: str) -> None:
    """Применить переход к делу.

    Args:
        case: Дело.
        target_stage: Целевая стадия.
    """
    case.stage = target_stage
