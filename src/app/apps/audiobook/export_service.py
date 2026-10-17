"""Durable audiobook export jobs bound to immutable chapter assemblies."""
from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import wave
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4


from app.persistence.database import PostgresDatabase
from app.persistence.contracts import BlobStore
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work

from .export import (FORMAT_MIME, ffmetadata,
                     encoded_seconds, ffmpeg_binary, ffmpeg_command, ffmpeg_version,
                     freeze_manifest, manifest_hash)
from .hashing import canonical_json
from .leases import JOB_LEASE_SECONDS, lease_heartbeat


_LOG = logging.getLogger(__name__)


def _stage_book_input(blobs: BlobStore, assets: list[tuple[str, str]],
                      root: Path) -> Path:
    """Prepare verified chapter files for FFmpeg's concat demuxer."""
    if not assets:
        raise ValueError("book has no chapter audio")
    root.mkdir(parents=True, exist_ok=True)
    sample_rate = None
    lines = ["ffconcat version 1.0"]
    for index, (key, checksum) in enumerate(assets):
        name = f"chapter-{index:06d}.wav"
        staged = root / name
        blobs.stage_verified_to(key, staged, expected_checksum=checksum)
        with wave.open(str(staged), "rb") as reader:
            if (reader.getnchannels() != 1 or reader.getsampwidth() != 2
                    or reader.getcomptype() != "NONE" or reader.getnframes() <= 0):
                raise ValueError("chapter audio must be non-empty mono PCM16 WAV")
            if sample_rate is None:
                sample_rate = reader.getframerate()
            elif reader.getframerate() != sample_rate:
                raise ValueError("chapter sample rates differ")
        lines.append(f"file {name}")
    playlist = root / "chapters.ffconcat"
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return playlist


