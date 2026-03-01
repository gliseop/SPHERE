"""Генерация библиотеки синтетических интервью.

Два режима:
  1. Библиотека (по умолчанию): матрица 5 архетипов x 4 роли x 3 вариации = 60 интервью.
     Результат: data/interview_library.jsonl
  2. Per-personality (--per-personality): читает data/personalities/*.json, для каждого
     генерирует расширенное интервью (30 вопросов), сохраняет в data/interviews/.

Запуск:
    python scripts/generate_interviews.py                  # библиотека
    python scripts/generate_interviews.py --per-personality # per-personality
    python scripts/generate_interviews.py --per-personality --role аудитор
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from magistry_sim.interviews import InterviewLibrary, generate_interview, save_interview
from magistry_sim.llm import create_embedding_provider, create_provider
from magistry_sim.personality import (
    CORRUPTION_ARCHETYPES,
    AgentPersonality,
    DarkTriadProfile,
    HEXACOProfile,
)

ROLES = ["чиновник", "бизнесмен", "аудитор", "кандидат"]

# Базовые профили для каждого архетипа (3 вариации)
ARCHETYPE_PROFILES: dict[str, list[dict]] = {
    "idealist": [
        {"hh": 90, "em": 50, "ex": 60, "ag": 75, "co": 85, "op": 60,
         "na": 10, "ma": 5, "ps": 5},
        {"hh": 85, "em": 60, "ex": 55, "ag": 70, "co": 90, "op": 65,
         "na": 15, "ma": 10, "ps": 5},
        {"hh": 95, "em": 45, "ex": 65, "ag": 80, "co": 80, "op": 55,
         "na": 5, "ma": 5, "ps": 10},
    ],
    "pragmatist": [
        {"hh": 55, "em": 45, "ex": 60, "ag": 55, "co": 70, "op": 50,
         "na": 35, "ma": 40, "ps": 20},
        {"hh": 50, "em": 50, "ex": 55, "ag": 50, "co": 65, "op": 55,
         "na": 40, "ma": 35, "ps": 25},
        {"hh": 60, "em": 40, "ex": 65, "ag": 60, "co": 75, "op": 45,
         "na": 30, "ma": 45, "ps": 15},
    ],
    "opportunist": [
        {"hh": 30, "em": 40, "ex": 65, "ag": 35, "co": 45, "op": 55,
         "na": 60, "ma": 70, "ps": 30},
        {"hh": 35, "em": 45, "ex": 70, "ag": 30, "co": 40, "op": 60,
         "na": 55, "ma": 65, "ps": 35},
        {"hh": 25, "em": 35, "ex": 60, "ag": 40, "co": 50, "op": 50,
         "na": 65, "ma": 75, "ps": 25},
    ],
    "initiator": [
        {"hh": 15, "em": 30, "ex": 80, "ag": 25, "co": 55, "op": 65,
         "na": 75, "ma": 85, "ps": 40},
        {"hh": 20, "em": 25, "ex": 75, "ag": 20, "co": 50, "op": 70,
         "na": 80, "ma": 80, "ps": 45},
        {"hh": 10, "em": 35, "ex": 85, "ag": 30, "co": 60, "op": 60,
         "na": 70, "ma": 90, "ps": 35},
    ],
    "machiavellist": [
        {"hh": 10, "em": 20, "ex": 70, "ag": 10, "co": 65, "op": 55,
         "na": 85, "ma": 95, "ps": 75},
        {"hh": 5, "em": 15, "ex": 75, "ag": 15, "co": 70, "op": 50,
         "na": 90, "ma": 90, "ps": 80},
        {"hh": 15, "em": 25, "ex": 65, "ag": 5, "co": 60, "op": 60,
         "na": 80, "ma": 95, "ps": 70},
    ],
}


def generate_library(llm, embedder) -> None:
    """Генерация 60 синтетических интервью в библиотеку."""
    lib = InterviewLibrary()
    counter = 0

    for archetype in CORRUPTION_ARCHETYPES:
        profiles = ARCHETYPE_PROFILES[archetype]
        for role in ROLES:
            for var_idx, params in enumerate(profiles):
                counter += 1
                interview_id = f"int_{counter:03d}"
                print(
                    f"[{counter}/60] {archetype}/{role}/v{var_idx} -> {interview_id}"
                )

                personality = AgentPersonality(
                    hexaco=HEXACOProfile(
                        honesty_humility=params["hh"],
                        emotionality=params["em"],
                        extraversion=params["ex"],
                        agreeableness=params["ag"],
                        conscientiousness=params["co"],
                        openness=params["op"],
                    ),
                    dark_triad=DarkTriadProfile(
                        narcissism=params["na"],
                        machiavellianism=params["ma"],
                        psychopathy=params["ps"],
                    ),
                )

                interview = generate_interview(
                    personality=personality,
                    role=role,
                    archetype=archetype,
                    llm=llm,
                    embedder=embedder,
                    interview_id=interview_id,
                )
                lib.add(interview)

    output_path = Path("data/interview_library.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lib.save_jsonl(output_path)
    print(f"\nГотово: {len(lib)} интервью сохранено в {output_path}")


def generate_per_personality(llm, embedder, role: str) -> None:
    """Генерация расширенных интервью для каждой личности из data/personalities/."""
    personalities_dir = Path("data/personalities")
    interviews_dir = Path("data/interviews")

    if not personalities_dir.exists():
        print(f"Директория {personalities_dir} не найдена")
        sys.exit(1)

    files = sorted(personalities_dir.glob("*.json"))
    if not files:
        print(f"Нет файлов личностей в {personalities_dir}")
        sys.exit(1)

    total = len(files)
    for idx, path in enumerate(files, 1):
        personality_id = path.stem
        print(f"[{idx}/{total}] {personality_id}")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  Ошибка чтения {path}: {exc}")
            continue

        hexaco_raw = raw.get("hexaco", {})
        dt_raw = raw.get("dark_triad", {})
        personality = AgentPersonality(
            hexaco=HEXACOProfile(**hexaco_raw),
            dark_triad=DarkTriadProfile(**dt_raw),
            neutralization_techniques=raw.get("neutralization_techniques", []),
            biography=raw.get("biography", ""),
        )

        archetype = personality.classify_archetype()
        print(f"  архетип: {archetype}, роль: {role}")

        interview = generate_interview(
            personality=personality,
            role=role,
            archetype=archetype,
            llm=llm,
            embedder=embedder,
            interview_id=personality_id,
            use_extended_protocol=True,
        )

        out_path = save_interview(interview, interviews_dir)
        print(f"  сохранено: {out_path}")

    print(f"\nГотово: интервью для {total} личностей сохранены в {interviews_dir}")


def main() -> None:
    """Точка входа."""
    parser = argparse.ArgumentParser(
        description="Генерация синтетических интервью для MAGISTRY",
    )
    parser.add_argument(
        "--per-personality",
        action="store_true",
        help="Генерировать расширенное интервью (30 вопросов) для каждой личности из data/personalities/",
    )
    parser.add_argument(
        "--role",
        default="чиновник",
        help="Роль персонажа для per-personality режима (по умолчанию: чиновник)",
    )
    args = parser.parse_args()

    llm = create_provider(mock=False)
    embedder = create_embedding_provider(mock=False)

    if args.per_personality:
        generate_per_personality(llm, embedder, role=args.role)
    else:
        generate_library(llm, embedder)


if __name__ == "__main__":
    main()
