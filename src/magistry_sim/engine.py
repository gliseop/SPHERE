from __future__ import annotations

from dataclasses import dataclass
from random import Random

import networkx as nx

from magistry_sim.auditor import Auditor
from magistry_sim.enums import AgentRole, GovernanceMode, ScenarioId
from magistry_sim.llm import LLMProvider
from magistry_sim.memory import MemoryItemType
from magistry_sim.models import (
    Agent,
    GovernanceConfig,
    NegotiationResult,
    PublicTenderData,
    RunResult,
    Scenario,
    TickOutcome,
)
from magistry_sim.negotiation import NegotiationProtocol
from magistry_sim.reputation import apply_growth, compute_tick_growth, freeze
from magistry_sim.scenarios import get_scenario
from magistry_sim.tribunal import Tribunal
from magistry_sim.world import World


@dataclass(slots=True)
class SimulationEngine:
    """Движок симуляции гибридного управления.

    Args:
        governance: Конфигурация режима управления (G0-G3).
        llm: LLM-провайдер (реальный или mock).
    """

    governance: GovernanceConfig
    llm: LLMProvider

    def _spawn_official(self, *, world: World, rng: Random) -> Agent:
        """Создать нового чиновника и добавить в мир."""
        idx = int(world.state.get("next_official_index", 0))
        while f"off_{idx}" in world.agents:
            idx += 1
        world.state["next_official_index"] = idx + 1

        agent_id = f"off_{idx}"
        agent = Agent(
            id=agent_id,
            name=f"Official {idx}",
            role=AgentRole.OFFICIAL,
            greed=rng.random(),
            fear=rng.random(),
            honesty=rng.random(),
            competence=rng.random(),
        )
        world.agents[agent_id] = agent
        world.social_graph.add_node(agent_id)
        return agent

    def _spawn_contractor(self, *, world: World, rng: Random) -> Agent:
        """Создать нового подрядчика и добавить в мир."""
        idx = int(world.state.get("next_contractor_index", 0))
        while f"biz_{idx}" in world.agents:
            idx += 1
        world.state["next_contractor_index"] = idx + 1

        agent_id = f"biz_{idx}"
        agent = Agent(
            id=agent_id,
            name=f"Business {idx}",
            role=AgentRole.CONTRACTOR,
            greed=rng.random(),
            fear=rng.random(),
            honesty=rng.random(),
            competence=rng.random(),
        )
        world.agents[agent_id] = agent
        world.social_graph.add_node(agent_id)
        return agent

    async def run(self, *, scenario_id: ScenarioId, seed: int | None = None, ticks: int | None = None) -> RunResult:
        """Запустить симуляцию сценария.

        Args:
            scenario_id: Идентификатор сценария (S0-S9).
            seed: Seed для RNG (по умолчанию из сценария).
            ticks: Число тиков (по умолчанию из сценария).

        Returns:
            RunResult с исходами всех тиков.
        """
        scenario = get_scenario(scenario_id)
        run_seed = scenario.seed if seed is None else seed
        rng = Random(run_seed)

        world = self._init_world(scenario=scenario, rng=rng)

        auditor = Auditor(config=self.governance, llm=self.llm)
        tribunal = Tribunal(self.governance)

        outcomes: list[TickOutcome] = []
        num_ticks = scenario.ticks if ticks is None else ticks
        for tick in range(num_ticks):
            outcome = await self._run_tick(world=world, scenario=scenario, tick=tick, rng=rng, auditor=auditor, tribunal=tribunal)
            outcomes.append(outcome)

        return RunResult(
            scenario=scenario.id,
            governance=self.governance.mode,
            seed=run_seed,
            ticks=num_ticks,
            outcomes=outcomes,
            final_agents={k: v for k, v in world.agents.items()},
            events=list(world.events),
        )

    def _init_world(self, *, scenario: Scenario, rng: Random) -> World:
        """Инициализировать мир с агентами и социальным графом."""
        agents: dict[str, Agent] = {}
        for i in range(scenario.num_officials):
            agent_id = f"off_{i}"
            agents[agent_id] = Agent(
                id=agent_id,
                name=f"Official {i}",
                role=AgentRole.OFFICIAL,
                greed=rng.random(),
                fear=rng.random(),
                honesty=rng.random(),
                competence=rng.random(),
            )

        for i in range(scenario.num_contractors):
            agent_id = f"biz_{i}"
            agents[agent_id] = Agent(
                id=agent_id,
                name=f"Business {i}",
                role=AgentRole.CONTRACTOR,
                greed=rng.random(),
                fear=rng.random(),
                honesty=rng.random(),
                competence=rng.random(),
            )

        # Иммунный актор (условный "президент") — официальный, но неподсудный.
        if scenario.include_immune_influencer:
            immune_id = "immune_0"
            agents[immune_id] = Agent(
                id=immune_id,
                name="Immune Leader",
                role=AgentRole.OFFICIAL,
                greed=0.9,
                fear=0.1,
                honesty=0.1,
                competence=0.8,
                immune=True,
            )

        social_graph = nx.Graph()
        for agent_id in agents:
            social_graph.add_node(agent_id)

        world = World(agents=agents, social_graph=social_graph)
        world.state.update(
            next_official_index=scenario.num_officials,
            next_contractor_index=scenario.num_contractors,
            relationship_strength=scenario.relationship_strength,
            explicit_bribe=scenario.explicit_bribe,
            mask_language=scenario.mask_language,
            enable_carousel=scenario.enable_carousel,
            enable_timing_anomaly=scenario.enable_timing_anomaly,
            noise_level=scenario.noise_level,
            enable_adaptation=scenario.enable_adaptation,
            enable_bottom_up_signal=scenario.enable_bottom_up_signal,
            include_immune_influencer=scenario.include_immune_influencer,
        )
        return world

    def _choose_lpr(self, world: World, rng: Random, *, tick: int) -> str:
        """Выбрать ЛПР (лицо, принимающее решение) для тендера."""
        candidates = [
            a
            for a in world.agents.values()
            if a.role == AgentRole.OFFICIAL and a.eligible_for_lpr
        ]
        if not candidates:
            spawned = self._spawn_official(world=world, rng=rng)
            world.log(tick, "market_entry", agent_id=spawned.id, role=spawned.role.value, reason="no_eligible_lpr")
            candidates = [spawned]
        return rng.choice(candidates).id

    def _get_contractors_for_tender(self, world: World, rng: Random, *, tick: int, min_required: int = 2) -> list[Agent]:
        """Получить подрядчиков для тендера, добавляя новых при нехватке."""
        contractors = [
            a
            for a in world.agents.values()
            if a.role == AgentRole.CONTRACTOR and a.eligible_for_contracts
        ]
        if len(contractors) < min_required:
            for _ in range(min_required - len(contractors)):
                spawned = self._spawn_contractor(world=world, rng=rng)
                world.log(
                    tick,
                    "market_entry",
                    agent_id=spawned.id,
                    role=spawned.role.value,
                    reason="insufficient_eligible_contractors",
                )
                contractors.append(spawned)
        return contractors

    async def _run_tick(
        self,
        *,
        world: World,
        scenario: Scenario,
        tick: int,
        rng: Random,
        auditor: Auditor,
        tribunal: Tribunal,
    ) -> TickOutcome:
        """Выполнить один тик симуляции.

        Фазы:
        1. Выбор ЛПР
        2. Получение подрядчиков
        3. Shadow layer (переговоры)
        4. Official layer (тендер)
        5. Governance layer (аудит → репутация → трибунал)
        """
        lpr_id = self._choose_lpr(world, rng, tick=tick)
        contractors = self._get_contractors_for_tender(world, rng, tick=tick, min_required=2)

        enable_carousel = bool(world.state.get("enable_carousel", False))
        target_contractor = rng.choice(contractors).id if contractors else "biz_0"

        # Социальная связь ЛПР ↔ целевой подрядчик.
        world.social_graph.add_edge(
            lpr_id,
            target_contractor,
            strength=float(world.state.get("relationship_strength", 0.0)),
            kind="school/work",
        )

        # Shadow layer: многораундовые LLM-переговоры
        negotiation = NegotiationProtocol(self.llm)
        negotiation_result_initial: NegotiationResult = await negotiation.negotiate(
            lpr=world.agents[lpr_id],
            contractor=world.agents[target_contractor],
            scenario=scenario,
            tick=tick,
            lpr_memory=world.get_memory(lpr_id),
            contractor_memory=world.get_memory(target_contractor),
            rng=rng,
        )

        # Official layer: генерируем предложения.
        bids: dict[str, float] = {}
        for contractor in contractors:
            base = scenario.tender_budget
            price_multiplier = 0.85 + (1.0 - contractor.competence) * 0.30 + rng.random() * 0.10
            bids[contractor.id] = base * price_multiplier

        fair_winner_id = min(bids.items(), key=lambda kv: kv[1])[0] if bids else target_contractor

        # Коррупция определяется РЕЗУЛЬТАТОМ переговоров
        corruption = negotiation_result_initial.deal_reached

        # Карусель: переопределяет победителя циклом
        if enable_carousel:
            idx = tick % max(1, len(contractors))
            target_contractor = contractors[idx].id
            corruption = True
            # Социальная связь ЛПР ↔ фактический победитель (карусель).
            world.social_graph.add_edge(
                lpr_id,
                target_contractor,
                strength=float(world.state.get("relationship_strength", 0.0)),
                kind="school/work",
            )

        actual_winner_id = target_contractor if corruption else fair_winner_id

        negotiation_result = negotiation_result_initial
        if enable_carousel and actual_winner_id != negotiation_result_initial.contractor_id:
            world.log(
                tick, "shadow_negotiation_initial",
                lpr_id=lpr_id,
                contractor_id=negotiation_result_initial.contractor_id,
                deal_reached=negotiation_result_initial.deal_reached,
                kickback_percent=negotiation_result_initial.kickback_percent,
                total_rounds=negotiation_result_initial.total_rounds,
                messages=[m.model_dump() for m in negotiation_result_initial.messages],
            )
            negotiation_result = await negotiation.negotiate(
                lpr=world.agents[lpr_id],
                contractor=world.agents[actual_winner_id],
                scenario=scenario,
                tick=tick,
                lpr_memory=world.get_memory(lpr_id),
                contractor_memory=world.get_memory(actual_winner_id),
                force_deal_reached=True,
                rng=rng,
            )

        world.log(
            tick, "shadow_negotiation",
            lpr_id=lpr_id,
            contractor_id=negotiation_result.contractor_id,
            deal_reached=negotiation_result.deal_reached,
            kickback_percent=negotiation_result.kickback_percent,
            total_rounds=negotiation_result.total_rounds,
            messages=[m.model_dump() for m in negotiation_result.messages],
        )

        # --- Memory: записать результат тендера для каждого подрядчика ---
        for contractor in contractors:
            mem = world.get_memory(contractor.id)
            if contractor.id == actual_winner_id:
                mem.add_event(tick, MemoryItemType.TENDER_WIN,
                              f"Выиграл тендер на {scenario.tender_budget:,.0f}",
                              budget=scenario.tender_budget)
            else:
                mem.add_event(tick, MemoryItemType.TENDER_LOSS,
                              f"Проиграл тендер на {scenario.tender_budget:,.0f}",
                              budget=scenario.tender_budget)

        # Behavioral: тайминг-аномалия
        timing_anomaly = bool(world.state.get("enable_timing_anomaly", False)) and rng.random() < 0.7
        world.state["timing_anomaly"] = timing_anomaly

        # Bottom-up сигнал: честный чиновник "стучит" (в baseline теряется).
        whistleblower_ids: list[str] = []
        if bool(world.state.get("enable_bottom_up_signal", False)):
            whistleblowers = [
                a
                for a in world.agents.values()
                if a.role == AgentRole.OFFICIAL and a.id != lpr_id and a.honesty > 0.75 and not a.immune
            ]
            if whistleblowers and rng.random() < 0.6:
                wb = rng.choice(whistleblowers)
                whistleblower_ids.append(wb.id)
                world.log(tick, "whistleblower_signal", from_id=wb.id, about_lpr=lpr_id, about_contractor=actual_winner_id)
                world.state["explicit_bribe"] = True

        world.log(
            tick,
            "official_award",
            lpr_id=lpr_id,
            fair_winner_id=fair_winner_id,
            actual_winner_id=actual_winner_id,
            corruption=corruption,
            bid_fair=float(bids.get(fair_winner_id, 0.0)),
            bid_actual=float(bids.get(actual_winner_id, 0.0)),
        )

        # Governance layer
        audit_risk: float | None = None
        audit_flagged = False
        tribunal_triggered = False
        tribunal_guilty: bool | None = None

        if self.governance.mode != GovernanceMode.G0_BASELINE:
            # Формируем PublicTenderData (только то, что видит аудитор)
            edge = world.social_graph.get_edge_data(lpr_id, actual_winner_id) or {}
            public_messages = negotiation_result.messages if negotiation_result.contractor_id == actual_winner_id else []
            public_data = PublicTenderData(
                tick=tick,
                lpr_id=lpr_id,
                contractor_id=actual_winner_id,
                messages=public_messages,
                bids=bids,
                winner_id=actual_winner_id,
                tender_budget=scenario.tender_budget,
                social_tie_strength=float(edge.get("strength", 0.0)),
                social_tie_kind=edge.get("kind"),
                response_times_ms=[m.response_time_ms for m in public_messages],
                noise_level=float(world.state.get("noise_level", 0.0)),
            )
            report = await auditor.assess(data=public_data)
            audit_risk = report.risk_score
            audit_flagged = report.risk_score >= self.governance.risk_threshold
            world.log(tick, "audit_report", **report.model_dump(exclude={"tick"}))

            # --- Memory: записать результат аудита ---
            if audit_flagged:
                for aid in (lpr_id, actual_winner_id):
                    mem = world.get_memory(aid)
                    mem.add_event(tick, MemoryItemType.AUDIT_FLAG,
                                  f"Аудитор пометил: risk={audit_risk:.2f}",
                                  risk_score=audit_risk)
            else:
                for aid in (lpr_id, actual_winner_id):
                    mem = world.get_memory(aid)
                    mem.add_event(tick, MemoryItemType.AUDIT_CLEAR,
                                  f"Аудит пройден: risk={audit_risk:.2f}",
                                  risk_score=audit_risk)

            if self.governance.mode in {GovernanceMode.G2_AUDIT_REPUTATION, GovernanceMode.G3_FULL}:
                lpr = world.agents[lpr_id]
                winner = world.agents[actual_winner_id]

                before_lpr = lpr.social_capital.total
                before_winner = winner.social_capital.total

                if audit_flagged:
                    freeze(lpr)
                    freeze(winner)
                else:
                    # Используем compute_tick_growth для начисления капитала
                    winner_growth = compute_tick_growth(
                        winner, tender_won=True,
                        tender_budget=scenario.tender_budget, tick=tick,
                    )
                    apply_growth(winner, **winner_growth)

                    lpr_growth = compute_tick_growth(
                        lpr, tender_won=False,
                        tender_budget=scenario.tender_budget, tick=tick,
                    )
                    apply_growth(lpr, **lpr_growth)

                world.log(
                    tick,
                    "reputation_update",
                    lpr_id=lpr_id,
                    winner_id=actual_winner_id,
                    lpr_before=before_lpr,
                    lpr_after=lpr.social_capital.total,
                    winner_before=before_winner,
                    winner_after=winner.social_capital.total,
                    frozen_lpr=lpr.frozen,
                    frozen_winner=winner.frozen,
                )

            if self.governance.mode == GovernanceMode.G3_FULL and report.critical:
                tribunal_triggered = True
                tribunal_result = tribunal.run(world=world, tick=tick, report=report, rng=rng)
                tribunal_guilty = tribunal_result.guilty

                # Начислить social capital присяжным
                if self.governance.mode in {GovernanceMode.G2_AUDIT_REPUTATION, GovernanceMode.G3_FULL}:
                    for juror_id in tribunal_result.juror_ids:
                        juror = world.agents[juror_id]
                        juror_growth = compute_tick_growth(
                            juror, tender_won=False, tender_budget=0,
                            is_juror=True, tick=tick,
                        )
                        apply_growth(juror, **juror_growth)

        # Whistleblower social capital bonus
        if whistleblower_ids and self.governance.mode in {GovernanceMode.G2_AUDIT_REPUTATION, GovernanceMode.G3_FULL}:
            for wb_id in whistleblower_ids:
                wb_agent = world.agents[wb_id]
                wb_growth = compute_tick_growth(
                    wb_agent, tender_won=False, tender_budget=0,
                    whistleblower=True, tick=tick,
                )
                apply_growth(wb_agent, **wb_growth)

        # --- Memory: компакция при пороге ---
        for agent_id in world.agents:
            mem = world.get_memory(agent_id)
            if mem.needs_compaction():
                await mem.compact(self.llm)

        # Адаптация к freeze
        if bool(world.state.get("enable_adaptation", False)):
            lpr = world.agents[lpr_id]
            if lpr.frozen:
                old = lpr.greed
                lpr.greed = max(0.0, lpr.greed - 0.10)
                world.log(tick, "adaptation", agent_id=lpr_id, greed_before=old, greed_after=lpr.greed)

        # Возвращаем состояние explicit_bribe обратно к сценарию (после временных сигналов).
        world.state["explicit_bribe"] = scenario.explicit_bribe

        return TickOutcome(
            tick=tick,
            tender_budget=scenario.tender_budget,
            fair_winner_id=fair_winner_id,
            actual_winner_id=actual_winner_id,
            corruption=corruption,
            negotiation=negotiation_result,
            audit_risk=audit_risk,
            audit_flagged=audit_flagged,
            tribunal_triggered=tribunal_triggered,
            tribunal_guilty=tribunal_guilty,
        )
