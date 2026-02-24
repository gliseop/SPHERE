"""Сценарии симуляции S0–S2 и вспомогательные функции."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import (
    AgentProfile,
    Capability,
    Connection,
    GovernanceConfig,
    Need,
    ResourcePool,
    ScenarioConfig,
)
from .enums import GovernanceMode, ScenarioId

MSK = timezone(timedelta(hours=3))

SCENARIOS: dict[ScenarioId, ScenarioConfig] = {
    ScenarioId.S0: ScenarioConfig(
        id=ScenarioId.S0,
        title="Чистая сделка",
        description=(
            "Базовый сценарий: одна закупка, два-три агента, "
            "без предпосылок к коррупции. Контрольный прогон."
        ),
        max_rounds=15,
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 27, 18, 0, tzinfo=MSK),
        seed=42,
        corruption_level=0.0,
        narrative_context=(
            "Государственное учреждение с прозрачными закупочными "
            "процедурами и строгим внутренним контролем."
        ),
        agents=[
            AgentProfile(
                id="off_1",
                name="Иванов А.П.",
                position="начальник отдела обеспечения",
                capabilities=[
                    Capability(
                        action="open_case",
                        case_types=[],
                    ),
                    Capability(
                        action="resolve_case",
                        case_types=[],
                    ),
                ],
                greed=0.2,
                fear=0.5,
                honesty=0.8,
                initial_resources=ResourcePool(
                    budget_limit=10_000_000,
                    staffing_slots=5,
                ),
            ),
            AgentProfile(
                id="biz_1",
                name="Петров С.И.",
                position="директор «ТехСнаб»",
                capabilities=[
                    Capability(
                        action="submit_proposal",
                        case_types=[],
                    ),
                ],
                greed=0.3,
                fear=0.3,
                honesty=0.7,
                competence=0.7,
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
            AgentProfile(
                id="biz_2",
                name="Сидоров В.К.",
                position="директор «ГрадТех»",
                capabilities=[
                    Capability(
                        action="submit_proposal",
                        case_types=[],
                    ),
                ],
                greed=0.3,
                fear=0.4,
                honesty=0.7,
                competence=0.6,
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
        ],
        needs=[
            Need(
                case_type="procurement",
                description="Закупка серверного оборудования",
                target_agent_id="off_1",
                appear_round=0,
                urgency="высокая",
            ),
        ],
        governance=GovernanceConfig(mode=GovernanceMode.G0),
    ),
    ScenarioId.S1: ScenarioConfig(
        id=ScenarioId.S1,
        title="Прямой сговор",
        description=(
            "Закупка с предпосылкой к сговору: чиновник и один из "
            "предпринимателей — бывшие коллеги, жадность повышена."
        ),
        max_rounds=25,
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 27, 18, 0, tzinfo=MSK),
        seed=42,
        corruption_level=0.5,
        narrative_context=(
            "Муниципальное предприятие с ослабленным контролем "
            "и неформальными связями между участниками закупок."
        ),
        agents=[
            AgentProfile(
                id="off_1",
                name="Козлов И.М.",
                position="начальник отдела обеспечения",
                capabilities=[
                    Capability(
                        action="open_case",
                        case_types=[],
                    ),
                    Capability(
                        action="resolve_case",
                        case_types=[],
                    ),
                ],
                greed=0.8,
                fear=0.3,
                honesty=0.2,
                connections=[
                    Connection(
                        target_id="biz_1",
                        name="Петров С.И.",
                        relation="бывший коллега",
                        strength=3.0,
                    ),
                ],
                initial_resources=ResourcePool(
                    budget_limit=10_000_000,
                    staffing_slots=5,
                ),
            ),
            AgentProfile(
                id="biz_1",
                name="Петров С.И.",
                position="директор «ТехСнаб»",
                capabilities=[
                    Capability(
                        action="submit_proposal",
                        case_types=[],
                    ),
                ],
                greed=0.8,
                fear=0.2,
                honesty=0.2,
                competence=0.4,
                connections=[
                    Connection(
                        target_id="off_1",
                        name="Козлов И.М.",
                        relation="бывший коллега",
                        strength=3.0,
                    ),
                ],
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
            AgentProfile(
                id="biz_2",
                name="Сидоров В.К.",
                position="директор «ГрадТех»",
                capabilities=[
                    Capability(
                        action="submit_proposal",
                        case_types=[],
                    ),
                ],
                greed=0.3,
                fear=0.4,
                honesty=0.7,
                competence=0.8,
                initial_resources=ResourcePool(
                    contract_capacity=3,
                ),
            ),
        ],
        needs=[
            Need(
                case_type="procurement",
                description="Закупка серверного оборудования",
                target_agent_id="off_1",
                appear_round=0,
                urgency="высокая",
            ),
        ],
        governance=GovernanceConfig(mode=GovernanceMode.G0),
    ),
    ScenarioId.S2: ScenarioConfig(
        id=ScenarioId.S2,
        title="Кумовство при найме",
        description=(
            "Найм сотрудника с предпосылкой к кумовству: один из "
            "кандидатов — родственник коллеги чиновника."
        ),
        max_rounds=25,
        start_time=datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
        end_time=datetime(2026, 2, 27, 18, 0, tzinfo=MSK),
        seed=42,
        corruption_level=0.7,
        narrative_context=(
            "Государственное ведомство с устоявшимися клановыми "
            "связями и практикой устройства родственников на должности."
        ),
        agents=[
            AgentProfile(
                id="off_1",
                name="Козлов И.М.",
                position="начальник отдела",
                capabilities=[
                    Capability(
                        action="open_case",
                        case_types=[],
                    ),
                    Capability(
                        action="resolve_case",
                        case_types=[],
                    ),
                ],
                greed=0.6,
                fear=0.3,
                honesty=0.3,
                connections=[
                    Connection(
                        target_id="off_2",
                        name="Волков Д.Н.",
                        relation="коллега по отделу",
                        strength=2.5,
                    ),
                ],
                initial_resources=ResourcePool(
                    budget_limit=5_000_000,
                    staffing_slots=5,
                ),
            ),
            AgentProfile(
                id="off_2",
                name="Волков Д.Н.",
                position="специалист отдела",
                capabilities=[],
                greed=0.4,
                fear=0.5,
                honesty=0.5,
                connections=[
                    Connection(
                        target_id="off_1",
                        name="Козлов И.М.",
                        relation="начальник",
                        strength=2.5,
                    ),
                    Connection(
                        target_id="cand_1",
                        name="Волков А.Д.",
                        relation="родственник (племянник)",
                        strength=5.0,
                    ),
                ],
            ),
            AgentProfile(
                id="cand_1",
                name="Волков А.Д.",
                position="кандидат",
                capabilities=[
                    Capability(
                        action="submit_proposal",
                        case_types=[],
                    ),
                ],
                greed=0.3,
                fear=0.5,
                honesty=0.5,
                competence=0.3,
                connections=[
                    Connection(
                        target_id="off_2",
                        name="Волков Д.Н.",
                        relation="дядя",
                        strength=5.0,
                    ),
                ],
            ),
            AgentProfile(
                id="cand_2",
                name="Антонова И.С.",
                position="кандидат",
                capabilities=[
                    Capability(
                        action="submit_proposal",
                        case_types=[],
                    ),
                ],
                greed=0.2,
                fear=0.3,
                honesty=0.8,
                competence=0.9,
            ),
        ],
        needs=[
            Need(
                case_type="hiring",
                description="Нужен инженер в отдел",
                target_agent_id="off_1",
                appear_round=0,
                urgency="средняя",
            ),
        ],
        governance=GovernanceConfig(mode=GovernanceMode.G0),
    ),
}


def get_scenario(scenario_id: ScenarioId) -> ScenarioConfig:
    """Получить конфигурацию сценария.

    Args:
        scenario_id: Идентификатор сценария.

    Returns:
        Конфигурация сценария.

    Raises:
        KeyError: Если сценарий не найден.
    """
    if scenario_id not in SCENARIOS:
        raise KeyError(f"Сценарий {scenario_id} не найден.")
    return SCENARIOS[scenario_id]


def add_governance_agents(
    config: ScenarioConfig, governance: GovernanceMode
) -> ScenarioConfig:
    """Добавить агентов управления в сценарий.

    Для G1+ добавляется аудитор, для G3 — присяжные.

    Args:
        config: Конфигурация сценария.
        governance: Режим управления.

    Returns:
        Обновлённая конфигурация.
    """
    agents = list(config.agents)
    gov = GovernanceConfig(
        mode=governance, jury_size=config.governance.jury_size
    )

    if governance in (
        GovernanceMode.G1,
        GovernanceMode.G2,
        GovernanceMode.G3,
    ):
        auditor_exists = any(
            any(c.action == "audit" for c in a.capabilities)
            for a in agents
        )
        if not auditor_exists:
            agents.append(
                AgentProfile(
                    id="auditor",
                    name="Аудитор",
                    position="независимый аудитор",
                    capabilities=[
                        Capability(action="audit", case_types=[]),
                        Capability(
                            action="file_report", case_types=[]
                        ),
                    ],
                    greed=0.1,
                    fear=0.2,
                    honesty=0.9,
                    immune=True,
                )
            )

    if governance == GovernanceMode.G3:
        for i in range(gov.jury_size):
            juror_id = f"juror_{i}"
            juror_exists = any(a.id == juror_id for a in agents)
            if not juror_exists:
                agents.append(
                    AgentProfile(
                        id=juror_id,
                        name=f"Присяжный {i + 1}",
                        position="присяжный заседатель",
                        capabilities=[
                            Capability(
                                action="vote", case_types=[]
                            ),
                        ],
                        greed=0.2,
                        fear=0.3,
                        honesty=0.7,
                    )
                )

    return config.model_copy(
        update={"agents": agents, "governance": gov}
    )
