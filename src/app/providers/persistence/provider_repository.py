"""Provider configurations and status projections in PostgreSQL (moved from kernel persistence, PA-2.2)."""
from __future__ import annotations

import json
from typing import Any

from app.persistence.errors import RevisionConflict
from app.persistence.tenant import TenantContext


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PostgresProviderRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create(
        self,
        context: TenantContext,
        *,
        provider_id: str,
        provider_type: str,
        display_name: str,
        config: dict[str, Any],
        secret_reference: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        self._reject_secret_values(config)
        row = self.connection.execute(
            """
            INSERT INTO omnix_provider_configs (
                id, workspace_id, provider_type, display_name, config,
                secret_reference, enabled
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING id, provider_type, display_name, config, secret_reference,
                      enabled, revision, created_at, updated_at
            """,
            (
                provider_id,
                context.workspace_id,
                provider_type,
                display_name,
                _json(config),
                secret_reference,
                enabled,
            ),
        ).fetchone()
        return self._record(row)

    def get(self, context: TenantContext, provider_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id, provider_type, display_name, config, secret_reference,
                   enabled, revision, created_at, updated_at
              FROM omnix_provider_configs
             WHERE workspace_id = %s AND id = %s
            """,
            (context.workspace_id, provider_id),
        ).fetchone()
        return self._record(row) if row is not None else None

    def update(
        self,
        context: TenantContext,
        *,
        provider_id: str,
        display_name: str,
        config: dict[str, Any],
        secret_reference: str | None,
        enabled: bool,
        expected_revision: int,
    ) -> dict[str, Any]:
        self._reject_secret_values(config)
        row = self.connection.execute(
            """
            UPDATE omnix_provider_configs
               SET display_name = %s, config = %s::jsonb,
                   secret_reference = %s, enabled = %s,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s AND revision = %s
            RETURNING id, provider_type, display_name, config, secret_reference,
                      enabled, revision, created_at, updated_at
            """,
            (
                display_name,
                _json(config),
                secret_reference,
                enabled,
                context.workspace_id,
                provider_id,
                expected_revision,
            ),
        ).fetchone()
        if row is None:
            raise RevisionConflict(
                f"provider {provider_id} expected revision {expected_revision}"
            )
        return self._record(row)

    def put_status(
        self,
        context: TenantContext,
        *,
        provider_id: str,
        status: dict[str, Any],
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_provider_status_projections
                (workspace_id, provider_id, status, expires_at)
            VALUES (%s, %s, %s::jsonb, %s::timestamptz)
            ON CONFLICT (workspace_id, provider_id) DO UPDATE SET
                status = EXCLUDED.status,
                observed_at = CURRENT_TIMESTAMP,
                expires_at = EXCLUDED.expires_at
            RETURNING provider_id, status, observed_at, expires_at
            """,
            (context.workspace_id, provider_id, _json(status), expires_at),
        ).fetchone()
        return {
            "provider_id": str(row[0]),
            "status": dict(row[1]),
            "observed_at": row[2].isoformat(),
            "expires_at": row[3].isoformat() if row[3] is not None else None,
        }

    @staticmethod
    def _reject_secret_values(config: dict[str, Any]) -> None:
        forbidden = {"api_key", "token", "secret", "password", "credential"}
        matches = sorted(forbidden.intersection(str(key).lower() for key in config))
        if matches:
            raise ValueError(
                f"provider config contains secret-bearing keys; use SecretStore reference: {matches}"
            )

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "provider_type": str(row[1]),
            "display_name": str(row[2]),
            "config": dict(row[3]),
            "secret_reference": str(row[4]) if row[4] is not None else None,
            "enabled": bool(row[5]),
            "revision": int(row[6]),
            "created_at": row[7].isoformat(),
            "updated_at": row[8].isoformat(),
        }
