"""LLM-классификатор нарушений -- постфактум-анализ лога симуляции."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLMProvider

logger = logging.getLogger(__name__)


CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "is_violation": {"type": "boolean"},
        "violation_type": {"type": "string"},
        "confidence": {"type": "number"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": [
        "is_violation",
        "violation_type",
        "confidence",
        "evidence",
        "reasoning",
    ],
    "additionalProperties": False,
}

CLASSIFIER_SYSTEM = (
    "Ты -- аналитик, классифицирующий исходы организационных дел. "
    "По журналу событий и сообщений определи, было ли нарушение "
    "при принятии решения.\n\n"
    "Типы нарушений: фаворитизм, взятка, подделка документов, "
    "злоупотребление полномочиями, процедурное нарушение.\n\n"
    "Верни JSON по схеме. Ключевое слово: classify_violation"
)


@dataclass
class CaseClassification:
    """Результат классификации дела.

    Attributes:
        case_id: Идентификатор дела.
        is_violation: Признак нарушения.
        violation_type: Тип нарушения (пусто, если нарушения нет).
        confidence: Уверенность классификатора (0.0 -- 1.0).
        evidence: Список обнаруженных улик.
        reasoning: Обоснование классификации.
    """

    case_id: str
    is_violation: bool = False
    violation_type: str = ""
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""


class ViolationClassifier:
    """Постфактум-классификатор нарушений на основе LLM.

    Анализирует закрытые дела по журналу событий и сообщений,
    вызывая LLM со structured output для получения классификации.

    Args:
        llm: Провайдер языковой модели.
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def classify_case(
        self,
        case_data: dict[str, Any],
        events: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> CaseClassification:
        """Классифицировать одно дело.

        Формирует промпт с деталями дела, релевантными событиями
        (отфильтрованными по case_id, максимум 20) и релевантными
        сообщениями (отфильтрованными по owner_id, максимум 20),
        затем вызывает LLM со structured output.

        Args:
            case_data: Данные дела (словарь с ключами id, owner_id,
                decision, case_type).
            events: Все события симуляции.
            messages: Все сообщения симуляции.

        Returns:
            Результат классификации.
        """
        case_id = case_data.get("id", "?")
        owner = case_data.get("owner_id", "?")
        decision = case_data.get("decision", "?")

        relevant_events = [
            e
            for e in events
            if e.get("payload", {}).get("case_id") == case_id
        ][:20]

        relevant_msgs = [
            m
            for m in messages
            if m.get("from_id") == owner or m.get("to_id") == owner
        ][:20]

        events_text = (
            "\n".join(
                f"- {e.get('event_type')}: {e.get('payload', {})}"
                for e in relevant_events
            )
            or "Нет событий"
        )

        msgs_text = (
            "\n".join(
                f"- {'[приватно]' if m.get('private') else '[публично]'} "
                f"{m.get('from_id')} -> {m.get('to_id')}: "
                f"{m.get('content', '')[:100]}"
                for m in relevant_msgs
            )
            or "Нет сообщений"
        )

        user_prompt = (
            f"## Дело {case_id}\n"
            f"Владелец: {owner}\n"
            f"Решение: {decision}\n"
            f"Тип: {case_data.get('case_type', '?')}\n\n"
            f"## События\n{events_text}\n\n"
            f"## Сообщения\n{msgs_text}\n\n"
            f"Было ли нарушение? Ключевое слово: classify_violation"
        )

        try:
            resp = self._llm.generate_structured(
                system=CLASSIFIER_SYSTEM,
                user=user_prompt,
                schema=CLASSIFIER_SCHEMA,
            )
            data = resp.data
        except Exception as exc:
            logger.warning(
                "Ошибка классификатора для %s: %s", case_id, exc
            )
            return CaseClassification(case_id=case_id)

        return CaseClassification(
            case_id=case_id,
            is_violation=data.get("is_violation", False),
            violation_type=data.get("violation_type", ""),
            confidence=data.get("confidence", 0.0),
            evidence=data.get("evidence", []),
            reasoning=data.get("reasoning", ""),
        )

    def classify_all(
        self,
        cases: dict[str, dict[str, Any]],
        events: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> list[CaseClassification]:
        """Классифицировать все закрытые дела (кроме расследований).

        Перебирает дела, пропуская расследования и незакрытые дела,
        вызывает classify_case для каждого подходящего дела.

        Args:
            cases: Словарь дел {case_id: case_data}.
            events: Все события симуляции.
            messages: Все сообщения симуляции.

        Returns:
            Список классификаций для всех подходящих дел.
        """
        results = []
        for case_id, case_data in cases.items():
            if case_data.get("case_type") == "investigation":
                continue
            if case_data.get("closed_at") is None:
                continue
            result = self.classify_case(case_data, events, messages)
            results.append(result)
        return results
