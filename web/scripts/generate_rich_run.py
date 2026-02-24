"""Генерация тестового rich-run через AsyncEnvironment.

Скрипт создаёт JSONL событий и names-файл в каталоге `results/`,
используя новый time-based движок вместо вручную составленного журнала.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from magistry_sim.async_environment import AsyncEnvironment
from magistry_sim.llm import MockLLMProvider
from magistry_sim.scenarios import get_scenario
from magistry_sim.enums import ScenarioId

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
ARTIFACTS_DIR = RESULTS_DIR / "artifacts"

MSK = timezone(timedelta(hours=3))


class RichRunRunner:
    """Простой scripted-runner для генерации насыщенного потока событий."""

    def __init__(self) -> None:
        self._talked = False
        self._doc_done = False
        self._moved = False

    def run_turn(self, agent_id, situation, tools, state):  # noqa: ANN001
        del situation, tools, state
        if agent_id.startswith("off_") and not self._talked:
            self._talked = True
            return [
                {
                    "tool": "talk_to",
                    "args": {
                        "agent_id": "biz_1",
                        "message": "Коллеги, открываем закупку серверов. Нужны условия поставки.",
                        "private": True,
                        "channel": "telegram",
                    },
                }
            ]
        if agent_id.startswith("off_") and not self._doc_done:
            self._doc_done = True
            return [
                {
                    "tool": "create_document",
                    "args": {
                        "doc_type": "technical_specification",
                        "title": "ТЗ на закупку серверного оборудования",
                        "case_id": "D-001",
                        "context": (
                            "Подготовить ТЗ на закупку серверов для муниципального ЦОД, "
                            "с требованиями к мощности, отказоустойчивости и срокам поставки."
                        ),
                    },
                }
            ]
        if agent_id.startswith("biz_") and not self._moved:
            self._moved = True
            return [
                {
                    "tool": "move_to",
                    "args": {"location": "meeting_room"},
                }
            ]
        return []

    def run_reply(self, agent_id, message, sender_id, context, state):  # noqa: ANN001
        del agent_id, message, sender_id, context, state
        return "Принято. Подготовлю проект и отправлю в течение часа."


async def generate(run_name: str) -> Path:
    """Сгенерировать rich-run и сохранить артефакты.

    Args:
        run_name: Базовое имя файлов результата.

    Returns:
        Путь к JSONL файлу событий.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    scenario = get_scenario(ScenarioId.S1).model_copy(
        update={
            "start_time": datetime(2026, 2, 16, 9, 0, tzinfo=MSK),
            "end_time": datetime(2026, 2, 16, 18, 0, tzinfo=MSK),
            "seed": 42,
        }
    )

    env = AsyncEnvironment(
        config=scenario,
        runner=RichRunRunner(),
        llm=MockLLMProvider(),
    )
    result = await env.run()

    events_path = RESULTS_DIR / f"{run_name}_events.jsonl"
    names_path = RESULTS_DIR / f"{run_name}_names.json"

    result.state.event_log.save_jsonl(events_path)

    names = {
        agent_id: profile.name
        for agent_id, profile in result.state.agents.items()
    }
    names_path.write_text(
        json.dumps(names, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for event in result.state.event_log.all_events:
        if event.event_type != "document_created":
            continue
        payload = event.payload
        doc_id = str(payload.get("doc_id", ""))
        content = str(payload.get("content", ""))
        if not doc_id:
            continue
        (ARTIFACTS_DIR / f"{doc_id}.md").write_text(
            content,
            encoding="utf-8",
        )

    return events_path


def main() -> None:
    """CLI-точка входа."""
    parser = argparse.ArgumentParser(
        description="Generate rich MAGISTRY run via AsyncEnvironment",
    )
    parser.add_argument(
        "--run-name",
        type=str,
        default="S1_G1_seed42",
        help="Базовое имя файла результата (без _events.jsonl)",
    )
    args = parser.parse_args()

    path = asyncio.run(generate(args.run_name))
    print(f"Generated events: {path}")


if __name__ == "__main__":
    main()
