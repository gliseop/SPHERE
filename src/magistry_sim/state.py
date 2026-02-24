"""Глобальное состояние мира симуляции."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .cases import Case
from .config import AgentProfile, Capability, Need
from .events import EventLog
from .graph import SocialGraph
from .locations import LocationManager
from .resources import ResourceManager


class Message(BaseModel):
    """Сообщение между агентами."""

    model_config = {"extra": "forbid"}

    from_id: str
    to_id: str
    content: str
    response: str = ""
    thread_id: str = ""
    channel: str = "telegram"
    timestamp: str = ""
    private: bool = True
    # Default 0 (not None) for backward compatibility: existing code passes
    # Message(round=state.round, ...) where state.round is always int.
    # Event.round defaults to None because new time-based events have no round.
    round: int | None = 0


class Complaint(BaseModel):
    """Анонимная жалоба (автор обезличен)."""

    model_config = {"extra": "forbid"}

    case_id: str
    assessment: str
    round: int = 0


class ReputationRecord(BaseModel):
    """Запись репутации агента."""

    model_config = {"extra": "forbid"}

    score: float = 10.0
    frozen: bool = False
    position_level: int = 0


class WorldState:
    """Полное состояние мира симуляции."""

    def __init__(self) -> None:
        self.cases: dict[str, Case] = {}
        self.agents: dict[str, AgentProfile] = {}
        self.resources: ResourceManager = ResourceManager()
        self.graph: SocialGraph = SocialGraph()
        self.event_log: EventLog = EventLog()
        self.locations: LocationManager | None = None
        self.messages: list[Message] = []
        self.complaints: list[Complaint] = []
        self.active_needs: list[Need] = []
        self.round: int = 0
        self.current_time: datetime | None = None
        self.reputation: dict[str, ReputationRecord] = {}
        self._case_counter: int = 0
        self._proposal_counter: int = 0
        self._note_counter: int = 0

    def new_case_id(self) -> str:
        """Сгенерировать уникальный идентификатор дела.

        Returns:
            Идентификатор вида D-001.
        """
        self._case_counter += 1
        return f"D-{self._case_counter:03d}"

    def new_proposal_id(self) -> str:
        """Сгенерировать уникальный идентификатор предложения.

        Returns:
            Идентификатор вида P-001.
        """
        self._proposal_counter += 1
        return f"P-{self._proposal_counter:03d}"

    def new_note_id(self) -> str:
        """Сгенерировать уникальный идентификатор записи.

        Returns:
            Идентификатор вида N-001.
        """
        self._note_counter += 1
        return f"N-{self._note_counter:03d}"

    def has_capability(
        self, agent_id: str, action: str, case_type: str = ""
    ) -> bool:
        """Проверить, обладает ли агент полномочием.

        Если case_type пуст, проверяет наличие полномочия для любого типа.
        Если case_type указан, проверяет наличие полномочия для этого типа.

        Args:
            agent_id: Идентификатор агента.
            action: Действие (open_case, submit_proposal и т.д.).
            case_type: Тип дела (пустая строка — любой тип).

        Returns:
            True, если полномочие есть.
        """
        profile = self.agents.get(agent_id)
        if profile is None:
            return False
        for cap in profile.capabilities:
            if cap.action == action:
                if (
                    not case_type
                    or not cap.case_types
                    or case_type in cap.case_types
                ):
                    return True
        return False

    def get_agent_cases(self, agent_id: str) -> list[Case]:
        """Получить дела, которыми владеет агент.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Список дел.
        """
        return [
            c for c in self.cases.values() if c.owner_id == agent_id
        ]

    def get_open_cases(self) -> list[Case]:
        """Получить все открытые дела.

        Дело считается открытым, если его стадия не равна 'closed'
        и оно не было закрыто (closed_at is None).

        Returns:
            Список открытых дел.
        """
        return [
            case for case in self.cases.values()
            if case.stage != "closed" and case.closed_at is None
        ]

    def get_cases_involving(self, agent_id: str) -> list[Case]:
        """Получить все дела, в которых участвует агент.

        Включает дела, где агент — владелец или автор предложения/записи.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Список дел.
        """
        result = []
        for case in self.cases.values():
            if case.owner_id == agent_id:
                result.append(case)
                continue
            if any(p.author_id == agent_id for p in case.proposals):
                result.append(case)
                continue
            if any(n.author_id == agent_id for n in case.notes):
                result.append(case)
                continue
            if any(v.voter_id == agent_id for v in case.votes):
                result.append(case)
        return result
