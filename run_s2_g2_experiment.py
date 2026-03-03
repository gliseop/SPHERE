"""Боевой эксперимент S2/G2: кумовство + аудитор + вторичные агенты.

Генерирует вторичных агентов (fam_1, fam_2, soc_1, soc_2) через LLM,
сохраняет обогащённый ScenarioConfig и запускает 25-раундовую симуляцию.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console

from magistry_sim.arbiter import Arbiter
from magistry_sim.cognitive_runner import CognitiveAgentRunner
from magistry_sim.config import AgentProfile, Connection, ScenarioConfig
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.environment import Environment
from magistry_sim.llm import create_embedding_provider, create_provider
from magistry_sim.metrics import (
    action_diversity,
    arbiter_rejection_rate,
    case_diversity,
    compute_metrics,
    compute_metrics_with_oracle,
    corruption_rate,
    detection_rate,
    false_positive_rate,
    scheme_depth,
)
from magistry_sim.oracle import ViolationOracle
from magistry_sim.personality import NeutralizationTechnique
from magistry_sim.scenarios import add_governance_agents, get_scenario
from magistry_sim.tracing import LLMTracer, TracingLLMProvider
from magistry_sim.world_generator import WorldGenerator

console = Console(file=sys.stdout)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)
SCENARIOS_DIR = Path("scenarios")
SCENARIOS_DIR.mkdir(exist_ok=True)

SEED = 42
MAX_ROUNDS = 25
FAMILY_COUNT = 2
SOCIETY_COUNT = 2
ENRICHED_PATH = SCENARIOS_DIR / "s2_g2_enriched.json"


def generate_secondary_agents(base_cfg: ScenarioConfig) -> ScenarioConfig:
    """Сгенерировать вторичных агентов через LLM.

    Повторяет логику эндпоинта POST /api/ai/secondary-agents, но
    без поднятия FastAPI-сервера.

    Args:
        base_cfg: Базовая конфигурация S2 с аудитором (G2).

    Returns:
        Обогащённая конфигурация с вторичными агентами.
    """
    total = FAMILY_COUNT + SOCIETY_COUNT
    existing_ids = {a.id for a in base_cfg.agents}

    def _next_id(prefix: str) -> str:
        n = 1
        while True:
            candidate = f"{prefix}_{n}"
            if candidate not in existing_ids:
                existing_ids.add(candidate)
                return candidate
            n += 1

    requested_family = [_next_id("fam") for _ in range(FAMILY_COUNT)]
    requested_society = [_next_id("soc") for _ in range(SOCIETY_COUNT)]
    requested_ids = requested_family + requested_society

    primary_agents = [
        {"id": a.id, "name": a.name, "position": a.position}
        for a in base_cfg.agents
        if a.id not in requested_ids
    ]
    primary_ids = [a["id"] for a in primary_agents]

    techniques = [t.value for t in NeutralizationTechnique]

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "narrative_context": {"type": "string", "maxLength": 2000},
            "agents": {
                "type": "array",
                "minItems": total,
                "maxItems": total,
                "items": {"$ref": "#/$defs/agent"},
            },
        },
        "required": ["narrative_context", "agents"],
        "$defs": {
            "capability": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "maxLength": 64},
                    "case_types": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 64},
                    },
                },
                "required": ["action", "case_types"],
            },
            "connection": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_id": {"type": "string", "maxLength": 64},
                    "name": {"type": "string", "maxLength": 128},
                    "relation": {"type": "string", "maxLength": 128},
                    "strength": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 5.0,
                    },
                },
                "required": ["target_id", "name", "relation", "strength"],
            },
            "hexaco": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "honesty_humility": {"type": "integer", "minimum": 0, "maximum": 100},
                    "emotionality": {"type": "integer", "minimum": 0, "maximum": 100},
                    "extraversion": {"type": "integer", "minimum": 0, "maximum": 100},
                    "agreeableness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "conscientiousness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "openness": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": [
                    "honesty_humility", "emotionality", "extraversion",
                    "agreeableness", "conscientiousness", "openness",
                ],
            },
            "dark_triad": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "narcissism": {"type": "integer", "minimum": 0, "maximum": 100},
                    "machiavellianism": {"type": "integer", "minimum": 0, "maximum": 100},
                    "psychopathy": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["narcissism", "machiavellianism", "psychopathy"],
            },
            "personality": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "biography": {"type": "string", "maxLength": 800},
                    "hexaco": {"$ref": "#/$defs/hexaco"},
                    "dark_triad": {"$ref": "#/$defs/dark_triad"},
                    "neutralization_techniques": {
                        "type": "array",
                        "items": {"type": "string", "enum": techniques},
                    },
                },
                "required": [
                    "biography", "hexaco", "dark_triad", "neutralization_techniques",
                ],
            },
            "resources": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "budget_limit": {"type": "number"},
                    "staffing_slots": {"type": "integer"},
                    "contract_capacity": {"type": "integer"},
                },
                "required": ["budget_limit", "staffing_slots", "contract_capacity"],
            },
            "agent": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "maxLength": 64},
                    "name": {"type": "string", "maxLength": 128},
                    "position": {"type": "string", "maxLength": 256},
                    "capabilities": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/capability"},
                    },
                    "greed": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "fear": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "honesty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "competence": {
                        "anyOf": [
                            {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            {"type": "null"},
                        ]
                    },
                    "immune": {"type": "boolean"},
                    "connections": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/connection"},
                    },
                    "personality": {"$ref": "#/$defs/personality"},
                    "initial_resources": {"$ref": "#/$defs/resources"},
                },
                "required": [
                    "id", "name", "position", "capabilities",
                    "greed", "fear", "honesty", "competence",
                    "immune", "connections", "personality", "initial_resources",
                ],
            },
        },
    }

    system = (
        "Вы — генератор вторичных агент-профилей для симуляции MAGISTRY. "
        "Верните ТОЛЬКО structured JSON по схеме."
    )
    user_prompt = (
        f"Контекст организации:\n{base_cfg.narrative_context}\n\n"
        "Пожелания пользователя (среда/контекст):\n"
        "Сценарий S2 (кумовство при найме) с режимом G2 (аудитор с репутационными санкциями). "
        "Вторичные агенты должны создавать давление среды: семейные агенты (fam_*) оказывают "
        "клановое давление на чиновников, усиливая мотивацию к кумовству. Общественные агенты "
        "(soc_*) представляют медиа и общественное мнение — они создают репутационные риски "
        "при обнаружении нарушений. Семейные агенты связаны с Козловым и Волковым. "
        "Общественные — с аудитором и кандидатами.\n\n"
        "Основные агенты (id, имя, должность):\n"
        + "\n".join(
            f"- {a['id']}: {a['name']} — {a['position']}" for a in primary_agents
        )
        + "\n\n"
        f"Нужно добавить вторичных агентов. Новые id ДОЛЖНЫ быть строго такими:\n"
        f"{', '.join(requested_ids)}\n\n"
        "Правила:\n"
        f"- connection.target_id только из: {', '.join(primary_ids)}\n"
        "- capabilities оставьте пустым массивом []\n"
        "- initial_resources заполните нулями\n"
        "- у каждого агента минимум 1 connection к основному агенту\n"
        "- biography 2–5 предложений, отражает мотивацию/давление среды\n"
    )

    console.print("[bold]Генерация вторичных агентов через LLM...[/bold]")
    provider = create_provider(mock=False, cache_path=".llm_cache.db", use_tool_calls=True)
    resp = provider.generate_structured(
        system=system,
        user=user_prompt,
        schema=schema,
        temperature=0.25,
    )

    data = resp.data if isinstance(resp.data, dict) else {}
    narrative_context = str(data.get("narrative_context", "") or "").strip()
    if not narrative_context:
        narrative_context = base_cfg.narrative_context

    raw_agents = data.get("agents", [])
    if not isinstance(raw_agents, list):
        console.print("[red]LLM вернул невалидные данные agents[/red]")
        sys.exit(1)

    validated: list[AgentProfile] = []
    for item in raw_agents:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id", "") or "")
        if aid not in requested_ids:
            continue
        if aid in {a.id for a in base_cfg.agents}:
            continue
        conns = item.get("connections", [])
        if isinstance(conns, list):
            item["connections"] = [
                c for c in conns
                if isinstance(c, dict)
                and str(c.get("target_id", "") or "") in primary_ids
            ]
        try:
            validated.append(AgentProfile.model_validate(item))
        except Exception as exc:
            console.print(f"[yellow]Пропущен агент {aid}: {exc}[/yellow]")
            continue

    if set(requested_ids) != {a.id for a in validated}:
        missing = sorted(set(requested_ids) - {a.id for a in validated})
        console.print(f"[red]LLM не вернул агентов: {', '.join(missing)}[/red]")
        console.print(f"[dim]Получены id: {[a.id for a in validated]}[/dim]")
        console.print(f"[dim]Raw: {json.dumps(raw_agents, ensure_ascii=False, indent=2)[:2000]}[/dim]")
        sys.exit(1)

    by_id: dict[str, AgentProfile] = {a.id: a for a in base_cfg.agents}

    def _add_backlink(target_id: str, source: AgentProfile, conn: Connection) -> None:
        target = by_id.get(target_id)
        if target is None:
            return
        if any(c.target_id == source.id for c in target.connections):
            return
        backlink = Connection(
            target_id=source.id,
            name=source.name,
            relation=conn.relation,
            strength=conn.strength,
        )
        by_id[target_id] = target.model_copy(
            update={"connections": list(target.connections) + [backlink]}
        )

    for agent in validated:
        by_id[agent.id] = agent
        for conn in agent.connections:
            _add_backlink(conn.target_id, agent, conn)

    final_agents: list[AgentProfile] = []
    seen: set[str] = set()
    for a in base_cfg.agents:
        updated = by_id.get(a.id)
        if updated and updated.id not in seen:
            final_agents.append(updated)
            seen.add(updated.id)
    for a in validated:
        if a.id not in seen:
            final_agents.append(by_id[a.id])
            seen.add(a.id)

    cfg = base_cfg.model_copy(
        update={"agents": final_agents, "narrative_context": narrative_context}
    )

    console.print(f"[green]Сгенерировано {len(validated)} вторичных агентов[/green]")
    for a in validated:
        conns_str = ", ".join(f"{c.target_id}({c.relation})" for c in a.connections)
        console.print(f"  {a.id}: {a.name} — {a.position} [{conns_str}]")

    return cfg


def run_experiment(scenario: ScenarioConfig) -> dict:
    """Выполнить один прогон эксперимента.

    Args:
        scenario: Полная конфигурация сценария с агентами.

    Returns:
        Словарь с результатами и метриками.
    """
    governance = scenario.governance.mode
    seed = scenario.seed

    agent_llm = create_provider(
        mock=False,
        model=os.getenv("LLM_MODEL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        cache_path=".llm_cache.db",
    )

    arbiter_llm = create_provider(
        mock=False,
        model=os.getenv("ARBITER_MODEL", "openai/gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        cache_path=".llm_cache.db",
        use_tool_calls=True,
    )

    embedder = create_embedding_provider(
        mock=False,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )

    tracer = LLMTracer()
    traced_agent = TracingLLMProvider(inner=agent_llm, tracer=tracer, role="agent")
    traced_arbiter = TracingLLMProvider(inner=arbiter_llm, tracer=tracer, role="arbiter")

    arbiter = Arbiter(llm=traced_arbiter)
    world_gen = WorldGenerator(llm=traced_arbiter)

    runner = CognitiveAgentRunner(
        llm_provider=traced_agent,
        embedder=embedder,
        verbose=True,
    )
    runner.use_free_actions = True

    events_path = (
        RESULTS_DIR
        / f"{scenario.id.value}_{governance.value}_seed{seed}_events.jsonl"
    )

    env = Environment(
        scenario=scenario,
        governance=governance,
        runner=runner,
        seed=seed,
        arbiter=arbiter,
        world_generator=world_gen,
        tracer=tracer,
    )

    env.state.event_log.set_stream_path(events_path)
    console.print(f"\n[bold]Запуск симуляции: {scenario.id.value}/{governance.value} "
                  f"seed={seed}, раундов={scenario.max_rounds}[/bold]")
    console.print(f"Агентов: {len(scenario.agents)}")
    for a in scenario.agents:
        console.print(f"  {a.id}: {a.name} ({a.position})")

    start = time.time()
    result = env.run()
    elapsed = time.time() - start

    metrics = compute_metrics(result)
    diversity = action_diversity(result)
    depth = scheme_depth(result)
    rejection = arbiter_rejection_rate(result)
    corr = corruption_rate(result)
    detect = detection_rate(result)
    fp = false_positive_rate(result)

    oracle = ViolationOracle(llm=traced_arbiter)
    oracle_verdicts = oracle.analyze(result.events, result.messages, result.cases)
    case_div = case_diversity(result)
    oracle_metrics = compute_metrics_with_oracle(result, oracle_verdicts)

    env.state.event_log.close_stream()

    trace_path = (
        RESULTS_DIR
        / f"{scenario.id.value}_{governance.value}_seed{seed}_trace.jsonl"
    )
    tracer.save_jsonl(trace_path)

    run_result = {
        "scenario": scenario.id.value,
        "governance": governance.value,
        "seed": seed,
        "elapsed_seconds": round(elapsed, 1),
        "rounds": result.rounds_completed,
        "total_cases": metrics.total_cases,
        "cases_by_type": metrics.cases_by_type,
        "case_diversity": case_div,
        "violations_total": metrics.violations_total,
        "violations_detected": metrics.violations_detected,
        "precision": oracle_metrics.confusion.precision,
        "recall": oracle_metrics.confusion.recall,
        "f1": oracle_metrics.confusion.f1,
        "tp": oracle_metrics.confusion.tp,
        "fp": oracle_metrics.confusion.fp,
        "tn": oracle_metrics.confusion.tn,
        "fn": oracle_metrics.confusion.fn,
        "heuristic_f1": metrics.confusion.f1,
        "private_message_ratio": metrics.private_message_ratio,
        "action_diversity": diversity,
        "scheme_depth": depth,
        "arbiter_rejection_rate": rejection,
        "corruption_rate": corr,
        "detection_rate": detect,
        "false_positive_rate": fp,
        "total_tokens": tracer.total_tokens,
        "oracle_violations": len(oracle_verdicts),
        "oracle_f1": oracle_metrics.confusion.f1,
        "messages_count": len(result.messages),
        "agents_count": len(scenario.agents),
    }

    console.print(f"\n[bold green]Симуляция завершена за {elapsed:.1f}с[/bold green]")
    console.print(f"  Раундов: {result.rounds_completed}")
    console.print(f"  Дел: {metrics.total_cases}")
    console.print(f"  Нарушений: {metrics.violations_total}")
    console.print(f"  Обнаружено: {metrics.violations_detected}")
    console.print(f"  F1 (oracle): {oracle_metrics.confusion.f1:.3f}")
    console.print(f"  Токенов: {tracer.total_tokens}")

    return run_result


def main():
    """Главная функция: генерация агентов + запуск эксперимента."""
    console.rule("[bold]Эксперимент MAGISTRY: S2/G2 + вторичные агенты[/bold]")

    # 1. Собрать базовый конфиг S2 + G2
    console.print("\n[bold]1. Подготовка базового конфига S2/G2[/bold]")
    base = get_scenario(ScenarioId.S2)
    base = add_governance_agents(base, GovernanceMode.G2)
    base = base.model_copy(update={"max_rounds": MAX_ROUNDS, "seed": SEED})

    console.print(f"  Базовые агенты ({len(base.agents)}):")
    for a in base.agents:
        console.print(f"    {a.id}: {a.name} — {a.position}")

    # 2. Генерация вторичных агентов (или загрузка из кеша)
    if ENRICHED_PATH.exists():
        console.print(f"\n[bold]2. Загрузка обогащённого конфига из {ENRICHED_PATH}[/bold]")
        enriched_data = json.loads(ENRICHED_PATH.read_text(encoding="utf-8"))
        enriched = ScenarioConfig.model_validate(enriched_data)
        console.print(f"  Загружено {len(enriched.agents)} агентов")
    else:
        console.print(f"\n[bold]2. Генерация вторичных агентов (fam: {FAMILY_COUNT}, soc: {SOCIETY_COUNT})[/bold]")
        enriched = generate_secondary_agents(base)
        enriched_data = enriched.model_dump(mode="json")
        with open(ENRICHED_PATH, "w", encoding="utf-8") as f:
            json.dump(enriched_data, f, ensure_ascii=False, indent=2)
        console.print(f"\n[green]Конфиг сохранён: {ENRICHED_PATH}[/green]")

    # 4. Запуск эксперимента
    console.print(f"\n[bold]3. Запуск эксперимента ({MAX_ROUNDS} раундов, seed={SEED})[/bold]")
    result = run_experiment(enriched)

    # 5. Сохранить сводку
    summary_path = RESULTS_DIR / "summary_v5.json"
    existing = []
    if summary_path.exists():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:
            existing = []

    existing.append(result)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    console.print(f"\n[green]Сводка: {summary_path}[/green]")
    console.rule("[bold]Эксперимент завершён[/bold]")


if __name__ == "__main__":
    main()
