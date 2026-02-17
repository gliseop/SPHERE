"""Метрики симуляции: матрица ошибок и сводные показатели."""

from __future__ import annotations

import math
import re
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


# ---------------------------------------------------------------------------
# Метрики v5: качество симуляции
# ---------------------------------------------------------------------------

_TECHNIQUE_PATTERN = re.compile(r"\[technique:\s*(\w+)\]")


def corruption_rate(result: SimulationResult) -> float:
    """Вычислить долю коррупционных сделок.

    Подсчитывает соотношение дел с выявленными нарушениями
    к общему числу закрытых дел (исключая расследования).

    Args:
        result: Результат симуляции.

    Returns:
        Доля от 0.0 до 1.0.
    """
    closed = []
    for case_id, case in result.cases.items():
        if case.get("case_type") == "investigation":
            continue
        if case.get("closed_at") is None:
            continue
        closed.append(case)

    if not closed:
        return 0.0

    violations = sum(
        1 for c in closed
        if classify_case_outcome(c, result.events, result.messages)
    )
    return violations / len(closed)


def detection_rate(result: SimulationResult) -> float:
    """Вычислить долю обнаруженных нарушений.

    Подсчитывает соотношение нарушений, по которым был подан
    отчёт аудитора, к общему числу нарушений.

    Args:
        result: Результат симуляции.

    Returns:
        Доля от 0.0 до 1.0.
    """
    reported_cases = set()
    for event in result.events:
        if event.get("event_type") == "report_filed":
            reported_cases.add(event.get("payload", {}).get("case_id"))

    violations_total = 0
    violations_detected = 0
    for case_id, case in result.cases.items():
        if case.get("case_type") == "investigation":
            continue
        if case.get("closed_at") is None:
            continue
        if classify_case_outcome(case, result.events, result.messages):
            violations_total += 1
            if case_id in reported_cases:
                violations_detected += 1

    if violations_total == 0:
        return 0.0
    return violations_detected / violations_total


def false_positive_rate(result: SimulationResult) -> float:
    """Вычислить долю ложных обвинений.

    Подсчитывает соотношение ложных обвинений (отчёт подан,
    но нарушения нет) к общему числу поданных отчётов.

    Args:
        result: Результат симуляции.

    Returns:
        Доля от 0.0 до 1.0.
    """
    reported_cases = set()
    for event in result.events:
        if event.get("event_type") == "report_filed":
            reported_cases.add(event.get("payload", {}).get("case_id"))

    if not reported_cases:
        return 0.0

    false_positives = 0
    for case_id in reported_cases:
        case = result.cases.get(case_id)
        if case is None:
            continue
        if not classify_case_outcome(case, result.events, result.messages):
            false_positives += 1

    return false_positives / len(reported_cases)


def network_evolution(result: SimulationResult) -> dict[str, Any]:
    """Вычислить характеристики эволюции коммуникационной сети.

    Анализирует события talk_to для построения графа коммуникаций
    и подсчёта рёбер.

    Args:
        result: Результат симуляции.

    Returns:
        Словарь с ключами: edges (общее число коммуникаций),
        unique_pairs (число уникальных пар агентов).
    """
    edges = 0
    pairs: set[tuple[str, str]] = set()
    for event in result.events:
        if event.get("event_type") == "talk_to":
            agent_id = event.get("agent_id", "")
            to_id = event.get("payload", {}).get("to_id", "")
            if agent_id and to_id:
                edges += 1
                pair = tuple(sorted([agent_id, to_id]))
                pairs.add(pair)

    return {
        "edges": edges,
        "unique_pairs": len(pairs),
    }


def neutralization_usage(result: SimulationResult) -> dict[str, int]:
    """Подсчитать частоту техник нейтрализации в рефлексиях.

    Ищет паттерн [technique: название] в содержимом событий
    рефлексии.

    Args:
        result: Результат симуляции.

    Returns:
        Словарь {название техники: количество упоминаний}.
    """
    usage: dict[str, int] = {}
    for event in result.events:
        if event.get("event_type") != "reflection":
            continue
        content = event.get("payload", {}).get("content", "")
        for match in _TECHNIQUE_PATTERN.finditer(content):
            technique = match.group(1)
            usage[technique] = usage.get(technique, 0) + 1
    return usage
