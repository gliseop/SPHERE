"""Строгие идентификаторы сущностей.

В MAGISTRY-LC используются типизированные ID с префиксом, чтобы:
- исключить коллизии между типами (agent vs org vs work item);
- упростить валидацию целей (антифантомы);
- сделать логи и YAML-журнал самодокументируемыми.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Collection


class EntityKind(StrEnum):
    """Тип сущности в реестре мира."""

    AGENT = "agent"
    ORG = "org"
    CHANNEL = "chan"
    WORK_ITEM = "work"
    ARTIFACT = "art"
    VOTE = "vote"


_ID_RE = re.compile(r"^(?P<kind>[a-z]+):(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]{0,127})$")
_SLUG_SEP_RE = re.compile(r"[^A-Za-z0-9]+")

_CYRILLIC_TRANSLIT = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
        "А": "a",
        "Б": "b",
        "В": "v",
        "Г": "g",
        "Д": "d",
        "Е": "e",
        "Ё": "e",
        "Ж": "zh",
        "З": "z",
        "И": "i",
        "Й": "i",
        "К": "k",
        "Л": "l",
        "М": "m",
        "Н": "n",
        "О": "o",
        "П": "p",
        "Р": "r",
        "С": "s",
        "Т": "t",
        "У": "u",
        "Ф": "f",
        "Х": "kh",
        "Ц": "ts",
        "Ч": "ch",
        "Ш": "sh",
        "Щ": "shch",
        "Ъ": "",
        "Ы": "y",
        "Ь": "",
        "Э": "e",
        "Ю": "yu",
        "Я": "ya",
    }
)


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


def normalize_slug(value: str, *, fallback: str = "auto") -> str:
    """Нормализовать произвольную строку в slug для typed ID."""

    raw = (value or "").strip()
    if not raw:
        raw = fallback

    translit = raw.translate(_CYRILLIC_TRANSLIT)
    ascii_only = unicodedata.normalize("NFKD", translit).encode("ascii", "ignore").decode("ascii")
    cleaned = _SLUG_SEP_RE.sub("_", ascii_only.lower()).strip("_")
    if not cleaned:
        if fallback == "auto":
            cleaned = "auto"
        else:
            cleaned = normalize_slug(fallback, fallback="auto")
    if not cleaned:
        cleaned = "auto"
    if not cleaned[0].isalnum():
        cleaned = f"a_{cleaned}"
    return cleaned[:120]


def make_unique_id(
    kind: EntityKind,
    slug: str,
    *,
    existing_ids: Collection[str],
    fallback: str = "auto",
) -> str:
    """Собрать уникальный typed ID, добавляя суффикс при коллизии."""

    base = normalize_slug(slug, fallback=fallback)
    entity_id = make_id(kind, base)
    if entity_id not in existing_ids:
        return entity_id

    n = 2
    while True:
        suffix = f"_{n}"
        trimmed = base[: max(1, 120 - len(suffix))]
        candidate = make_id(kind, f"{trimmed}{suffix}")
        if candidate not in existing_ids:
            return candidate
        n += 1


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