def start_export(database: PostgresDatabase, blobs: BlobStore,
                 context: TenantContext, *, project_id: str,
                 format: str = "m4b") -> dict[str, str]:
    if format not in FORMAT_MIME:
        raise ValueError("unsupported audiobook export format")
    executable = ffmpeg_binary()
    version = ffmpeg_version(executable)
    with unit_of_work(database) as work:
        project = work.connection.execute(
            """SELECT id, title, author, language, cover_asset_id,
                      current_source_revision_id, state,
                      settings->>'current_render_run_id'
                 FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL FOR UPDATE""",
            (context.workspace_id, project_id),
        ).fetchone()
        if project is None:
            raise KeyError(project_id)
        if project[6] not in {"ready_to_export", "exported"}:
            raise ValueError("chapters are not ready for export")
        source = work.connection.execute(
            """SELECT id, canonical_hash FROM omnix_audiobook_source_revisions
                WHERE workspace_id = %s AND id = %s""",
            (context.workspace_id, project[5]),
        ).fetchone()
        if source is None:
            raise ValueError("canonical source is missing")
        render_jobs = work.jobs.query_jobs(
            context,
            module="audiobook",
            job_type="audiobook.render-chapter",
            input_fields=(("render_run_id", str(project[7])),),
            order_by="created_asc",
            limit=500,
        )
        rendered_chapter_ids = sorted({
            str((job["input_payload"] or {}).get("chapter_id") or "")
            for job in render_jobs
            if (job["input_payload"] or {}).get("chapter_id")
        })
        chapter_rows = work.connection.execute(
            """SELECT id, ordinal, title, canonical_hash
                 FROM omnix_audiobook_chapters
                WHERE workspace_id = %s AND source_revision_id = %s
                  AND id = ANY(%s)
                ORDER BY ordinal""",
            (context.workspace_id, source[0], rendered_chapter_ids),
        ).fetchall()
        assembly_jobs = work.jobs.query_jobs(
            context,
            module="audiobook",
            job_type="audiobook.assemble-chapter",
            input_fields=(("render_run_id", str(project[7])),),
            statuses=("completed",),
            order_by="completed_desc",
            limit=500,
        )
        latest_assembly_by_chapter: dict[str, dict[str, Any]] = {}
        for assembly_job in assembly_jobs:
            chapter_id = str((assembly_job["input_payload"] or {}).get("chapter_id") or "")
            if chapter_id and chapter_id not in latest_assembly_by_chapter:
                latest_assembly_by_chapter[chapter_id] = assembly_job
        if not chapter_rows:
            raise ValueError("render run has no audiobook chapters")
        chapters = []
        for chapter_id, ordinal, title, canonical_hash in chapter_rows:
            job = latest_assembly_by_chapter.get(str(chapter_id))
            if not job or not job["output_refs"]:
                raise ValueError(f"chapter {ordinal} has no completed assembly")
            assembly_id = job["output_refs"][0]["assembly_id"]
            assembly = work.connection.execute(
                """SELECT assembly_key, render_ids, audio_asset_id,
                          audio_checksum, duration_seconds, sample_rate,
                          pause_policy, loudness, timeline
                     FROM omnix_audiobook_chapter_assemblies
                    WHERE workspace_id = %s AND id = %s AND chapter_id = %s""",
                (context.workspace_id, assembly_id, chapter_id),
            ).fetchone()
            audio = work.assets.asset_fields(context, str(assembly[2]), active_only=True) if assembly else None
            if audio is not None:
                assembly = (*assembly, audio["storage_key"])
            if assembly is None or audio is None:
                raise ValueError(f"chapter {ordinal} assembly asset is missing")
            with blobs.open_verified(str(assembly[9]), expected_checksum=str(assembly[3])):
                pass
            render_ids = [str(item) for item in assembly[1]]
            renders = []
            for render_id in render_ids:
                row = work.connection.execute(
                    """SELECT r.render_key, r.annotation_id, r.casting_id,
                              r.speech_plan_hash, r.provider, r.generation_settings,
                              r.audio_checksum, c.voice_profile_id, c.voice_revision_hash,
                              a.revision, c.revision
                         FROM omnix_audiobook_renders r
                         LEFT JOIN omnix_audiobook_castings c ON c.id = r.casting_id
                         JOIN omnix_audiobook_annotations a ON a.id = r.annotation_id
                        WHERE r.workspace_id = %s AND r.id = %s""",
                    (context.workspace_id, render_id),
                ).fetchone()
                if row is None:
                    raise ValueError("assembly references a missing render")
                renders.append({"id": render_id, "render_key": row[0],
                                "annotation_id": row[1], "casting_id": row[2],
                                "speech_plan_hash": row[3], "provider": row[4],
                                "generation_settings": row[5],
                                "audio_checksum": row[6],
                                "voice_profile_id": row[7],
                                "voice_revision_hash": row[8],
                                "annotation_revision": row[9],
                                "casting_revision": row[10]})
            chapters.append({
                "id": str(chapter_id), "ordinal": int(ordinal), "title": str(title),
                "canonical_hash": str(canonical_hash),
                "assembly_id": str(assembly_id), "assembly_key": str(assembly[0]),
                "render_ids": render_ids, "renders": renders,
                "audio_asset_id": str(assembly[2]), "audio_checksum": str(assembly[3]),
                "duration_seconds": float(assembly[4]), "sample_rate": int(assembly[5]),
                "pause_policy": assembly[6], "loudness": assembly[7],
                "timeline": assembly[8],
            })
        cover = None
        if project[4]:
            row = work.assets.asset_fields(context, project[4], active_only=True)
            if row is None:
                raise ValueError("project cover asset is missing")
            with blobs.open_verified(str(row["storage_key"]), expected_checksum=str(row["checksum_sha256"])):
                pass
            cover = {"asset_id": row["id"], "checksum": row["checksum_sha256"], "mime_type": row["mime_type"]}
        cast_rows = work.connection.execute(
            """SELECT display_name FROM omnix_audiobook_speakers
                WHERE workspace_id = %s AND project_id = %s AND status = 'active'
                ORDER BY display_name""", (context.workspace_id, project_id),
        ).fetchall()
        manifest = freeze_manifest(
            project={"id": project_id, "title": project[1], "author": project[2],
                     "language": project[3], "cover": cover,
                     "render_run_id": project[7],
                     "cast": [{"name": row[0]} for row in cast_rows]},
            source={"id": source[0], "canonical_hash": source[1]},
            chapters=chapters, format=format, encoder_version=version,
            settings={"codec": {"m4b": "aac", "flac": "flac",
                                "wav": "pcm_s16le", "mp3": "libmp3lame"}[format]},
        )
        job_id = f"ab:job:{uuid4().hex}"
        manifest_id = f"ab:manifest:{uuid4().hex}"
        work.jobs.create_job(context, {
            "id": job_id, "module": "audiobook", "job_type": "audiobook.export",
            "resource_class": "cpu", "priority": 0,
            "input_payload": {"manifest_id": manifest_id, "project_id": project_id},
            "max_attempts": 3,
        })
        work.connection.execute(
            """INSERT INTO omnix_audiobook_export_manifests
                 (id, workspace_id, project_id, source_revision_id, job_id,
                  format, manifest, manifest_hash)
                 VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)""",
            (manifest_id, context.workspace_id, project_id, source[0], job_id,
             format, canonical_json(manifest), manifest_hash(manifest)),
        )
        work.commit()
    return {"job_id": job_id, "manifest_id": manifest_id,
            "manifest_hash": manifest_hash(manifest)}


