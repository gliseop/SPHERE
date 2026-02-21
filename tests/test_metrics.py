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
        """Нарушение фиксируется при более чем одном приватном сообщении."""
        case = {
            "id": "D-001",
            "owner_id": "off_1",
            "decision": "Выбран biz_1",
            "proposals": [
                {"author_id": "biz_1"},
            ],
        }
        messages = [
            {"from_id": "off_1", "to_id": "biz_1", "private": True},
            {"from_id": "biz_1", "to_id": "off_1", "private": True},
        ]
        assert classify_case_outcome(case, [], messages)

    def test_no_violation_single_private_message(self):
        """Одно приватное сообщение не является достаточным признаком нарушения."""
        case = {
            "id": "D-001",
            "owner_id": "off_1",
            "decision": "Выбран biz_1",
            "proposals": [{"author_id": "biz_1"}],
        }
        messages = [
            {"from_id": "off_1", "to_id": "biz_1", "private": True},
        ]
        assert not classify_case_outcome(case, [], messages)

    def test_violation_strong_graph_link(self):
        """Нарушение фиксируется при сильной связи в графе + приватный контакт."""
        case = {
            "id": "D-001",
            "owner_id": "off_1",
            "decision": "Выбран biz_1",
            "proposals": [{"author_id": "biz_1"}],
        }
        messages = [
            {"from_id": "off_1", "to_id": "biz_1", "private": True},
        ]
        agent_connections = {"off_1": {"biz_1": 3.0}}
        assert classify_case_outcome(case, [], messages, agent_connections)

    def test_no_violation_weak_graph_link(self):
        """Слабая связь в графе не является признаком нарушения."""
        case = {
            "id": "D-001",
            "owner_id": "off_1",
            "decision": "Выбран biz_1",
            "proposals": [{"author_id": "biz_1"}],
        }
        messages = [
            {"from_id": "off_1", "to_id": "biz_1", "private": True},
        ]
        agent_connections = {"off_1": {"biz_1": 1.0}}
        assert not classify_case_outcome(case, [], messages, agent_connections)


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


class TestCorruptionRate:
    """Тесты доли коррупционных сделок."""

    def test_corruption_rate_with_violations(self):
        from magistry_sim.metrics import corruption_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            cases={
                "D-001": {"id": "D-001", "owner_id": "off_1",
                           "case_type": "procurement", "closed_at": 3,
                           "decision": "Выбран biz_1", "proposals": [{"author_id": "biz_1"}]},
                "D-002": {"id": "D-002", "owner_id": "off_1",
                           "case_type": "procurement", "closed_at": 4,
                           "decision": "Выбран biz_2", "proposals": [{"author_id": "biz_2"}]},
            },
            events=[],
            messages=[
                {"from_id": "off_1", "to_id": "biz_1", "private": True},
                {"from_id": "biz_1", "to_id": "off_1", "private": True},
            ],
        )
        rate = corruption_rate(result)
        assert 0.0 <= rate <= 1.0
        assert rate == 0.5  # 1 из 2 дел — нарушение

    def test_corruption_rate_no_cases(self):
        from magistry_sim.metrics import corruption_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
        )
        rate = corruption_rate(result)
        assert rate == 0.0


class TestDetectionRate:
    """Тесты доли обнаруженных нарушений."""

    def test_detection_rate_some_detected(self):
        from magistry_sim.metrics import detection_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G2", seed=42,
            rounds_completed=5,
            cases={
                "D-001": {"id": "D-001", "owner_id": "off_1",
                           "case_type": "procurement", "closed_at": 3,
                           "decision": "Выбран biz_1",
                           "proposals": [{"author_id": "biz_1"}]},
                "D-002": {"id": "D-002", "owner_id": "off_1",
                           "case_type": "procurement", "closed_at": 4,
                           "decision": "Выбран biz_2",
                           "proposals": [{"author_id": "biz_2"}]},
            },
            events=[
                {"event_type": "report_filed", "payload": {"case_id": "D-001"}},
            ],
            messages=[
                {"from_id": "off_1", "to_id": "biz_1", "private": True},
                {"from_id": "biz_1", "to_id": "off_1", "private": True},
                {"from_id": "off_1", "to_id": "biz_2", "private": True},
                {"from_id": "biz_2", "to_id": "off_1", "private": True},
            ],
        )
        rate = detection_rate(result)
        assert rate == 0.5  # 1 обнаружено из 2 нарушений

    def test_detection_rate_no_violations(self):
        from magistry_sim.metrics import detection_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
        )
        rate = detection_rate(result)
        assert rate == 0.0


class TestFalsePositiveRate:
    """Тесты ложных обвинений."""

    def test_false_positive_rate(self):
        from magistry_sim.metrics import false_positive_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G2", seed=42,
            rounds_completed=5,
            cases={
                "D-001": {"id": "D-001", "owner_id": "off_1",
                           "case_type": "procurement", "closed_at": 3,
                           "decision": "Выбран biz_1",
                           "proposals": [{"author_id": "biz_1"}]},
            },
            events=[
                {"event_type": "report_filed", "payload": {"case_id": "D-001"}},
            ],
            messages=[],  # нет приватных — нет нарушения → ложное обвинение
        )
        rate = false_positive_rate(result)
        assert rate == 1.0  # 1 FP из 1 обвинения

    def test_false_positive_rate_no_reports(self):
        from magistry_sim.metrics import false_positive_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
        )
        rate = false_positive_rate(result)
        assert rate == 0.0


