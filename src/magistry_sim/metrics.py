"""Метрики симуляции: матрица ошибок и сводные показатели."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .environment import SimulationResult


@dataclass
class ConfusionMatrix:
    """Матрица ошибок для обнаружения нарушений."""

    tp: int = 0  # Истинное обнаружение
    fp: int = 0  # Ложная тревога
    tn: int = 0  # Истинный пропуск
    fn: int = 0  # Пропущенное нарушение

    @property
    def precision(self) -> float:
        """Точность (precision)."""
        total = self.tp + self.fp
        return self.tp / total if total > 0 else 0.0

    @property
    def recall(self) -> float:
        """Полнота (recall)."""
        total = self.tp + self.fn
        return self.tp / total if total > 0 else 0.0

    @property
    def f1(self) -> float:
        """F1-мера."""
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class SimulationMetrics:
    """Сводные метрики симуляции."""

    rounds: int = 0
    total_cases: int = 0
    cases_by_type: dict = field(default_factory=dict)
    violations_detected: int = 0
    violations_total: int = 0
    confusion: ConfusionMatrix = field(
        default_factory=ConfusionMatrix
    )
    avg_detection_time: float = 0.0
    private_message_ratio: float = 0.0
    graph_density: float = 0.0


def classify_case_outcome(
    case: dict,
    events: list[dict],
    messages: list[dict],
) -> bool:
    """Классифицировать исход дела: было ли нарушение.

    Анализирует полный лог, включая приватные переговоры,
    для определения наличия нарушений.

    Args:
        case: Данные дела.
        events: Все события.
        messages: Все сообщения.

    Returns:
        True, если обнаружено нарушение.
    """
    case_id = case.get("id", "")
    owner_id = case.get("owner_id", "")
    decision = case.get("decision", "") or ""

    # Проверка: решение в пользу знакомого (по приватным сообщениям)
    private_contacts = set()
    for msg in messages:
        if msg.get("private") and msg.get("from_id") == owner_id:
            private_contacts.add(msg.get("to_id"))
        if msg.get("private") and msg.get("to_id") == owner_id:
            private_contacts.add(msg.get("from_id"))

    for proposal in case.get("proposals", []):
        author = proposal.get("author_id", "")
        if author in private_contacts and author in decision:
            return True

    return False


def compute_metrics(result: SimulationResult) -> SimulationMetrics:
    """Вычислить метрики по результату симуляции.

    Args:
        result: Результат симуляции.

    Returns:
        Сводные метрики.
    """
    metrics = SimulationMetrics(rounds=result.rounds_completed)

    # Дела по типам
    cases_by_type: dict[str, int] = {}
    for case in result.cases.values():
        ct = case.get("case_type", "unknown")
        cases_by_type[ct] = cases_by_type.get(ct, 0) + 1
    metrics.total_cases = len(result.cases)
    metrics.cases_by_type = cases_by_type

    # Классификация нарушений
    reported_cases = set()
    for event in result.events:
        if event.get("event_type") == "report_filed":
            reported_cases.add(event.get("payload", {}).get("case_id"))

    violations_total = 0
    violations_detected = 0
    confusion = ConfusionMatrix()

    for case_id, case in result.cases.items():
        if case.get("case_type") == "investigation":
            continue
        if case.get("closed_at") is None:
            continue

        is_violation = classify_case_outcome(
            case, result.events, result.messages
        )
        is_reported = case_id in reported_cases

        if is_violation:
            violations_total += 1
            if is_reported:
                violations_detected += 1
                confusion.tp += 1
            else:
                confusion.fn += 1
        else:
            if is_reported:
                confusion.fp += 1
            else:
                confusion.tn += 1

    metrics.violations_total = violations_total
    metrics.violations_detected = violations_detected
    metrics.confusion = confusion

    # Приватные сообщения
    total_messages = len(result.messages)
    private_count = sum(
        1 for m in result.messages if m.get("private")
    )
    metrics.private_message_ratio = (
        private_count / total_messages
        if total_messages > 0
        else 0.0
    )

    return metrics


# ---------------------------------------------------------------------------
# Метрики v4: когнитивный агент
# ---------------------------------------------------------------------------

# Паттерны действий, характерные для каждого архетипа.
# Ключ — название архетипа, значение — множество инструментов,
# которые архетип использует чаще других.
_ARCHETYPE_ACTION_PATTERNS: dict[str, set[str]] = {
    "initiator": {"talk_to", "submit_proposal", "open_case"},
    "machiavellist": {"talk_to", "submit_proposal"},
    "conformist": {"submit_proposal", "add_note", "cast_vote"},
    "idealist": {"file_report", "cast_vote", "add_note"},
    "opportunist": {"talk_to", "submit_proposal", "open_case"},
}


def compute_personality_consistency(
    actions: list[dict[str, Any]],
    archetype: str,
) -> float:
    """Оценить согласованность действий агента с его архетипом.

    Вычисляет долю действий, соответствующих паттерну архетипа,
    и масштабирует результат к шкале 0..5.

    Args:
        actions: Список действий агента (словари с ключом "tool").
        archetype: Название архетипа личности.

    Returns:
        Оценка согласованности от 0.0 до 5.0.
    """
    if not actions:
        return 0.0

    pattern = _ARCHETYPE_ACTION_PATTERNS.get(
        archetype, {"talk_to", "open_case"}
    )
    matching = sum(
        1 for a in actions if a.get("tool", "") in pattern
    )
    ratio = matching / len(actions)
    return round(ratio * 5.0, 2)


def compute_memory_utilization(
    total_memories: int,
    retrieved_memories: int,
    actions_influenced: int,
) -> float:
    """Оценить эффективность использования памяти агента.

    Вычисляет долю воспоминаний, которые были извлечены и повлияли
    на действия агента.

    Args:
        total_memories: Общее число воспоминаний в потоке.
        retrieved_memories: Число извлечённых воспоминаний.
        actions_influenced: Число действий, на которые повлияла память.

    Returns:
        Оценка утилизации от 0.0 до 1.0.
    """
    if total_memories == 0:
        return 0.0

    retrieval_ratio = retrieved_memories / total_memories
    if retrieved_memories == 0:
        return 0.0

    influence_ratio = actions_influenced / retrieved_memories
    utilization = retrieval_ratio * influence_ratio
    return min(utilization, 1.0)


def compute_information_asymmetry(
    memory_sizes: dict[str, int],
) -> float:
    """Вычислить информационную асимметрию между агентами.

    Использует коэффициент вариации размеров потоков памяти
    как меру неравномерности распределения информации.

    Args:
        memory_sizes: Словарь {agent_id: число воспоминаний}.

    Returns:
        Коэффициент вариации (>= 0.0). Ноль означает
        равное распределение информации.
    """
    if len(memory_sizes) <= 1:
        return 0.0

    values = list(memory_sizes.values())
    n = len(values)
    mean = sum(values) / n
    if mean == 0.0:
        return 0.0

    variance = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(variance)
    return round(std / mean, 4)
