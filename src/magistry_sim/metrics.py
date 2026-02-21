"""Метрики симуляции: матрица ошибок и сводные показатели."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .environment import SimulationResult

if TYPE_CHECKING:
    from .oracle import OracleVerdict


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
    agent_connections: dict[str, dict[str, float]] | None = None,
) -> bool:
    """Классифицировать исход дела: было ли нарушение.

    Нарушение фиксируется только при одновременном выполнении нескольких
    признаков, чтобы исключить ложные срабатывания в чистых сценариях,
    где агенты могут общаться приватно по законным причинам.

    Условие срабатывания (одно из двух):
        - (A) Победитель имел более одного приватного сообщения с владельцем
          дела (одно сообщение считается случайным контактом).
        - (B) Победитель связан с владельцем в социальном графе с силой > 2.5
          И при этом имел хотя бы одно приватное сообщение с ним
          (сильная предустановленная связь в сочетании с приватным контактом).

    Если ``agent_connections`` не передан, применяется только критерий (A).

    Args:
        case: Данные дела (словарь с ключами id, owner_id, decision,
            proposals).
        events: Все события симуляции.
        messages: Все сообщения симуляции.
        agent_connections: Граф социальных связей в формате
            ``{agent_id: {connected_agent_id: strength}}``. Если None,
            критерий силы связи не применяется.

    Returns:
        True, если обнаружено нарушение.
    """
    owner_id = case.get("owner_id", "")
    decision = case.get("decision", "") or ""

    # Подсчёт приватных сообщений между владельцем дела и каждым агентом.
    private_contact_count: dict[str, int] = {}
    for msg in messages:
        if not msg.get("private"):
            continue
        from_id = msg.get("from_id", "")
        to_id = msg.get("to_id", "")
        if from_id == owner_id:
            private_contact_count[to_id] = (
                private_contact_count.get(to_id, 0) + 1
            )
        elif to_id == owner_id:
            private_contact_count[from_id] = (
                private_contact_count.get(from_id, 0) + 1
            )

    owner_connections: dict[str, float] = {}
    if agent_connections is not None:
        owner_connections = agent_connections.get(owner_id, {})

    for proposal in case.get("proposals", []):
        author = proposal.get("author_id", "")
        if not author or author not in decision:
            continue

        count = private_contact_count.get(author, 0)
        has_private = count > 0

        # Критерий A: более одного приватного сообщения.
        if count > 1:
            return True

        # Критерий B: сильная связь в графе + хотя бы одно приватное сообщение.
        if has_private and agent_connections is not None:
            strength = owner_connections.get(author, 0.0)
            if strength > 2.5:
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
        if case.get("closed_at") is None:
            continue

        agent_connections = getattr(result, "agent_connections", None)
        is_violation = classify_case_outcome(
            case, result.events, result.messages, agent_connections
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


def case_diversity(result: SimulationResult) -> int:
    """Подсчитать количество уникальных типов дел.

    Чем больше различных case_type встречается среди дел,
    тем разнообразнее организационные процессы в симуляции.

    Args:
        result: Результат симуляции.

    Returns:
        Количество уникальных значений case_type.
    """
    unique_types: set[str] = set()
    for case in result.cases.values():
        ct = case.get("case_type", "")
        if ct:
            unique_types.add(ct)
    return len(unique_types)


def compute_metrics_with_oracle(
    result: SimulationResult,
    oracle_verdicts: list["OracleVerdict"],
) -> SimulationMetrics:
    """Вычислить метрики, используя вердикты оракула как ground truth.

    Оракул определяет, какие дела содержат нарушения. Затем
    на основе отчётов аудитора вычисляется матрица ошибок.

    Args:
        result: Результат симуляции.
        oracle_verdicts: Список вердиктов оракула о нарушениях.

    Returns:
        Сводные метрики с матрицей ошибок на основе оракула.
    """
    metrics = SimulationMetrics(rounds=result.rounds_completed)

    # Дела по типам
    cases_by_type: dict[str, int] = {}
    for case in result.cases.values():
        ct = case.get("case_type", "unknown")
        cases_by_type[ct] = cases_by_type.get(ct, 0) + 1
    metrics.total_cases = len(result.cases)
    metrics.cases_by_type = cases_by_type

    # Множество дел с нарушениями по вердикту оракула
    oracle_violation_cases: set[str] = {
        v.case_id for v in oracle_verdicts
    }

    # Множество дел, по которым поданы отчёты
    reported_cases: set[str] = set()
    for event in result.events:
        if event.get("event_type") == "report_filed":
            reported_cases.add(
                event.get("payload", {}).get("case_id")
            )

    violations_total = 0
    violations_detected = 0
    confusion = ConfusionMatrix()

    for case_id, case in result.cases.items():
        if case.get("closed_at") is None:
            continue

        is_violation = case_id in oracle_violation_cases
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
    "initiator": {"talk_to", "submit_proposal", "open_case", "perform_action"},
    "machiavellist": {"talk_to", "submit_proposal", "perform_action"},
    "conformist": {"submit_proposal", "add_note", "cast_vote", "perform_action"},
    "idealist": {"file_report", "cast_vote", "add_note", "perform_action"},
    "opportunist": {"talk_to", "submit_proposal", "open_case", "perform_action"},
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


# ---------------------------------------------------------------------------
# Метрики v5: свободный мир
# ---------------------------------------------------------------------------

_CORRUPTION_EVENT_TYPES = {"funds_transferred", "evidence_removed"}


def _is_corruption_event(event: dict[str, Any]) -> bool:
    """Проверить, является ли событие коррупционным.

    Коррупционными считаются события типа funds_transferred,
    evidence_removed, а также evidence_added с типом улики,
    содержащим «forge» или «подделк».

    Args:
        event: Словарь события.

    Returns:
        True, если событие коррупционное.
    """
    etype = event.get("event_type", "")
    if etype in _CORRUPTION_EVENT_TYPES:
        return True
    if etype == "evidence_added":
        evidence_type = event.get("payload", {}).get("evidence_type", "")
        if "forge" in evidence_type.lower() or "подделк" in evidence_type.lower():
            return True
    return False


def action_diversity(result: SimulationResult) -> int:
    """Подсчитать количество уникальных типов событий.

    Чем больше различных event_type встречается в журнале событий,
    тем разнообразнее поведение агентов в симуляции.

    Args:
        result: Результат симуляции.

    Returns:
        Количество уникальных значений event_type.
    """
    unique_types: set[str] = set()
    for event in result.events:
        etype = event.get("event_type", "")
        if etype:
            unique_types.add(etype)
    return len(unique_types)


def scheme_depth(result: SimulationResult) -> int:
    """Найти максимальную глубину коррупционной схемы.

    Ищет самую длинную непрерывную цепочку коррупционных событий
    одного агента, отсортированных по раунду. Цепочка прерывается,
    если между коррупционными событиями агента встречается «честное»
    событие того же агента.

    Коррупционными считаются: funds_transferred, evidence_removed,
    evidence_added (с evidence_type, содержащим «forge» или «подделк»).

    Args:
        result: Результат симуляции.

    Returns:
        Длина самой длинной цепочки. 0, если коррупционных событий нет.
    """
    # Группируем события по agent_id, сортируем по раунду.
    agent_events: dict[str, list[dict[str, Any]]] = {}
    for event in result.events:
        agent_id = event.get("agent_id", "")
        if not agent_id:
            continue
        agent_events.setdefault(agent_id, []).append(event)

    max_depth = 0

    for agent_id, events in agent_events.items():
        sorted_events = sorted(events, key=lambda e: e.get("round", 0))
        current_chain = 0
        for event in sorted_events:
            if _is_corruption_event(event):
                current_chain += 1
                if current_chain > max_depth:
                    max_depth = current_chain
            else:
                current_chain = 0

    return max_depth


def arbiter_rejection_rate(result: SimulationResult) -> float:
    """Вычислить долю отклонённых арбитром действий.

    Подсчитывает отношение событий arbiter_rejected к общему числу
    арбитражных событий (arbiter_rejected + arbiter_approved).

    Args:
        result: Результат симуляции.

    Returns:
        Доля от 0.0 до 1.0. Возвращает 0.0, если арбитражных
        событий нет.
    """
    rejected = 0
    approved = 0
    for event in result.events:
        etype = event.get("event_type", "")
        if etype == "arbiter_rejected":
            rejected += 1
        elif etype == "arbiter_approved":
            approved += 1

    total = rejected + approved
    if total == 0:
        return 0.0
    return rejected / total
