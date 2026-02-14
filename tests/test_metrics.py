"""Тесты метрик."""

from magistry_sim.agents import MockAgentRunner
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.environment import Environment
from magistry_sim.metrics import (
    ConfusionMatrix,
    SimulationMetrics,
    classify_case_outcome,
    compute_metrics,
)
from magistry_sim.scenarios import get_scenario


class TestConfusionMatrix:
    def test_precision(self):
        cm = ConfusionMatrix(tp=8, fp=2, tn=5, fn=3)
        assert abs(cm.precision - 0.8) < 0.01

    def test_recall(self):
        cm = ConfusionMatrix(tp=8, fp=2, tn=5, fn=3)
        assert abs(cm.recall - 8 / 11) < 0.01

    def test_f1(self):
        cm = ConfusionMatrix(tp=8, fp=2, tn=5, fn=3)
        assert cm.f1 > 0.0

    def test_empty(self):
        cm = ConfusionMatrix()
        assert cm.precision == 0.0
        assert cm.recall == 0.0
        assert cm.f1 == 0.0


class TestClassifyCaseOutcome:
    def test_no_violation(self):
        case = {
            "id": "D-001",
            "owner_id": "off_1",
            "decision": "Выбран biz_1",
            "proposals": [
                {"author_id": "biz_1"},
            ],
        }
        assert not classify_case_outcome(case, [], [])

    def test_violation_private_contact(self):
        case = {
            "id": "D-001",
            "owner_id": "off_1",
            "decision": "Выбран biz_1",
            "proposals": [
                {"author_id": "biz_1"},
            ],
        }
        messages = [
            {
                "from_id": "off_1",
                "to_id": "biz_1",
                "private": True,
            },
        ]
        assert classify_case_outcome(case, [], messages)


class TestComputeMetrics:
    def test_compute_metrics_s0(self):
        scenario = get_scenario(ScenarioId.S0)
        env = Environment(
            scenario=scenario,
            governance=GovernanceMode.G0,
            runner=MockAgentRunner(),
        )
        result = env.run()
        metrics = compute_metrics(result)
        assert isinstance(metrics, SimulationMetrics)
        assert metrics.rounds == scenario.max_rounds
        assert metrics.total_cases >= 0
