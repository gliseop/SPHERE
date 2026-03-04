"""Мелкие утилиты MAGISTRY-LC."""

from __future__ import annotations


def redact_numbers(obj: object) -> object:
    """Заменить все числовые значения на маркер `<num>`.

    Используется, чтобы не передавать LLM "сырые числа" из внутренних структур.
    """
    if isinstance(obj, dict):
        return {k: redact_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_numbers(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return "<num>"
    return obj
