"""Глобальное состояние мира симуляции."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .cases import Case, CASE_REGISTRY
from .config import AgentProfile, Capability, Need
from .events import EventLog
from .graph import SocialGraph
from .resources import ResourceManager


class Message(BaseModel):
    """Сообщение между агентами."""

    model_config = {"extra": "forbid"}

    from_id: str
    to_id: str
    content: str
    response: str = ""
    private: bool = True
    round: int = 0


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
        self.messages: list[Message] = []
        self.complaints: list[Complaint] = []
        self.active_needs: list[Need] = []
        self.round: int = 0
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
        self, agent_id: str, action: str, case_type: str
    ) -> bool:
        """Проверить, обладает ли агент полномочием.

        Args:
            agent_id: Идентификатор агента.
            action: Действие (open_case, submit_proposal и т.д.).
            case_type: Тип дела.

        Returns:
            True, если полномочие есть.
        """
        profile = self.agents.get(agent_id)
        if profile is None:
            return False
        for cap in profile.capabilities:
            if cap.action == action:
                if not cap.case_types or case_type in cap.case_types:
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

        Returns:
            Список дел, не находящихся в терминальной стадии.
        """
        result = []
        for case in self.cases.values():
            schema = CASE_REGISTRY.get(case.case_type)
            if schema and case.stage not in schema.terminal_stages:
                result.append(case)
        return result

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
