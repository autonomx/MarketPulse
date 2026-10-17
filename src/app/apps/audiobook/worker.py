"""Leased audiobook worker entrypoint; production work never runs at job creation."""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from app.persistence.blob_store import default_blob_store
from app.persistence.contracts import BlobStore
from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import current_tenant
from app.persistence.runtime import ensure_postgresql_runtime_ready
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work

from .extraction import UnsupportedSource, extract_source, resegment_revision
from .document_structure import (
    analyze_document_structure, dialogue_targets_for_analysis,
    mask_span_for_analysis,
)
from .document_structure_classifier import local_structure_classifier
from .document_structure_repository import PostgresAudiobookDocumentStructureRepository
from .analysis_repository import PostgresAudiobookAnalysisRepository
from .repository import PostgresAudiobookRepository
from .review_repository import PostgresAudiobookReviewRepository
from .assembly_service import run_assemble_once
from .export_service import run_export_once
from .annotation import (
    Speaker, SpeakerAlias, SpanAnnotation, annotate_span_batches, narrator_id,
    normalize_speaker_name, proposed_speaker_id,
)
from .classifier import local_classifier, with_classification_rules
from .style_discovery import discover_dialogue_styles
from .models import SourceSpan
from .leases import JOB_LEASE_SECONDS, lease_heartbeat


_LOG = logging.getLogger(__name__)

class _AnalysisPaused(Exception):
    """Internal signal used after an analysis lease is safely paused."""


class _AnalysisCanceled(Exception):
    """Internal signal used after an analysis cancellation is acknowledged."""


def _pause_analysis_if_requested(
    work: Any, context: TenantContext, *, job_id: str,
    worker_id: str, lease_token: str,
) -> bool:
    """Release an analysis lease when the UI requested a safe pause."""
    current = work.jobs.get_job(context, job_id)
    if not current or current["status"] == "cancel_requested":
        return False
    if not bool((current.get("metadata") or {}).get("pause_requested")):
        return False
    updated = work.jobs.patch_job(
        context,
        job_id=job_id,
        expected_statuses=("leased", "running"),
        lease_owner=worker_id,
        lease_token=lease_token,
        status="paused",
        metadata_set={"paused": True},
        metadata_remove=("pause_requested",),
        clear_lease=True,
    )
    if updated is None:
        return False
    work.jobs.update_attempt_status(
        context,
        job_id=job_id,
        lease_token=lease_token,
        expected_statuses=("leased", "running"),
        status="paused",
    )
    return True


