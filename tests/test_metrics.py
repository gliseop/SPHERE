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


class TestV4Metrics:
    def test_personality_consistency_metric(self):
        from magistry_sim.metrics import compute_personality_consistency

        score = compute_personality_consistency(
            actions=[
                {"tool": "talk_to", "params": {"agent_id": "biz_1", "private": True}},
                {"tool": "talk_to", "params": {"agent_id": "biz_1", "private": True}},
            ],
            archetype="initiator",
        )
        assert 0.0 <= score <= 5.0

    def test_personality_consistency_empty_actions(self):
        from magistry_sim.metrics import compute_personality_consistency

        score = compute_personality_consistency(actions=[], archetype="idealist")
        assert score == 0.0

    def test_personality_consistency_unknown_archetype(self):
        from magistry_sim.metrics import compute_personality_consistency

        score = compute_personality_consistency(
            actions=[{"tool": "open_case", "params": {}}],
            archetype="unknown_archetype",
        )
        assert 0.0 <= score <= 5.0

    def test_memory_utilization_metric(self):
        from magistry_sim.metrics import compute_memory_utilization

        score = compute_memory_utilization(
            total_memories=100,
            retrieved_memories=20,
            actions_influenced=15,
        )
        assert 0.0 <= score <= 1.0

    def test_memory_utilization_zero_memories(self):
        from magistry_sim.metrics import compute_memory_utilization

        score = compute_memory_utilization(
            total_memories=0,
            retrieved_memories=0,
            actions_influenced=0,
        )
        assert score == 0.0

    def test_memory_utilization_full(self):
        from magistry_sim.metrics import compute_memory_utilization

        score = compute_memory_utilization(
            total_memories=50,
            retrieved_memories=50,
            actions_influenced=50,
        )
        assert score == 1.0

    def test_information_asymmetry(self):
        from magistry_sim.metrics import compute_information_asymmetry

        score = compute_information_asymmetry(
            memory_sizes={"off_1": 50, "biz_1": 30, "auditor": 80},
        )
        assert score >= 0.0

    def test_information_asymmetry_equal(self):
        from magistry_sim.metrics import compute_information_asymmetry

        score = compute_information_asymmetry(
            memory_sizes={"a": 10, "b": 10, "c": 10},
        )
        assert score == 0.0

    def test_information_asymmetry_empty(self):
        from magistry_sim.metrics import compute_information_asymmetry

        score = compute_information_asymmetry(memory_sizes={})
        assert score == 0.0

    def test_information_asymmetry_single_agent(self):
        from magistry_sim.metrics import compute_information_asymmetry

        score = compute_information_asymmetry(memory_sizes={"a": 100})
        assert score == 0.0
