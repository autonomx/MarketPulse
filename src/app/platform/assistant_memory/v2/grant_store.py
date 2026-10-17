from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import MemoryGrant, MemorySpaceKey, VisibilityScope


class MemoryGrantStoreError(RuntimeError):
    pass


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _grant_from_row(row: Any) -> MemoryGrant:
    return MemoryGrant(
        grant_id=str(row[0]),
        source_space=MemorySpaceKey(
            principal_id=str(row[1]),
            owner_type=cast(Any, str(row[2])),
            owner_id=str(row[3]),
        ),
        target_space=MemorySpaceKey(
            principal_id=str(row[4]),
            owner_type=cast(Any, str(row[5])),
            owner_id=str(row[6]),
        ),
        access=cast(Any, str(row[7])),
        allowed_domains=cast(Any, tuple(str(item) for item in row[8])),
        max_sensitivity=cast(Any, str(row[9])),
        scope_constraints=tuple(VisibilityScope.model_validate(item) for item in row[10]),
        created_by=str(row[11]),
        created_at=row[12],
        revoked_at=row[13],
        revision=int(row[14]),
    )


_GRANT_COLUMNS = """
grant_id,
source_principal_id, source_owner_type, source_owner_id,
target_principal_id, target_owner_type, target_owner_id,
access, allowed_domains, max_sensitivity, scope_constraints,
created_by, created_at, revoked_at, revision
"""


class PostgresMemoryV2GrantStore:
    """Durable read-only cross-space authorization policy."""

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    def put(self, grant: MemoryGrant) -> MemoryGrant:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_grants (
                    grant_id,
                    source_principal_id, source_owner_type, source_owner_id,
                    target_principal_id, target_owner_type, target_owner_id,
                    access, allowed_domains, max_sensitivity, scope_constraints,
                    created_by, created_at, revoked_at, revision
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s, %s
                )
                ON CONFLICT (grant_id) DO NOTHING
                """,
                (
                    grant.grant_id,
                    *_space_values(grant.source_space),
                    *_space_values(grant.target_space),
                    grant.access,
                    _json(grant.allowed_domains),
                    grant.max_sensitivity,
                    _json([item.model_dump(mode="json") for item in grant.scope_constraints]),
                    grant.created_by,
                    grant.created_at,
                    grant.revoked_at,
                    grant.revision,
                ),
            )
            row = connection.execute(
                f"SELECT {_GRANT_COLUMNS} FROM omnix_memory_v2_grants WHERE grant_id = %s",
                (grant.grant_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - transaction invariant
            raise MemoryGrantStoreError("grant insert committed without readable row")
        stored = _grant_from_row(row)
        if stored != grant:
            raise MemoryGrantStoreError("grant_id already exists with different policy")
        return stored

    def get(self, grant_id: str) -> MemoryGrant | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                f"SELECT {_GRANT_COLUMNS} FROM omnix_memory_v2_grants WHERE grant_id = %s",
                (grant_id,),
            ).fetchone()
        return _grant_from_row(row) if row is not None else None

    def revoke(
        self,
        grant_id: str,
        *,
        target_space: MemorySpaceKey,
        revoked_at: datetime,
    ) -> MemoryGrant:
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                UPDATE omnix_memory_v2_grants
                   SET revoked_at = COALESCE(revoked_at, %s),
                       revision = CASE WHEN revoked_at IS NULL THEN revision + 1 ELSE revision END
                 WHERE grant_id = %s
                   AND target_principal_id = %s
                   AND target_owner_type = %s
                   AND target_owner_id = %s
                RETURNING {_GRANT_COLUMNS}
                """,
                (revoked_at, grant_id, *_space_values(target_space)),
            ).fetchone()
        if row is None:
            raise MemoryGrantStoreError("grant not found for target memory space")
        return _grant_from_row(row)

    def active_for_target(
        self,
        target_space: MemorySpaceKey,
        *,
        grant_ids: tuple[str, ...] = (),
    ) -> list[MemoryGrant]:
        """Return grants authorized now; historical `as_of` cannot resurrect revocation."""

        conditions = [
            "target_principal_id = %s",
            "target_owner_type = %s",
            "target_owner_id = %s",
            "created_at <= CURRENT_TIMESTAMP",
            "revoked_at IS NULL",
        ]
        params: list[Any] = [*_space_values(target_space)]
        if grant_ids:
            conditions.append("grant_id = ANY(%s)")
            params.append(list(grant_ids))
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT {_GRANT_COLUMNS}
                  FROM omnix_memory_v2_grants
                 WHERE {' AND '.join(conditions)}
                 ORDER BY grant_id
                """,
                tuple(params),
            ).fetchall()
        return [_grant_from_row(row) for row in rows]
