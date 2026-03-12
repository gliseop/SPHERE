"""Сущности мира и реестр (EntityRegistry)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .ids import EntityKind, ensure_kind, parse_typed_id


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """Запись в реестре сущностей."""

    entity_id: str
    kind: EntityKind
    created_by: str | None = None
    created_tick: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class EntityRegistry:
    """Единая точка истины для существования сущностей.

    Реестр используется для антифантомов:
    - любой `target_id`/получатель сообщения должен существовать в registry;
    - создание новых сущностей происходит через `register(...)` и логируется как `entity_created`.
    """

    def __init__(self) -> None:
        self._items: dict[str, EntityRecord] = {}

    def register(
        self,
        record: EntityRecord,
        *,
        overwrite: bool = False,
    ) -> None:
        """Зарегистрировать сущность.

        Args:
            record: Запись сущности.
            overwrite: Разрешить перезапись существующей записи.

        Raises:
            ValueError: Если ID или kind некорректны.
        """
        parsed = parse_typed_id(record.entity_id)
        if parsed.kind != record.kind:
            raise ValueError(
                f"Entity kind mismatch for {record.entity_id!r}: "
                f"id kind={parsed.kind.value}, record kind={record.kind.value}"
            )
        if not overwrite and record.entity_id in self._items:
            raise ValueError(f"Entity already exists: {record.entity_id!r}")
        self._items[record.entity_id] = record

    def exists(self, entity_id: str) -> bool:
        """Проверить наличие сущности."""
        return entity_id in self._items

    def get(self, entity_id: str) -> EntityRecord | None:
        """Получить запись или None."""
        return self._items.get(entity_id)

    def require(self, entity_id: str, kind: EntityKind | None = None) -> EntityRecord:
        """Получить запись, иначе исключение.

        Args:
            entity_id: ID.
            kind: Если задано, проверяет соответствие вида.

        Returns:
            EntityRecord.

        Raises:
            KeyError: Если сущность не зарегистрирована.
            ValueError: Если kind не совпал.
        """
        record = self._items.get(entity_id)
        if record is None:
            raise KeyError(f"Unknown entity: {entity_id!r}")
        if kind is not None:
            ensure_kind(entity_id, kind)
        return record

    def list_ids(self, kind: EntityKind | None = None) -> list[str]:
        """Список ID по виду (или все)."""
        if kind is None:
            return sorted(self._items.keys())
        return sorted([eid for eid, rec in self._items.items() if rec.kind == kind])

    def items(self) -> Iterable[EntityRecord]:
        """Итерировать по записям."""
        return self._items.values()

