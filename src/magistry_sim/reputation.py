"""Репутационная система: социальный капитал и его динамика."""

from __future__ import annotations

from magistry_sim.models import Agent


def compute_tick_growth(
    agent: Agent,
    *,
    tender_won: bool,
    tender_budget: float,
    is_juror: bool = False,
    whistleblower: bool = False,
    tick: int = 0,
) -> dict[str, float]:
    """Вычислить прирост социального капитала за тик.

    Args:
        agent: Агент.
        tender_won: Выиграл ли тендер.
        tender_budget: Бюджет тендера.
        is_juror: Участвовал ли в трибунале как присяжный.
        whistleblower: Подал ли сигнал как whistleblower.
        tick: Номер тика.

    Returns:
        Dict с ключами work, research, social.
    """
    growth: dict[str, float] = {"work": 0.0, "research": 0.0, "social": 0.0}

    # Work: выигрыш тендера + компетентность
    if tender_won:
        growth["work"] = 8.0 + agent.competence * 4.0

    # Research: вероятностно, зависит от компетентности
    if agent.competence > 0.7 and tick % 3 == 0:
        growth["research"] = 3.0 + agent.competence * 2.0

    # Social: участие в governance
    if is_juror:
        growth["social"] = 5.0
    if whistleblower:
        growth["social"] += 8.0  # Бонус за civic duty

    return growth


def apply_growth(agent: Agent, *, work: float = 0.0, research: float = 0.0, social: float = 0.0) -> None:
    """Начислить социальный капитал (заморозка блокирует рост).

    Args:
        agent: Агент.
        work: Прирост work-капитала.
        research: Прирост research-капитала.
        social: Прирост social-капитала.
    """
    if agent.frozen:
        return

    agent.social_capital.work += max(0.0, work)
    agent.social_capital.research += max(0.0, research)
    agent.social_capital.social += max(0.0, social)


def freeze(agent: Agent) -> None:
    """Заморозить рост репутации."""
    agent.frozen = True


def unfreeze(agent: Agent) -> None:
    """Снять заморозку роста репутации."""
    agent.frozen = False
