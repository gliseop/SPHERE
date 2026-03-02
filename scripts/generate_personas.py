"""Пакетная генерация персон (личность + интервью) для агентов сценария.

Обёртка над ``generate_personas_parallel`` из ``magistry_sim.persona_generator``.

Запуск:
    python scripts/generate_personas.py --scenario S1
    python scripts/generate_personas.py --scenario-json path/to/config.json
    python scripts/generate_personas.py --scenario S2 --seed 123 --max-workers 3
    python scripts/generate_personas.py --scenario S1 --output-dir ./my_personas --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> None:
    """Точка входа скрипта генерации персон."""
    parser = argparse.ArgumentParser(
        description="Пакетная генерация персон для агентов сценария",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Идентификатор встроенного сценария (S0-S6)",
    )
    parser.add_argument(
        "--scenario-json",
        type=str,
        default=None,
        help="Путь к JSON-файлу ScenarioConfig (перекрывает --scenario)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Зерно генератора (по умолчанию: 42)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Максимум параллельных потоков (по умолчанию: 5)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Директория для результатов. По умолчанию: data/personalities + data/interviews",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Перегенерировать персоны даже для агентов с personality_archetype",
    )
    args = parser.parse_args()

    if not args.scenario and not args.scenario_json:
        parser.error("Укажите --scenario или --scenario-json")

    from magistry_sim.config import ScenarioConfig
    from magistry_sim.enums import ScenarioId
    from magistry_sim.llm import create_embedding_provider, create_provider
    from magistry_sim.persona_generator import generate_personas_parallel
    from magistry_sim.scenarios import get_scenario

    if args.scenario_json:
        try:
            raw = json.loads(Path(args.scenario_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Ошибка чтения --scenario-json: {exc}", file=sys.stderr)
            sys.exit(1)
        config = ScenarioConfig.model_validate(raw)
    else:
        try:
            config = get_scenario(ScenarioId(args.scenario))
        except (ValueError, KeyError) as exc:
            print(f"Неизвестный сценарий: {exc}", file=sys.stderr)
            sys.exit(1)

    root = Path(__file__).resolve().parent.parent
    if args.output_dir:
        out_base = Path(args.output_dir)
        personalities_dir = out_base / "personalities"
        interviews_dir = out_base / "interviews"
    else:
        personalities_dir = root / "data" / "personalities"
        interviews_dir = root / "data" / "interviews"

    llm = create_provider(mock=False)
    embedder = create_embedding_provider(mock=False)

    print(
        f"Генерация персон: {len(config.agents)} агентов, "
        f"seed={args.seed}, workers={args.max_workers}, "
        f"force={args.force}"
    )
    print(f"  personalities -> {personalities_dir}")
    print(f"  interviews    -> {interviews_dir}")

    updated_config, artifacts = generate_personas_parallel(
        config,
        llm=llm,
        embedder=embedder,
        out_personalities_dir=personalities_dir,
        out_interviews_dir=interviews_dir,
        max_workers=args.max_workers,
        seed=args.seed,
        force=args.force,
    )

    print(f"\nГотово: {len(artifacts)} персон сгенерировано.")
    for art in artifacts:
        print(f"  {art.agent_id} -> {art.personality_path.name}")


if __name__ == "__main__":
    main()
