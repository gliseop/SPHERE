# tests/test_biography.py
import pytest
from unittest.mock import MagicMock
from magistry_sim.personality import (
    AgentPersonality,
    HEXACOProfile,
    DarkTriadProfile,
    NeutralizationTechnique,
)
from magistry_sim.biography import generate_biography


class TestBiographyGeneration:
    def test_generates_text(self):
        personality = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=15,
                emotionality=30,
                extraversion=70,
                agreeableness=20,
                conscientiousness=50,
                openness=60,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=85, psychopathy=40
            ),
            neutralization_techniques=[NeutralizationTechnique.EVERYONE_DOES_IT],
        )
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(
            text="Козлов Иван Михайлович родился в 1981 году в семье "
                 "военнослужащего. С детства привык добиваться своего..."
        )
        bio = generate_biography(
            personality=personality,
            name="Козлов И.М.",
            position="начальник отдела закупок",
            llm=mock_llm,
        )
        assert len(bio) > 50
        assert isinstance(bio, str)

    def test_prompt_includes_hexaco(self):
        personality = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=90,
                emotionality=50,
                extraversion=50,
                agreeableness=60,
                conscientiousness=85,
                openness=50,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=10, machiavellianism=15, psychopathy=5
            ),
            neutralization_techniques=[],
        )
        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(text="biography text")
        generate_biography(
            personality=personality,
            name="Иванов А.А.",
            position="аудитор",
            llm=mock_llm,
        )
        call_args = mock_llm.generate.call_args
        user_prompt = call_args.kwargs.get("user", "") or call_args[1].get("user", "")
        assert "90" in user_prompt  # honesty_humility value
