"""Тесты модуля иерархического планирования."""

import pytest
from unittest.mock import MagicMock
from magistry_sim.memory import MemoryStream
from magistry_sim.planning import (
    AgentPlan,
    generate_strategic_plan,
    generate_tactical_plan,
    STRATEGIC_PLAN_INTERVAL,
)


class TestAgentPlan:
    def test_create_plan(self):
        plan = AgentPlan(
            strategic_goals=["Получить контракт по закупке D-001"],
            tactical_steps=["Отправить предложение off_1"],
            last_strategic_round=0,
        )
        assert len(plan.strategic_goals) == 1
        assert len(plan.tactical_steps) == 1

    def test_needs_strategic_update(self):
        plan = AgentPlan(
            strategic_goals=[],
            tactical_steps=[],
            last_strategic_round=0,
        )
        assert plan.needs_strategic_update(current_round=0)  # first round
        assert not plan.needs_strategic_update(current_round=2)
        assert plan.needs_strategic_update(current_round=STRATEGIC_PLAN_INTERVAL)


class TestPlanGeneration:
    def test_generate_strategic_plan(self):
        stream = MemoryStream(agent_id="biz_1")
        stream.add(
            content="Открыта закупка серверного оборудования D-001",
            importance=7.0,
            kind="observation",
            round_num=0,
            embedding=[0.5, 0.5],
        )

        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text='["Получить контракт D-001", "Укрепить связь с off_1"]'
        )

        goals = generate_strategic_plan(
            stream=stream,
            llm=mock_llm,
            agent_role="предприниматель",
            current_round=0,
        )
        assert len(goals) >= 1
        assert isinstance(goals[0], str)

    def test_generate_tactical_plan(self):
        stream = MemoryStream(agent_id="biz_1")
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text='["Подать заявку на D-001", "Связаться с off_1"]'
        )

        steps = generate_tactical_plan(
            stream=stream,
            llm=mock_llm,
            strategic_goals=["Получить контракт D-001"],
            current_round=1,
        )
        assert len(steps) >= 1
