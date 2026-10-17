"""Reconstruct source and attest every asset named by a frozen export."""
from __future__ import annotations

from typing import Any

from app.persistence.blob_store import BlobIntegrityError
from app.persistence.contracts import BlobStore
from app.persistence.database import PostgresDatabase
from app.persistence.asset_repository import PostgresAssetRepository
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work

from .export import manifest_hash
from .integrity import SourceIntegrityError, validate_revision
from .models import CanonicalChapter, SourceRevision, SourceSpan


def _asset_check(connection: Any, blobs: BlobStore, context: TenantContext,
                 asset_id: str, checksum: str) -> bool:
    asset = PostgresAssetRepository(connection).asset_fields(context, asset_id)
    if (asset is None or asset["checksum_sha256"] != checksum or asset["storage_provider"] != blobs.provider
            or asset["lifecycle_status"] != "active"):
        return False
    try:
        return bool(blobs.read_bytes(str(asset["storage_key"]), expected_checksum=checksum))
    except (BlobIntegrityError, FileNotFoundError, OSError):
        return False


def audit_export(database: PostgresDatabase, blobs: BlobStore,
                 context: TenantContext, *, project_id: str,
                 export_id: str) -> dict[str, Any]:
    with unit_of_work(database) as work:
        export = work.connection.execute(
            """SELECT source_revision_id, manifest, manifest_hash, output_asset_id
                 FROM omnix_audiobook_exports
                WHERE workspace_id = %s AND project_id = %s AND id = %s""",
            (context.workspace_id, project_id, export_id),
        ).fetchone()
        output = work.assets.asset_fields(context, str(export[3])) if export else None
        export = (*export, output["checksum_sha256"]) if export and output else None
        if export is None:
            raise KeyError(export_id)
        source = work.connection.execute(
            """SELECT original_asset_id, original_asset_hash, source_format,
                      extractor_version, extraction_settings, extraction_settings_hash,
                      canonical_hash, metadata, warnings
                 FROM omnix_audiobook_source_revisions
                WHERE workspace_id = %s AND id = %s""",
            (context.workspace_id, export[0]),
        ).fetchone()
        if source is None:
            raise ValueError("export source revision is missing")
        chapter_rows = work.connection.execute(
            """SELECT id, ordinal, title, canonical_text, canonical_hash, structure
                 FROM omnix_audiobook_chapters
                WHERE workspace_id = %s AND source_revision_id = %s
                ORDER BY ordinal""",
            (context.workspace_id, export[0]),
        ).fetchall()
        chapters = []
        for chapter in chapter_rows:
            span_rows = work.connection.execute(
                """SELECT id, ordinal, start_offset, end_offset, source_text,
                          source_hash, structural_kind, detector_version
                     FROM omnix_audiobook_spans
                    WHERE workspace_id = %s AND chapter_id = %s ORDER BY ordinal""",
                (context.workspace_id, chapter[0]),
            ).fetchall()
            spans = tuple(SourceSpan(
                str(row[0]), str(chapter[0]), int(row[1]), int(row[2]), int(row[3]),
                str(row[4]), str(row[5]), str(row[6]), str(row[7]),
            ) for row in span_rows)
            chapters.append(CanonicalChapter(
                str(chapter[0]), int(chapter[1]), str(chapter[2]),
                str(chapter[3]), str(chapter[4]), spans, dict(chapter[5] or {}),
            ))
        revision = SourceRevision(
            id=str(export[0]), project_id=project_id,
            original_asset_hash=str(source[1]), source_format=str(source[2]),
            extractor_version=str(source[3]), extraction_settings=dict(source[4]),
            extraction_settings_hash=str(source[5]), canonical_hash=str(source[6]),
            chapters=tuple(chapters), metadata=dict(source[7] or {}),
            warnings=tuple(source[8] or []),
        )
        checks: list[dict[str, Any]] = []

        def check(kind: str, subject_id: str, passed: bool) -> None:
            checks.append({"kind": kind, "id": subject_id, "passed": passed})

        try:
            validate_revision(revision)
            check("canonical_source", revision.id, True)
        except SourceIntegrityError:
            check("canonical_source", revision.id, False)
        check("original_asset", str(source[0]), _asset_check(
            work.connection, blobs, context, str(source[0]), str(source[1])))
        manifest = dict(export[1])
        rendered_chapter_jobs = work.jobs.query_jobs(
            context,
            module="audiobook",
            job_type="audiobook.render-chapter",
            input_fields=(
                ("render_run_id", str(manifest.get("render_run_id") or "")),
                ("source_revision_id", revision.id),
            ),
            order_by="created_asc",
            limit=500,
        )
        rendered_chapter_ids = {
            str((job["input_payload"] or {}).get("chapter_id") or "")
            for job in rendered_chapter_jobs
        }
        expected_audio_chapters = [
            (item.id, item.canonical_hash)
            for item in revision.chapters
            if item.id in rendered_chapter_ids
        ]
        check("manifest", export_id, manifest_hash(manifest) == str(export[2])
              and manifest["source_revision_id"] == revision.id
              and manifest["source_canonical_hash"] == revision.canonical_hash
              and [(item["id"], item["canonical_hash"]) for item in manifest["chapters"]]
              == expected_audio_chapters)
        check("output_asset", str(export[3]), _asset_check(
            work.connection, blobs, context, str(export[3]), str(export[4])))
        render_details = []
        generated_duration_seconds = 0.0
        generation_wall_seconds = 0.0
        for chapter in manifest["chapters"]:
            assembly = work.connection.execute(
                """SELECT assembly_key, audio_asset_id, audio_checksum, render_ids
                     FROM omnix_audiobook_chapter_assemblies
                    WHERE workspace_id = %s AND id = %s AND chapter_id = %s""",
                (context.workspace_id, chapter["assembly_id"], chapter["id"]),
            ).fetchone()
            assembly_valid = bool(assembly and assembly[0] == chapter["assembly_key"]
                                  and assembly[1] == chapter["audio_asset_id"]
                                  and assembly[2] == chapter["audio_checksum"]
                                  and assembly[3] == chapter["render_ids"])
            check("chapter_assembly", str(chapter["assembly_id"]), assembly_valid and _asset_check(
                work.connection, blobs, context,
                str(chapter["audio_asset_id"]), str(chapter["audio_checksum"])))
            for render in chapter["renders"]:
                row = work.connection.execute(
                    """SELECT r.render_key, r.audio_asset_id, r.audio_checksum,
                              r.annotation_id, r.casting_id, r.speech_plan_hash,
                              r.tts_input_text, r.transformations,
                              a.revision, c.revision, c.voice_profile_id,
                              c.voice_revision_hash, r.duration_seconds,
                              r.diagnostics
                         FROM omnix_audiobook_renders r
                         JOIN omnix_audiobook_annotations a ON a.id = r.annotation_id
                         LEFT JOIN omnix_audiobook_castings c ON c.id = r.casting_id
                        WHERE r.workspace_id = %s AND r.id = %s""",
                    (context.workspace_id, render["id"]),
                ).fetchone()
                passed = bool(row and row[0] == render["render_key"]
                              and row[2] == render["audio_checksum"]
                              and row[3] == render["annotation_id"]
                              and row[4] == render["casting_id"]
                              and row[5] == render["speech_plan_hash"]
                              and row[8] == render["annotation_revision"]
                              and row[9] == render["casting_revision"]
                              and row[10] == render["voice_profile_id"]
                              and row[11] == render["voice_revision_hash"]
                              and _asset_check(work.connection, blobs, context,
                                               str(row[1]), str(row[2])))
                check("span_render", str(render["id"]), passed)
                if row:
                    generated_duration_seconds += float(row[12])
                    generation_wall_seconds += float((row[13] or {}).get("generation_wall_seconds", 0))
                    render_details.append({"render_id": render["id"],
                                           "tts_input_text": row[6],
                                           "transformations": row[7],
                                           "duration_seconds": row[12],
                                           "diagnostics": row[13]})
        cover = manifest.get("cover")
        if cover:
            check("cover_asset", str(cover["asset_id"]), _asset_check(
                work.connection, blobs, context, str(cover["asset_id"]),
                str(cover["checksum"])))
        work.rollback()
    return {"export_id": export_id, "project_id": project_id,
            "source_revision_id": revision.id, "manifest_hash": str(export[2]),
            "manifest": manifest, "checks": checks,
            "render_details": render_details,
            "generation_summary": {
                "render_count": len(render_details),
                "audio_duration_seconds": generated_duration_seconds,
                "generation_wall_seconds": generation_wall_seconds,
                "real_time_factor": (generation_wall_seconds / generated_duration_seconds
                                     if generated_duration_seconds else None),
            },
            "passed": all(item["passed"] for item in checks),
            "failed_checks": [item for item in checks if not item["passed"]]}
