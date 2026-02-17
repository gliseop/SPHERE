"""Система репутации (социального капитала) агентов."""

from __future__ import annotations

from .state import ReputationRecord


POSITION_THRESHOLDS = [
    (0, "специалист"),
    (15, "старший специалист"),
    (25, "заместитель начальника"),
    (35, "начальник отдела"),
    (50, "директор департамента"),
]


def apply_decay(record: ReputationRecord, decay_factor: float = 1.0) -> None:
    """Применить затухание репутации.

    Умножает текущую репутацию на коэффициент затухания. При значении 1.0
    затухание не происходит (обратная совместимость). Замороженная
    репутация не изменяется.

    Args:
        record: Запись репутации.
        decay_factor: Коэффициент затухания (0.0-1.0).
    """
    if not record.frozen:
        record.score *= decay_factor


def compute_round_growth(
    record: ReputationRecord,
    cases_resolved: int = 0,
    complaints_received: int = 0,
) -> float:
    """Рассчитать прирост репутации за раунд.

    Args:
        record: Текущая запись репутации.
        cases_resolved: Количество закрытых дел.
        complaints_received: Количество жалоб.

    Returns:
        Прирост (может быть отрицательным).
    """
    if record.frozen:
        return 0.0
    growth = 1.0 + cases_resolved * 0.5 - complaints_received * 0.3
    return max(growth, 0.0)


def apply_growth(record: ReputationRecord, growth: float) -> None:
    """Применить прирост репутации.

    Args:
        record: Запись репутации.
        growth: Значение прироста.
    """
    if not record.frozen:
        record.score += growth


def freeze(record: ReputationRecord) -> None:
    """Заморозить репутацию агента.

    Args:
        record: Запись репутации.
    """
    record.frozen = True


def unfreeze(record: ReputationRecord) -> None:
    """Разморозить репутацию агента.

    Args:
        record: Запись репутации.
    """
    record.frozen = False


def check_promotion(record: ReputationRecord) -> tuple[bool, str, str]:
    """Проверить, доступно ли повышение.

    Args:
        record: Запись репутации.

    Returns:
        Кортеж (доступно ли повышение, текущая должность, следующая).
    """
    current_title = POSITION_THRESHOLDS[0][1]
    next_title = ""
    next_threshold = 0.0

    for threshold, title in POSITION_THRESHOLDS:
        if record.score >= threshold:
            current_title = title
        else:
            next_title = title
            next_threshold = threshold
            break

    if record.frozen:
        return False, current_title, next_title

    promoted = record.score >= next_threshold and next_threshold > 0
    return promoted, current_title, next_title
