"""Тесты визуализации (Stage 8)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from random import Random

import pytest

from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.llm import MockLLMProvider
from magistry_sim.metrics import compute_metrics
from magistry_sim.models import GovernanceConfig
from magistry_sim.viz import (
    plot_confusion_heatmap,
    plot_corruption_timeline,
    plot_governance_comparison,
    plot_reputation_dynamics,
    plot_social_graph,
)


def _make_engine(mode: GovernanceMode = GovernanceMode.G3_FULL) -> SimulationEngine:
    return SimulationEngine(
        governance=GovernanceConfig(mode=mode),
        llm=MockLLMProvider(seed=42),
    )


@pytest.mark.asyncio
async def test_plot_governance_comparison() -> None:
    """plot_governance_comparison выполняется без ошибок."""
    llm = MockLLMProvider(seed=42)
    results = []
    for sid in [ScenarioId.S0_CLEAN, ScenarioId.S1_KICKBACK]:
        for mode in GovernanceMode:
            engine = SimulationEngine(governance=GovernanceConfig(mode=mode), llm=llm)
            result = await engine.run(scenario_id=sid)
            metrics = compute_metrics(result)
            results.append((sid, mode, metrics))

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "comparison.png"
        plot_governance_comparison(results, output)
        assert output.exists()
        assert output.stat().st_size > 0


@pytest.mark.asyncio
async def test_plot_corruption_timeline() -> None:
    """plot_corruption_timeline выполняется без ошибок."""
    engine = _make_engine()
    result = await engine.run(scenario_id=ScenarioId.S3_CAROUSEL, ticks=6)

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "timeline.png"
        plot_corruption_timeline(result, output)
        assert output.exists()
        assert output.stat().st_size > 0


@pytest.mark.asyncio
async def test_plot_social_graph() -> None:
    """plot_social_graph выполняется без ошибок."""
    engine = _make_engine()
    scenario = GovernanceConfig(mode=GovernanceMode.G3_FULL)
    rng = Random(42)
    from magistry_sim.scenarios import get_scenario
    world = engine._init_world(scenario=get_scenario(ScenarioId.S1_KICKBACK), rng=rng)

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "social_graph.png"
        plot_social_graph(world, output)
        assert output.exists()
        assert output.stat().st_size > 0


@pytest.mark.asyncio
async def test_plot_confusion_heatmap() -> None:
    """plot_confusion_heatmap выполняется без ошибок."""
    llm = MockLLMProvider(seed=42)
    results = []
    for sid in ScenarioId:
        for mode in GovernanceMode:
            engine = SimulationEngine(governance=GovernanceConfig(mode=mode), llm=llm)
            result = await engine.run(scenario_id=sid)
            metrics = compute_metrics(result)
            results.append((sid, mode, metrics))

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "heatmap.png"
        plot_confusion_heatmap(results, output)
        assert output.exists()
        assert output.stat().st_size > 0


@pytest.mark.asyncio
async def test_plot_reputation_dynamics() -> None:
    """plot_reputation_dynamics выполняется без ошибок."""
    engine = _make_engine()
    result = await engine.run(scenario_id=ScenarioId.S3_CAROUSEL, ticks=6)

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "reputation.png"
        plot_reputation_dynamics(result, output)
        assert output.exists()
        assert output.stat().st_size > 0
