"""Модель личности агента на основе HEXACO и Тёмной триады."""

from enum import Enum

from pydantic import BaseModel, Field


class NeutralizationTechnique(str, Enum):
    """Техники нейтрализации по Sykes & Matza (1957).

    Attributes:
        DENIAL_OF_INJURY: Отрицание ущерба.
        DENIAL_OF_VICTIM: Отрицание жертвы.
        CONDEMNATION_OF_CONDEMNERS: Осуждение осуждающих.
        APPEAL_TO_HIGHER_LOYALTIES: Апелляция к высшим ценностям.
        DENIAL_OF_RESPONSIBILITY: Отрицание ответственности.
        EVERYONE_DOES_IT: «Все так делают».
        CLAIM_OF_ENTITLEMENT: Претензия на право.
        DEFENSE_OF_NECESSITY: Защита необходимостью.
    """

    DENIAL_OF_INJURY = "denial_of_injury"
    DENIAL_OF_VICTIM = "denial_of_victim"
    CONDEMNATION_OF_CONDEMNERS = "condemnation_of_condemners"
    APPEAL_TO_HIGHER_LOYALTIES = "appeal_to_higher_loyalties"
    DENIAL_OF_RESPONSIBILITY = "denial_of_responsibility"
    EVERYONE_DOES_IT = "everyone_does_it"
    CLAIM_OF_ENTITLEMENT = "claim_of_entitlement"
    DEFENSE_OF_NECESSITY = "defense_of_necessity"


class HEXACOProfile(BaseModel, extra="forbid"):
    """Шестифакторная модель личности (Ashton & Lee, 2007).

    Attributes:
        honesty_humility: Честность-скромность (0-100).
        emotionality: Эмоциональность (0-100).
        extraversion: Экстраверсия (0-100).
        agreeableness: Доброжелательность (0-100).
        conscientiousness: Добросовестность (0-100).
        openness: Открытость опыту (0-100).
    """

    honesty_humility: int = Field(ge=0, le=100)
    emotionality: int = Field(ge=0, le=100)
    extraversion: int = Field(ge=0, le=100)
    agreeableness: int = Field(ge=0, le=100)
    conscientiousness: int = Field(ge=0, le=100)
    openness: int = Field(ge=0, le=100)


class DarkTriadProfile(BaseModel, extra="forbid"):
    """Тёмная триада (Paulhus & Williams, 2002).

    Attributes:
        narcissism: Нарциссизм (0-100).
        machiavellianism: Макиавеллизм (0-100).
        psychopathy: Психопатия (0-100).
    """

    narcissism: int = Field(ge=0, le=100)
    machiavellianism: int = Field(ge=0, le=100)
    psychopathy: int = Field(ge=0, le=100)


CORRUPTION_ARCHETYPES: list[str] = [
    "idealist",
    "pragmatist",
    "opportunist",
    "initiator",
    "machiavellist",
]


class AgentPersonality(BaseModel, extra="forbid"):
    """Полная модель личности агента.

    Объединяет шестифакторный профиль HEXACO, профиль Тёмной триады
    и набор техник нейтрализации для моделирования коррупционного
    поведения в симуляции.

    Attributes:
        hexaco: Профиль HEXACO.
        dark_triad: Профиль Тёмной триады.
        neutralization_techniques: Доступные техники нейтрализации.
        biography: Биография агента.
    """

    hexaco: HEXACOProfile
    dark_triad: DarkTriadProfile
    neutralization_techniques: list[NeutralizationTechnique] = []
    biography: str = ""

    def classify_archetype(self) -> str:
        """Определяет архетип коррупционного поведения по параметрам.

        Классификация основана на пороговых значениях факторов HEXACO
        и Тёмной триады. Проверки идут от наиболее специфичного
        архетипа к наименее специфичному.

        Returns:
            Название архетипа из CORRUPTION_ARCHETYPES.
        """
        hh = self.hexaco.honesty_humility
        con = self.hexaco.conscientiousness
        agr = self.hexaco.agreeableness
        narc = self.dark_triad.narcissism
        mach = self.dark_triad.machiavellianism
        psyc = self.dark_triad.psychopathy
        dark_max = max(narc, mach, psyc)

        if hh >= 80 and con >= 80 and dark_max < 20:
            return "idealist"
        if hh <= 15 and agr <= 15 and mach >= 90 and psyc >= 70:
            return "machiavellist"
        if hh <= 20 and mach >= 80 and narc >= 70:
            return "initiator"
        if hh <= 40 and narc >= 50:
            return "opportunist"
        return "pragmatist"
