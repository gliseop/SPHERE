"""Библиотека шаблонов сценариев v4 на основе реальных коррупционных дел."""

from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel, Field

from magistry_sim.config import (
    AgentProfile,
    Capability,
    Connection,
    GovernanceConfig,
    Need,
    ResourcePool,
    ScenarioConfig,
)
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.event_generator import ScheduledEvent, StochasticConfig
from magistry_sim.personality import (
    AgentPersonality,
    DarkTriadProfile,
    HEXACOProfile,
    NeutralizationTechnique,
)


class TraitRange(BaseModel):
    """Диапазон значений для генерации черты личности.

    Attributes:
        low: Нижняя граница (0-100).
        high: Верхняя граница (0-100).
    """

    model_config = {"extra": "forbid"}

    low: int = Field(ge=0, le=100)
    high: int = Field(ge=0, le=100)


class AgentTemplate(BaseModel):
    """Шаблон агента для генерации конкретного профиля.

    Attributes:
        role_id: Идентификатор роли (используется как префикс id).
        name: Имя агента.
        position: Должность.
        hexaco_ranges: Диапазоны для шести факторов HEXACO.
        dark_triad_ranges: Диапазоны для трёх факторов Тёмной триады.
        neutralization_techniques: Техники нейтрализации, доступные агенту.
        capabilities: Полномочия агента.
        connections_to: Список идентификаторов ролей, с которыми связан агент.
        immune: Неприкосновенность.
    """

    model_config = {"extra": "forbid"}

    role_id: str
    name: str
    position: str
    hexaco_ranges: dict[str, TraitRange]
    dark_triad_ranges: dict[str, TraitRange]
    neutralization_techniques: list[NeutralizationTechnique] = []
    capabilities: list[Capability] = Field(default_factory=list)
    connections_to: list[str] = Field(default_factory=list)
    immune: bool = False


class ScenarioTemplate(BaseModel):
    """Шаблон сценария с привязкой к реальному прототипу.

    Attributes:
        historical_prototype: Описание реального дела-прототипа.
        corruption_pattern: Тип коррупционной схемы.
        agent_templates: Шаблоны агентов для генерации.
        round_count: Количество раундов симуляции.
        scheduled_events: Запланированные события.
        stochastic_config: Настройки стохастических событий.
        needs: Потребности организации.
        governance_default: Режим управления по умолчанию.
    """

    model_config = {"extra": "forbid"}

    historical_prototype: str
    corruption_pattern: str
    agent_templates: list[AgentTemplate]
    round_count: int = 12
    scheduled_events: list[ScheduledEvent] = Field(default_factory=list)
    stochastic_config: StochasticConfig = Field(default_factory=StochasticConfig)
    needs: list[Need] = Field(default_factory=list)
    governance_default: GovernanceMode = GovernanceMode.G0


def _sample_trait(rng: random.Random, trait_range: TraitRange) -> int:
    """Генерирует значение черты в заданном диапазоне.

    Args:
        rng: Генератор случайных чисел.
        trait_range: Диапазон значений.

    Returns:
        Целое значение в пределах диапазона.
    """
    return rng.randint(trait_range.low, trait_range.high)


def _build_personality(
    rng: random.Random, template: AgentTemplate
) -> AgentPersonality:
    """Создаёт конкретный профиль личности из шаблона.

    Args:
        rng: Генератор случайных чисел.
        template: Шаблон агента.

    Returns:
        Сгенерированный профиль личности.
    """
    hexaco_fields = [
        "honesty_humility", "emotionality", "extraversion",
        "agreeableness", "conscientiousness", "openness",
    ]
    hexaco_values = {
        field: _sample_trait(rng, template.hexaco_ranges[field])
        for field in hexaco_fields
    }

    dark_fields = ["narcissism", "machiavellianism", "psychopathy"]
    dark_values = {
        field: _sample_trait(rng, template.dark_triad_ranges[field])
        for field in dark_fields
    }

    return AgentPersonality(
        hexaco=HEXACOProfile(**hexaco_values),
        dark_triad=DarkTriadProfile(**dark_values),
        neutralization_techniques=list(template.neutralization_techniques),
    )


