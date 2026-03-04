"""Строгие идентификаторы сущностей.

В MAGISTRY-LC используются типизированные ID с префиксом, чтобы:
- исключить коллизии между типами (agent vs org vs work item);
- упростить валидацию целей (антифантомы);
- сделать логи и YAML-журнал самодокументируемыми.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class EntityKind(StrEnum):
    """Тип сущности в реестре мира."""

    AGENT = "agent"
    ORG = "org"
    CHANNEL = "chan"
    WORK_ITEM = "work"
    ARTIFACT = "art"
    VOTE = "vote"


_ID_RE = re.compile(r"^(?P<kind>[a-z]+):(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]{0,127})$")


@dataclass(frozen=True, slots=True)
class ParsedId:
    """Результат разбора типизированного ID."""

    kind: EntityKind
    slug: str


def parse_typed_id(entity_id: str) -> ParsedId:
    """Разобрать типизированный ID вида `kind:slug`.

    Args:
        entity_id: Строковый ID.

    Returns:
        ParsedId.

    Raises:
        ValueError: Если формат или kind некорректны.
    """
    match = _ID_RE.match(entity_id.strip())
    if not match:
        raise ValueError(f"Invalid entity id format: {entity_id!r}")
    kind_raw = match.group("kind")
    try:
        kind = EntityKind(kind_raw)
    except ValueError as exc:
        raise ValueError(f"Unknown entity kind: {kind_raw!r}") from exc
    return ParsedId(kind=kind, slug=match.group("slug"))


def ensure_kind(entity_id: str, kind: EntityKind) -> None:
    """Проверить, что ID принадлежит нужному виду.

    Args:
        entity_id: ID сущности.
        kind: Ожидаемый вид.

    Raises:
        ValueError: Если вид не совпал.
    """
    parsed = parse_typed_id(entity_id)
    if parsed.kind != kind:
        raise ValueError(
            f"Expected {kind.value} id, got {parsed.kind.value}: {entity_id!r}"
        )


def make_id(kind: EntityKind, slug: str) -> str:
    """Собрать типизированный ID.

    Args:
        kind: Вид сущности.
        slug: Часть после двоеточия.

    Returns:
        Строковый ID `kind:slug`.
    """
    slug = slug.strip()
    if not slug:
        raise ValueError("slug must be non-empty")
    # Валидация через parse_typed_id.
    entity_id = f"{kind.value}:{slug}"
    _ = parse_typed_id(entity_id)
    return entity_id


PUBLIC_AUDIENCE = "aud:public"
INTERNAL_AUDIENCE = "aud:internal"


def is_audience_ref(value: str) -> bool:
    """Проверить, является ли строка ссылкой на аудиторию.

    Args:
        value: Значение.

    Returns:
        True, если это `aud:*`.
    """
    return value.startswith("aud:")

