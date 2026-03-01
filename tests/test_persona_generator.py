"""Тесты параллельной генерации персон (личность + интервью)."""

from __future__ import annotations

from pathlib import Path

from magistry_sim.config import AgentProfile, ScenarioConfig
from magistry_sim.enums import ScenarioId
from magistry_sim.llm import MockEmbeddingProvider, MockLLMProvider
from magistry_sim.persona_generator import generate_personas_parallel


def test_generate_personas_parallel_writes_files(tmp_path: Path) -> None:
    llm = MockLLMProvider(
        structured_responses={
            # Seed generation
            "Нужно создать seed-профили": {
                "seeds": [
                    {
                        "age": 35,
                        "gender": "male",
                        "education": "Экономическое, региональный вуз",
                        "origin": "Небольшой промышленный город",
                        "family_situation": "Женат, один ребенок",
                        "life_events": [
                            "Срочная ротация в другой регион из-за кризиса",
                            "Конфликт с начальником из-за нарушений процедур",
                        ],
                        "temperament_hint": "осторожный, расчётливый",
                    }
                ]
            },
            # Personality generation
            "Seed-профиль": {
                "biography": "Краткая биография персонажа в 3–5 абзацев.",
                "hexaco": {
                    "honesty_humility": 45,
                    "emotionality": 40,
                    "extraversion": 55,
                    "agreeableness": 35,
                    "conscientiousness": 70,
                    "openness": 50,
                },
                "dark_triad": {
                    "narcissism": 40,
                    "machiavellianism": 55,
                    "psychopathy": 20,
                },
                "neutralization_techniques": [],
            },
        }
    )
    embedder = MockEmbeddingProvider(dimensions=384)

    cfg = ScenarioConfig(
        id=ScenarioId.S0,
        title="T",
        description="D",
        agents=[
            AgentProfile(
                id="agent_1",
                name="Агент 1",
                position="чиновник",
            )
        ],
    )

    out_personalities = tmp_path / "personalities"
    out_interviews = tmp_path / "interviews"

    updated, artifacts = generate_personas_parallel(
        cfg,
        llm=llm,
        embedder=embedder,
        out_personalities_dir=out_personalities,
        out_interviews_dir=out_interviews,
        max_workers=2,
        seed=123,
    )

    assert len(artifacts) == 1
    assert updated.agents[0].personality_archetype == "agent_1"
    assert updated.agents[0].personality is None

    assert (out_personalities / "agent_1.json").exists()
    assert (out_interviews / "agent_1.json").exists()

