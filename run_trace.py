"""Запуск одного прогона с полным дампом событий и сообщений.

Сохраняет результаты в results/trace_{scenario}_{governance}.json.
Использует LLMAgentRunner (без локальных ML-моделей).
"""

import json
import os
import sys
from pathlib import Path

os.environ["CREWAI_TRACING_ENABLED"] = "false"

from dotenv import load_dotenv

load_dotenv()

from magistry_sim.agents import LLMAgentRunner
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.environment import Environment
from magistry_sim.metrics import compute_metrics
from magistry_sim.scenarios import get_scenario

SCENARIO = ScenarioId[sys.argv[1]] if len(sys.argv) > 1 else ScenarioId.S1
GOVERNANCE = GovernanceMode[sys.argv[2]] if len(sys.argv) > 2 else GovernanceMode.G2
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 8
SEED = int(sys.argv[4]) if len(sys.argv) > 4 else 42

print(f"Запуск: {SCENARIO.value}/{GOVERNANCE.value}, раундов={ROUNDS}, seed={SEED}")

from magistry_sim.llm import create_provider

llm = create_provider(mock=False)
runner = LLMAgentRunner(llm_provider=llm)

scenario = get_scenario(SCENARIO)
scenario.max_rounds = ROUNDS

env = Environment(scenario=scenario, governance=GOVERNANCE, runner=runner, seed=SEED)
result = env.run()
metrics = compute_metrics(result)

# Формируем полный дамп
dump = {
    "scenario": SCENARIO.value,
    "governance": GOVERNANCE.value,
    "seed": SEED,
    "rounds_completed": result.rounds_completed,
    "metrics": {
        "f1": metrics.confusion.f1,
        "precision": metrics.confusion.precision,
        "recall": metrics.confusion.recall,
        "tp": metrics.confusion.tp,
        "fp": metrics.confusion.fp,
        "tn": metrics.confusion.tn,
        "fn": metrics.confusion.fn,
        "violations_total": metrics.violations_total,
        "violations_detected": metrics.violations_detected,
        "private_message_ratio": metrics.private_message_ratio,
        "graph_density": metrics.graph_density,
    },
    "reputation": {
        aid: {"score": rec.score, "frozen": rec.frozen}
        for aid, rec in env.state.reputation.items()
    },
    "cases": result.cases,
    "events": [
        e if isinstance(e, dict) else vars(e)
        for e in result.events
    ],
    "messages": [
        m if isinstance(m, dict) else vars(m)
        for m in result.messages
    ],
}

out_path = (
    Path("results")
    / f"trace_{SCENARIO.value}_{GOVERNANCE.value}_seed{SEED}.json"
)
out_path.parent.mkdir(exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(dump, f, ensure_ascii=False, indent=2, default=str)

print(f"\nСохранено: {out_path}")
print(f"Дел: {len(result.cases)}  Сообщений: {len(result.messages)}")
print(
    f"F1={metrics.confusion.f1:.2f}  TP={metrics.confusion.tp}  FP={metrics.confusion.fp} "
    f"TN={metrics.confusion.tn}  FN={metrics.confusion.fn}"
)
print(f"Приватных сообщений: {metrics.private_message_ratio:.2f}")
