from __future__ import annotations

import json
from typing import Any

from .errors import EntityNotFound, RevisionConflict
from app.runtime.pagination import MAX_PAGE_SIZE, page_limit
from .tenant import TenantContext


def _asset(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "owner_user_id": str(row[2]) if row[2] is not None else None,
        "module": str(row[3]),
        "asset_type": str(row[4]),
        "mime_type": str(row[5]),
        "byte_size": int(row[6]),
        "checksum_sha256": str(row[7]),
        "storage_provider": str(row[8]),
        "storage_key": str(row[9]),
        "lifecycle_status": str(row[10]),
        "generation_job_id": str(row[11]) if row[11] is not None else None,
        "revision": int(row[12]),
        "created_at": row[13].isoformat(),
        "updated_at": row[14].isoformat(),
        "metadata": dict(row[15] or {}),
        "compat": dict(row[16] or {}),
    }


_ASSET_COLUMNS = """
id, workspace_id, owner_user_id, module, asset_type, mime_type,
byte_size, checksum_sha256, storage_provider, storage_key,
lifecycle_status, generation_job_id, revision, created_at, updated_at,
metadata, compat
"""
_ASSET_COLUMN_NAMES = tuple(name.strip() for name in _ASSET_COLUMNS.split(","))


class PostgresAssetRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create(self, context: TenantContext, payload: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(payload["id"]).strip()
        context.require_workspace(str(payload.get("workspace_id") or context.workspace_id))
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_assets (
                id, workspace_id, owner_user_id, module, asset_type, mime_type,
                byte_size, checksum_sha256, storage_provider, storage_key,
                lifecycle_status, generation_job_id, metadata, compat
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s::jsonb, %s::jsonb
            )
            RETURNING {_ASSET_COLUMNS}
            """,
            (
                asset_id,
                context.workspace_id,
                payload.get("owner_user_id") or context.user_id,
                str(payload["module"]),
                str(payload["asset_type"]),
                str(payload["mime_type"]),
                int(payload["byte_size"]),
                str(payload["checksum_sha256"]),
                str(payload["storage_provider"]),
                str(payload["storage_key"]),
                str(payload.get("lifecycle_status") or "active"),
                payload.get("generation_job_id"),
                json.dumps(payload.get("metadata") or {}, sort_keys=True, separators=(",", ":")),
                json.dumps(payload.get("compat") or {}, sort_keys=True, separators=(",", ":")),
            ),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO omnix_asset_versions (
                asset_id, version, checksum_sha256, byte_size,
                storage_provider, storage_key, metadata
            ) VALUES (%s, 1, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                asset_id,
                payload["checksum_sha256"],
                int(payload["byte_size"]),
                payload["storage_provider"],
                payload["storage_key"],
                json.dumps(payload.get("metadata") or {}, sort_keys=True, separators=(",", ":")),
            ),
        )
        return _asset(row)

    def get_asset(self, context: TenantContext, asset_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_ASSET_COLUMNS} FROM omnix_assets "
            "WHERE id = %s AND workspace_id = %s",
            (asset_id, context.workspace_id),
        ).fetchone()
        return _asset(row) if row is not None else None

    def asset_fields(
        self, context: TenantContext, asset_id: str | None, *, active_only: bool = False, lock: bool = False,
    ) -> dict[str, Any] | None:
        """One asset's columns with their database types (datetimes, ints), for a module's own transaction.

        A module reads asset metadata here instead of joining ``omnix_assets``
        (PA-2.2). ``active_only`` skips an asset that is not active; ``lock``
        holds its row until the transaction ends.
        """
        if not asset_id:
            return None
        row = self.connection.execute(
            f"SELECT {_ASSET_COLUMNS} FROM omnix_assets WHERE workspace_id = %s AND id = %s"
            + (" AND lifecycle_status = 'active'" if active_only else "")
            + (" FOR UPDATE" if lock else ""),
            (context.workspace_id, asset_id),
        ).fetchone()
        return dict(zip(_ASSET_COLUMN_NAMES, row)) if row is not None else None

    def find_by_storage(
        self,
        context: TenantContext,
        *,
        storage_provider: str,
        storage_key: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_ASSET_COLUMNS} FROM omnix_assets "
            "WHERE workspace_id = %s AND storage_provider = %s AND storage_key = %s",
            (context.workspace_id, storage_provider, storage_key),
        ).fetchone()
        return _asset(row) if row is not None else None

    def list_assets(
        self,
        context: TenantContext,
        *,
        asset_type: str | None = None,
        modules: tuple[str, ...] | None = None,
        limit: int = 100,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first, keyset-paged by ``(created_at, id)`` (WP-5.5)."""
        clauses = ["workspace_id = %s", "lifecycle_status <> 'deleted'"]
        parameters: list[Any] = [context.workspace_id]
        if asset_type is not None:
            clauses.append("asset_type = %s")
            parameters.append(asset_type)
        if modules:
            clauses.append("module = ANY(%s)")
            parameters.append(list(modules))
        if before_created_at is not None and before_id is not None:
            clauses.append("(created_at, id) < (%s::timestamptz, %s)")
            parameters.extend([before_created_at, before_id])
        # One more than a page, so callers can tell whether another follows.
        parameters.append(page_limit(limit, maximum=MAX_PAGE_SIZE + 1))
        rows = self.connection.execute(
            f"SELECT {_ASSET_COLUMNS} FROM omnix_assets WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, id DESC LIMIT %s",
            tuple(parameters),
        ).fetchall()
        return [_asset(row) for row in rows]

    def update_descriptor(
        self,
        context: TenantContext,
        asset_id: str,
        *,
        module: str,
        asset_type: str,
        mime_type: str,
        metadata: dict[str, Any],
        compat: dict[str, Any],
        generation_job_id: str | None,
    ) -> dict[str, Any] | None:
        """Update an asset's descriptive fields in one statement (WP-8.1).

        Content (storage key, checksum, size) is immutable here; a provided
        source job replaces the recorded one, otherwise it is kept.
        """
        row = self.connection.execute(
            f"""
            UPDATE omnix_assets
               SET module = %s, asset_type = %s, mime_type = %s,
                   metadata = %s::jsonb, compat = %s::jsonb,
                   generation_job_id = COALESCE(%s, generation_job_id),
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s
            RETURNING {_ASSET_COLUMNS}
            """,
            (
                module,
                asset_type,
                mime_type,
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                json.dumps(compat, sort_keys=True, separators=(",", ":")),
                generation_job_id,
                context.workspace_id,
                asset_id,
            ),
        ).fetchone()
        return _asset(row) if row is not None else None

    def mark_deleted(
        self,
        context: TenantContext,
        *,
        asset_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_assets
               SET lifecycle_status = 'deleted',
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s AND revision = %s
            RETURNING {_ASSET_COLUMNS}
            """,
            (asset_id, context.workspace_id, expected_revision),
        ).fetchone()
        if row is not None:
            return _asset(row)
        current = self.connection.execute(
            "SELECT revision FROM omnix_assets WHERE id = %s AND workspace_id = %s",
            (asset_id, context.workspace_id),
        ).fetchone()
        if current is None:
            raise EntityNotFound(asset_id)
        raise RevisionConflict(
            f"asset {asset_id} expected revision {expected_revision}; current {int(current[0])}"
        )


class PostgresSettingsRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get(self, context: TenantContext, *, scope: str, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT value, revision, created_at, updated_at
              FROM omnix_settings
             WHERE workspace_id = %s AND setting_scope = %s AND setting_key = %s
            """,
            (context.workspace_id, scope, key),
        ).fetchone()
        if row is None:
            return None
        return {
            "scope": scope,
            "key": key,
            "value": row[0],
            "revision": int(row[1]),
            "created_at": row[2].isoformat(),
            "updated_at": row[3].isoformat(),
        }

    def put(
        self,
        context: TenantContext,
        *,
        scope: str,
        key: str,
        value: Any,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if expected_revision is None:
            row = self.connection.execute(
                """
                INSERT INTO omnix_settings
                    (workspace_id, setting_scope, setting_key, value, updated_by)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (workspace_id, setting_scope, setting_key) DO NOTHING
                RETURNING value, revision, created_at, updated_at
                """,
                (context.workspace_id, scope, key, serialized, context.user_id),
            ).fetchone()
            if row is None:
                raise RevisionConflict(f"setting {scope}/{key} already exists")
        else:
            row = self.connection.execute(
                """
                UPDATE omnix_settings
                   SET value = %s::jsonb,
                       revision = revision + 1,
                       updated_by = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND setting_scope = %s
                   AND setting_key = %s AND revision = %s
                RETURNING value, revision, created_at, updated_at
                """,
                (
                    serialized,
                    context.user_id,
                    context.workspace_id,
                    scope,
                    key,
                    expected_revision,
                ),
            ).fetchone()
            if row is None:
                raise RevisionConflict(
                    f"setting {scope}/{key} expected revision {expected_revision}"
                )
        return {
            "scope": scope,
            "key": key,
            "value": row[0],
            "revision": int(row[1]),
            "created_at": row[2].isoformat(),
            "updated_at": row[3].isoformat(),
        }


class PostgresSecretReferenceRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def register(
        self,
        context: TenantContext,
        *,
        reference: str,
        provider: str,
        purpose: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_secret_references
                (workspace_id, secret_reference, provider, purpose, created_by, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, secret_reference) DO UPDATE SET
                provider = EXCLUDED.provider,
                purpose = EXCLUDED.purpose,
                status = 'active',
                updated_at = CURRENT_TIMESTAMP,
                metadata = EXCLUDED.metadata
            RETURNING secret_reference, provider, purpose, status, created_at, updated_at, metadata
            """,
            (
                context.workspace_id,
                reference,
                provider,
                purpose,
                context.user_id,
                json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
            ),
        ).fetchone()
        return {
            "reference": str(row[0]),
            "provider": str(row[1]),
            "purpose": str(row[2]),
            "status": str(row[3]),
            "created_at": row[4].isoformat(),
            "updated_at": row[5].isoformat(),
            "metadata": dict(row[6] or {}),
        }
