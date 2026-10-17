from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import (
    CutoverReadiness,
    MemoryAuthorityEpoch,
    MemorySpaceKey,
)


class MemoryAuthorityError(RuntimeError):
    pass



@dataclass(frozen=True, slots=True)
class AuthorityEpochState:
    epoch: MemoryAuthorityEpoch
    readiness_receipt_ids: tuple[str, ...]
    reason: str | None = None


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)



class PostgresMemoryV2AuthorityStore:
    """The current memory authority epoch.

    Memory v2 has been the only authority since Memory v1 was retired
    (migration 0156); the epochs record how the installation got there.
    """

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    @staticmethod
    def _epoch_from_row(row: Any) -> AuthorityEpochState:
        readiness_data = dict(row[4]) if row[4] is not None else None
        epoch = MemoryAuthorityEpoch(
            epoch=int(row[0]),
            authority=cast(Any, str(row[1])),
            activated_at=row[2],
            previous_epoch=int(row[3]) if row[3] is not None else None,
            readiness=(
                CutoverReadiness.model_validate(readiness_data)
                if readiness_data is not None
                else None
            ),
            activated_by=str(row[6]),
        )
        return AuthorityEpochState(
            epoch=epoch,
            readiness_receipt_ids=tuple(str(item) for item in row[5]),
            reason=str(row[7]) if row[7] is not None else None,
        )

    def current(self) -> AuthorityEpochState:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT e.epoch, e.authority, e.activated_at, e.previous_epoch,
                       e.readiness, e.readiness_receipt_ids, e.activated_by, e.reason
                  FROM omnix_memory_v2_authority_current c
                  JOIN omnix_memory_v2_authority_epochs e ON e.epoch = c.current_epoch
                 WHERE c.singleton = TRUE
                """
            ).fetchone()
        if row is None:  # pragma: no cover
            raise MemoryAuthorityError("Memory v2 current authority epoch is missing")
        return self._epoch_from_row(row)