def build_scenario_from_template(
    template: ScenarioTemplate,
    seed: int = 42,
) -> ScenarioConfig:
    """Строит конфигурацию сценария из шаблона.

    Генерирует конкретные значения личности для каждого агента
    в пределах диапазонов, заданных шаблоном, и собирает связи.

    Args:
        template: Шаблон сценария.
        seed: Зерно генератора случайных чисел.

    Returns:
        Полная конфигурация сценария.
    """
    rng = random.Random(seed)
    role_to_id: dict[str, str] = {}
    agents: list[AgentProfile] = []

    for tmpl in template.agent_templates:
        agent_id = tmpl.role_id
        role_to_id[tmpl.role_id] = agent_id
        personality = _build_personality(rng, tmpl)

        connections = [
            Connection(
                target_id=conn_role,
                name=conn_role,
                relation="коллега",
            )
            for conn_role in tmpl.connections_to
        ]

        profile = AgentProfile(
            id=agent_id,
            name=tmpl.name,
            position=tmpl.position,
            capabilities=list(tmpl.capabilities),
            personality=personality,
            immune=tmpl.immune,
            connections=connections,
        )
        agents.append(profile)

    return ScenarioConfig(
        id=ScenarioId.S0,
        title=template.historical_prototype,
        description=f"Сценарий на основе: {template.historical_prototype}",
        max_rounds=template.round_count,
        seed=seed,
        agents=agents,
        needs=list(template.needs),
        governance=GovernanceConfig(mode=template.governance_default),
    )


# --- Библиотека шаблонов ---

_KICKBACK_PROCUREMENT = ScenarioTemplate(
    historical_prototype="Дело о закупках медицинского оборудования (2019)",
    corruption_pattern="kickback",
    round_count=12,
    agent_templates=[
        AgentTemplate(
            role_id="off_1",
            name="Козлов И.М.",
            position="начальник отдела закупок",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=10, high=25),
                "emotionality": TraitRange(low=20, high=40),
                "extraversion": TraitRange(low=60, high=80),
                "agreeableness": TraitRange(low=15, high=30),
                "conscientiousness": TraitRange(low=40, high=60),
                "openness": TraitRange(low=50, high=70),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=70, high=90),
                "machiavellianism": TraitRange(low=75, high=95),
                "psychopathy": TraitRange(low=30, high=50),
            },
            neutralization_techniques=[
                NeutralizationTechnique.EVERYONE_DOES_IT,
                NeutralizationTechnique.CLAIM_OF_ENTITLEMENT,
            ],
            capabilities=[
                Capability(action="open_case", case_types=["procurement"]),
                Capability(action="evaluate_proposal", case_types=["procurement"]),
                Capability(action="resolve_case", case_types=["procurement"]),
            ],
            connections_to=["biz_1"],
        ),
        AgentTemplate(
            role_id="biz_1",
            name="Дроздов А.В.",
            position="директор компании-поставщика",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=15, high=30),
                "emotionality": TraitRange(low=30, high=50),
                "extraversion": TraitRange(low=70, high=85),
                "agreeableness": TraitRange(low=40, high=60),
                "conscientiousness": TraitRange(low=50, high=70),
                "openness": TraitRange(low=40, high=60),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=60, high=80),
                "machiavellianism": TraitRange(low=70, high=85),
                "psychopathy": TraitRange(low=20, high=40),
            },
            neutralization_techniques=[
                NeutralizationTechnique.DEFENSE_OF_NECESSITY,
            ],
            capabilities=[
                Capability(action="submit_proposal", case_types=["procurement"]),
            ],
            connections_to=["off_1"],
        ),
        AgentTemplate(
            role_id="off_2",
            name="Семёнова Е.К.",
            position="заместитель начальника отдела",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=50, high=70),
                "emotionality": TraitRange(low=40, high=60),
                "extraversion": TraitRange(low=40, high=60),
                "agreeableness": TraitRange(low=50, high=70),
                "conscientiousness": TraitRange(low=60, high=80),
                "openness": TraitRange(low=40, high=60),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=20, high=40),
                "machiavellianism": TraitRange(low=30, high=50),
                "psychopathy": TraitRange(low=10, high=25),
            },
            neutralization_techniques=[],
            capabilities=[
                Capability(action="evaluate_proposal", case_types=["procurement"]),
            ],
            connections_to=["off_1"],
        ),
    ],
    scheduled_events=[
        ScheduledEvent(
            round=3,
            event_type="new_procurement_need",
            params={"case_type": "procurement", "budget": 5_000_000},
        ),
    ],
    stochastic_config=StochasticConfig(
        journalist_investigation=0.08,
        citizen_complaint=0.05,
    ),
    needs=[
        Need(
            case_type="procurement",
            description="Закупка медицинского оборудования",
            target_agent_id="off_1",
            appear_round=1,
            urgency="высокая",
        ),
    ],
)

