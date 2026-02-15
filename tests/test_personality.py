# tests/test_personality.py
import pytest
from magistry_sim.personality import (
    HEXACOProfile,
    DarkTriadProfile,
    AgentPersonality,
    NeutralizationTechnique,
    CORRUPTION_ARCHETYPES,
)


class TestHEXACOProfile:
    def test_valid_profile(self):
        p = HEXACOProfile(
            honesty_humility=75,
            emotionality=50,
            extraversion=60,
            agreeableness=40,
            conscientiousness=80,
            openness=55,
        )
        assert p.honesty_humility == 75

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            HEXACOProfile(
                honesty_humility=101,
                emotionality=50,
                extraversion=60,
                agreeableness=40,
                conscientiousness=80,
                openness=55,
            )

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            HEXACOProfile(
                honesty_humility=-1,
                emotionality=50,
                extraversion=60,
                agreeableness=40,
                conscientiousness=80,
                openness=55,
            )


class TestDarkTriadProfile:
    def test_valid_profile(self):
        d = DarkTriadProfile(narcissism=30, machiavellianism=60, psychopathy=10)
        assert d.machiavellianism == 60

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            DarkTriadProfile(narcissism=110, machiavellianism=60, psychopathy=10)


class TestNeutralizationTechnique:
    def test_all_techniques_valid(self):
        for t in NeutralizationTechnique:
            assert isinstance(t.value, str)

    def test_has_denial_of_injury(self):
        assert NeutralizationTechnique.DENIAL_OF_INJURY.value == "denial_of_injury"


class TestAgentPersonality:
    def test_full_personality(self):
        p = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=20,
                emotionality=30,
                extraversion=80,
                agreeableness=25,
                conscientiousness=60,
                openness=70,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=90, psychopathy=70
            ),
            neutralization_techniques=[
                NeutralizationTechnique.EVERYONE_DOES_IT,
                NeutralizationTechnique.CLAIM_OF_ENTITLEMENT,
            ],
        )
        assert p.hexaco.honesty_humility == 20
        assert len(p.neutralization_techniques) == 2

    def test_classify_archetype(self):
        initiator = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=10,
                emotionality=30,
                extraversion=70,
                agreeableness=20,
                conscientiousness=50,
                openness=60,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=80, machiavellianism=85, psychopathy=40
            ),
            neutralization_techniques=[NeutralizationTechnique.CLAIM_OF_ENTITLEMENT],
        )
        archetype = initiator.classify_archetype()
        assert archetype in CORRUPTION_ARCHETYPES


class TestArchetypes:
    def test_idealist(self):
        p = AgentPersonality(
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
        assert p.classify_archetype() == "idealist"

    def test_machiavellist(self):
        p = AgentPersonality(
            hexaco=HEXACOProfile(
                honesty_humility=5,
                emotionality=20,
                extraversion=60,
                agreeableness=10,
                conscientiousness=40,
                openness=50,
            ),
            dark_triad=DarkTriadProfile(
                narcissism=70, machiavellianism=95, psychopathy=75
            ),
            neutralization_techniques=[
                NeutralizationTechnique.CONDEMNATION_OF_CONDEMNERS,
            ],
        )
        assert p.classify_archetype() == "machiavellist"
