from __future__ import annotations

from magistry_sim.models import Agent


def apply_growth(agent: Agent, *, work: float = 0.0, research: float = 0.0, social: float = 0.0) -> None:
    if agent.frozen:
        return

    agent.social_capital.work += max(0.0, work)
    agent.social_capital.research += max(0.0, research)
    agent.social_capital.social += max(0.0, social)


def freeze(agent: Agent) -> None:
    agent.frozen = True


def unfreeze(agent: Agent) -> None:
    agent.frozen = False

