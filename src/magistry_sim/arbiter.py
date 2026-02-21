"""LLM-арбитр: оценка допустимости свободных действий агентов."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .state_ops import StateOp, parse_state_ops
from .world_rules import WORLD_RULES, ARBITER_RESPONSE_SCHEMA

if TYPE_CHECKING:
    from .llm import LLMProvider
    from .state import WorldState

logger = logging.getLogger(__name__)


@dataclass
class ArbiterVerdict:
    """Вердикт арбитра по действию агента.

    Attributes:
        feasible: Реализуемо ли действие.
        state_changes: Список операций для применения к состоянию мира.
        side_effects: Побочные эффекты (улики, репутационные риски).
        narrative: Текстовое описание результата действия.
    """

    feasible: bool
    state_changes: list[StateOp] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    narrative: str = ""


class Arbiter:
    """LLM-арбитр для оценки свободных действий.

    Заменяет жёсткую систему инструментов: агенты описывают действия
    в произвольной форме, арбитр оценивает их реалистичность и формирует
    набор операций для применения к состоянию мира.

    Attributes:
        _llm: Провайдер языковой модели.
    """

    def __init__(self, llm: LLMProvider) -> None:
        """Инициализировать арбитра.

        Args:
            llm: Провайдер языковой модели.
        """
        self._llm = llm

    def build_world_snapshot(self, state: WorldState) -> str:
        """Построить сжатое описание состояния мира для арбитра.

        Формирует текстовый снимок, включающий информацию о раунде,
        агентах с репутацией, делах с предложениями, социальном графе
        и активных потребностях.

        Args:
            state: Состояние мира.

        Returns:
            Текстовое описание состояния мира.
        """
        parts: list[str] = []

        parts.append(f"Раунд: {state.round}")

        parts.append("\n## Агенты")
        for aid, profile in state.agents.items():
            rep = state.reputation.get(aid)
            rep_str = f", репутация={rep.score:.1f}" if rep else ""
            parts.append(
                f"- {aid}: {profile.name}, {profile.position}{rep_str}"
            )

        if state.cases:
            parts.append("\n## Дела")
            for cid, case in state.cases.items():
                parts.append(
                    f"- {cid}: {case.title} (тип={case.case_type}, "
                    f"стадия={case.stage}, владелец={case.owner_id})"
                )
                for p in case.proposals:
                    parts.append(
                        f"  Предложение: {p.author_id}: {p.content[:80]}"
                    )

        conns: list[tuple[str, str]] = []
        for aid in state.agents:
            for conn in state.graph.get_connections(aid):
                pair = tuple(sorted([aid, conn["agent_id"]]))
                if pair not in conns:
                    conns.append(pair)
        if conns:
            parts.append("\n## Социальный граф")
            for a, b in conns:
                strength = state.graph.get_strength(a, b)
                parts.append(f"- {a} <-> {b}: сила={strength:.1f}")

        if state.active_needs:
            parts.append("\n## Потребности")
            for need in state.active_needs:
                parts.append(
                    f"- {need.target_agent_id}: {need.description} "
                    f"(тип={need.case_type}, срочность={need.urgency})"
                )

        return "\n".join(parts)

    def evaluate(
        self,
        agent_id: str,
        description: str,
        target: str,
        justification: str,
        state: WorldState,
        round_num: int,
    ) -> ArbiterVerdict:
        """Оценить действие агента.

        Строит снимок мира, формирует пользовательский промпт
        с информацией об агенте и его действии, вызывает LLM
        для получения структурированного вердикта. При положительном
        решении парсит операции через parse_state_ops.

        Args:
            agent_id: Идентификатор агента.
            description: Описание действия.
            target: Цель действия.
            justification: Обоснование.
            state: Состояние мира.
            round_num: Номер раунда.

        Returns:
            Вердикт арбитра.
        """
        snapshot = self.build_world_snapshot(state)
        profile = state.agents.get(agent_id)
        agent_desc = (
            f"{profile.name} ({profile.position})"
            if profile
            else agent_id
        )

        user_prompt = (
            f"## Состояние мира\n{snapshot}\n\n"
            f"## Действие агента\n"
            f"Агент: {agent_desc} ({agent_id})\n"
            f"Действие: {description}\n"
            f"Цель: {target or 'не указана'}\n"
            f"Обоснование: {justification}\n\n"
            f"Оцени допустимость этого действия и верни JSON по схеме. "
            f"Ключевое слово для поиска: perform_action"
        )

        try:
            resp = self._llm.generate_structured(
                system=WORLD_RULES,
                user=user_prompt,
                schema=ARBITER_RESPONSE_SCHEMA,
            )
            data = resp.data
        except Exception as exc:
            logger.warning("Ошибка арбитра: %s", exc)
            return ArbiterVerdict(
                feasible=False,
                narrative=f"Ошибка арбитра: {exc}",
            )

        feasible = data.get("feasible", False)
        raw_changes = data.get("state_changes", [])
        side_effects = data.get("side_effects", [])
        narrative = data.get("narrative", "")

        state_changes = parse_state_ops(raw_changes) if feasible else []

        return ArbiterVerdict(
            feasible=feasible,
            state_changes=state_changes,
            side_effects=side_effects,
            narrative=narrative,
        )
