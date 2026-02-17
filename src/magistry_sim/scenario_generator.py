"""LLM-генератор сценариев симуляции."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import (
    AgentProfile,
    GovernanceConfig,
    Need,
    ScenarioConfig,
)
from .enums import GovernanceMode, ScenarioId
from .personality import (
    AgentPersonality,
    DarkTriadProfile,
    HEXACOProfile,
)

if TYPE_CHECKING:
    from .llm import LLMProvider


_SCENARIO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "agents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "position": {"type": "string"},
                    "hexaco": {
                        "type": "object",
                        "properties": {
                            "honesty_humility": {"type": "number"},
                            "emotionality": {"type": "number"},
                            "extraversion": {"type": "number"},
                            "agreeableness": {"type": "number"},
                            "conscientiousness": {"type": "number"},
                            "openness": {"type": "number"},
                        },
                        "required": [
                            "honesty_humility", "emotionality",
                            "extraversion", "agreeableness",
                            "conscientiousness", "openness",
                        ],
                    },
                    "dark_triad": {
                        "type": "object",
                        "properties": {
                            "narcissism": {"type": "number"},
                            "machiavellianism": {"type": "number"},
                            "psychopathy": {"type": "number"},
                        },
                        "required": [
                            "narcissism", "machiavellianism", "psychopathy",
                        ],
                    },
                },
                "required": [
                    "id", "name", "position", "hexaco", "dark_triad",
                ],
            },
        },
        "needs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "case_type": {"type": "string"},
                    "description": {"type": "string"},
                    "target_agent_id": {"type": "string"},
                    "appear_round": {"type": "number"},
                },
                "required": [
                    "case_type", "description",
                    "target_agent_id", "appear_round",
                ],
            },
        },
    },
    "required": ["title", "description", "agents", "needs"],
}


def generate_scenario(
    llm: LLMProvider,
    corruption_type: str,
    agent_count: int,
    economic_pressure: float,
    governance: GovernanceMode,
    max_rounds: int = 8,
    seed: int = 42,
    scenario_id: ScenarioId = ScenarioId.S6,
) -> ScenarioConfig:
    """Генерирует конфигурацию сценария через LLM.

    Принимает высокоуровневые параметры и использует LLM для генерации
    детальной конфигурации: профили агентов с HEXACO и Dark Triad,
    организационные потребности, название и описание сценария.

    Args:
        llm: Провайдер языковой модели.
        corruption_type: Тип коррупции (kickback, bribery, embezzlement).
        agent_count: Количество агентов.
        economic_pressure: Уровень экономического давления (0.0-1.0).
        governance: Режим управления.
        max_rounds: Количество раундов симуляции.
        seed: Зерно генератора случайных чисел.
        scenario_id: Идентификатор сценария.

    Returns:
        Валидная конфигурация сценария.
    """
    result = llm.generate_structured(
        system=(
            "Ты — генератор сценариев для агентной симуляции организационной "
            "коррупции. Создай реалистичный сценарий с указанными параметрами. "
            "Каждый агент должен иметь уникальный профиль личности HEXACO "
            "(0-100) и Тёмной триады (0-100), соответствующий его роли "
            "и типу коррупционного поведения."
        ),
        user=(
            f"Создай сценарий симуляции со следующими параметрами:\n"
            f"- Тип коррупции: {corruption_type}\n"
            f"- Количество агентов: {agent_count}\n"
            f"- Уровень экономического давления: {economic_pressure}\n"
            f"- Режим управления: {governance.value}\n\n"
            f"Для каждого агента укажи роль (чиновник, бизнесмен, аудитор, "
            f"кандидат), профиль HEXACO и Тёмную триаду. Профили должны "
            f"отражать реалистичные паттерны для данного типа коррупции.\n\n"
            f"Также создай 2-3 организационные потребности (закупки, контракты, "
            f"кадровые решения), которые будут появляться в разные раунды."
        ),
        schema=_SCENARIO_SCHEMA,
    )

    data = result.data
    agents = _build_agents(data.get("agents", []))
    needs = _build_needs(data.get("needs", []))

    return ScenarioConfig(
        id=scenario_id,
        title=data.get("title", ""),
        description=data.get("description", ""),
        max_rounds=max_rounds,
        seed=seed,
        agents=agents,
        needs=needs,
        governance=GovernanceConfig(mode=governance),
    )


def _build_agents(raw_agents: list[dict]) -> list[AgentProfile]:
    """Собирает профили агентов из сырых данных LLM.

    Args:
        raw_agents: Список словарей с данными агентов.

    Returns:
        Список валидных профилей агентов.
    """
    agents: list[AgentProfile] = []
    for raw in raw_agents:
        hexaco_data = raw.get("hexaco", {})
        dt_data = raw.get("dark_triad", {})

        hexaco = HEXACOProfile(
            honesty_humility=_clamp(hexaco_data.get("honesty_humility", 50)),
            emotionality=_clamp(hexaco_data.get("emotionality", 50)),
            extraversion=_clamp(hexaco_data.get("extraversion", 50)),
            agreeableness=_clamp(hexaco_data.get("agreeableness", 50)),
            conscientiousness=_clamp(hexaco_data.get("conscientiousness", 50)),
            openness=_clamp(hexaco_data.get("openness", 50)),
        )

        dark_triad = DarkTriadProfile(
            narcissism=_clamp(dt_data.get("narcissism", 30)),
            machiavellianism=_clamp(dt_data.get("machiavellianism", 30)),
            psychopathy=_clamp(dt_data.get("psychopathy", 30)),
        )

        personality = AgentPersonality(
            hexaco=hexaco,
            dark_triad=dark_triad,
        )

        agent = AgentProfile(
            id=raw.get("id", f"agent_{len(agents)+1}"),
            name=raw.get("name", f"Агент {len(agents)+1}"),
            position=raw.get("position", "чиновник"),
            personality=personality,
        )
        agents.append(agent)

    return agents


def _build_needs(raw_needs: list[dict]) -> list[Need]:
    """Собирает потребности из сырых данных LLM.

    Args:
        raw_needs: Список словарей с данными потребностей.

    Returns:
        Список валидных потребностей.
    """
    needs: list[Need] = []
    for raw in raw_needs:
        need = Need(
            case_type=raw.get("case_type", "procurement"),
            description=raw.get("description", ""),
            target_agent_id=raw.get("target_agent_id", ""),
            appear_round=int(raw.get("appear_round", 0)),
        )
        needs.append(need)
    return needs


def _clamp(value: int | float, low: int = 0, high: int = 100) -> int:
    """Ограничивает значение в допустимом диапазоне.

    Args:
        value: Исходное значение.
        low: Нижняя граница.
        high: Верхняя граница.

    Returns:
        Целое число в диапазоне [low, high].
    """
    return max(low, min(high, int(value)))
