"""Тесты LLM-арбитра."""

import json

from magistry_sim.arbiter import Arbiter, ArbiterVerdict
from magistry_sim.llm import MockLLMProvider, StructuredLLMResponse
from magistry_sim.state import WorldState
from magistry_sim.config import AgentProfile, Capability
from magistry_sim.state import ReputationRecord


class TestArbiter:
    """Оценка действий агентов."""

    def _make_state(self) -> WorldState:
        state = WorldState()
        state.agents["off_1"] = AgentProfile(
            id="off_1", name="Козлов", position="начальник",
            capabilities=[Capability(action="open_case", case_types=["procurement"])],
        )
        state.agents["biz_1"] = AgentProfile(
            id="biz_1", name="Петров", position="директор",
        )
        state.graph.add_agent("off_1")
        state.graph.add_agent("biz_1")
        state.reputation["off_1"] = ReputationRecord()
        state.reputation["biz_1"] = ReputationRecord()
        return state

    def test_feasible_action(self):
        verdict_data = {
            "feasible": True,
            "state_changes": [
                {"op": "create_case", "params": {
                    "case_type": "procurement",
                    "title": "Закупка",
                    "description": "Тест",
                    "owner_id": "off_1",
                }}
            ],
            "side_effects": [],
            "narrative": "Козлов открыл закупку.",
        }
        mock = MockLLMProvider(
            structured_responses={"perform_action": verdict_data}
        )
        arbiter = Arbiter(llm=mock)
        state = self._make_state()

        verdict = arbiter.evaluate(
            agent_id="off_1",
            description="Открыть закупку серверов",
            target="",
            justification="Нужны серверы",
            state=state,
            round_num=0,
        )
        assert verdict.feasible is True
        assert len(verdict.state_changes) == 1
        assert verdict.narrative != ""

    def test_infeasible_action(self):
        verdict_data = {
            "feasible": False,
            "state_changes": [],
            "side_effects": [],
            "narrative": "Невозможно: у агента нет полномочий.",
        }
        mock = MockLLMProvider(
            structured_responses={"perform_action": verdict_data}
        )
        arbiter = Arbiter(llm=mock)
        state = self._make_state()

        verdict = arbiter.evaluate(
            agent_id="biz_1",
            description="Уволить начальника",
            target="off_1",
            justification="Мне не нравится",
            state=state,
            round_num=0,
        )
        assert verdict.feasible is False
        assert len(verdict.state_changes) == 0

    def test_build_snapshot_includes_agents(self):
        arbiter = Arbiter(llm=MockLLMProvider())
        state = self._make_state()
        snapshot = arbiter.build_world_snapshot(state)
        assert "Козлов" in snapshot
        assert "Петров" in snapshot
