"""LLM-оракул: постфактум-анализ нарушений с полным доступом к логу."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLMProvider

logger = logging.getLogger(__name__)


ORACLE_SCHEMA = {
    "type": "object",
    "properties": {
        "violations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "violation_type": {"type": "string"},
                    "agents_involved": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "case_id", "violation_type", "agents_involved",
                    "confidence", "evidence", "reasoning",
                ],
            },
        },
    },
    "required": ["violations"],
    "additionalProperties": False,
}

ORACLE_SYSTEM = (
    "Ты --- всеведущий аналитик организационных нарушений. "
    "Ты видишь ВСЕ события и сообщения, включая приватные. "
    "Твоя задача --- определить, какие действия представляют собой "
    "нарушения: взятки, сговор, фаворитизм, подделка документов, "
    "злоупотребление полномочиями, нецелевое расходование, давление.\n\n"
    "Для каждого нарушения укажи: связанное дело (case_id), тип нарушения, "
    "вовлечённых агентов, уверенность (0.0-1.0), улики и обоснование.\n\n"
    "Если нарушений нет --- верни пустой список violations.\n\n"
    "Верни JSON по заданной схеме."
)


@dataclass
class OracleVerdict:
    """Вердикт оракула по одному нарушению.

    Attributes:
        case_id: Идентификатор связанного дела.
        violation_type: Тип нарушения.
        agents_involved: Список вовлечённых агентов.
        confidence: Уверенность оракула (0.0-1.0).
        evidence: Список обнаруженных улик.
        reasoning: Обоснование вердикта.
    """

    case_id: str
    violation_type: str
    agents_involved: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""


class ViolationOracle:
    """LLM-оракул для постфактум-анализа нарушений.

    Получает полный лог событий и сообщений (включая приватные)
    и определяет ground truth нарушений для расчёта метрик.

    Args:
        llm: Провайдер языковой модели.
    """

    def __init__(self, llm: "LLMProvider") -> None:
        self._llm = llm

    def analyze(
        self,
        events: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        cases: dict[str, dict[str, Any]],
    ) -> list[OracleVerdict]:
        """Проанализировать все события и определить нарушения.

        Args:
            events: Полный журнал событий.
            messages: Все сообщения (включая приватные).
            cases: Словарь дел.

        Returns:
            Список вердиктов о нарушениях.
        """
        events_text = "\n".join(
            f"- [{e.get('round', '?')}] {e.get('agent_id', '?')}: "
            f"{e.get('event_type', '?')} {e.get('payload', {})}"
            for e in events[:100]
        ) or "Нет событий"

        msgs_text = "\n".join(
            f"- [{m.get('round', '?')}] "
            f"{'[ПРИВАТНО]' if m.get('private') else '[публично]'} "
            f"{m.get('from_id', '?')} -> {m.get('to_id', '?')}: "
            f"{m.get('content', '')[:200]}"
            for m in messages[:100]
        ) or "Нет сообщений"

        cases_text = "\n".join(
            f"- {cid}: {c.get('case_type', '?')}, "
            f"{c.get('title', '?')}, "
            f"владелец={c.get('owner_id', '?')}, "
            f"решение={c.get('decision', 'нет')}"
            for cid, c in cases.items()
        ) or "Нет дел"

        user_prompt = (
            f"## Дела\n{cases_text}\n\n"
            f"## Все события\n{events_text}\n\n"
            f"## Все сообщения (включая приватные)\n{msgs_text}\n\n"
            "Определи все нарушения."
        )

        try:
            resp = self._llm.generate_structured(
                system=ORACLE_SYSTEM,
                user=user_prompt,
                schema=ORACLE_SCHEMA,
            )
            data = resp.data
        except Exception as exc:
            logger.warning("Ошибка оракула: %s", exc)
            return []

        verdicts = []
        for v in data.get("violations", []):
            verdicts.append(OracleVerdict(
                case_id=v.get("case_id", ""),
                violation_type=v.get("violation_type", ""),
                agents_involved=v.get("agents_involved", []),
                confidence=v.get("confidence", 0.0),
                evidence=v.get("evidence", []),
                reasoning=v.get("reasoning", ""),
            ))
        return verdicts
