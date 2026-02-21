"""Генератор событий среды: динамические потребности и мировые события."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .state_ops import StateOp, parse_state_ops

if TYPE_CHECKING:
    from .llm import LLMProvider
    from .state import WorldState

logger = logging.getLogger(__name__)


WORLD_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "state_changes": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["type", "description", "state_changes"],
                "additionalProperties": False,
            },
        },
        "narrative": {"type": "string"},
    },
    "required": ["events", "narrative"],
    "additionalProperties": False,
}


WORLD_GEN_SYSTEM = """Ты --- генератор событий в симуляции организационных процессов. \
По итогам раунда ты решаешь, какие новые события происходят в мире.

Примеры событий:
- Новая потребность организации (закупка, найм, бюджет, обучение, аттестация, ревизия)
- Внешняя проверка (реакция на отчёты аудитора)
- Утечка информации (приватные переговоры стали известны)
- Кадровые изменения (болезнь сотрудника)
- Изменение бюджета (сокращение, дополнительное финансирование)
- Организационные мероприятия (повышение квалификации, внутренний аудит)

Не генерируй более 2 событий за раунд. Можешь вернуть пустой список, если раунд прошёл спокойно.

Верни JSON по заданной схеме. Ключевое слово: generate_events"""


@dataclass
class WorldGenResult:
    """Результат генерации событий среды.

    Attributes:
        ops: Список операций, порождённых генератором.
        narrative: Краткое описание произошедших событий.
    """

    ops: list[StateOp] = field(default_factory=list)
    narrative: str = ""


class WorldGenerator:
    """Генератор мировых событий на основе LLM.

    Запускается в конце каждого раунда для динамического создания
    новых потребностей, внешних событий, утечек и прочих изменений
    в состоянии мира.

    Args:
        llm: Провайдер языковой модели (дешёвая модель).
    """

    def __init__(self, llm: "LLMProvider") -> None:
        self._llm = llm

    def generate(
        self,
        state: "WorldState",
        round_num: int,
        round_events: list[dict[str, Any]],
        org_context: str = "",
    ) -> WorldGenResult:
        """Сгенерировать события среды по итогам раунда.

        Собирает сводку текущего состояния мира (агенты, дела,
        потребности, события раунда) и передаёт её LLM для получения
        структурированного ответа с новыми мировыми событиями.

        Args:
            state: Состояние мира.
            round_num: Номер раунда.
            round_events: События, произошедшие в этом раунде.
            org_context: Описание организации для контекста генерации.

        Returns:
            Результат генерации с операциями и нарративом.
        """
        events_summary = "\n".join(
            f"- {e.get('event_type', '?')}: {e.get('payload', {})}"
            for e in round_events[:20]
        ) or "Нет событий"

        agents_summary = "\n".join(
            f"- {aid}: {p.name}, {p.position}"
            for aid, p in state.agents.items()
        )

        cases_summary = "\n".join(
            f"- {cid}: {c.title} (стадия={c.stage})"
            for cid, c in state.cases.items()
        ) or "Нет дел"

        needs_summary = "\n".join(
            f"- {n.target_agent_id}: {n.description}"
            for n in state.active_needs
        ) or "Нет потребностей"

        user_prompt = (
            f"Раунд {round_num} завершён.\n\n"
            f"## Агенты\n{agents_summary}\n\n"
            f"## Дела\n{cases_summary}\n\n"
            f"## Текущие потребности\n{needs_summary}\n\n"
            f"## События раунда\n{events_summary}\n\n"
            f"Какие мировые события произойдут? Ключевое слово: generate_events"
        )

        ctx_block = (
            f"\nКонтекст организации: {org_context}\n"
            if org_context
            else ""
        )
        system_prompt = WORLD_GEN_SYSTEM + ctx_block

        try:
            resp = self._llm.generate_structured(
                system=system_prompt,
                user=user_prompt,
                schema=WORLD_GEN_SCHEMA,
            )
            data = resp.data
        except Exception as exc:
            logger.warning("Ошибка генератора среды: %s", exc)
            return WorldGenResult(narrative=f"Ошибка: {exc}")

        narrative = data.get("narrative", "")
        all_ops: list[StateOp] = []
        for event in data.get("events", []):
            raw_changes = event.get("state_changes", [])
            ops = parse_state_ops(raw_changes)
            all_ops.extend(ops)

        return WorldGenResult(ops=all_ops, narrative=narrative)
