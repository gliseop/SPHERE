"""Социальный граф на основе NetworkX."""

from __future__ import annotations

import networkx as nx


class SocialGraph:
    """Граф связей между агентами."""

    def __init__(self) -> None:
        self._graph: nx.Graph = nx.Graph()

    def add_agent(self, agent_id: str) -> None:
        """Добавить агента в граф.

        Args:
            agent_id: Идентификатор агента.
        """
        if not self._graph.has_node(agent_id):
            self._graph.add_node(agent_id)

    def add_connection(
        self,
        agent_a: str,
        agent_b: str,
        relation: str = "знакомы",
        strength: float = 1.0,
    ) -> None:
        """Добавить или обновить связь между агентами.

        Args:
            agent_a: Первый агент.
            agent_b: Второй агент.
            relation: Тип связи.
            strength: Сила связи.
        """
        self.add_agent(agent_a)
        self.add_agent(agent_b)
        if self._graph.has_edge(agent_a, agent_b):
            self._graph[agent_a][agent_b]["strength"] = max(
                self._graph[agent_a][agent_b]["strength"], strength
            )
        else:
            self._graph.add_edge(
                agent_a, agent_b, relation=relation, strength=strength
            )

    def strengthen(
        self, agent_a: str, agent_b: str, delta: float = 0.1
    ) -> None:
        """Усилить связь между агентами.

        Args:
            agent_a: Первый агент.
            agent_b: Второй агент.
            delta: Величина усиления.
        """
        if self._graph.has_edge(agent_a, agent_b):
            self._graph[agent_a][agent_b]["strength"] += delta
        else:
            self.add_connection(agent_a, agent_b, strength=delta)

    def get_connections(self, agent_id: str) -> list[dict]:
        """Получить связи агента.

        Args:
            agent_id: Идентификатор агента.

        Returns:
            Список словарей с информацией о связях.
        """
        if not self._graph.has_node(agent_id):
            return []
        result = []
        for neighbor in self._graph.neighbors(agent_id):
            edge = self._graph[agent_id][neighbor]
            result.append({
                "agent_id": neighbor,
                "relation": edge.get("relation", "знакомы"),
                "strength": edge.get("strength", 1.0),
            })
        return result

    def get_strength(self, agent_a: str, agent_b: str) -> float:
        """Получить силу связи между агентами.

        Args:
            agent_a: Первый агент.
            agent_b: Второй агент.

        Returns:
            Сила связи (0.0, если связи нет).
        """
        if self._graph.has_edge(agent_a, agent_b):
            return self._graph[agent_a][agent_b].get("strength", 0.0)
        return 0.0

    def get_suspicious_edges(self, threshold: float = 3.0) -> list[tuple]:
        """Получить подозрительно сильные связи.

        Args:
            threshold: Порог силы связи.

        Returns:
            Список рёбер (agent_a, agent_b, strength).
        """
        result = []
        for a, b, data in self._graph.edges(data=True):
            if data.get("strength", 0.0) >= threshold:
                result.append((a, b, data["strength"]))
        return result

    def density(self) -> float:
        """Плотность графа.

        Returns:
            Плотность от 0.0 до 1.0.
        """
        return nx.density(self._graph)

    @property
    def graph(self) -> nx.Graph:
        """Доступ к базовому графу NetworkX."""
        return self._graph
