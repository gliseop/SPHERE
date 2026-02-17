"""Нарративная сводка мира для симуляции."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider


_ROUND_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events_summary": {"type": "string"},
        "key_decisions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "tensions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "agent_motivations": {
            "type": "object",
        },
    },
    "required": [
        "events_summary", "key_decisions", "tensions", "agent_motivations",
    ],
}


_SIMULATION_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "key_findings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "outcome": {"type": "string"},
    },
    "required": ["narrative", "key_findings", "outcome"],
}


@dataclass
class RoundSummary:
    """Сводка одного раунда симуляции.

    Attributes:
        round_num: Номер раунда.
        events_summary: Текстовое описание событий раунда.
        key_decisions: Ключевые решения, принятые агентами.
        tensions: Напряжённости и конфликты.
        agent_motivations: Мотивации агентов (agent_id → описание).
    """

    round_num: int
    events_summary: str
    key_decisions: list[str] = field(default_factory=list)
    tensions: list[str] = field(default_factory=list)
    agent_motivations: dict[str, str] = field(default_factory=dict)


class WorldNarrator:
    """Генератор нарративных сводок мира.

    Формирует текстовые сводки по каждому раунду и итоговый нарратив
    всей симуляции через LLM с использованием structured output.
    """

    def __init__(self) -> None:
        self._round_summaries: list[RoundSummary] = []

    @property
    def round_summaries(self) -> list[RoundSummary]:
        """Накопленные сводки раундов."""
        return list(self._round_summaries)

    def summarize_round(
        self,
        round_num: int,
        events: list[dict[str, Any]],
        agent_ids: list[str],
        llm: LLMProvider,
    ) -> RoundSummary:
        """Сгенерировать сводку раунда через LLM.

        Args:
            round_num: Номер раунда.
            events: Список событий раунда (словари с event_type, agent_id и т.д.).
            agent_ids: Идентификаторы всех агентов в симуляции.
            llm: Провайдер языковой модели.

        Returns:
            Сводка раунда.
        """
        events_text = "\n".join(
            f"- {e.get('agent_id', '?')}: {e.get('event_type', 'действие')}"
            for e in events
        ) if events else "Нет событий."

        agents_text = ", ".join(agent_ids)

        result = llm.generate_structured(
            system=(
                "Ты — нарратор симуляции организационных процессов. "
                "На основе событий раунда создай краткую, но информативную "
                "сводку: что произошло, какие решения были приняты, какие "
                "напряжённости возникли, и какими мотивами руководствовались "
                "участники."
            ),
            user=(
                f"Раунд {round_num}.\n\n"
                f"Участники: {agents_text}\n\n"
                f"События раунда:\n{events_text}\n\n"
                f"Составь сводку раунда."
            ),
            schema=_ROUND_SUMMARY_SCHEMA,
        )

        data = result.data
        summary = RoundSummary(
            round_num=round_num,
            events_summary=data.get("events_summary", ""),
            key_decisions=data.get("key_decisions", []),
            tensions=data.get("tensions", []),
            agent_motivations=data.get("agent_motivations", {}),
        )

        self._round_summaries.append(summary)
        return summary

    def summarize_simulation(
        self,
        summaries: list[RoundSummary] | None = None,
        final_reputation: dict[str, float] | None = None,
        llm: LLMProvider | None = None,
    ) -> dict[str, Any]:
        """Сгенерировать итоговый нарратив симуляции.

        Args:
            summaries: Список сводок раундов (по умолчанию — накопленные).
            final_reputation: Итоговые оценки репутации агентов.
            llm: Провайдер языковой модели.

        Returns:
            Словарь с итоговым нарративом, ключевыми выводами и исходом.
        """
        all_summaries = summaries or self._round_summaries
        rep = final_reputation or {}

        rounds_text = "\n\n".join(
            f"Раунд {s.round_num}: {s.events_summary}"
            for s in all_summaries
        )

        rep_text = "\n".join(
            f"- {agent_id}: {score:.1f}"
            for agent_id, score in rep.items()
        ) if rep else "Нет данных."

        result = llm.generate_structured(
            system=(
                "Ты — аналитик симуляции организационных процессов. "
                "На основе хронологии раундов и итоговой репутации "
                "участников составь итоговый нарратив: что произошло, "
                "какие закономерности обнаружены, каков итог."
            ),
            user=(
                f"Хронология симуляции:\n{rounds_text}\n\n"
                f"Итоговая репутация:\n{rep_text}\n\n"
                f"Составь итоговый нарратив симуляции."
            ),
            schema=_SIMULATION_SUMMARY_SCHEMA,
        )

        return result.data
