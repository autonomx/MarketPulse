from __future__ import annotations

import json
from typing import Any

from app.persistence.errors import EntityNotFound, RevisionConflict
from app.persistence.tenant import TenantContext
from app.runtime.pagination import page_limit


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _character(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "owner_user_id": str(row[2]) if row[2] is not None else None,
        "visibility": str(row[3]),
        "active_version": int(row[4]),
        "status": str(row[5]),
        "enabled": bool(row[6]),
        "revision": int(row[7]),
        "profile": dict(row[8]),
        "created_at": row[9].isoformat(),
        "updated_at": row[10].isoformat(),
    }


_CHARACTER_COLUMNS = """
id, workspace_id, owner_user_id, visibility, active_version, status,
enabled, revision, profile, created_at, updated_at
"""


class PostgresCharacterRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create(
        self,
        context: TenantContext,
        *,
        character_id: str,
        profile: dict[str, Any],
        visibility: str = "private",
        enabled: bool = True,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_characters
                (id, workspace_id, owner_user_id, visibility, enabled, profile)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING {_CHARACTER_COLUMNS}
            """,
            (
                character_id,
                context.workspace_id,
                context.user_id,
                visibility,
                enabled,
                _json(profile),
            ),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO omnix_character_versions
                (character_id, version, profile, created_by)
            VALUES (%s, 1, %s::jsonb, %s)
            """,
            (character_id, _json(profile), context.user_id),
        )
        return _character(row)

    def get_character(
        self,
        context: TenantContext,
        character_id: str,
        *,
        include_archived: bool = False,
    ) -> dict[str, Any] | None:
        status_clause = "" if include_archived else " AND status = 'active'"
        row = self.connection.execute(
            f"SELECT {_CHARACTER_COLUMNS} FROM omnix_characters "
            f"WHERE id = %s AND workspace_id = %s{status_clause}",
            (character_id, context.workspace_id),
        ).fetchone()
        return _character(row) if row is not None else None

    def list_characters(
        self,
        context: TenantContext,
        *,
        include_archived: bool = False,
        limit: int = 100,
        after_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """By display name; ``after_id`` is the last character seen (WP-5.5).

        The database computes the sort key of ``after_id``, so paging uses the
        same collation as the ordering.
        """
        status_clause = "" if include_archived else " AND status = 'active'"
        rows = self.connection.execute(
            f"SELECT {_CHARACTER_COLUMNS} FROM omnix_characters "
            f"WHERE workspace_id = %s{status_clause} "
            "AND (%s::text IS NULL OR (lower(profile->>'display_name'), id) > ("
            "SELECT lower(last.profile->>'display_name'), last.id FROM omnix_characters AS last "
            "WHERE last.workspace_id = %s AND last.id = %s)) "
            "ORDER BY lower(profile->>'display_name'), id LIMIT %s",
            (
                context.workspace_id,
                after_id,
                context.workspace_id,
                after_id,
                page_limit(limit, default=100),
            ),
        ).fetchall()
        return [_character(row) for row in rows]

    def update(
        self,
        context: TenantContext,
        *,
        character_id: str,
        profile: dict[str, Any],
        expected_version: int,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_characters
               SET profile = %s::jsonb,
                   active_version = active_version + 1,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s AND status = 'active'
               AND active_version = %s
            RETURNING {_CHARACTER_COLUMNS}
            """,
            (_json(profile), character_id, context.workspace_id, expected_version),
        ).fetchone()
        if row is None:
            current = self.connection.execute(
                "SELECT active_version FROM omnix_characters "
                "WHERE id = %s AND workspace_id = %s",
                (character_id, context.workspace_id),
            ).fetchone()
            if current is None:
                raise EntityNotFound(character_id)
            raise RevisionConflict(
                f"character {character_id} expected version {expected_version}; current {int(current[0])}"
            )
        result = _character(row)
        self.connection.execute(
            """
            INSERT INTO omnix_character_versions
                (character_id, version, profile, created_by)
            VALUES (%s, %s, %s::jsonb, %s)
            """,
            (character_id, result["active_version"], _json(profile), context.user_id),
        )
        return result

    def archive(
        self,
        context: TenantContext,
        *,
        character_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_characters
               SET status = 'archived', enabled = FALSE,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s AND revision = %s
            RETURNING {_CHARACTER_COLUMNS}
            """,
            (character_id, context.workspace_id, expected_revision),
        ).fetchone()
        if row is None:
            raise RevisionConflict(
                f"character {character_id} expected revision {expected_revision}"
            )
        return _character(row)

    def versions(
        self,
        context: TenantContext,
        character_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        exists = self.get_character(context, character_id, include_archived=True)
        if exists is None:
            raise EntityNotFound(character_id)
        rows = self.connection.execute(
            """
            SELECT version, profile, created_by, created_at
              FROM omnix_character_versions
             WHERE character_id = %s ORDER BY version DESC LIMIT %s
            """,
            (character_id, max(1, min(int(limit), 100))),
        ).fetchall()
        return [
            {
                "character_id": character_id,
                "version": int(row[0]),
                "profile": dict(row[1]),
                "created_by": str(row[2]) if row[2] is not None else None,
                "created_at": row[3].isoformat(),
            }
            for row in rows
        ]


