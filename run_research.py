"""Исследование MAGISTRY v5: многосидовые прогоны с CognitiveAgentRunner.

Использует арбитр + генератор среды + LLM-классификатор.
Результаты сохраняются в results/.
"""

import argparse
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.table import Table

from magistry_sim.arbiter import Arbiter
from magistry_sim.cognitive_runner import CognitiveAgentRunner
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
from magistry_sim.scenarios import get_scenario
from magistry_sim.tracing import LLMTracer, TracingLLMProvider
from magistry_sim.world_generator import WorldGenerator

console = Console(file=sys.stdout)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="Исследование MAGISTRY v5")
parser.add_argument(
    "--seeds",
    type=int,
    default=10,
    help="Количество seed-ов на конфигурацию",
)
parser.add_argument(
    "--scenarios",
    nargs="+",
    default=["S0", "S1", "S2"],
    help="Сценарии",
)
parser.add_argument(
    "--governances",
    nargs="+",
    default=["G0", "G1", "G2", "G3"],
    help="Режимы",
)
parser.add_argument(
    "--model",
    default=None,
    help="Модель LLM (по умолчанию из OPENAI_MODEL)",
)
parser.add_argument(
    "--arbiter-model",
    default=None,
    help="Модель арбитра (дешёвая)",
)
parser.add_argument(
    "--max-rounds",
    type=int,
    default=None,
    help="Ограничить количество раундов (для тестов)",
)
parser.add_argument(
    "--agent-provider",
    nargs="+",
    default=None,
    help="Провайдеры OpenRouter для агентов (например: DeepInfra Groq)",
)
parser.add_argument(
    "--arbiter-provider",
    nargs="+",
    default=None,
    help="Провайдеры OpenRouter для арбитра (например: Groq Cerebras)",
)
parser.add_argument(
    "--tool-calls",
    action="store_true",
    help="Использовать function calling вместо json_schema (быстрее)",
)
parser.add_argument(
    "--verbose",
    action="store_true",
    help="Подробный вывод",
)


def run_single(
    scenario_id: ScenarioId,
    governance: GovernanceMode,
    agent_llm,
    arbiter_llm,
    embedder,
    seed: int = 42,
    verbose: bool = False,
    max_rounds: int | None = None,
) -> dict:
    """Выполнить один прогон и вернуть результаты.

    Создаёт трассировочные обёртки, арбитра, генератор среды
    и CognitiveAgentRunner. По завершении прогона вычисляет
    метрики v5, запускает LLM-классификатор и сохраняет трассу.

    Args:
        scenario_id: Идентификатор сценария.
        governance: Режим управления.
        agent_llm: Провайдер LLM для агентов.
        arbiter_llm: Провайдер LLM для арбитра и генератора.
        embedder: Провайдер эмбеддингов.
        seed: Зерно генератора случайных чисел.
        verbose: Подробный вывод.
        max_rounds: Ограничение числа раундов (None = из сценария).

    Returns:
        Словарь с результатами и метриками прогона.
    """
    scenario = get_scenario(scenario_id)
    if max_rounds is not None:
        scenario = scenario.model_copy(
            update={"max_rounds": max_rounds}
        )

    # Трассировка
    tracer = LLMTracer()
    traced_agent = TracingLLMProvider(
        inner=agent_llm, tracer=tracer, role="agent"
    )
    traced_arbiter = TracingLLMProvider(
        inner=arbiter_llm, tracer=tracer, role="arbiter"
    )

    # Арбитр и генератор
    arbiter = Arbiter(llm=traced_arbiter)
    world_gen = WorldGenerator(llm=traced_arbiter)

    # CognitiveAgentRunner
    runner = CognitiveAgentRunner(
        llm_provider=traced_agent,
        embedder=embedder,
        verbose=verbose,
    )
    runner.use_free_actions = True

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
    start = time.time()
    result = env.run()
    elapsed = time.time() - start

    # Метрики v5
    metrics = compute_metrics(result)
    diversity = action_diversity(result)
    depth = scheme_depth(result)
    rejection = arbiter_rejection_rate(result)
    corr = corruption_rate(result)
    detect = detection_rate(result)
    fp = false_positive_rate(result)

    # Оракул нарушений
    oracle = ViolationOracle(llm=traced_arbiter)
    oracle_verdicts = oracle.analyze(
        result.events, result.messages, result.cases
    )
    case_div = case_diversity(result)
    oracle_metrics = compute_metrics_with_oracle(result, oracle_verdicts)

    # Сохранение трассы
    trace_path = (
        RESULTS_DIR
        / f"{scenario_id.value}_{governance.value}_seed{seed}_trace.jsonl"
    )
    tracer.save_jsonl(trace_path)

    # Сохранить журнал событий
    events_path = (
        RESULTS_DIR
        / f"{scenario_id.value}_{governance.value}_seed{seed}_events.jsonl"
    )
    env.state.event_log.save_jsonl(events_path)

    return {
        "scenario": scenario_id.value,
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
    }


