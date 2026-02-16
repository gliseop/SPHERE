"""Генерация библиотеки синтетических интервью.

Матрица: 5 архетипов x 4 роли x 3 вариации = 60 интервью.
Результат: data/interview_library.jsonl

Запуск:
    python scripts/generate_interviews.py
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from magistry_sim.interviews import InterviewLibrary, generate_interview
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


def main() -> None:
    """Генерация 60 синтетических интервью."""
    llm = create_provider(mock=False)
    embedder = create_embedding_provider(mock=False)

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


if __name__ == "__main__":
    main()