def run_export_once(database: PostgresDatabase, blobs: BlobStore,
                    context: TenantContext, *, worker_id: str) -> bool:
    with unit_of_work(database) as work:
        job = work.jobs.claim_next(
            context, worker_id=worker_id, resource_classes=["cpu"],
            job_types=["audiobook.export"], lease_seconds=JOB_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return False
        job = work.jobs.mark_running(
            context, job_id=job["id"], worker_id=worker_id,
            lease_token=job["lease_token"],
        )
        work.commit()
    with lease_heartbeat(database, context, job_id=job["id"], worker_id=worker_id,
                         lease_token=job["lease_token"]):
        return _export_claimed(database, blobs, context, job, worker_id=worker_id)


# How often a running export checks for cancellation and reports progress.
_EXPORT_POLL_SECONDS = 2.0


def _progress_tail(path: Path, size: int = 4096) -> str:
    """The end of FFmpeg's progress file: it only grows, and the latest block is last."""
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - size))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _report_export_progress(
    database: PostgresDatabase, context: TenantContext, *, job_id: str,
    worker_id: str, lease_token: str, percent: int, message: str,
) -> None:
    with unit_of_work(database) as work:
        work.jobs.renew_lease(
            context, job_id=job_id, worker_id=worker_id,
            lease_token=lease_token, lease_seconds=JOB_LEASE_SECONDS,
        )
        work.jobs.update_progress(
            context, job_id=job_id, worker_id=worker_id, lease_token=lease_token,
            progress={"current": max(0, min(100, percent)), "total": 100, "message": message},
        )
        work.commit()