def _print_aggregate_table(
    all_results: list[dict],
    scenarios: list[ScenarioId],
    governances: list[GovernanceMode],
) -> None:
    """Вывести агрегированную таблицу (медианы по seed-ам).

    Args:
        all_results: Список словарей-результатов всех прогонов.
        scenarios: Список сценариев.
        governances: Список режимов управления.
    """
    table = Table(title="Агрегированные результаты (медиана по seed-ам)")
    table.add_column("Сценарий")
    table.add_column("Режим")
    table.add_column("N", justify="right")
    table.add_column("Дел", justify="right")
    table.add_column("Типы дел", justify="right")
    table.add_column("Нарушений", justify="right")
    table.add_column("Обнаружено", justify="right")
    table.add_column("F1", justify="right")
    table.add_column("Diversity", justify="right")
    table.add_column("Reject%", justify="right")
    table.add_column("Токены", justify="right")

    for sid in scenarios:
        for gov in governances:
            subset = [
                r
                for r in all_results
                if r.get("scenario") == sid.value
                and r.get("governance") == gov.value
                and "error" not in r
            ]
            if not subset:
                table.add_row(
                    sid.value, gov.value, "0",
                    "-", "-", "-", "-", "-", "-", "-", "-",
                )
                continue

            n = len(subset)
            med_cases = statistics.median(
                [r["total_cases"] for r in subset]
            )
            med_case_div = statistics.median(
                [r.get("case_diversity", 0) for r in subset]
            )
            med_violations = statistics.median(
                [r["violations_total"] for r in subset]
            )
            med_detected = statistics.median(
                [r["violations_detected"] for r in subset]
            )
            med_f1 = statistics.median(
                [r["f1"] for r in subset]
            )
            med_diversity = statistics.median(
                [r["action_diversity"] for r in subset]
            )
            med_reject = statistics.median(
                [r["arbiter_rejection_rate"] for r in subset]
            )
            med_tokens = statistics.median(
                [r["total_tokens"] for r in subset]
            )

            table.add_row(
                sid.value,
                gov.value,
                str(n),
                f"{med_cases:.0f}",
                f"{med_case_div:.0f}",
                f"{med_violations:.0f}",
                f"{med_detected:.0f}",
                f"{med_f1:.2f}",
                f"{med_diversity:.0f}",
                f"{med_reject:.1%}",
                f"{med_tokens:.0f}",
            )

    console.print(table)


def main():
    """Запуск исследования MAGISTRY v5.

    Парсит аргументы командной строки, создаёт провайдеры LLM
    и эмбеддингов, выполняет прогоны для всех комбинаций
    сценариев, режимов и seed-ов, сохраняет сводный отчёт.
    """
    args = parser.parse_args()

    console.rule("[bold]Исследование MAGISTRY v5[/bold]")

    scenarios = [ScenarioId(s) for s in args.scenarios]
    governances = [GovernanceMode(g) for g in args.governances]
    total_runs = len(scenarios) * len(governances) * args.seeds

    console.print(
        f"Сценарии: {args.scenarios}, Режимы: {args.governances}"
    )
    console.print(
        f"Seed-ов на конфигурацию: {args.seeds}, "
        f"Всего прогонов: {total_runs}"
    )
    console.print()

    # Создание провайдеров
    agent_llm = create_provider(
        mock=False,
        model=args.model or os.getenv("OPENAI_MODEL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        cache_path=".llm_cache.db",
        provider_order=args.agent_provider,
    )

    arbiter_llm = create_provider(
        mock=False,
        model=args.arbiter_model
        or os.getenv("ARBITER_MODEL", "openai/gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        cache_path=".llm_cache.db",
        provider_order=args.arbiter_provider,
        use_tool_calls=args.tool_calls,
    )

    embedder = create_embedding_provider(
        mock=False,
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )

    all_results = []
    run_idx = 0

    for sid in scenarios:
        for gov in governances:
            for seed_offset in range(args.seeds):
                seed = 42 + seed_offset
                run_idx += 1
                label = f"{sid.value}/{gov.value}/seed={seed}"
                console.print(
                    f"[bold][{run_idx}/{total_runs}] {label}[/bold] ...",
                    end=" ",
                )

                try:
                    result = run_single(
                        sid, gov,
                        agent_llm, arbiter_llm, embedder,
                        seed, args.verbose,
                        max_rounds=args.max_rounds,
                    )
                    all_results.append(result)
                    console.print(
                        f"[green]OK[/green] "
                        f"({result['elapsed_seconds']}s, "
                        f"дел: {result['total_cases']}, "
                        f"токенов: {result['total_tokens']})"
                    )
                except Exception as exc:
                    console.print(f"[red]ОШИБКА: {exc}[/red]")
                    all_results.append({
                        "scenario": sid.value,
                        "governance": gov.value,
                        "seed": seed,
                        "error": str(exc),
                    })

    # Сохранить общий отчёт
    summary_path = RESULTS_DIR / "summary_v5.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    console.print()
    console.rule("[bold]Сводные результаты[/bold]")

    _print_aggregate_table(all_results, scenarios, governances)

    console.print(f"\nОтчёт сохранён: {summary_path}")
    console.print(
        f"Трассы и журналы: {RESULTS_DIR}/*_trace.jsonl, "
        f"{RESULTS_DIR}/*_events.jsonl"
    )


if __name__ == "__main__":
    main()
