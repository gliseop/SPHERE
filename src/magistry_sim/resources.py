"""Управление ресурсами агентов."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentResources(BaseModel):
    """Текущие ресурсы агента."""

    model_config = {"extra": "forbid"}

    budget_limit: float = 0.0
    budget_spent: float = 0.0
    staffing_slots: int = 0
    staffing_filled: int = 0
    contract_capacity: int = 0
    contracts_active: int = 0


class ResourceManager:
    """Менеджер ресурсов всех агентов."""

    def __init__(self) -> None:
        self._resources: dict[str, AgentResources] = {}

    def init_agent(
        self,
        agent_id: str,
        budget_limit: float = 0.0,
        staffing_slots: int = 0,
        contract_capacity: int = 0,
    ) -> None:
        """Инициализировать ресурсы агента.

        Args:
            agent_id: Идентификатор агента.
            budget_limit: Бюджетный лимит.
            staffing_slots: Количество ставок.
            contract_capacity: Контрактная ёмкость.
        """
        self._resources[agent_id] = AgentResources(
            budget_limit=budget_limit,
            staffing_slots=staffing_slots,
            contract_capacity=contract_capacity,
        )

    def get(self, agent_id: str) -> AgentResources | None:
        """Получить ресурсы агента.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Ресурсы агента или None.
        """
        return self._resources.get(agent_id)

    def check_budget(self, agent_id: str, amount: float) -> bool:
        """Проверить, хватает ли бюджета.

        Args:
            agent_id: Идентификатор агента.
            amount: Запрашиваемая сумма.

        Returns:
            True, если бюджета достаточно.
        """
        res = self._resources.get(agent_id)
        if res is None:
            return False
        return (res.budget_limit - res.budget_spent) >= amount

    def spend_budget(self, agent_id: str, amount: float) -> bool:
        """Расходовать бюджет.

        Args:
            agent_id: Идентификатор агента.
            amount: Сумма расхода.

        Returns:
            True, если расход произведён.
        """
        res = self._resources.get(agent_id)
        if res is None:
            return False
        if (res.budget_limit - res.budget_spent) < amount:
            return False
        res.budget_spent += amount
        return True

    def fill_slot(self, agent_id: str) -> bool:
        """Заполнить ставку.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            True, если ставка заполнена.
        """
        res = self._resources.get(agent_id)
        if res is None:
            return False
        if res.staffing_filled >= res.staffing_slots:
            return False
        res.staffing_filled += 1
        return True

    def free_slot(self, agent_id: str) -> bool:
        """Освободить ставку.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            True, если ставка освобождена.
        """
        res = self._resources.get(agent_id)
        if res is None:
            return False
        if res.staffing_filled <= 0:
            return False
        res.staffing_filled -= 1
        return True

    def use_capacity(self, agent_id: str) -> bool:
        """Занять контрактную ёмкость.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            True, если ёмкость занята.
        """
        res = self._resources.get(agent_id)
        if res is None:
            return False
        if res.contracts_active >= res.contract_capacity:
            return False
        res.contracts_active += 1
        return True

    def free_capacity(self, agent_id: str) -> bool:
        """Освободить контрактную ёмкость.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            True, если ёмкость освобождена.
        """
        res = self._resources.get(agent_id)
        if res is None:
            return False
        if res.contracts_active <= 0:
            return False
        res.contracts_active -= 1
        return True
