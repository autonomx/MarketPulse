from __future__ import annotations

from typing import Any, Mapping

from app.apps.rpg.narration.narrative_engine.publisher_guard import CANONICAL_PUBLISHER

from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


class NarrativeRetirementConflict(RuntimeError):
    pass


_COLUMNS = """
workspace_id, response_id, content_hash, publisher,
canonical_publish_count, alternate_publish_count, rejected_alternate_count,
legacy_ownership_retired, compatibility_projection_only, delivery_mode,
production_certification_jsonb, deletion_audit_jsonb, metadata_jsonb,
created_at, updated_at
"""


def _row(value: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(value[0]),
        "response_id": str(value[1]),
        "content_hash": str(value[2]),
        "publisher": str(value[3]),
        "canonical_publish_count": int(value[4]),
        "alternate_publish_count": int(value[5]),
        "rejected_alternate_count": int(value[6]),
        "legacy_ownership_retired": bool(value[7]),
        "compatibility_projection_only": bool(value[8]),
        "delivery_mode": str(value[9]),
        "production_certification": dict(value[10] or {}),
        "deletion_audit": dict(value[11] or {}),
        "metadata": dict(value[12] or {}),
        "created_at": value[13].isoformat(),
        "updated_at": value[14].isoformat(),
    }


class PostgresRpgNarrativeRetirementRepository:
    """Durable per-response proof that visible legacy ownership is retired."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def put(
        self,
        context: TenantContext,
        *,
        response_id: str,
        content_hash: str,
        publisher: str,
        canonical_publish_count: int,
        alternate_publish_count: int,
        rejected_alternate_count: int,
        legacy_ownership_retired: bool,
        compatibility_projection_only: bool,
        delivery_mode: str,
        production_certification: Mapping[str, Any],
        deletion_audit: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if publisher != CANONICAL_PUBLISHER:
            raise NarrativeRetirementConflict(
                f"retirement record requires canonical publisher: {publisher or '<missing>'}"
            )
        if alternate_publish_count != 0:
            raise NarrativeRetirementConflict(
                "retirement record requires zero alternate publishers"
            )
        if not legacy_ownership_retired or not compatibility_projection_only:
            raise NarrativeRetirementConflict(
                "retirement record requires retired ownership and projection-only compatibility"
            )
        if deletion_audit.get("passed") is not True:
            raise NarrativeRetirementConflict(
                "retirement record requires a passing legacy deletion audit"
            )

        response = self.connection.execute(
            """
            SELECT content_hash
              FROM omnix_rpg_narrative_responses
             WHERE workspace_id = %s AND response_id = %s
             FOR SHARE
            """,
            (context.workspace_id, response_id),
        ).fetchone()
        if response is None:
            raise NarrativeRetirementConflict(
                f"canonical response must exist before retirement proof: {response_id}"
            )
        if str(response[0]) != content_hash:
            raise NarrativeRetirementConflict(
                f"retirement content hash differs from canonical response: {response_id}"
            )

        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_narrative_retirement_records (
                workspace_id, response_id, content_hash, publisher,
                canonical_publish_count, alternate_publish_count,
                rejected_alternate_count, legacy_ownership_retired,
                compatibility_projection_only, delivery_mode,
                production_certification_jsonb, deletion_audit_jsonb,
                metadata_jsonb
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb
            )
            ON CONFLICT (workspace_id, response_id) DO UPDATE
               SET publisher = EXCLUDED.publisher,
                   canonical_publish_count = GREATEST(
                       omnix_rpg_narrative_retirement_records.canonical_publish_count,
                       EXCLUDED.canonical_publish_count
                   ),
                   alternate_publish_count = EXCLUDED.alternate_publish_count,
                   rejected_alternate_count = GREATEST(
                       omnix_rpg_narrative_retirement_records.rejected_alternate_count,
                       EXCLUDED.rejected_alternate_count
                   ),
                   legacy_ownership_retired = EXCLUDED.legacy_ownership_retired,
                   compatibility_projection_only = EXCLUDED.compatibility_projection_only,
                   delivery_mode = EXCLUDED.delivery_mode,
                   production_certification_jsonb = EXCLUDED.production_certification_jsonb,
                   deletion_audit_jsonb = EXCLUDED.deletion_audit_jsonb,
                   metadata_jsonb = EXCLUDED.metadata_jsonb,
                   updated_at = CURRENT_TIMESTAMP
             WHERE omnix_rpg_narrative_retirement_records.content_hash = EXCLUDED.content_hash
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                response_id,
                content_hash,
                publisher,
                max(0, int(canonical_publish_count)),
                0,
                max(0, int(rejected_alternate_count)),
                True,
                True,
                str(delivery_mode or "blocking"),
                canonical_json(dict(production_certification)),
                canonical_json(dict(deletion_audit)),
                canonical_json(dict(metadata or {})),
            ),
        ).fetchone()
        if row is None:
            raise NarrativeRetirementConflict(
                f"retirement response identity changed: {response_id}"
            )
        return _row(row)

    def get(
        self,
        context: TenantContext,
        response_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"""
            SELECT {_COLUMNS}
              FROM omnix_rpg_narrative_retirement_records
             WHERE workspace_id = %s AND response_id = %s
            """,
            (context.workspace_id, response_id),
        ).fetchone()
        return _row(row) if row is not None else None

    def release_snapshot(self, context: TenantContext) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT COUNT(*),
                   COALESCE(MAX(canonical_publish_count), 0),
                   COALESCE(MAX(alternate_publish_count), 0),
                   COALESCE(MAX(rejected_alternate_count), 0),
                   COUNT(*) FILTER (
                       WHERE publisher <> %s
                          OR legacy_ownership_retired IS NOT TRUE
                          OR compatibility_projection_only IS NOT TRUE
                          OR (deletion_audit_jsonb->>'passed')::boolean IS NOT TRUE
                   ),
                   MAX(updated_at)
              FROM omnix_rpg_narrative_retirement_records
             WHERE workspace_id = %s
            """,
            (CANONICAL_PUBLISHER, context.workspace_id),
        ).fetchone()
        record_count = int(row[0])
        violations = int(row[4])
        return {
            "record_count": record_count,
            "canonical_publish_count": int(row[1]),
            "alternate_publish_count": int(row[2]),
            "rejected_alternate_count": int(row[3]),
            "violation_count": violations,
            "zero_alternate_publishers": int(row[2]) == 0,
            "legacy_publisher_deletion_certified": record_count > 0 and violations == 0,
            "latest_recorded_at": row[5].isoformat() if row[5] is not None else None,
        }
