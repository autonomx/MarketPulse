from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, TypeVar

T = TypeVar("T", bound="KnownRecord")


class KnownRecord(Protocol):
    omnix_known_at: datetime | None


def server_now() -> datetime:
    return datetime.now(timezone.utc)


def visible_as_of(record: KnownRecord, decision_at: datetime) -> bool:
    if decision_at.tzinfo is None:
        raise ValueError("decision_at must be timezone-aware")
    known = record.omnix_known_at
    return known is not None and known <= decision_at.astimezone(timezone.utc)


def latest_as_of(records: list[T] | tuple[T, ...], decision_at: datetime) -> T | None:
    visible = [item for item in records if visible_as_of(item, decision_at)]
    if not visible:
        return None
    return max(visible, key=lambda item: item.omnix_known_at or datetime.min.replace(tzinfo=timezone.utc))