def _export_claimed(
    database: PostgresDatabase, blobs: BlobStore, context: TenantContext,
    job: dict[str, Any], *, worker_id: str,
) -> bool:
    job_id, token = job["id"], job["lease_token"]
    storage_key = None
    persisted = False
    started_at = time.perf_counter()
    try:
        with unit_of_work(database) as work:
            row = work.connection.execute(
                """SELECT manifest, manifest_hash, project_id, source_revision_id
                    FROM omnix_audiobook_export_manifests
                    WHERE workspace_id = %s AND id = %s AND job_id = %s""",
                (context.workspace_id, job["input_payload"]["manifest_id"], job_id),
            ).fetchone()
            if row is None or manifest_hash(row[0]) != row[1]:
                raise ValueError("frozen export manifest is missing or corrupt")
            manifest = row[0]
            assets = []
            for chapter in manifest["chapters"]:
                asset = work.assets.asset_fields(context, chapter["audio_asset_id"], active_only=True)
                if asset is None or asset["checksum_sha256"] != chapter["audio_checksum"]:
                    raise ValueError("frozen chapter asset is unavailable")
                assets.append((str(asset["storage_key"]), str(asset["checksum_sha256"])))
            cover_asset = None
            if manifest.get("cover"):
                cover = manifest["cover"]
                stored_cover = work.assets.asset_fields(context, cover["asset_id"], active_only=True)
                if stored_cover is None or stored_cover["checksum_sha256"] != cover["checksum"]:
                    raise ValueError("frozen cover asset is unavailable")
                cover_asset = (stored_cover["storage_key"], stored_cover["checksum_sha256"])
            work.rollback()
        executable = ffmpeg_binary()
        if ffmpeg_version(executable) != manifest["encoder"]["version"]:
            raise ValueError("FFmpeg version differs from frozen manifest")
        book_seconds = sum(float(chapter.get("duration_seconds") or 0) for chapter in manifest["chapters"])
        report = partial(_report_export_progress, database, context, job_id=job_id,
                         worker_id=worker_id, lease_token=token)
        report(percent=0, message="Preparing chapter audio")
        with tempfile.TemporaryDirectory(prefix="omnix-audiobook-",
                                         dir=blobs.scratch_dir()) as temporary:
            root = Path(temporary)
            input_path = _stage_book_input(blobs, assets, root)
            progress_path = root / "encoder-progress.txt"
            staged_audio_bytes = sum(path.stat().st_size for path in root.glob("chapter-*.wav"))
            metadata_path = root / "chapters.ffmeta"
            output_path = root / f"book.{manifest['format']}"
            metadata_path.write_text(ffmetadata(manifest), encoding="utf-8")
            cover_path = None
            if cover_asset is not None:
                cover_path = str(root / "cover")
                Path(cover_path).write_bytes(blobs.read_bytes(
                    str(cover_asset[0]), expected_checksum=str(cover_asset[1])))
            command = ffmpeg_command(
                executable, input_wav=str(input_path),
                metadata_path=str(metadata_path), output_path=str(output_path),
                format=manifest["format"], cover_path=cover_path,
                concat_input=True, progress_path=str(progress_path),
            )
            report(percent=0, message=f"Encoding {manifest['format'].upper()}")
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 7200
            try:
                while True:
                    try:
                        return_code = process.wait(timeout=_EXPORT_POLL_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("audiobook export encoder timed out")
                        with unit_of_work(database) as work:
                            current = work.jobs.get_job(context, job_id)
                            if current["status"] == "cancel_requested":
                                process.kill()
                                process.wait()
                                work.jobs.acknowledge_cancel(
                                    context, job_id=job_id, worker_id=worker_id,
                                    lease_token=token,
                                )
                                work.commit()
                                return True
                            work.rollback()
                        encoded = encoded_seconds(_progress_tail(progress_path))
                        percent = int(99 * encoded / book_seconds) if encoded and book_seconds > 0 else 0
                        report(percent=percent, message=f"Encoding {manifest['format'].upper()}")
                if return_code != 0:
                    raise RuntimeError(f"FFmpeg encoder exited with code {return_code}")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if output_path.stat().st_size <= 0:
                raise ValueError("encoder produced an empty export")
            report(percent=99, message="Saving export")
            asset_id = f"ab:export-audio:{uuid4().hex}"
            export_id = f"ab:export:{uuid4().hex}"
            storage_key = f"audiobook/export/{uuid4().hex}.{manifest['format']}"
            blob = blobs.put_file(storage_key, output_path)
        with unit_of_work(database) as work:
            current = work.jobs.get_job(context, job_id)
            if current["status"] == "cancel_requested":
                work.jobs.acknowledge_cancel(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                )
                work.commit()
                return True
            current_project = work.connection.execute(
                """SELECT current_source_revision_id,
                          settings->>'current_render_run_id',
                          title, author, language, cover_asset_id
                     FROM omnix_audiobook_projects
                    WHERE workspace_id = %s AND id = %s
                      AND deleted_at IS NULL
                    FOR UPDATE""",
                (context.workspace_id, row[2]),
            ).fetchone()
            manifest_cover_id = (
                str(manifest["cover"]["asset_id"])
                if manifest.get("cover") else ""
            )
            current_cover_id = (
                str(current_project[5])
                if current_project is not None and current_project[5] else ""
            )
            if (
                current_project is None
                or str(current_project[0]) != str(row[3])
                or str(current_project[1] or "") != str(manifest["render_run_id"])
                or str(current_project[2]) != str(manifest["title"])
                or str(current_project[3]) != str(manifest["author"])
                or str(current_project[4]) != str(manifest["language"])
                or current_cover_id != manifest_cover_id
            ):
                raise ValueError(
                    "export manifest no longer matches the current project state"
                )
            work.assets.create(context, {
                "id": asset_id, "module": "audiobook", "asset_type": "export",
                "mime_type": FORMAT_MIME[manifest["format"]],
                "byte_size": blob["byte_size"],
                "checksum_sha256": blob["checksum_sha256"],
                "storage_provider": blob["storage_provider"], "storage_key": storage_key,
                "generation_job_id": job_id,
                "metadata": {"manifest_id": job["input_payload"]["manifest_id"],
                             "manifest_hash": row[1]},
            })
            work.connection.execute(
                """INSERT INTO omnix_audiobook_exports
                    (id, workspace_id, project_id, source_revision_id, job_id,
                     format, manifest, manifest_hash, output_asset_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                (export_id, context.workspace_id, row[2], row[3], job_id,
                 manifest["format"], canonical_json(manifest), row[1], asset_id),
            )
            work.jobs.complete(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                output_refs=[{"export_id": export_id, "asset_id": asset_id}],
                progress={"current": 1, "total": 1, "message": "export encoded",
                          "chapter_count": len(assets),
                          "staged_audio_bytes": staged_audio_bytes,
                          "output_audio_bytes": blob["byte_size"],
                          "wall_seconds": round(time.perf_counter() - started_at, 3)},
            )
            work.connection.execute(
                """UPDATE omnix_audiobook_projects SET state = 'exported',
                          updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND id = %s
                      AND current_source_revision_id = %s
                      AND settings->>'current_render_run_id' = %s""",
                (context.workspace_id, row[2], row[3], manifest["render_run_id"]),
            )
            work.commit()
            persisted = True
    except Exception as exc:
        _LOG.exception("Audiobook export failed for job %s", job_id)
        with unit_of_work(database) as work:
            work.jobs.fail(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                error={"code": "export_failed", "message": str(exc),
                       "retryable": not isinstance(exc, ValueError)},
            )
            work.commit()
    finally:
        if storage_key and not persisted:
            blobs.delete(storage_key)
    return True
