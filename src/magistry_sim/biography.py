"""Генератор биографий агентов на основе профиля личности."""

from __future__ import annotations

from typing import TYPE_CHECKING

from magistry_sim.personality import AgentPersonality

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider


def generate_biography(
    personality: AgentPersonality,
    name: str,
    position: str,
    llm: LLMProvider,
) -> str:
    """Генерирует биографию агента через LLM.

    Args:
        personality: Профиль личности HEXACO + Dark Triad.
        name: Имя агента.
        position: Должность.
        llm: Провайдер языковой модели.

    Returns:
        Текст биографии (1000-2000 слов).
    """
    h = personality.hexaco
    d = personality.dark_triad
    techniques = ", ".join(t.value for t in personality.neutralization_techniques)

    prompt = (
        f"Сгенерируй биографию для персонажа симуляции.\n\n"
        f"Имя: {name}\n"
        f"Должность: {position}\n\n"
        f"Профиль HEXACO (0-100):\n"
        f"- Честность-скромность: {h.honesty_humility}\n"
        f"- Эмоциональность: {h.emotionality}\n"
        f"- Экстраверсия: {h.extraversion}\n"
        f"- Доброжелательность: {h.agreeableness}\n"
        f"- Добросовестность: {h.conscientiousness}\n"
        f"- Открытость: {h.openness}\n\n"
        f"Тёмная триада (0-100):\n"
        f"- Нарциссизм: {d.narcissism}\n"
        f"- Макиавеллизм: {d.machiavellianism}\n"
        f"- Психопатия: {d.psychopathy}\n\n"
        f"Техники рационализации: {techniques or 'нет'}\n\n"
        f"Напиши связную биографию от третьего лица. Включи:\n"
        f"1. Детство и формирование характера\n"
        f"2. Профессиональный путь\n"
        f"3. Ключевые жизненные события\n"
        f"4. Отношение к деньгам и власти\n"
        f"5. Моральные установки\n\n"
        f"Биография должна быть 1000-2000 слов, без списков, "
        f"связным повествованием."
    )
    response = llm.generate(system="", user=prompt)
    return response.text.strip()
