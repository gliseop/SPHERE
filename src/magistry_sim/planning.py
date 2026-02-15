"""Модуль иерархического планирования по модели Park et al. (2023)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from magistry_sim.memory import MemoryStream

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider

STRATEGIC_PLAN_INTERVAL = 5


@dataclass
class AgentPlan:
    """Текущий план агента.

    Attributes:
        strategic_goals: Стратегические цели на несколько раундов.
        tactical_steps: Тактические шаги на текущий раунд.
        last_strategic_round: Раунд последнего обновления стратегии.
    """

    strategic_goals: list[str] = field(default_factory=list)
    tactical_steps: list[str] = field(default_factory=list)
    last_strategic_round: int = 0

    def needs_strategic_update(self, current_round: int) -> bool:
        """Проверяет, нужно ли обновить стратегический план.

        Обновление нужно в двух случаях: при пустых целях в начальном
        раунде (когда current_round ещё не ушёл вперёд от last_strategic_round)
        или при достижении интервала планирования.

        Args:
            current_round: Номер текущего раунда.

        Returns:
            True, если план пуст на старте или прошло достаточно раундов.
        """
        if not self.strategic_goals and current_round <= self.last_strategic_round:
            return True
        return (current_round - self.last_strategic_round) >= STRATEGIC_PLAN_INTERVAL


def generate_strategic_plan(
    stream: MemoryStream,
    llm: LLMProvider,
    agent_role: str,
    current_round: int,
) -> list[str]:
    """Генерирует стратегические цели агента.

    Основывается на недавних воспоминаниях и рефлексиях.

    Args:
        stream: Поток памяти агента.
        llm: Провайдер языковой модели.
        agent_role: Роль агента (для контекста промпта).
        current_round: Номер текущего раунда.

    Returns:
        Список стратегических целей (2-4 строки).
    """
    recent = stream.get_recent(n=50)
    reflections = stream.get_by_kind("reflection")[-5:]
    context = "\n".join(f"- {r.content}" for r in recent[:20])
    ref_text = (
        "\n".join(f"- {r.content}" for r in reflections)
        if reflections
        else "нет"
    )

    prompt = (
        f"Ты — {agent_role} (агент {stream.agent_id}). "
        f"Сейчас раунд {current_round}.\n\n"
        f"Последние наблюдения:\n{context}\n\n"
        f"Твои выводы (рефлексии):\n{ref_text}\n\n"
        f"Сформулируй 2-4 стратегические цели на ближайшие "
        f"{STRATEGIC_PLAN_INTERVAL} раундов. "
        f"Верни JSON-массив строк."
    )
    response = llm.generate(system="", user=prompt)
    try:
        goals = json.loads(response.text)
        if isinstance(goals, list):
            return [str(g) for g in goals[:4]]
    except (json.JSONDecodeError, TypeError):
        pass
    return ["Действовать по обстоятельствам"]


def generate_tactical_plan(
    stream: MemoryStream,
    llm: LLMProvider,
    strategic_goals: list[str],
    current_round: int,
) -> list[str]:
    """Генерирует тактические шаги на текущий раунд.

    Args:
        stream: Поток памяти агента.
        llm: Провайдер языковой модели.
        strategic_goals: Текущие стратегические цели.
        current_round: Номер текущего раунда.

    Returns:
        Список тактических шагов (1-3 строки).
    """
    recent = stream.get_recent(n=10)
    context = "\n".join(f"- {r.content}" for r in recent)
    goals_text = "\n".join(f"- {g}" for g in strategic_goals)

    prompt = (
        f"Агент {stream.agent_id}, раунд {current_round}.\n\n"
        f"Стратегические цели:\n{goals_text}\n\n"
        f"Текущая обстановка:\n{context}\n\n"
        f"Сформулируй 1-3 конкретных шага на этот раунд. "
        f"Верни JSON-массив строк."
    )
    response = llm.generate(system="", user=prompt)
    try:
        steps = json.loads(response.text)
        if isinstance(steps, list):
            return [str(s) for s in steps[:3]]
    except (json.JSONDecodeError, TypeError):
        pass
    return ["Оценить обстановку"]
