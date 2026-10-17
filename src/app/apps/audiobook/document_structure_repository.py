"""Persistence for document-structure interpretations and scoped user overrides."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.persistence.tenant import TenantContext

from .document_structure import (
    CONTENT_ROLES, DocumentBlock, DocumentStructureAnalysis, StructuralRegion,
)
from .hashing import canonical_json, object_hash


class PostgresAudiobookDocumentStructureRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def append_analysis(
        self, context: TenantContext, analysis: DocumentStructureAnalysis,
        *, classifier: dict[str, Any] | None = None,
    ) -> str:
        run_hash = object_hash({
            "source_revision_id": analysis.source_revision_id,
            "version": analysis.version,
            "regions": [
                {
                    "id": region.id,
                    "role": region.content_role,
                    "confidence": region.confidence,
                    "block_ids": list(region.block_ids),
                }
                for region in analysis.regions
            ],
            "blocks": [
                {
                    "id": block.id,
                    "role": block.content_role,
                    "confidence": block.confidence,
                    "provenance": list(block.provenance),
                    "recurrence_group": block.recurrence_group,
                }
                for block in analysis.blocks
            ],
        })
        run_id = f"ab:dsr:{run_hash}"
        existing = self.connection.execute(
            """SELECT id FROM omnix_audiobook_structure_runs
                WHERE workspace_id = %s AND id = %s""",
            (context.workspace_id, run_id),
        ).fetchone()
        if existing is not None:
            return run_id
        self.connection.execute(
            """INSERT INTO omnix_audiobook_structure_runs
                   (id, workspace_id, source_revision_id, analyzer_version,
                    classifier, ai_fallback_used)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
            (
                run_id, context.workspace_id, analysis.source_revision_id,
                analysis.version, canonical_json(classifier or {}),
                analysis.ai_fallback_used,
            ),
        )
        for region in analysis.regions:
            self.connection.execute(
                """INSERT INTO omnix_audiobook_structural_regions
                       (id, workspace_id, structure_run_id, source_revision_id,
                        chapter_id, start_offset, end_offset, block_ids,
                        content_role, confidence, provenance)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                           %s, %s, %s::jsonb)""",
                (
                    region.id, context.workspace_id, run_id,
                    analysis.source_revision_id, region.chapter_id,
                    region.start_offset, region.end_offset,
                    canonical_json(list(region.block_ids)),
                    region.content_role, region.confidence,
                    canonical_json(list(region.provenance)),
                ),
            )
        for block in analysis.blocks:
            self.connection.execute(
                """INSERT INTO omnix_audiobook_document_blocks
                       (id, workspace_id, structure_run_id, source_revision_id,
                        chapter_id, ordinal, start_offset, end_offset,
                        source_span_ids, original_text, normalized_text,
                        content_role, confidence, provenance, recurrence_group,
                        structure_quality, page_index, page_block_index,
                        reading_order, distance_from_top, distance_from_bottom,
                        bounding_box, font_size, font_weight, font_style,
                        parent_block_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                           %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                           %s, %s, %s::jsonb, %s, %s, %s, %s)""",
                (
                    block.id, context.workspace_id, run_id,
                    analysis.source_revision_id, block.chapter_id, block.ordinal,
                    block.start_offset, block.end_offset,
                    canonical_json(list(block.source_span_ids)),
                    block.original_text, block.normalized_text,
                    block.content_role, block.confidence,
                    canonical_json(list(block.provenance)),
                    block.recurrence_group, block.structure_quality,
                    block.page_index, block.page_block_index,
                    block.reading_order, block.distance_from_top,
                    block.distance_from_bottom,
                    canonical_json(list(block.bounding_box))
                    if block.bounding_box is not None else None,
                    block.font_size, block.font_weight, block.font_style,
                    block.parent_block_id,
                ),
            )
        return run_id

    def latest_run_id(
        self, context: TenantContext, source_revision_id: str,
    ) -> str | None:
        row = self.connection.execute(
            """SELECT id FROM omnix_audiobook_structure_runs
                WHERE workspace_id = %s AND source_revision_id = %s
                ORDER BY created_at DESC, id DESC LIMIT 1""",
            (context.workspace_id, source_revision_id),
        ).fetchone()
        return str(row[0]) if row else None

    def list_blocks(
        self, context: TenantContext, *, source_revision_id: str,
        chapter_id: str | None = None,
    ) -> list[DocumentBlock]:
        run_id = self.latest_run_id(context, source_revision_id)
        if run_id is None:
            return []
        rows = self.connection.execute(
            """SELECT id, chapter_id, ordinal, start_offset, end_offset,
                      source_span_ids, original_text, normalized_text,
                      content_role, confidence, provenance, recurrence_group,
                      structure_quality, page_index, page_block_index,
                      reading_order, distance_from_top, distance_from_bottom,
                      bounding_box, font_size, font_weight, font_style,
                      parent_block_id
                 FROM omnix_audiobook_document_blocks
                WHERE workspace_id = %s AND structure_run_id = %s
                  AND (%s::text IS NULL OR chapter_id = %s)
                ORDER BY ordinal""",
            (context.workspace_id, run_id, chapter_id, chapter_id),
        ).fetchall()
        return [
            DocumentBlock(
                id=str(row[0]), chapter_id=str(row[1]), ordinal=int(row[2]),
                start_offset=int(row[3]), end_offset=int(row[4]),
                source_span_ids=tuple(str(item) for item in (row[5] or [])),
                original_text=str(row[6]), normalized_text=str(row[7]),
                content_role=str(row[8]), confidence=float(row[9]),
                provenance=tuple(dict(item) for item in (row[10] or [])),
                recurrence_group=str(row[11]) if row[11] else None,
                structure_quality=str(row[12]),
                page_index=int(row[13]) if row[13] is not None else None,
                page_block_index=int(row[14]) if row[14] is not None else None,
                reading_order=int(row[15]) if row[15] is not None else None,
                distance_from_top=float(row[16]) if row[16] is not None else None,
                distance_from_bottom=float(row[17]) if row[17] is not None else None,
                bounding_box=(
                    tuple(float(value) for value in row[18])
                    if row[18] is not None else None
                ),
                font_size=float(row[19]) if row[19] is not None else None,
                font_weight=str(row[20]) if row[20] else None,
                font_style=str(row[21]) if row[21] else None,
                parent_block_id=str(row[22]) if row[22] else None,
            )
            for row in rows
        ]

    def list_regions(
        self, context: TenantContext, *, source_revision_id: str,
        chapter_id: str | None = None,
    ) -> list[StructuralRegion]:
        run_id = self.latest_run_id(context, source_revision_id)
        if run_id is None:
            return []
        rows = self.connection.execute(
            """SELECT id, chapter_id, start_offset, end_offset, block_ids,
                      content_role, confidence, provenance
                 FROM omnix_audiobook_structural_regions
                WHERE workspace_id = %s AND structure_run_id = %s
                  AND (%s::text IS NULL OR chapter_id = %s)
                ORDER BY chapter_id, start_offset""",
            (context.workspace_id, run_id, chapter_id, chapter_id),
        ).fetchall()
        return [
            StructuralRegion(
                id=str(row[0]), chapter_id=str(row[1]),
                start_offset=int(row[2]), end_offset=int(row[3]),
                block_ids=tuple(str(item) for item in (row[4] or [])),
                content_role=str(row[5]), confidence=float(row[6]),
                provenance=tuple(dict(item) for item in (row[7] or [])),
            )
            for row in rows
        ]

    def list_overrides(
        self, context: TenantContext, *, project_id: str,
        source_revision_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT DISTINCT ON (scope, scope_key)
                      id, revision, scope, scope_key, action, role_override,
                      provenance, created_at
                 FROM omnix_audiobook_document_overrides
                WHERE workspace_id = %s AND project_id = %s
                  AND source_revision_id = %s
                ORDER BY scope, scope_key, revision DESC""",
            (context.workspace_id, project_id, source_revision_id),
        ).fetchall()
        return [
            {
                "id": str(row[0]), "revision": int(row[1]),
                "scope": str(row[2]), "scope_key": str(row[3]),
                "action": str(row[4]),
                "role_override": str(row[5]) if row[5] else None,
                "provenance": dict(row[6] or {}),
                "created_at": row[7].isoformat(),
            }
            for row in rows
        ]

    def append_override(
        self, context: TenantContext, *, project_id: str,
        source_revision_id: str, scope: str, scope_key: str,
        action: str = "DEFAULT", role_override: str | None = None,
    ) -> dict[str, Any]:
        scope = scope.strip().upper()
        action = action.strip().upper()
        scope_key = scope_key.strip()
        if scope not in {"BLOCK", "REGION", "RECURRENCE_GROUP", "DOCUMENT_ROLE"}:
            raise ValueError("invalid document override scope")
        if action not in {"DEFAULT", "READ", "SKIP", "READ_ONCE"}:
            raise ValueError("invalid document override action")
        if not scope_key:
            raise ValueError("document override scope key is required")
        if role_override is not None:
            role_override = role_override.strip()
            if role_override not in CONTENT_ROLES:
                raise ValueError("invalid document content role")

        source = self.connection.execute(
            """SELECT 1 FROM omnix_audiobook_source_revisions
                WHERE workspace_id = %s AND id = %s AND project_id = %s""",
            (context.workspace_id, source_revision_id, project_id),
        ).fetchone()
        if source is None:
            raise ValueError("document override must target this project's source revision")

        run_id = self.latest_run_id(context, source_revision_id)
        if run_id is None:
            raise ValueError("document structure analysis is unavailable")
        target_exists = True
        if scope == "BLOCK":
            target_exists = self.connection.execute(
                """SELECT 1 FROM omnix_audiobook_document_blocks
                    WHERE workspace_id = %s AND source_revision_id = %s
                      AND structure_run_id = %s AND id = %s""",
                (context.workspace_id, source_revision_id, run_id, scope_key),
            ).fetchone() is not None
        elif scope == "REGION":
            target_exists = self.connection.execute(
                """SELECT 1 FROM omnix_audiobook_structural_regions
                    WHERE workspace_id = %s AND source_revision_id = %s
                      AND structure_run_id = %s AND id = %s""",
                (context.workspace_id, source_revision_id, run_id, scope_key),
            ).fetchone() is not None
        elif scope == "RECURRENCE_GROUP":
            target_exists = self.connection.execute(
                """SELECT 1 FROM omnix_audiobook_document_blocks
                    WHERE workspace_id = %s AND source_revision_id = %s
                      AND structure_run_id = %s AND recurrence_group = %s
                    LIMIT 1""",
                (context.workspace_id, source_revision_id, run_id, scope_key),
            ).fetchone() is not None
        elif scope == "DOCUMENT_ROLE":
            target_exists = scope_key in CONTENT_ROLES
        if not target_exists:
            raise ValueError("document override target is not in the current structure analysis")

        previous = self.connection.execute(
            """SELECT revision FROM omnix_audiobook_document_overrides
                WHERE workspace_id = %s AND project_id = %s
                  AND source_revision_id = %s AND scope = %s AND scope_key = %s
                ORDER BY revision DESC LIMIT 1""",
            (
                context.workspace_id, project_id, source_revision_id,
                scope, scope_key,
            ),
        ).fetchone()
        revision = int(previous[0]) + 1 if previous else 1
        override_id = f"ab:do:{uuid4().hex}"
        provenance = {
            "mode": "user_override",
            "user_id": context.user_id,
        }
        self.connection.execute(
            """INSERT INTO omnix_audiobook_document_overrides
                   (id, workspace_id, project_id, source_revision_id, revision,
                    scope, scope_key, action, role_override, provenance,
                    created_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)""",
            (
                override_id, context.workspace_id, project_id, source_revision_id,
                revision, scope, scope_key, action, role_override,
                canonical_json(provenance), context.user_id,
            ),
        )
        return {
            "id": override_id, "revision": revision, "scope": scope,
            "scope_key": scope_key, "action": action,
            "role_override": role_override, "provenance": provenance,
        }
