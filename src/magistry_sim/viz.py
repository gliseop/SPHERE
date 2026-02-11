"""Визуализация результатов симуляции (matplotlib)."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless mode

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np

from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.metrics import Metrics
from magistry_sim.models import RunResult
from magistry_sim.world import World


def plot_governance_comparison(
    batch_results: list[tuple[ScenarioId, GovernanceMode, Metrics]],
    output_path: Path,
) -> None:
    """Bar chart: F1/precision/recall по G0-G3 для каждого сценария.

    Args:
        batch_results: Результаты пакетного запуска.
        output_path: Путь для сохранения PNG.
    """
    modes = list(GovernanceMode)
    scenarios = list(ScenarioId)

    # Группируем данные по сценарию и режиму
    data: dict[tuple[str, str], Metrics] = {}
    for sid, mode, m in batch_results:
        data[(sid.value, mode.value)] = m

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    metric_names = ["precision", "recall", "f1"]
    metric_titles = ["Precision", "Recall", "F1 Score"]

    x = np.arange(len(scenarios))
    width = 0.2

    for ax_idx, (metric_name, title) in enumerate(zip(metric_names, metric_titles)):
        ax = axes[ax_idx]
        for mode_idx, mode in enumerate(modes):
            values = []
            for sid in scenarios:
                m = data.get((sid.value, mode.value))
                values.append(getattr(m, metric_name, 0.0) if m else 0.0)
            offset = (mode_idx - len(modes) / 2 + 0.5) * width
            ax.bar(x + offset, values, width, label=mode.value, alpha=0.85)

        ax.set_xlabel("Сценарий")
        ax.set_ylabel(title)
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels([s.value for s in scenarios], rotation=45, ha="right")
        ax.legend(fontsize=8)
        ax.set_ylim(0, 1.05)

    fig.suptitle("Сравнение режимов управления (G0-G3)", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_corruption_timeline(
    result: RunResult,
    output_path: Path,
) -> None:
    """Timeline: corruption rate, audit flags, tribunal events по тикам.

    Args:
        result: Результат одного прогона.
        output_path: Путь для сохранения PNG.
    """
    ticks = [o.tick for o in result.outcomes]
    corruption = [1 if o.corruption else 0 for o in result.outcomes]
    audit_flagged = [1 if o.audit_flagged else 0 for o in result.outcomes]
    tribunal = [1 if o.tribunal_triggered else 0 for o in result.outcomes]
    risk_scores = [o.audit_risk or 0.0 for o in result.outcomes]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    # Верхний: бинарные события
    ax1.step(ticks, corruption, where="mid", label="Corruption (GT)", color="red", linewidth=2)
    ax1.step(ticks, audit_flagged, where="mid", label="Audit Flag", color="orange", linewidth=2, linestyle="--")
    ax1.step(ticks, tribunal, where="mid", label="Tribunal", color="purple", linewidth=1.5, linestyle=":")
    ax1.set_ylabel("Event (0/1)")
    ax1.set_title(f"Timeline: {result.scenario.value} / {result.governance.value}")
    ax1.legend(loc="upper right")
    ax1.set_ylim(-0.1, 1.5)

    # Нижний: risk score
    ax2.plot(ticks, risk_scores, color="blue", marker="o", linewidth=2, label="Risk Score")
    ax2.axhline(y=0.70, color="gray", linestyle="--", alpha=0.5, label="Default threshold (0.70)")
    ax2.set_xlabel("Tick")
    ax2.set_ylabel("Risk Score")
    ax2.legend(loc="upper right")
    ax2.set_ylim(0, 1.05)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_social_graph(
    world: World,
    output_path: Path,
) -> None:
    """NetworkX graph visualization с цветами по ролям и связям.

    Args:
        world: Состояние мира.
        output_path: Путь для сохранения PNG.
    """
    G = world.social_graph
    if len(G.nodes()) == 0:
        return

    # Цвета по ролям
    color_map = []
    for node in G.nodes():
        agent = world.agents.get(node)
        if agent is None:
            color_map.append("gray")
        elif agent.immune:
            color_map.append("gold")
        elif agent.frozen:
            color_map.append("lightblue")
        elif agent.role.value == "official":
            color_map.append("steelblue")
        else:
            color_map.append("salmon")

    # Толщина рёбер по strength
    edge_widths = []
    for u, v, data_edge in G.edges(data=True):
        strength = float(data_edge.get("strength", 0.1))
        edge_widths.append(max(0.5, strength * 5))

    fig, ax = plt.subplots(figsize=(10, 8))
    pos = nx.spring_layout(G, seed=42, k=2)
    nx.draw_networkx(
        G, pos, ax=ax,
        node_color=color_map,
        node_size=500,
        width=edge_widths if edge_widths else 1,
        font_size=8,
        alpha=0.9,
        edge_color="gray",
    )

    # Легенда
    import matplotlib.patches as mpatches
    legend_items = [
        mpatches.Patch(color="steelblue", label="Official"),
        mpatches.Patch(color="salmon", label="Contractor"),
        mpatches.Patch(color="lightblue", label="Frozen"),
        mpatches.Patch(color="gold", label="Immune"),
    ]
    ax.legend(handles=legend_items, loc="upper left")
    ax.set_title("Social Graph")

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_confusion_heatmap(
    batch_results: list[tuple[ScenarioId, GovernanceMode, Metrics]],
    output_path: Path,
) -> None:
    """Heatmap: F1 по сценариям × режимам управления.

    Args:
        batch_results: Результаты пакетного запуска.
        output_path: Путь для сохранения PNG.
    """
    modes = list(GovernanceMode)
    scenarios = list(ScenarioId)

    data: dict[tuple[str, str], Metrics] = {}
    for sid, mode, m in batch_results:
        data[(sid.value, mode.value)] = m

    # Матрица F1
    matrix = np.zeros((len(scenarios), len(modes)))
    for i, sid in enumerate(scenarios):
        for j, mode in enumerate(modes):
            m = data.get((sid.value, mode.value))
            matrix[i, j] = m.f1 if m else 0.0

    fig, ax = plt.subplots(figsize=(8, 10))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)

    ax.set_xticks(range(len(modes)))
    ax.set_xticklabels([m.value for m in modes])
    ax.set_yticks(range(len(scenarios)))
    ax.set_yticklabels([s.value for s in scenarios])

    # Аннотации
    for i in range(len(scenarios)):
        for j in range(len(modes)):
            text = f"{matrix[i, j]:.2f}"
            color = "white" if matrix[i, j] < 0.4 else "black"
            ax.text(j, i, text, ha="center", va="center", color=color, fontsize=9)

    ax.set_xlabel("Governance Mode")
    ax.set_ylabel("Scenario")
    ax.set_title("F1 Score Heatmap: Scenarios × Governance Modes")
    fig.colorbar(im, ax=ax, label="F1 Score")

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_reputation_dynamics(
    result: RunResult,
    output_path: Path,
) -> None:
    """Line chart: social capital trajectory для ключевых агентов.

    Args:
        result: Результат одного прогона.
        output_path: Путь для сохранения PNG.
    """
    # Собираем social capital по тикам из events
    agent_totals: dict[str, list[float]] = {}
    tick_count = result.ticks

    # Начальные значения = 0
    for agent_id in result.final_agents:
        agent_totals[agent_id] = [0.0] * tick_count

    # Парсим reputation_update events
    for event in result.events:
        if event.event_type == "reputation_update":
            tick = event.tick
            if tick < tick_count:
                lpr_id = event.payload.get("lpr_id", "")
                winner_id = event.payload.get("winner_id", "")
                if lpr_id in agent_totals:
                    agent_totals[lpr_id][tick] = event.payload.get("lpr_after", 0.0)
                if winner_id in agent_totals:
                    agent_totals[winner_id][tick] = event.payload.get("winner_after", 0.0)

    # Только агенты с ненулевой историей
    active_agents = {
        aid: totals for aid, totals in agent_totals.items()
        if any(v > 0 for v in totals)
    }

    if not active_agents:
        # Фолбэк: просто показать финальные значения
        fig, ax = plt.subplots(figsize=(10, 6))
        agents_sorted = sorted(
            result.final_agents.items(),
            key=lambda kv: kv[1].social_capital.total,
            reverse=True,
        )[:10]
        names = [a.name for _, a in agents_sorted]
        totals = [a.social_capital.total for _, a in agents_sorted]
        ax.barh(names, totals, color="steelblue")
        ax.set_xlabel("Social Capital (total)")
        ax.set_title(f"Reputation: {result.scenario.value} / {result.governance.value}")
        fig.tight_layout()
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    ticks = list(range(tick_count))
    for aid, totals in active_agents.items():
        agent = result.final_agents.get(aid)
        label = agent.name if agent else aid
        style = "--" if (agent and agent.frozen) else "-"
        ax.plot(ticks, totals, label=label, linestyle=style, linewidth=2, alpha=0.8)

    ax.set_xlabel("Tick")
    ax.set_ylabel("Social Capital (total)")
    ax.set_title(f"Reputation Dynamics: {result.scenario.value} / {result.governance.value}")
    ax.legend(fontsize=8, loc="upper left", ncol=2)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
