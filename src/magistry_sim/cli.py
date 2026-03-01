"""Интерфейс командной строки MAGISTRY-SIM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .enums import GovernanceMode, ScenarioId
from .environment import Environment
from .metrics import compute_metrics
from .scenarios import SCENARIOS, get_scenario


console = Console(file=sys.stdout, force_terminal=False)


def _list_scenarios() -> None:
    """Вывести таблицу доступных сценариев."""
    table = Table(title="Доступные сценарии")
    table.add_column("ID", style="bold")
    table.add_column("Название")
    table.add_column("Описание")
    table.add_column("Раундов", justify="right")
    table.add_column("Агентов", justify="right")

    for sid, cfg in SCENARIOS.items():
        table.add_row(
            sid.value,
            cfg.title,
            cfg.description[:60] + "..."
            if len(cfg.description) > 60
            else cfg.description,
            str(cfg.max_rounds),
            str(len(cfg.agents)),
        )

    console.print(table)


def _print_result(result, metrics) -> None:
    """Вывести результаты симуляции.

    Args:
        result: Результат симуляции.
        metrics: Метрики симуляции.
    """
    console.print()
    console.rule("[bold]Результаты симуляции[/bold]")

    # Основные параметры
    info_table = Table(show_header=False)
    info_table.add_column("Параметр", style="bold")
    info_table.add_column("Значение")
    info_table.add_row("Сценарий", result.scenario_id)
    info_table.add_row("Режим управления", result.governance)
    info_table.add_row("Зерно", str(result.seed))
    info_table.add_row("Раундов", str(result.rounds_completed))
    info_table.add_row("Всего дел", str(metrics.total_cases))
    console.print(info_table)

    # Дела по типам
    if metrics.cases_by_type:
        type_table = Table(title="Дела по типам")
        type_table.add_column("Тип")
        type_table.add_column("Количество", justify="right")
        for ct, count in metrics.cases_by_type.items():
            type_table.add_row(ct, str(count))
        console.print(type_table)

    # Матрица ошибок
    cm = metrics.confusion
    cm_table = Table(title="Матрица ошибок")
    cm_table.add_column("Показатель")
    cm_table.add_column("Значение", justify="right")
    cm_table.add_row("Истинные обнаружения (TP)", str(cm.tp))
    cm_table.add_row("Ложные тревоги (FP)", str(cm.fp))
    cm_table.add_row("Истинные пропуски (TN)", str(cm.tn))
    cm_table.add_row("Пропущенные нарушения (FN)", str(cm.fn))
    cm_table.add_row("Точность (Precision)", f"{cm.precision:.2f}")
    cm_table.add_row("Полнота (Recall)", f"{cm.recall:.2f}")
    cm_table.add_row("F1-мера", f"{cm.f1:.2f}")
    console.print(cm_table)

    # Репутация
    if result.final_reputation:
        rep_table = Table(title="Итоговая репутация")
        rep_table.add_column("Агент")
        rep_table.add_column("Оценка", justify="right")
        rep_table.add_column("Заморожен")
        for aid, rep in result.final_reputation.items():
            rep_table.add_row(
                aid,
                f"{rep['score']:.1f}",
                "да" if rep["frozen"] else "нет",
            )
        console.print(rep_table)

    # Дела
    if result.cases:
        cases_table = Table(title="Дела")
        cases_table.add_column("ID")
        cases_table.add_column("Тип")
        cases_table.add_column("Название")
        cases_table.add_column("Стадия")
        cases_table.add_column("Решение")
        for cid, case in result.cases.items():
            cases_table.add_row(
                cid,
                case.get("case_type", ""),
                case.get("title", "")[:30],
                case.get("stage", ""),
                (case.get("decision") or "-")[:40],
            )
        console.print(cases_table)

    console.print()


def _create_runner(
    runner_type: str,
    interview_path: str | None = None,
    interviews_dir: str | None = None,
    personalities_dir: str | None = None,
) -> object:
    """Создать runner указанного типа.

    Для runner-ов, требующих API-ключей (llm, crewai, cognitive),
    загружает переменные окружения через dotenv.

    Args:
        runner_type: Тип runner-а (llm, crewai, cognitive).
        interview_path: Путь к библиотеке интервью (JSONL).

    Returns:
        Экземпляр runner-а.
    """
    if runner_type in ("llm", "crewai"):
        import os

        from dotenv import load_dotenv
        load_dotenv()

        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")

        if runner_type == "crewai":
            from .agents import CrewAIAgentRunner
            return CrewAIAgentRunner(verbose=True)

        from .agents import LLMAgentRunner
        from .llm import create_provider
        provider = create_provider(mock=False)
        return LLMAgentRunner(llm_provider=provider, verbose=True)

    if runner_type == "cognitive":
        import os
        from pathlib import Path

        from dotenv import load_dotenv
        load_dotenv()

        from .cognitive_runner import CognitiveAgentRunner
        from .llm import create_embedding_provider, create_provider

        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")

        llm = create_provider(mock=False)
        embed_mode = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
        if embed_mode not in ("local", "openai"):
            embed_mode = "openai"
        embedder = create_embedding_provider(mock=False, provider=embed_mode)

        lib_path = Path(interview_path) if interview_path else None
        interviews_dir_path = Path(interviews_dir) if interviews_dir else None
        personalities_dir_path = Path(personalities_dir) if personalities_dir else None

        return CognitiveAgentRunner(
            llm_provider=llm,
            embedder=embedder,
            verbose=True,
            interview_library_path=lib_path,
            interviews_dir=interviews_dir_path,
            personalities_dir=personalities_dir_path,
        )

    raise RuntimeError(f"Unknown runner type: {runner_type}")


def _run_batch(args, scenario) -> None:
    """Выполнить пакетный запуск симуляций.

    Args:
        args: Аргументы командной строки.
        scenario: Конфигурация сценария.
    """
    from pathlib import Path

    from .batch import BatchRunner

    if args.batch_modes:
        try:
            modes = [
                GovernanceMode(m.strip())
                for m in args.batch_modes.split(",")
            ]
        except ValueError as exc:
            console.print(f"[red]Ошибка в --batch-modes: {exc}[/red]")
            sys.exit(1)
    else:
        modes = list(GovernanceMode)

    output_dir = Path(args.output_dir) if args.output_dir else Path("batch_results")
    base_seed = args.seed or 42

    console.print(
        f"[bold]Пакетный запуск: {scenario.title}, "
        f"{args.batch_runs} прогонов × {len(modes)} режимов[/bold]"
    )

    runner = BatchRunner(
        scenario=scenario,
        modes=modes,
        runs_per_mode=args.batch_runs,
        output_dir=output_dir,
        base_seed=base_seed,
    )
    results = runner.run()

    console.print(
        f"[green]Завершено: {len(results)} симуляций. "
        f"Результаты в {output_dir}[/green]"
    )


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="MAGISTRY-SIM: симуляция организационных процессов"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="S0",
        help="Идентификатор сценария (S0–S6)",
    )
    parser.add_argument(
        "--scenario-json",
        type=str,
        default=None,
        help="Путь к JSON-конфигу ScenarioConfig (перекрывает --scenario)",
    )
    parser.add_argument(
        "--governance",
        type=str,
        default=None,
        help="Режим управления (G0–G3)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Зерно генератора случайных чисел",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Количество раундов",
    )
    parser.add_argument(
        "--runner",
        type=str,
        default="cognitive",
        choices=["llm", "crewai", "cognitive"],
        help="Тип runner-а: llm, crewai, cognitive",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="Показать список сценариев",
    )
    parser.add_argument(
        "--jsonl",
        type=str,
        default=None,
        help="Сохранить журнал событий в JSONL",
    )
    parser.add_argument(
        "--summary-json",
        type=str,
        default=None,
        help="Сохранить метрики в JSON",
    )
    parser.add_argument(
        "--interviews",
        type=str,
        default=None,
        help="Путь к библиотеке интервью (JSONL)",
    )
    parser.add_argument(
        "--interviews-dir",
        type=str,
        default=None,
        help="Директория с per-personality/per-agent интервью (JSON) для fragment-based retrieval",
    )
    parser.add_argument(
        "--personalities-dir",
        type=str,
        default=None,
        help="Директория с личностями (архетипы JSON) для personality_archetype",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        default=False,
        help="Пакетный запуск: N прогонов по каждому режиму governance",
    )
    parser.add_argument(
        "--batch-runs",
        type=int,
        default=10,
        help="Количество прогонов на режим (для --batch)",
    )
    parser.add_argument(
        "--batch-modes",
        type=str,
        default=None,
        help="Режимы governance через запятую, например G0,G2,G3 (для --batch)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Директория для результатов пакетного запуска",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="sync",
        choices=["sync", "async"],
        help="Режим симуляции: sync (раундовый) или async (непрерывное время)",
    )
    parser.add_argument(
        "--parallel-agents",
        action="store_true",
        default=False,
        help="Параллельная генерация решений агентами (ускоряет LLM-симуляции)",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=None,
        help="Максимум потоков для --parallel-agents (по умолчанию: авто)",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        default=None,
        help="Время начала симуляции (ISO 8601), для async-режима",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        default=None,
        help="Время окончания симуляции (ISO 8601), для async-режима",
    )

    args = parser.parse_args()

    if args.list_scenarios:
        _list_scenarios()
        return

    if args.scenario_json:
        try:
            raw = json.loads(Path(args.scenario_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]Не удалось прочитать --scenario-json: {exc}[/red]")
            sys.exit(1)

        from .config import ScenarioConfig

        try:
            scenario = ScenarioConfig.model_validate(raw)
        except Exception as exc:
            console.print(f"[red]Некорректный ScenarioConfig в --scenario-json: {exc}[/red]")
            sys.exit(1)
    else:
        try:
            scenario_id = ScenarioId(args.scenario)
        except ValueError:
            console.print(
                f"[red]Неизвестный сценарий: {args.scenario}[/red]"
            )
            sys.exit(1)

        scenario = get_scenario(scenario_id)

    if args.rounds is not None:
        scenario = scenario.model_copy(
            update={"max_rounds": args.rounds}
        )

    if args.batch:
        _run_batch(args, scenario)
        return

    governance = None
    if args.governance:
        try:
            governance = GovernanceMode(args.governance)
        except ValueError:
            console.print(
                f"[red]Неизвестный режим: {args.governance}[/red]"
            )
            sys.exit(1)

    runner_type = args.runner or "cognitive"
    try:
        runner = _create_runner(
            runner_type,
            interview_path=args.interviews,
            interviews_dir=args.interviews_dir,
            personalities_dir=args.personalities_dir,
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if args.mode == "async":
        _run_async(args, scenario, runner)
    else:
        _run_sync(args, scenario, runner, governance)


def _run_sync(args, scenario, runner, governance) -> None:
    """Запуск синхронной (раундовой) симуляции."""
    env = Environment(
        scenario=scenario,
        governance=governance,
        runner=runner,
        seed=args.seed,
        parallel_agents=bool(args.parallel_agents),
        parallel_workers=args.parallel_workers,
    )

    console.print(
        f"[bold]Запуск (sync): {scenario.title} "
        f"({governance or scenario.governance.mode})[/bold]"
    )

    stream_path: Path | None = None
    if args.jsonl:
        stream_path = Path(args.jsonl)
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream_path.unlink(missing_ok=True)
        except OSError:
            pass
        env.state.event_log.set_stream_path(stream_path)

    try:
        result = env.run()
    finally:
        if stream_path is not None:
            env.state.event_log.close_stream()
    metrics = compute_metrics(result)

    _print_result(result, metrics)

    if args.jsonl:
        console.print(f"Журнал сохранён: {args.jsonl}")

    if args.summary_json:
        from dataclasses import asdict

        with open(args.summary_json, "w", encoding="utf-8") as f:
            json.dump(
                asdict(metrics), f, ensure_ascii=False, indent=2
            )
        console.print(f"Метрики сохранены: {args.summary_json}")


def _run_async(args, scenario, runner) -> None:
    """Запуск асинхронной (непрерывное время) симуляции."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from .async_environment import AsyncEnvironment
    from .narrator import WorldNarrator
    from .world_generator import WorldGenerator
    from .scenarios import add_governance_agents

    # Переопределение временных границ из CLI
    if args.start_time:
        try:
            scenario = scenario.model_copy(
                update={"start_time": datetime.fromisoformat(args.start_time)}
            )
        except ValueError:
            console.print(f"[red]Неверный формат --start-time: {args.start_time}[/red]")
            sys.exit(1)

    if args.end_time:
        try:
            scenario = scenario.model_copy(
                update={"end_time": datetime.fromisoformat(args.end_time)}
            )
        except ValueError:
            console.print(f"[red]Неверный формат --end-time: {args.end_time}[/red]")
            sys.exit(1)

    if args.seed is not None:
        scenario = scenario.model_copy(update={"seed": args.seed})

    # Режим управления и governance-агенты (как в sync).
    try:
        gov = GovernanceMode(args.governance) if args.governance else scenario.governance.mode
    except ValueError:
        console.print(f"[red]Неизвестный режим: {args.governance}[/red]")
        sys.exit(1)
    scenario = add_governance_agents(scenario, gov)

    # Создание LLM-провайдера для нарратора и мирового генератора
    llm = None
    try:
        from .llm import create_provider
        llm = create_provider(mock=False)
    except Exception:
        pass

    narrator = WorldNarrator() if llm is not None else None
    world_gen = WorldGenerator(llm=llm) if llm is not None else None

    env = AsyncEnvironment(
        config=scenario,
        runner=runner,
        llm=llm or runner._llm if hasattr(runner, "_llm") else None,
        narrator=narrator,
        world_generator=world_gen,
        parallel_agents=bool(args.parallel_agents),
        parallel_workers=args.parallel_workers,
    )

    console.print(
        f"[bold]Запуск (async): {scenario.title}[/bold]"
    )

    stream_path: Path | None = None
    if args.jsonl:
        stream_path = Path(args.jsonl)
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream_path.unlink(missing_ok=True)
        except OSError:
            pass
        env.state.event_log.set_stream_path(stream_path)

    try:
        result = asyncio.run(env.run())
    finally:
        if stream_path is not None:
            env.state.event_log.close_stream()

    console.print()
    console.rule("[bold]Результаты асинхронной симуляции[/bold]")

    info_table = Table(show_header=False)
    info_table.add_column("Параметр", style="bold")
    info_table.add_column("Значение")
    info_table.add_row("Сценарий", result.scenario_id)
    info_table.add_row("Зерно", str(result.seed))
    info_table.add_row("Начало", result.started_at)
    info_table.add_row("Конец", result.ended_at)
    info_table.add_row("Событий", str(result.events_count))
    info_table.add_row("Дел", str(len(result.cases)))
    console.print(info_table)

    if result.final_reputation:
        rep_table = Table(title="Итоговая репутация")
        rep_table.add_column("Агент")
        rep_table.add_column("Оценка", justify="right")
        rep_table.add_column("Заморожен")
        for aid, rep in result.final_reputation.items():
            rep_table.add_row(
                aid,
                f"{rep['score']:.1f}",
                "да" if rep["frozen"] else "нет",
            )
        console.print(rep_table)

    if args.jsonl:
        console.print(f"Журнал сохранён: {args.jsonl}")

    if args.summary_json:
        from dataclasses import asdict

        summary_data = {
            "scenario_id": result.scenario_id,
            "seed": result.seed,
            "started_at": result.started_at,
            "ended_at": result.ended_at,
            "events_count": result.events_count,
            "cases": result.cases,
            "final_reputation": result.final_reputation,
            "round_summaries": result.round_summaries,
            "agents": result.agents,
        }
        with open(args.summary_json, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        console.print(f"Результат сохранён: {args.summary_json}")


if __name__ == "__main__":
    main()
