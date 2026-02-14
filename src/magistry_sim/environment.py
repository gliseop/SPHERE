"""Среда исполнения симуляции."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .agents import AgentRunner, MockAgentRunner
from .cases import CASE_REGISTRY, apply_transition, check_condition
from .config import ScenarioConfig
from .context import build_situation
from .enums import GovernanceMode
from .reputation import (
    apply_growth,
    compute_round_growth,
    freeze,
)
from .scenarios import add_governance_agents
from .state import ReputationRecord, WorldState
from .tools import current_agent_id, current_runner, current_state
from .tools.actions import (
    cast_vote,
    file_report,
    open_case,
    resolve_case,
    submit_proposal,
    add_note,
)
from .tools.communication import talk_to


TOOL_DISPATCH = {
    "open_case": open_case,
    "submit_proposal": submit_proposal,
    "add_note": add_note,
    "resolve_case": resolve_case,
    "file_report": file_report,
    "cast_vote": cast_vote,
    "talk_to": talk_to,
}


@dataclass
class SimulationResult:
    """Результат симуляции."""

    scenario_id: str
    governance: str
    seed: int
    rounds_completed: int
    cases: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    final_reputation: dict = field(default_factory=dict)
    agents: list = field(default_factory=list)
    messages: list = field(default_factory=list)


class Environment:
    """Среда симуляции.

    Управляет временем, формирует сводки, применяет правила
    конечных автоматов. Не принимает решений за агентов.
    """

    def __init__(
        self,
        scenario: ScenarioConfig,
        governance: GovernanceMode | None = None,
        runner: AgentRunner | None = None,
        seed: int | None = None,
    ) -> None:
        gov = governance or scenario.governance.mode
        self._scenario = add_governance_agents(scenario, gov)
        self._runner = runner or MockAgentRunner()
        self._seed = seed or scenario.seed
        self._rng = random.Random(self._seed)
        self._state = WorldState()
        self._max_rounds = scenario.max_rounds
        self._init_state()

    def _init_state(self) -> None:
        """Инициализировать состояние мира из конфигурации."""
        for profile in self._scenario.agents:
            self._state.agents[profile.id] = profile
            self._state.graph.add_agent(profile.id)
            self._state.reputation[profile.id] = ReputationRecord()

            res = profile.initial_resources
            self._state.resources.init_agent(
                profile.id,
                budget_limit=res.budget_limit,
                staffing_slots=res.staffing_slots,
                contract_capacity=res.contract_capacity,
            )

            for conn in profile.connections:
                self._state.graph.add_connection(
                    profile.id,
                    conn.target_id,
                    relation=conn.relation,
                    strength=conn.strength,
                )

    def run(self) -> SimulationResult:
        """Запустить симуляцию.

        Returns:
            Результат симуляции.
        """
        for round_num in range(self._max_rounds):
            self._state.round = round_num
            self._generate_needs(round_num)
            self._check_conditional_transitions()

            agent_ids = list(self._state.agents.keys())
            self._rng.shuffle(agent_ids)

            for agent_id in agent_ids:
                self._run_agent_turn(agent_id)

            self._apply_round_end_effects()

        return self._build_result()

    def _generate_needs(self, round_num: int) -> None:
        """Сгенерировать потребности для текущего раунда.

        Args:
            round_num: Номер раунда.
        """
        for need in self._scenario.needs:
            if need.appear_round == round_num:
                already = any(
                    n.case_type == need.case_type
                    and n.target_agent_id == need.target_agent_id
                    for n in self._state.active_needs
                )
                if not already:
                    self._state.active_needs.append(need)

    def _run_agent_turn(self, agent_id: str) -> None:
        """Выполнить ход одного агента.

        Args:
            agent_id: Идентификатор агента.
        """
        token_state = current_state.set(self._state)
        token_agent = current_agent_id.set(agent_id)
        token_runner = current_runner.set(self._runner)

        try:
            situation = build_situation(agent_id, self._state)

            available_tools = list(TOOL_DISPATCH.keys())
            actions = self._runner.run_turn(
                agent_id=agent_id,
                situation=situation,
                tools=available_tools,
                state=self._state,
            )

            for action in actions:
                tool_name = action.get("tool", "")
                args = action.get("args", {})
                func = TOOL_DISPATCH.get(tool_name)
                if func:
                    try:
                        func(**args)
                    except TypeError:
                        pass
        finally:
            current_state.reset(token_state)
            current_agent_id.reset(token_agent)
            current_runner.reset(token_runner)

    def _check_conditional_transitions(self) -> None:
        """Проверить и применить условные переходы конечных автоматов."""
        for case in list(self._state.cases.values()):
            schema = CASE_REGISTRY.get(case.case_type)
            if schema is None:
                continue

            cond = schema.conditional_transitions.get(case.stage)
            if cond:
                target_stage, condition = cond
                if check_condition(
                    condition, case, self._state.round
                ):
                    apply_transition(case, target_stage)
                    self._state.event_log.log(
                        round=self._state.round,
                        event_type="auto_transition",
                        payload={
                            "case_id": case.id,
                            "from": case.stage,
                            "to": target_stage,
                            "condition": condition,
                        },
                    )

    def _apply_round_end_effects(self) -> None:
        """Применить эффекты конца раунда."""
        governance = self._scenario.governance.mode

        # Пересчёт репутации
        for agent_id, rep in self._state.reputation.items():
            cases_resolved = len([
                e
                for e in self._state.event_log.get_events(
                    event_type="case_resolved",
                    agent_id=agent_id,
                    round=self._state.round,
                )
            ])
            growth = compute_round_growth(
                rep, cases_resolved=cases_resolved
            )
            apply_growth(rep, growth)

        # Обработка отчётов аудитора (G2+)
        if governance in (GovernanceMode.G2, GovernanceMode.G3):
            reports = self._state.event_log.get_events(
                event_type="report_filed", round=self._state.round
            )
            for report in reports:
                rec = report.payload.get("recommendation", "")
                case_id = report.payload.get("case_id", "")
                case = self._state.cases.get(case_id)
                if case is None:
                    continue

                if rec == "frozen":
                    owner_rep = self._state.reputation.get(
                        case.owner_id
                    )
                    if owner_rep:
                        freeze(owner_rep)
                    self._state.event_log.log(
                        round=self._state.round,
                        event_type="reputation_frozen",
                        agent_id=case.owner_id,
                        payload={"case_id": case_id},
                    )

                elif (
                    rec == "tribunal"
                    and governance == GovernanceMode.G3
                ):
                    self._form_tribunal(case_id, case.owner_id)

        # Проверка кворума трибуналов
        for case in self._state.cases.values():
            if (
                case.case_type == "investigation"
                and case.stage == "tribunal"
            ):
                if check_condition(
                    "quorum_reached", case, self._state.round
                ):
                    apply_transition(case, "verdict")
                    guilty = sum(
                        1
                        for v in case.votes
                        if "виновен" in v.verdict.lower()
                    )
                    not_guilty = len(case.votes) - guilty
                    verdict = (
                        "виновен"
                        if guilty > not_guilty
                        else "невиновен"
                    )
                    case.decision = verdict
                    case.closed_at = self._state.round

                    self._state.event_log.log(
                        round=self._state.round,
                        event_type="tribunal_verdict",
                        payload={
                            "case_id": case.id,
                            "verdict": verdict,
                            "guilty_votes": guilty,
                            "not_guilty_votes": not_guilty,
                        },
                    )

    def _form_tribunal(
        self, case_id: str, accused_id: str
    ) -> None:
        """Сформировать дело трибунала.

        Args:
            case_id: Идентификатор исходного дела.
            accused_id: Обвиняемый.
        """
        from .cases import Case

        tribunal_id = self._state.new_case_id()
        tribunal = Case(
            id=tribunal_id,
            case_type="investigation",
            title=f"Трибунал по делу {case_id}",
            description=(
                f"Расследование нарушений по делу {case_id}. "
                f"Обвиняемый: {accused_id}."
            ),
            owner_id="auditor",
            stage="tribunal",
            params=f"source_case={case_id},accused={accused_id}",
            created_at=self._state.round,
        )
        self._state.cases[tribunal_id] = tribunal

        self._state.event_log.log(
            round=self._state.round,
            event_type="tribunal_formed",
            payload={
                "tribunal_id": tribunal_id,
                "source_case": case_id,
                "accused": accused_id,
            },
        )

    def _build_result(self) -> SimulationResult:
        """Построить результат симуляции.

        Returns:
            Результат.
        """
        cases_data = {}
        for cid, case in self._state.cases.items():
            cases_data[cid] = case.model_dump()

        events_data = [
            e.model_dump()
            for e in self._state.event_log.all_events
        ]

        rep_data = {
            aid: {"score": r.score, "frozen": r.frozen}
            for aid, r in self._state.reputation.items()
        }

        msg_data = [m.model_dump() for m in self._state.messages]

        return SimulationResult(
            scenario_id=self._scenario.id.value,
            governance=self._scenario.governance.mode.value,
            seed=self._seed,
            rounds_completed=self._max_rounds,
            cases=cases_data,
            events=events_data,
            final_reputation=rep_data,
            agents=[a.id for a in self._scenario.agents],
            messages=msg_data,
        )

    @property
    def state(self) -> WorldState:
        """Текущее состояние мира."""
        return self._state