_SINGLE_BIDDER = ScenarioTemplate(
    historical_prototype="Дело о единственном поставщике строительных работ (2020)",
    corruption_pattern="bid_rigging",
    round_count=10,
    agent_templates=[
        AgentTemplate(
            role_id="off_1",
            name="Петров В.С.",
            position="руководитель управления капстроительства",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=20, high=35),
                "emotionality": TraitRange(low=25, high=45),
                "extraversion": TraitRange(low=55, high=75),
                "agreeableness": TraitRange(low=30, high=50),
                "conscientiousness": TraitRange(low=45, high=65),
                "openness": TraitRange(low=35, high=55),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=55, high=75),
                "machiavellianism": TraitRange(low=65, high=85),
                "psychopathy": TraitRange(low=25, high=45),
            },
            neutralization_techniques=[
                NeutralizationTechnique.DENIAL_OF_INJURY,
                NeutralizationTechnique.APPEAL_TO_HIGHER_LOYALTIES,
            ],
            capabilities=[
                Capability(action="open_case", case_types=["procurement"]),
                Capability(action="resolve_case", case_types=["procurement"]),
            ],
            connections_to=["biz_1"],
        ),
        AgentTemplate(
            role_id="biz_1",
            name="Крылов Р.Н.",
            position="владелец строительной компании",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=10, high=25),
                "emotionality": TraitRange(low=15, high=35),
                "extraversion": TraitRange(low=65, high=80),
                "agreeableness": TraitRange(low=25, high=45),
                "conscientiousness": TraitRange(low=55, high=75),
                "openness": TraitRange(low=30, high=50),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=75, high=90),
                "machiavellianism": TraitRange(low=80, high=95),
                "psychopathy": TraitRange(low=40, high=60),
            },
            neutralization_techniques=[
                NeutralizationTechnique.CONDEMNATION_OF_CONDEMNERS,
                NeutralizationTechnique.EVERYONE_DOES_IT,
            ],
            capabilities=[
                Capability(action="submit_proposal", case_types=["procurement"]),
            ],
            connections_to=["off_1"],
        ),
    ],
    needs=[
        Need(
            case_type="procurement",
            description="Строительство школы",
            target_agent_id="off_1",
            appear_round=1,
        ),
    ],
)

_HIRING_NEPOTISM = ScenarioTemplate(
    historical_prototype="Дело о трудоустройстве родственников чиновника (2021)",
    corruption_pattern="nepotism",
    round_count=8,
    agent_templates=[
        AgentTemplate(
            role_id="off_1",
            name="Громов С.А.",
            position="начальник кадровой службы",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=25, high=40),
                "emotionality": TraitRange(low=40, high=60),
                "extraversion": TraitRange(low=50, high=70),
                "agreeableness": TraitRange(low=55, high=75),
                "conscientiousness": TraitRange(low=50, high=70),
                "openness": TraitRange(low=30, high=50),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=40, high=60),
                "machiavellianism": TraitRange(low=50, high=70),
                "psychopathy": TraitRange(low=15, high=30),
            },
            neutralization_techniques=[
                NeutralizationTechnique.APPEAL_TO_HIGHER_LOYALTIES,
                NeutralizationTechnique.DENIAL_OF_VICTIM,
            ],
            capabilities=[
                Capability(action="open_case", case_types=["hiring"]),
                Capability(action="evaluate_proposal", case_types=["hiring"]),
                Capability(action="resolve_case", case_types=["hiring"]),
            ],
            connections_to=["cand_1"],
        ),
        AgentTemplate(
            role_id="cand_1",
            name="Громова М.С.",
            position="кандидат на должность",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=40, high=60),
                "emotionality": TraitRange(low=50, high=70),
                "extraversion": TraitRange(low=40, high=60),
                "agreeableness": TraitRange(low=50, high=70),
                "conscientiousness": TraitRange(low=30, high=50),
                "openness": TraitRange(low=40, high=60),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=30, high=50),
                "machiavellianism": TraitRange(low=20, high=40),
                "psychopathy": TraitRange(low=5, high=20),
            },
            neutralization_techniques=[
                NeutralizationTechnique.CLAIM_OF_ENTITLEMENT,
            ],
            capabilities=[
                Capability(action="submit_proposal", case_types=["hiring"]),
            ],
            connections_to=["off_1"],
        ),
        AgentTemplate(
            role_id="cand_2",
            name="Алексеев Д.П.",
            position="кандидат на должность",
            hexaco_ranges={
                "honesty_humility": TraitRange(low=60, high=80),
                "emotionality": TraitRange(low=40, high=60),
                "extraversion": TraitRange(low=50, high=70),
                "agreeableness": TraitRange(low=50, high=70),
                "conscientiousness": TraitRange(low=70, high=90),
                "openness": TraitRange(low=50, high=70),
            },
            dark_triad_ranges={
                "narcissism": TraitRange(low=10, high=25),
                "machiavellianism": TraitRange(low=10, high=25),
                "psychopathy": TraitRange(low=5, high=15),
            },
            neutralization_techniques=[],
            capabilities=[
                Capability(action="submit_proposal", case_types=["hiring"]),
            ],
            connections_to=[],
        ),
    ],
    needs=[
        Need(
            case_type="hiring",
            description="Набор специалиста в отдел",
            target_agent_id="off_1",
            appear_round=1,
        ),
    ],
)


SCENARIO_LIBRARY: dict[str, ScenarioTemplate] = {
    "kickback_procurement": _KICKBACK_PROCUREMENT,
    "single_bidder": _SINGLE_BIDDER,
    "hiring_nepotism": _HIRING_NEPOTISM,
}
