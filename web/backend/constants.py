"""Константы, ранее получавшиеся из удалённого legacy-пакета симуляции.

После выноса движка в `sphere_lc` значения перечислений оставлены здесь, чтобы
web-backend мог продолжать работать для CRUD-операций.
"""

from __future__ import annotations

from enum import Enum

# ── Режимы управления ──────────────────────────────────────────────


class GovernanceMode(str, Enum):
    """Режим управления организацией (legacy enum для web CRUD)."""

    G0 = "G0"
    G1 = "G1"
    G2 = "G2"
    G3 = "G3"


# ── Идентификаторы сценариев ───────────────────────────────────────


class ScenarioId(str, Enum):
    """Идентификаторы встроенных сценариев (legacy enum для web CRUD)."""

    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    S6 = "S6"


# ── Техники нейтрализации ─────────────────────────────────────────

NEUTRALIZATION_TECHNIQUES: list[str] = [
    "denial_of_injury",
    "denial_of_victim",
    "condemnation_of_condemners",
    "appeal_to_higher_loyalties",
    "denial_of_responsibility",
    "everyone_does_it",
    "claim_of_entitlement",
    "defense_of_necessity",
]

# ── Метки и описания режимов управления ────────────────────────────

GOVERNANCE_LABELS: dict[str, str] = {
    "G0": "Без контроля",
    "G1": "Аудитор (рекомендательный)",
    "G2": "Аудитор (санкции по репутации)",
    "G3": "Полный контроль (трибунал)",
}

GOVERNANCE_DESCRIPTIONS: dict[str, str] = {
    "G0": "Нет надзора со стороны аудитора или трибунала.",
    "G1": "Аудитор может наблюдать и давать рекомендации.",
    "G2": "Аудитор может рекомендовать заморозку репутации участников.",
    "G3": "Аудитор может инициировать трибунал; решение принимает коллегия присяжных.",
}