class TestNetworkEvolution:
    """Тесты изменения сетевой структуры."""

    def test_network_evolution_basic(self):
        from magistry_sim.metrics import network_evolution
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            agents=["off_1", "biz_1", "biz_2"],
            events=[
                {"event_type": "talk_to", "agent_id": "off_1",
                 "payload": {"to_id": "biz_1"}},
                {"event_type": "talk_to", "agent_id": "off_1",
                 "payload": {"to_id": "biz_2"}},
                {"event_type": "talk_to", "agent_id": "biz_1",
                 "payload": {"to_id": "biz_2"}},
            ],
        )
        evo = network_evolution(result)
        assert "edges" in evo
        assert "unique_pairs" in evo
        assert evo["edges"] == 3
        assert evo["unique_pairs"] >= 1

    def test_network_evolution_no_events(self):
        from magistry_sim.metrics import network_evolution
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
        )
        evo = network_evolution(result)
        assert evo["edges"] == 0


class TestNeutralizationUsage:
    """Тесты подсчёта техник нейтрализации в рефлексиях."""

    def test_neutralization_usage_found(self):
        from magistry_sim.metrics import neutralization_usage
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            events=[
                {"event_type": "reflection", "agent_id": "off_1",
                 "payload": {"content": "используя [technique: denial_of_injury], я считаю"}},
                {"event_type": "reflection", "agent_id": "off_1",
                 "payload": {"content": "по технике [technique: everyone_does_it], это нормально"}},
                {"event_type": "reflection", "agent_id": "off_1",
                 "payload": {"content": "обычная рефлексия без техник"}},
            ],
        )
        usage = neutralization_usage(result)
        assert "denial_of_injury" in usage
        assert usage["denial_of_injury"] == 1
        assert "everyone_does_it" in usage
        assert usage["everyone_does_it"] == 1

    def test_neutralization_usage_none(self):
        from magistry_sim.metrics import neutralization_usage
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
        )
        usage = neutralization_usage(result)
        assert usage == {}


class TestV5FreeWorldMetrics:
    """Тесты метрик свободного мира (v5)."""

    def test_action_diversity_counts_unique_types(self):
        from magistry_sim.metrics import action_diversity
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            events=[
                {"event_type": "talk_to", "agent_id": "off_1", "round": 1},
                {"event_type": "submit_proposal", "agent_id": "biz_1", "round": 1},
                {"event_type": "talk_to", "agent_id": "biz_2", "round": 2},
                {"event_type": "arbiter_approved", "agent_id": "off_1", "round": 2},
                {"event_type": "funds_transferred", "agent_id": "off_1", "round": 3},
            ],
        )
        assert action_diversity(result) == 4

    def test_action_diversity_empty_events(self):
        from magistry_sim.metrics import action_diversity
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
        )
        assert action_diversity(result) == 0

    def test_scheme_depth_finds_chain(self):
        from magistry_sim.metrics import scheme_depth
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=10,
            events=[
                {"event_type": "funds_transferred", "agent_id": "off_1", "round": 1},
                {"event_type": "evidence_removed", "agent_id": "off_1", "round": 2},
                {"event_type": "evidence_added", "agent_id": "off_1", "round": 3,
                 "payload": {"evidence_type": "forged_document"}},
                {"event_type": "talk_to", "agent_id": "off_1", "round": 4},
                {"event_type": "funds_transferred", "agent_id": "off_1", "round": 5},
                {"event_type": "talk_to", "agent_id": "biz_1", "round": 1},
                {"event_type": "funds_transferred", "agent_id": "biz_1", "round": 2},
            ],
        )
        # off_1: цепочка из 3 коррупционных (раунды 1-3), потом разрыв, потом 1
        # biz_1: цепочка из 1
        assert scheme_depth(result) == 3

    def test_scheme_depth_no_corruption(self):
        from magistry_sim.metrics import scheme_depth
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            events=[
                {"event_type": "talk_to", "agent_id": "off_1", "round": 1},
                {"event_type": "submit_proposal", "agent_id": "biz_1", "round": 2},
                {"event_type": "cast_vote", "agent_id": "off_1", "round": 3},
            ],
        )
        assert scheme_depth(result) == 0

    def test_arbiter_rejection_rate_basic(self):
        from magistry_sim.metrics import arbiter_rejection_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            events=[
                {"event_type": "arbiter_approved", "agent_id": "off_1", "round": 1},
                {"event_type": "arbiter_approved", "agent_id": "biz_1", "round": 1},
                {"event_type": "arbiter_rejected", "agent_id": "off_1", "round": 2},
                {"event_type": "arbiter_approved", "agent_id": "biz_2", "round": 2},
            ],
        )
        # 1 rejected / 4 total = 0.25
        assert arbiter_rejection_rate(result) == 0.25

    def test_arbiter_rejection_rate_no_events(self):
        from magistry_sim.metrics import arbiter_rejection_rate
        from magistry_sim.environment import SimulationResult

        result = SimulationResult(
            scenario_id="S0", governance="G0", seed=42,
            rounds_completed=5,
            events=[
                {"event_type": "talk_to", "agent_id": "off_1", "round": 1},
            ],
        )
        assert arbiter_rejection_rate(result) == 0.0
