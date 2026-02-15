"""Система физических локаций."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Location(BaseModel):
    """Физическая локация в симуляции.

    Attributes:
        id: Уникальный идентификатор локации.
        name: Человекочитаемое название.
        public: Является ли локация публичной (действия видны всем).
        available_actions: Список допустимых действий в этой локации.
        suspicion_modifier: Модификатор подозрительности действий в локации.
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str
    public: bool = True
    available_actions: list[str] = Field(default_factory=list)
    suspicion_modifier: float = 0.0


class LocationManager:
    """Управление расположением агентов по локациям.

    Отслеживает, какие агенты находятся в каких локациях,
    и определяет возможность наблюдения между агентами.
    """

    def __init__(self) -> None:
        self._locations: dict[str, Location] = {}
        self._agent_locations: dict[str, str] = {}

    def add_location(self, location: Location) -> None:
        """Регистрирует локацию в менеджере.

        Args:
            location: Локация для регистрации.
        """
        self._locations[location.id] = location

    def get_location(self, location_id: str) -> Location | None:
        """Возвращает локацию по идентификатору.

        Args:
            location_id: Идентификатор локации.

        Returns:
            Локация или None, если не найдена.
        """
        return self._locations.get(location_id)

    def place_agent(self, agent_id: str, location_id: str) -> None:
        """Размещает агента в локации.

        Args:
            agent_id: Идентификатор агента.
            location_id: Идентификатор локации.
        """
        self._agent_locations[agent_id] = location_id

    def move_agent(self, agent_id: str, location_id: str) -> None:
        """Перемещает агента в другую локацию.

        Args:
            agent_id: Идентификатор агента.
            location_id: Идентификатор целевой локации.
        """
        self._agent_locations[agent_id] = location_id

    def get_agent_location(self, agent_id: str) -> str | None:
        """Возвращает идентификатор локации агента.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Идентификатор локации или None, если агент не размещён.
        """
        return self._agent_locations.get(agent_id)

    def agents_at(self, location_id: str) -> list[str]:
        """Возвращает список агентов в данной локации.

        Args:
            location_id: Идентификатор локации.

        Returns:
            Список идентификаторов агентов.
        """
        return [
            aid for aid, lid in self._agent_locations.items()
            if lid == location_id
        ]

    def can_observe(self, observer_id: str, target_id: str) -> bool:
        """Проверяет, может ли наблюдатель видеть цель.

        Наблюдение возможно только если оба агента находятся
        в одной и той же локации.

        Args:
            observer_id: Идентификатор наблюдателя.
            target_id: Идентификатор цели.

        Returns:
            True, если наблюдение возможно.
        """
        obs_loc = self._agent_locations.get(observer_id)
        tgt_loc = self._agent_locations.get(target_id)
        if obs_loc is None or tgt_loc is None:
            return False
        return obs_loc == tgt_loc