def _reuse_existing_dialogue_segmentation(
    database: PostgresDatabase, context: TenantContext, revision: Any,
) -> Any | None:
    """Reuse a prior verified span map for identical canonical source content."""
    with unit_of_work(database) as work:
        row = work.connection.execute(
            """
            SELECT id, metadata
              FROM omnix_audiobook_source_revisions
             WHERE workspace_id = %s AND project_id = %s
               AND original_asset_hash = %s
               AND source_format = %s
               AND extractor_version = %s
               AND extraction_settings_hash = %s
               AND canonical_hash = %s
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (
                context.workspace_id,
                revision.project_id,
                revision.original_asset_hash,
                revision.source_format,
                revision.extractor_version,
                revision.extraction_settings_hash,
                revision.canonical_hash,
            ),
        ).fetchone()
        work.rollback()
    if row is None:
        return None

    existing_id = str(row[0])
    metadata = dict(row[1] or {})
    discovery = metadata.get("dialogue_style_discovery")
    if not isinstance(discovery, dict):
        # A prior base-detector revision may predate the current detector
        # contract. Treat it as a cache miss and rebuild with the current
        # detector instead of failing an otherwise valid identical-source ingest.
        return None
    styles = discovery.get("styles")
    if (
        not isinstance(styles, list)
        or not all(isinstance(item, str) and item for item in styles)
    ):
        return None
    reused = resegment_revision(
        revision,
        styles=tuple(styles),
        discovery=discovery,
    )
    if reused.id != existing_id:
        # Detector-version upgrades intentionally change revision identity even
        # when canonical source bytes and verified style names are unchanged.
        return None
    return reused


def run_ingest_once(
    database: PostgresDatabase, blobs: BlobStore, context: TenantContext,
    *, worker_id: str,
) -> bool:
    with unit_of_work(database) as work:
        job = work.jobs.claim_next(
            context, worker_id=worker_id, resource_classes=["cpu"],
            job_types=["audiobook.ingest"], lease_seconds=JOB_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return False
        job = work.jobs.mark_running(
            context, job_id=job["id"], worker_id=worker_id, lease_token=job["lease_token"],
        )
        work.commit()
    with lease_heartbeat(database, context, job_id=job["id"], worker_id=worker_id,
                         lease_token=job["lease_token"]):
        return _ingest_claimed(database, blobs, context, job, worker_id=worker_id)


def _ingest_claimed(
    database: PostgresDatabase, blobs: BlobStore, context: TenantContext,
    job: dict[str, Any], *, worker_id: str,
) -> bool:
    job_id = job["id"]
    token = job["lease_token"]
    payload = job["input_payload"]
    try:
        with unit_of_work(database) as work:
            if not PostgresAudiobookRepository(work.connection).is_current_ingest(
                context, project_id=payload["project_id"], job_id=job_id,
            ):
                work.jobs.request_cancel(context, job_id)
                work.jobs.acknowledge_cancel(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                )
                work.commit()
                return True
            work.rollback()
        with unit_of_work(database) as work:
            row = work.assets.asset_fields(context, payload["source_asset_id"], active_only=True)
            work.rollback()
        if row is None:
            raise UnsupportedSource("source asset is missing")
        content = blobs.read_bytes(str(row["storage_key"]), expected_checksum=str(row["checksum_sha256"]))
        revision = extract_source(
            project_id=payload["project_id"], content=content,
            source_format=payload["source_format"],
            settings=payload.get("extraction_settings"),
        )
        with unit_of_work(database) as progress_work:
            progress_work.jobs.update_progress(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                progress={"current": 1, "total": 3,
                          "message": "checking dialogue style"},
            )
            progress_work.commit()
        # A new rules-driven pass must start from base detection, including when
        # rules were cleared. Previously learned styles are not fresh evidence.
        reused_revision = None
        if not payload.get("force_reclassify") and not payload.get("custom_rules"):
            reused_revision = _reuse_existing_dialogue_segmentation(
                database, context, revision,
            )
        if reused_revision is not None:
            revision = reused_revision
        if bool(payload.get("force_reclassify")) or reused_revision is None:
            style_classifier = local_classifier()
            if style_classifier is not None:
                try:
                    preliminary_structure = analyze_document_structure(
                        revision, region_classifier=None,
                    )
                    style_probe_spans = [
                        mask_span_for_analysis(
                            span, preliminary_structure.blocks,
                            consumer="dialogue_coverage",
                        )
                        for chapter in revision.chapters
                        for span in chapter.spans
                    ]
                    revision = discover_dialogue_styles(
                        revision, classifier=with_classification_rules(
                            style_classifier[0], payload.get("custom_rules", ""),
                        ),
                        classifier_details=style_classifier[1],
                        probe_spans=style_probe_spans,
                        custom_rules=payload.get("custom_rules", ""),
                    )
                except Exception:
                    # The independent coverage audit will put unresolved speech cues
                    # into review. A model outage must not discard valid source text
                    # or a previously verified segmentation.
                    _LOG.exception(
                        "Audiobook dialogue style discovery failed for job %s", job_id
                    )
        with unit_of_work(database) as progress_work:
            progress_work.jobs.update_progress(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                progress={"current": 2, "total": 3,
                          "message": "preparing source structure"},
            )
            progress_work.commit()
        structure_classifier = local_structure_classifier()
        structure_analysis = analyze_document_structure(
            revision,
            region_classifier=(
                structure_classifier[0] if structure_classifier is not None else None
            ),
        )
        with unit_of_work(database) as work:
            work.jobs.renew_lease(
                context, job_id=job_id, worker_id=worker_id,
                lease_token=token, lease_seconds=JOB_LEASE_SECONDS,
            )
            current = work.jobs.get_job(context, job_id)
            if current["status"] == "cancel_requested" or not PostgresAudiobookRepository(
                work.connection
            ).is_current_ingest(context, project_id=payload["project_id"], job_id=job_id):
                work.jobs.request_cancel(context, job_id)
                work.jobs.acknowledge_cancel(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                )
                work.commit()
                return True
            PostgresAudiobookRepository(work.connection).append_source_revision(
                context, revision, original_asset_id=payload["source_asset_id"],
            )
            structure_run_id = PostgresAudiobookDocumentStructureRepository(
                work.connection
            ).append_analysis(
                context, structure_analysis,
                classifier=(structure_classifier[1] if structure_classifier else None),
            )
            # Extraction publishes reviewable quote spans. Speaker classification
            # is a separate user-requested phase and must never be queued here.
            work.connection.execute(
                """UPDATE omnix_audiobook_projects
                      SET settings = jsonb_set(settings, '{quote_extraction_rules}', to_jsonb(%s::text), true)
                    WHERE workspace_id = %s AND id = %s""",
                (payload.get("custom_rules", ""), context.workspace_id, payload["project_id"]),
            )
            work.jobs.complete(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                output_refs=[{
                    "source_revision_id": revision.id,
                    "document_structure_run_id": structure_run_id,
                }],
                progress={"current": 3, "total": 3, "message": "Quotes extracted. Review quote spans before classifying text."},
            )
            work.commit()
    except Exception as exc:
        _LOG.exception("Audiobook source ingestion failed for job %s", job_id)
        with unit_of_work(database) as work:
            work.jobs.fail(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                error={"code": "source_invalid" if isinstance(exc, UnsupportedSource) else "ingest_failed",
                       "message": str(exc), "retryable": not isinstance(exc, UnsupportedSource)},
            )
            work.commit()
    return True


def run_analyze_once(
    database: PostgresDatabase, context: TenantContext, *, worker_id: str,
) -> bool:
    with unit_of_work(database) as work:
        job = work.jobs.claim_next(
            context, worker_id=worker_id, resource_classes=["cpu"],
            job_types=["audiobook.analyze"], lease_seconds=JOB_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return False
        job = work.jobs.mark_running(
            context, job_id=job["id"], worker_id=worker_id, lease_token=job["lease_token"],
        )
        work.commit()
    with lease_heartbeat(database, context, job_id=job["id"], worker_id=worker_id,
                         lease_token=job["lease_token"]):
        return _analyze_claimed(database, context, job, worker_id=worker_id)


def _analyze_claimed(
    database: PostgresDatabase, context: TenantContext,
    job: dict[str, Any], *, worker_id: str,
) -> bool:
    job_id, token = job["id"], job["lease_token"]
    payload = job["input_payload"]
    force_reclassify = bool(payload.get("force_reclassify"))
    try:
        with unit_of_work(database) as work:
            chapters = work.connection.execute(
                """SELECT c.id, count(s.id)
                     FROM omnix_audiobook_chapters c
                     LEFT JOIN omnix_audiobook_spans s
                       ON s.workspace_id = c.workspace_id AND s.chapter_id = c.id
                    WHERE c.workspace_id = %s AND c.source_revision_id = %s
                    GROUP BY c.id, c.ordinal ORDER BY c.ordinal""",
                (context.workspace_id, payload["source_revision_id"]),
            ).fetchall()
            speaker_rows = work.connection.execute(
                """SELECT id, canonical_name, kind, status
                     FROM omnix_audiobook_speakers
                    WHERE workspace_id = %s AND project_id = %s
                      AND status IN ('active', 'proposed')""",
                (context.workspace_id, payload["project_id"]),
            ).fetchall()
            alias_rows = work.connection.execute(
                """SELECT alias, speaker_id, status
                     FROM omnix_audiobook_speaker_aliases
                    WHERE workspace_id = %s AND project_id = %s
                      AND status IN ('confirmed', 'proposed')""",
                (context.workspace_id, payload["project_id"]),
            ).fetchall()
            structure_overrides = PostgresAudiobookDocumentStructureRepository(
                work.connection
            ).list_overrides(
                context,
                project_id=payload["project_id"],
                source_revision_id=payload["source_revision_id"],
            )
            work.rollback()
        speakers = [
            Speaker(str(row[0]), str(row[1]), str(row[2]), str(row[3]))
            for row in speaker_rows
        ]
        if not any(item.id == narrator_id(payload["project_id"]) for item in speakers):
            speakers.append(Speaker(narrator_id(payload["project_id"]), "Narrator", "narrator"))
        aliases = [SpeakerAlias(str(row[0]), str(row[1]), str(row[2])) for row in alias_rows]
        classifier = local_classifier()
        if classifier is not None and payload.get("custom_rules"):
            classifier[1]["custom_rules"] = payload["custom_rules"]
            classifier = (
                with_classification_rules(classifier[0], payload["custom_rules"]),
                classifier[1],
            )
        classifier_details = classifier[1] if classifier is not None else None
        if classifier_details is not None:
            # Keep the provider-owned dict live so its resolved model identity can
            # still update after the first response.
            classifier_details["analysis_job_id"] = job_id
        if force_reclassify and classifier is None:
            raise ValueError(
                "text reclassification requires a configured chat provider; "
                "no classifier was available, so the existing review state was preserved"
            )
        total_spans = sum(int(row[1]) for row in chapters)
        completed_spans = 0
        chapter_classified_spans: set[str] = set()

        with unit_of_work(database) as progress_work:
            progress_work.jobs.update_progress(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                progress={
                    "current": 0,
                    "total": total_spans,
                    "message": "preparing classification",
                },
            )
            progress_work.commit()

        def checkpoint_classification_progress(
            span_ids: object, *, message: str = "analyzing chapters",
        ) -> None:
            """Persist span-level progress while a chapter is being classified."""
            if isinstance(span_ids, str):
                candidate_ids = [span_ids]
            elif isinstance(span_ids, (list, tuple)):
                candidate_ids = [
                    item for item in span_ids if isinstance(item, str) and item
                ]
            else:
                candidate_ids = []
            new_ids = [
                item for item in candidate_ids
                if item not in chapter_classified_spans
            ]
            if not new_ids:
                return
            chapter_classified_spans.update(new_ids)
            with unit_of_work(database) as progress_work:
                current = progress_work.jobs.get_job(context, job_id)
                if current["status"] == "cancel_requested":
                    progress_work.jobs.acknowledge_cancel(
                        context, job_id=job_id, worker_id=worker_id, lease_token=token,
                    )
                    progress_work.commit()
                    raise _AnalysisCanceled
                if _pause_analysis_if_requested(
                    progress_work, context, job_id=job_id,
                    worker_id=worker_id, lease_token=token,
                ):
                    progress_work.commit()
                    raise _AnalysisPaused
                progress_work.jobs.update_progress(
                    context, job_id=job_id, worker_id=worker_id,
                    lease_token=token,
                    progress={
                        "current": completed_spans + len(chapter_classified_spans),
                        "total": total_spans,
                        "message": message,
                    },
                )
                progress_work.commit()

        def classify(context_payload: dict[str, object]) -> str | dict[str, Any]:
            with unit_of_work(database) as renewal:
                current = renewal.jobs.get_job(context, job_id)
                if current["status"] == "cancel_requested":
                    renewal.jobs.acknowledge_cancel(
                        context, job_id=job_id, worker_id=worker_id,
                        lease_token=token,
                    )
                    renewal.commit()
                    raise _AnalysisCanceled
                if _pause_analysis_if_requested(
                    renewal, context, job_id=job_id,
                    worker_id=worker_id, lease_token=token,
                ):
                    renewal.commit()
                    raise _AnalysisPaused
                renewal.jobs.renew_lease(
                    context, job_id=job_id, worker_id=worker_id,
                    lease_token=token, lease_seconds=JOB_LEASE_SECONDS,
                )
                window_number = context_payload.get("window_number")
                window_count = context_payload.get("window_count")
                message = (
                    f"classifying batch {window_number} of {window_count}"
                    if window_number and window_count
                    else "classifying dialogue"
                )
                renewal.jobs.update_progress(
                    context, job_id=job_id, worker_id=worker_id,
                    lease_token=token,
                    progress={
                        "current": completed_spans + len(chapter_classified_spans),
                        "total": total_spans,
                        "message": message,
                    },
                )
                renewal.commit()
            result = classifier[0](context_payload)
            task = str(context_payload.get("task") or "")
            if (
                "span_id" in context_payload
                or task in {
                    "verify_story_dialogue_full_context",
                    "retry_verify_story_dialogue_full_context",
                    "verify_story_dialogue_full_context_escalated",
                    "repair_story_dialogue_missing_spans",
                    "retry_repair_story_dialogue_missing_spans",
                    "repair_verification_missing_spans",
                    "retry_repair_verification_missing_spans",
                    "repair_escalated_verification_missing_spans",
                }
            ):
                checkpoint_classification_progress(
                    context_payload.get("span_ids")
                    or context_payload.get("span_id")
                )
            return result

        for chapter_id, span_count in chapters:
            chapter_classified_spans.clear()
            with unit_of_work(database) as work:
                current = work.jobs.get_job(context, job_id)
                if current["status"] == "cancel_requested":
                    work.jobs.acknowledge_cancel(
                        context, job_id=job_id, worker_id=worker_id, lease_token=token,
                    )
                    work.commit()
                    return True
                if _pause_analysis_if_requested(
                    work, context, job_id=job_id,
                    worker_id=worker_id, lease_token=token,
                ):
                    work.commit()
                    return True
                existing = 0
                if not force_reclassify:
                    existing = work.connection.execute(
                        """SELECT count(a.id)
                             FROM omnix_audiobook_spans s
                             JOIN omnix_audiobook_annotations a
                               ON a.workspace_id = s.workspace_id AND a.span_id = s.id
                              AND a.revision = 1
                            WHERE s.workspace_id = %s AND s.chapter_id = %s""",
                        (context.workspace_id, chapter_id),
                    ).fetchone()[0]
                else:
                    # A retry of the same forced reclassification must not spend
                    # another model call on chapters this exact analysis job has
                    # already completed. User-resolved spans remain authoritative.
                    existing = work.connection.execute(
                        """SELECT count(s.id)
                             FROM omnix_audiobook_spans s
                             JOIN LATERAL (
                               SELECT a.evidence, a.review_status
                                 FROM omnix_audiobook_annotations a
                                WHERE a.workspace_id = s.workspace_id
                                  AND a.span_id = s.id
                                ORDER BY a.revision DESC
                                LIMIT 1
                             ) a ON TRUE
                            WHERE s.workspace_id = %s AND s.chapter_id = %s
                              AND (
                                a.review_status = 'user_resolved'
                                OR a.evidence->>'classifier_analysis_job_id' = %s
                              )""",
                        (context.workspace_id, chapter_id, job_id),
                    ).fetchone()[0]
                if int(existing) == int(span_count):
                    completed_spans += int(span_count)
                    work.jobs.renew_lease(
                        context, job_id=job_id, worker_id=worker_id,
                        lease_token=token, lease_seconds=JOB_LEASE_SECONDS,
                    )
                    work.jobs.update_progress(
                        context, job_id=job_id, worker_id=worker_id, lease_token=token,
                        progress={"current": completed_spans, "total": total_spans,
                                  "message": "analyzing chapters"},
                    )
                    work.commit()
                    continue
                rows = work.connection.execute(
                    """SELECT id, chapter_id, ordinal, start_offset, end_offset,
                              source_text, source_hash, structural_kind, detector_version
                         FROM omnix_audiobook_spans
                        WHERE workspace_id = %s AND chapter_id = %s
                        ORDER BY ordinal""",
                    (context.workspace_id, chapter_id),
                ).fetchall()
                structure_blocks = PostgresAudiobookDocumentStructureRepository(
                    work.connection
                ).list_blocks(
                    context,
                    source_revision_id=payload["source_revision_id"],
                    chapter_id=str(chapter_id),
                )
                work.rollback()
            chapter_spans = [
                SourceSpan(str(row[0]), str(row[1]), int(row[2]), int(row[3]),
                           int(row[4]), str(row[5]), str(row[6]), str(row[7]), str(row[8]))
                for row in rows
            ]
            analysis_spans = [
                mask_span_for_analysis(
                    span, structure_blocks,
                    consumer="speaker_attribution",
                    overrides=structure_overrides,
                )
                for span in chapter_spans
            ] if structure_blocks else chapter_spans
            coverage_spans = [
                mask_span_for_analysis(
                    span, structure_blocks,
                    consumer="dialogue_coverage",
                    overrides=structure_overrides,
                )
                for span in chapter_spans
            ] if structure_blocks else chapter_spans
            dialogue_target_ids = (
                dialogue_targets_for_analysis(
                    chapter_spans, structure_blocks,
                    consumer="speaker_attribution",
                    overrides=structure_overrides,
                )
                if structure_blocks else None
            )
            annotations: dict[str, SpanAnnotation] = {}
            discoveries = ()
            if classifier is not None:
                batch_analysis = annotate_span_batches(
                    project_id=payload["project_id"], spans=analysis_spans,
                    speakers=speakers, aliases=aliases, classifier=classify,
                    classifier_details=classifier_details,
                    dialogue_target_ids=dialogue_target_ids,
                    on_batch_complete=lambda span_ids, batch_number, batch_count: (
                        checkpoint_classification_progress(
                            span_ids,
                            message=(
                                f"classified batch {batch_number} of {batch_count}"
                            ),
                        )
                    ),
                )
                failed_dialogue = [
                    annotation
                    for annotation in batch_analysis.annotations
                    if (
                        annotation.role == "dialogue"
                        and annotation.review_reason in {
                            "FALLBACK_NARRATOR",
                            "AI_VERIFICATION_UNAVAILABLE",
                        }
                    )
                ]
                if force_reclassify and failed_dialogue:
                    raise RuntimeError(
                        "text reclassification could not classify "
                        f"{len(failed_dialogue)} dialogue spans in chapter {chapter_id}; "
                        "existing annotations for this chapter were preserved"
                    )
                discoveries = batch_analysis.discovered_speakers
                for annotation in batch_analysis.annotations:
                    annotations[annotation.span_id] = annotation

            with unit_of_work(database) as work:
                current = work.jobs.get_job(context, job_id)
                if current["status"] == "cancel_requested":
                    work.jobs.acknowledge_cancel(
                        context, job_id=job_id, worker_id=worker_id, lease_token=token,
                    )
                    work.commit()
                    return True
                if _pause_analysis_if_requested(
                    work, context, job_id=job_id,
                    worker_id=worker_id, lease_token=token,
                ):
                    work.commit()
                    return True
                analysis_repository = PostgresAudiobookAnalysisRepository(work.connection)
                if discoveries:
                    analysis_repository.register_proposed_speakers(
                        context, project_id=payload["project_id"],
                        discoveries=discoveries,
                    )
                analysis_repository.prepare_review(
                    context, project_id=payload["project_id"],
                    source_revision_id=payload["source_revision_id"],
                    annotations=annotations,
                    classifier=classifier[1] if classifier else None,
                    chapter_id=str(chapter_id), finalize=False,
                    force_reclassify=force_reclassify,
                    coverage_spans=coverage_spans,
                    dialogue_target_ids=dialogue_target_ids,
                )
                refreshed_speaker_rows = work.connection.execute(
                    """SELECT id, canonical_name, kind, status
                         FROM omnix_audiobook_speakers
                        WHERE workspace_id = %s AND project_id = %s
                          AND status IN ('active', 'proposed')""",
                    (context.workspace_id, payload["project_id"]),
                ).fetchall()
                refreshed_alias_rows = work.connection.execute(
                    """SELECT alias, speaker_id, status
                         FROM omnix_audiobook_speaker_aliases
                        WHERE workspace_id = %s AND project_id = %s
                          AND status IN ('confirmed', 'proposed')""",
                    (context.workspace_id, payload["project_id"]),
                ).fetchall()
                completed_spans += int(span_count)
                work.jobs.renew_lease(
                    context, job_id=job_id, worker_id=worker_id,
                    lease_token=token, lease_seconds=JOB_LEASE_SECONDS,
                )
                work.jobs.update_progress(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                    progress={"current": completed_spans, "total": total_spans,
                              "message": "analyzing chapters"},
                )
                work.commit()
            # Persistence is the identity authority. Refresh after every chapter
            # so confirmed-alias deduplication and fallback candidate proposals
            # are visible to the next chapter's semantic analysis.
            speakers = [
                Speaker(str(row[0]), str(row[1]), str(row[2]), str(row[3]))
                for row in refreshed_speaker_rows
            ]
            if not any(
                item.id == narrator_id(payload["project_id"]) for item in speakers
            ):
                speakers.append(
                    Speaker(
                        narrator_id(payload["project_id"]),
                        "Narrator", "narrator",
                    )
                )
            aliases = [
                SpeakerAlias(str(row[0]), str(row[1]), str(row[2]))
                for row in refreshed_alias_rows
            ]
        with unit_of_work(database) as work:
            current = work.jobs.get_job(context, job_id)
            if current["status"] == "cancel_requested":
                work.jobs.acknowledge_cancel(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                )
            elif _pause_analysis_if_requested(
                work, context, job_id=job_id,
                worker_id=worker_id, lease_token=token,
            ):
                work.commit()
                return True
            else:
                # Suggested aliases are confirmed automatically (rarely wrong); the user unconfirms any that are.
                PostgresAudiobookReviewRepository(work.connection).confirm_suggested_aliases(
                    context, project_id=payload["project_id"],
                )
                result = PostgresAudiobookAnalysisRepository(work.connection).finalize_review(
                    context, project_id=payload["project_id"],
                    source_revision_id=payload["source_revision_id"],
                )
                work.jobs.complete(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                    output_refs=[result],
                    progress={"current": result["spans"], "total": result["spans"],
                              "message": "review queue prepared"},
                )
                if classifier is not None:
                    # The chat model that just classified the book also directs its dialogue.
                    from .performance_direction import queue_after_classification

                    queue_after_classification(
                        work, context, project_id=payload["project_id"],
                        source_revision_id=payload["source_revision_id"],
                    )
            work.commit()
    except (_AnalysisPaused, _AnalysisCanceled):
        _LOG.info("Audiobook analysis job %s stopped by operator control", job_id)
        return True
    except Exception as exc:
        _LOG.exception("Audiobook analysis failed for job %s", job_id)
        with unit_of_work(database) as work:
            if force_reclassify:
                work.connection.execute(
                    """UPDATE omnix_audiobook_projects
                          SET state = 'review_required', updated_at = CURRENT_TIMESTAMP
                        WHERE workspace_id = %s AND id = %s
                          AND state = 'analyzing'""",
                    (context.workspace_id, payload["project_id"]),
                )
            work.jobs.fail(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                error={"code": "analysis_failed", "message": str(exc),
                       "retryable": not isinstance(exc, ValueError)},
            )
            work.commit()
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database = default_database()
    ensure_postgresql_runtime_ready(database)
    context = current_tenant()
    blobs = default_blob_store()
    worker_id = f"audiobook:ingest:{uuid4().hex}"
    while True:
        try:
            if (not run_ingest_once(database, blobs, context, worker_id=worker_id)
                    and not run_analyze_once(database, context, worker_id=worker_id)
                    and not run_assemble_once(database, blobs, context, worker_id=worker_id)
                    and not run_export_once(database, blobs, context, worker_id=worker_id)):
                time.sleep(1.0)
        except Exception:
            _LOG.exception("Audiobook ingest worker error")
            time.sleep(5.0)


if __name__ == "__main__":
    main()
