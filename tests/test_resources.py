"""Тесты расширенной экономической модели: обслуживание, доход, фонд взяток."""

import pytest
from magistry_sim.resources import AgentResources, ResourceManager, apply_maintenance


class TestMaintenance:
    """Тесты расхода на обслуживание."""

    def test_apply_maintenance_spends_budget(self):
        """apply_maintenance списывает maintenance_cost из бюджета."""
        res = AgentResources(budget_limit=1000.0, maintenance_cost=50.0)
        apply_maintenance(res)
        assert res.budget_spent == 50.0

    def test_apply_maintenance_zero_cost(self):
        """При нулевой стоимости обслуживания бюджет не меняется."""
        res = AgentResources(budget_limit=1000.0, maintenance_cost=0.0)
        apply_maintenance(res)
        assert res.budget_spent == 0.0

    def test_apply_maintenance_does_not_exceed_budget(self):
        """Обслуживание не превышает доступный бюджет."""
        res = AgentResources(
            budget_limit=100.0, budget_spent=80.0, maintenance_cost=50.0,
        )
        apply_maintenance(res)
        # Может списать максимум 20.0 (остаток)
        assert res.budget_spent <= res.budget_limit


class TestBribeFund:
    """Тесты фонда взяток."""

    def test_add_to_bribe_fund(self):
        """Фонд взяток увеличивается при добавлении средств."""
        res = AgentResources()
        res.add_to_bribe_fund(100.0)
        assert res.bribe_fund == 100.0

    def test_add_to_bribe_fund_accumulates(self):
        """Средства в фонде накапливаются."""
        res = AgentResources()
        res.add_to_bribe_fund(100.0)
        res.add_to_bribe_fund(200.0)
        assert res.bribe_fund == 300.0

    def test_has_bribe_fund_true(self):
        """has_bribe_fund возвращает True при наличии средств."""
        res = AgentResources()
        res.add_to_bribe_fund(500.0)
        assert res.has_bribe_fund()

    def test_has_bribe_fund_false(self):
        """has_bribe_fund возвращает False при пустом фонде."""
        res = AgentResources()
        assert not res.has_bribe_fund()

    def test_clear_bribe_fund(self):
        """clear_bribe_fund обнуляет фонд."""
        res = AgentResources()
        res.add_to_bribe_fund(500.0)
        res.clear_bribe_fund()
        assert res.bribe_fund == 0.0
        assert not res.has_bribe_fund()


class TestRevenuePerContract:
    """Тесты дохода за контракт."""

    def test_revenue_per_contract_field(self):
        """Поле revenue_per_contract доступно и по умолчанию 0."""
        res = AgentResources()
        assert res.revenue_per_contract == 0.0

    def test_revenue_per_contract_custom(self):
        """revenue_per_contract можно задать при создании."""
        res = AgentResources(revenue_per_contract=150.0)
        assert res.revenue_per_contract == 150.0
