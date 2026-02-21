"""Модели делопроизводства: дела, предложения, записи, голоса."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Proposal(BaseModel):
    """Предложение (отклик) по делу.

    Attributes:
        id: Уникальный идентификатор предложения.
        case_id: Идентификатор дела, к которому относится предложение.
        author_id: Идентификатор автора предложения.
        content: Текст предложения.
        submitted_at: Раунд подачи предложения.
    """

    model_config = {"extra": "ignore"}

    id: str
    case_id: str
    author_id: str
    content: str
    submitted_at: int


class Note(BaseModel):
    """Публичная запись в деле.

    Attributes:
        id: Уникальный идентификатор записи.
        case_id: Идентификатор дела, к которому относится запись.
        author_id: Идентификатор автора записи.
        content: Текст записи.
        created_at: Раунд создания записи.
    """

    model_config = {"extra": "ignore"}

    id: str
    case_id: str
    author_id: str
    content: str
    created_at: int


class Vote(BaseModel):
    """Голос присяжного по делу трибунала.

    Attributes:
        voter_id: Идентификатор голосующего.
        case_id: Идентификатор дела.
        verdict: Вынесенный вердикт.
        reasoning: Обоснование вердикта.
        round: Раунд голосования.
    """

    model_config = {"extra": "ignore"}

    voter_id: str
    case_id: str
    verdict: str
    reasoning: str
    round: int


class Case(BaseModel):
    """Организационный процесс (дело).

    Тип дела (case_type) и стадия (stage) задаются произвольно,
    без привязки к фиксированному реестру или конечному автомату.

    Attributes:
        id: Уникальный идентификатор дела.
        case_type: Тип дела (произвольная строка).
        title: Заголовок дела.
        description: Описание дела.
        owner_id: Идентификатор владельца дела.
        stage: Текущая стадия дела (произвольная строка).
        params: Дополнительные параметры в свободной форме.
        proposals: Список предложений по делу.
        notes: Список записей в деле.
        votes: Список голосов по делу.
        created_at: Раунд создания дела.
        deadline_round: Раунд крайнего срока (если задан).
        closed_at: Раунд закрытия дела (если закрыто).
        decision: Принятое решение (если есть).
        justification: Обоснование решения (если есть).
    """

    model_config = {"extra": "ignore"}

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
