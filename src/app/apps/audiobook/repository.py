"""PostgreSQL authority for audiobook projects and immutable canonical revisions."""
from __future__ import annotations

from typing import Any

from app.persistence.asset_repository import PostgresAssetRepository
from app.persistence.tenant import TenantContext
from app.runtime.pagination import page_limit

from .annotation import narrator_id
from .hashing import canonical_json
from .integrity import validate_revision
from .models import SourceRevision


def cancel_render_run_jobs(jobs: Any, context: TenantContext, render_run_id: str) -> None:
    cursor = None
    while True:
        rows = jobs.query_jobs(
            context,
            module="audiobook",
            job_types=("audiobook.render-chapter", "audiobook.assemble-chapter"),
            input_fields=(("render_run_id", render_run_id),),
            statuses=("queued", "waiting", "retrying", "leased", "running", "paused", "cancel_requested"),
            after_created=cursor,
            order_by="created_asc",
            limit=100,
            for_update=True,
        )
        for row in rows:
            jobs.request_cancel(context, str(row["id"]))
        if len(rows) < 100:
            return
        last = rows[-1]
        cursor = (last["created_at"], str(last["id"]))


class PostgresAudiobookRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def register_ingest_request(self, context: TenantContext, *, project_id: str,
                                job_id: str) -> None:
        self.connection.execute(
            """UPDATE omnix_audiobook_projects
                  SET settings = jsonb_set(settings, '{current_ingest_job_id}',
                                           to_jsonb(%s::text), true)
                WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL""",
            (job_id, context.workspace_id, project_id),
        )

    def is_current_ingest(self, context: TenantContext, *, project_id: str,
                          job_id: str) -> bool:
        # Keep the project locked through publication. Submission takes this
        # same lock, so a newer request cannot race this completion check.
        project = self.connection.execute(
            """SELECT settings->>'current_ingest_job_id'
                 FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL
                FOR UPDATE""", (context.workspace_id, project_id),
        ).fetchone()
        if project is None:
            return False
        if project[0]:
            return str(project[0]) == job_id
        # Jobs created before request fencing was introduced have no pointer.
        from app.persistence.job_repository import PostgresJobRepository

        latest = PostgresJobRepository(self.connection).query_jobs(
            context,
            module="audiobook",
            job_type="audiobook.ingest",
            input_fields=(("project_id", project_id),),
            order_by="created_desc",
            limit=1,
        )
        return bool(latest and str(latest[0]["id"]) == job_id)

    def create_project(
        self, context: TenantContext, *, project_id: str,
        title: str, author: str = "", language: str = "en",
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("title is required")
        row = self.connection.execute(
            """
            INSERT INTO omnix_audiobook_projects
                (id, workspace_id, owner_user_id, title, author, language, settings)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id, title, author, language, state, current_source_revision_id
            """,
            (
                project_id, context.workspace_id, context.user_id,
                title.strip(), author.strip(), language.strip(),
                canonical_json({"audiobook_mode": "story_only"}),
            ),
        ).fetchone()
        return self._project(row)

    def get_project(self, context: TenantContext, project_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id, title, author, language, state, current_source_revision_id
             FROM omnix_audiobook_projects
             WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL
            """, (context.workspace_id, project_id),
        ).fetchone()
        return self._project(row) if row else None

    def list_projects(self, context: TenantContext, *, limit: int = 100,
                      offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, title, author, language, state, current_source_revision_id
             FROM omnix_audiobook_projects
             WHERE workspace_id = %s AND deleted_at IS NULL
             ORDER BY updated_at DESC, id LIMIT %s OFFSET %s
            """, (context.workspace_id, page_limit(limit, default=100), max(0, offset)),
        ).fetchall()
        return [self._project(row) for row in rows]

    @staticmethod
    def _project(row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]), "title": str(row[1]), "author": str(row[2]),
            "language": str(row[3]), "state": str(row[4]),
            "current_source_revision_id": str(row[5]) if row[5] else None,
        }

    def append_source_revision(
        self, context: TenantContext, revision: SourceRevision, *, original_asset_id: str,
    ) -> str:
        """Caller owns one transaction, including the project pointer update."""
        validate_revision(revision)
        project = self.get_project(context, revision.project_id)
        if project is None:
            raise KeyError(revision.project_id)
        asset = PostgresAssetRepository(self.connection).asset_fields(context, original_asset_id, active_only=True)
        if asset is None or str(asset["checksum_sha256"]) != revision.original_asset_hash:
            raise ValueError("original asset is missing or its checksum differs")
        existing = self.connection.execute(
            """SELECT original_asset_hash, source_format, extractor_version,
                      extraction_settings_hash, canonical_hash
                 FROM omnix_audiobook_source_revisions
                WHERE workspace_id = %s AND id = %s""",
            (context.workspace_id, revision.id),
        ).fetchone()
        if existing is not None:
            expected = (
                revision.original_asset_hash,
                revision.source_format,
                revision.extractor_version,
                revision.extraction_settings_hash,
                revision.canonical_hash,
            )
            actual = tuple(str(value) for value in existing)
            if actual != expected:
                raise ValueError("source revision identity collision")
        else:
            self.connection.execute(
                """
                INSERT INTO omnix_audiobook_source_revisions
                    (id, workspace_id, project_id, original_asset_id, original_asset_hash,
                     source_format, extractor_version, extraction_settings,
                     extraction_settings_hash, canonical_hash, metadata, warnings)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (revision.id, context.workspace_id, revision.project_id, original_asset_id,
                 revision.original_asset_hash, revision.source_format, revision.extractor_version,
                 canonical_json(revision.extraction_settings), revision.extraction_settings_hash,
                 revision.canonical_hash, canonical_json(revision.metadata), canonical_json(revision.warnings)),
            )
            for chapter in revision.chapters:
                self.connection.execute(
                    """
                    INSERT INTO omnix_audiobook_chapters
                        (id, workspace_id, source_revision_id, ordinal, title,
                         canonical_text, canonical_hash, structure)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (chapter.id, context.workspace_id, revision.id, chapter.ordinal,
                     chapter.title, chapter.canonical_text, chapter.canonical_hash,
                     canonical_json(chapter.structure)),
                )
                for span in chapter.spans:
                    self.connection.execute(
                        """
                        INSERT INTO omnix_audiobook_spans
                            (id, workspace_id, chapter_id, ordinal, start_offset, end_offset,
                             source_text, source_hash, structural_kind, detector_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (span.id, context.workspace_id, chapter.id, span.ordinal,
                         span.start_offset, span.end_offset, span.source_text,
                         span.source_hash, span.structural_kind, span.detector_version),
                    )
        # Manual interpretation is available as soon as extraction publishes.
        # Its deterministic narrator must not depend on an AI analysis pass.
        self.connection.execute(
            """INSERT INTO omnix_audiobook_speakers
                (id, workspace_id, project_id, canonical_name, display_name, kind)
               VALUES (%s::uuid, %s, %s, 'Narrator', 'Narrator', 'narrator')
               ON CONFLICT (id) DO NOTHING""",
            (narrator_id(revision.project_id), context.workspace_id, revision.project_id),
        )
        active = self.connection.execute(
            """
            SELECT settings->>'current_render_run_id'
              FROM omnix_audiobook_projects
             WHERE workspace_id = %s AND id = %s FOR UPDATE
            """, (context.workspace_id, revision.project_id),
        ).fetchone()
        if active and active[0]:
            from app.persistence.job_repository import PostgresJobRepository

            jobs = PostgresJobRepository(self.connection)
            cancel_render_run_jobs(jobs, context, str(active[0]))
        self.connection.execute(
            """
            UPDATE omnix_audiobook_projects
               SET current_source_revision_id = %s, state = 'extracted',
                   settings = settings - 'current_render_run_id',
                   settings_revision = settings_revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s
            """, (revision.id, context.workspace_id, revision.project_id),
        )
        return revision.id

    def list_chapters(self, context: TenantContext, source_revision_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT c.id, c.ordinal, c.title, c.canonical_text, c.canonical_hash
              FROM omnix_audiobook_chapters AS c
              JOIN omnix_audiobook_source_revisions AS s
                ON s.workspace_id = c.workspace_id AND s.id = c.source_revision_id
             WHERE c.workspace_id = %s AND c.source_revision_id = %s
             ORDER BY c.ordinal
            """, (context.workspace_id, source_revision_id),
        ).fetchall()
        return [{"id": str(row[0]), "ordinal": int(row[1]), "title": str(row[2]),
                 "canonical_text": str(row[3]), "canonical_hash": str(row[4])} for row in rows]

    def list_spans(self, context: TenantContext, chapter_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, ordinal, start_offset, end_offset, source_text,
                   source_hash, structural_kind, detector_version
              FROM omnix_audiobook_spans
             WHERE workspace_id = %s AND chapter_id = %s ORDER BY ordinal
            """, (context.workspace_id, chapter_id),
        ).fetchall()
        return [dict(id=str(row[0]), ordinal=int(row[1]), start_offset=int(row[2]),
                     end_offset=int(row[3]), source_text=str(row[4]), source_hash=str(row[5]),
                     structural_kind=str(row[6]), detector_version=str(row[7])) for row in rows]
