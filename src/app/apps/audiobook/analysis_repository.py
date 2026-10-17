"""Initial deterministic interpretation and review queue persistence."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from app.persistence.tenant import TenantContext

from .annotation import (
    DiscoveredSpeaker, SpanAnnotation, display_speaker_name, narrator_id,
    normalize_speaker_name, proposed_speaker_id,
)
from .dialogue_coverage import audit_dialogue_coverage
from .hashing import canonical_json, text_hash
from .models import SourceSpan
from .speech_exclusions import chapter_removed_sql


def _carried_direction(previous: Any, *, role: str, speaker_id: Any, delivery: str) -> str:
    """A generated delivery direction that survives re-classification.

    It carries over when the line is still dialogue spoken by the same speaker
    and the classifier gives no delivery of its own; a re-assigned line loses it,
    since the direction was written for the other speaker.
    """
    if previous is None or delivery or role != "dialogue":
        return ""
    _id, _revision, _status, previous_role, previous_speaker, previous_delivery, evidence = previous
    if (previous_role != "dialogue" or str(previous_speaker or "") != str(speaker_id or "")
            or dict(evidence or {}).get("delivery_source") != "performance_director"):
        return ""
    return str(previous_delivery or "")


class PostgresAudiobookAnalysisRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def register_proposed_speakers(
        self, context: TenantContext, *, project_id: str,
        discoveries: list[DiscoveredSpeaker] | tuple[DiscoveredSpeaker, ...],
    ) -> int:
        """Persist classifier discoveries without making them castable.

        Existing active speakers keep their identity and castability; classifier
        metadata and proposed aliases may be enriched. Proposed aliases become
        authoritative when the analysis finishes (``confirm_suggested_aliases``),
        and the user can unconfirm any of them.
        """
        touched = 0
        for discovery in discoveries:
            name = display_speaker_name(discovery.canonical_name)
            normalized = normalize_speaker_name(name)
            if not normalized or normalized == "narrator":
                continue
            metadata = {
                key: value
                for key, value in {
                    "role": discovery.role,
                    "traits": list(discovery.traits),
                    "estimated_age": discovery.estimated_age,
                    "gender_presentation": discovery.gender_presentation,
                }.items()
                if value
            }
            existing = self.connection.execute(
                """SELECT id, status,
                          lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g'))
                     FROM omnix_audiobook_speakers
                    WHERE workspace_id = %s AND project_id = %s
                      AND status IN ('active', 'proposed')
                      AND lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g')) = %s
                    ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END
                    LIMIT 1
                    FOR UPDATE""",
                (context.workspace_id, project_id, normalized),
            ).fetchone()
            if existing is None:
                existing = self.connection.execute(
                    """SELECT s.id, s.status,
                              lower(regexp_replace(trim(s.canonical_name), '\\s+', ' ', 'g'))
                         FROM omnix_audiobook_speaker_aliases a
                         JOIN omnix_audiobook_speakers s
                           ON s.workspace_id = a.workspace_id
                          AND s.project_id = a.project_id
                          AND s.id = a.speaker_id
                        WHERE a.workspace_id = %s AND a.project_id = %s
                          AND a.status = 'confirmed'
                          AND s.status IN ('active', 'proposed')
                          AND lower(regexp_replace(trim(a.alias), '\\s+', ' ', 'g')) = %s
                        ORDER BY CASE WHEN s.status = 'active' THEN 0 ELSE 1 END
                        LIMIT 1
                        FOR UPDATE OF s""",
                    (context.workspace_id, project_id, normalized),
                ).fetchone()
            if existing is None:
                # A rejected canonical name is a tombstone, not an identity
                # authority. Resolve any confirmed alias first; only fall back to
                # the rejected row when no live/proposed identity owns this label.
                existing = self.connection.execute(
                    """SELECT id, status,
                              lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g'))
                         FROM omnix_audiobook_speakers
                        WHERE workspace_id = %s AND project_id = %s
                          AND status = 'rejected'
                          AND lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g')) = %s
                        ORDER BY created_at DESC, id
                        LIMIT 1
                        FOR UPDATE""",
                    (context.workspace_id, project_id, normalized),
                ).fetchone()
            speaker_id = (
                str(existing[0]) if existing is not None
                else proposed_speaker_id(project_id, name)
            )
            resolved_canonical = (
                str(existing[2]) if existing is not None else normalized
            )
            if existing is not None:
                # Rediscovery enriches an existing identity but never renames or
                # resurrects it. Rejected identities may retain classifier profile
                # evidence for audit/display, but the rejected status below prevents
                # any new proposed aliases from being attached.
                if metadata:
                    self.connection.execute(
                        """UPDATE omnix_audiobook_speakers
                              SET analysis_metadata = analysis_metadata || %s::jsonb
                            WHERE workspace_id = %s AND project_id = %s
                              AND id = %s::uuid""",
                        (
                            canonical_json(metadata), context.workspace_id,
                            project_id, speaker_id,
                        ),
                    )
            else:
                self.connection.execute(
                    """
                    INSERT INTO omnix_audiobook_speakers
                        (id, workspace_id, project_id, canonical_name, display_name,
                         kind, status, analysis_metadata)
                    VALUES (%s::uuid, %s, %s, %s, %s, 'character', 'proposed',
                            %s::jsonb)
                    ON CONFLICT (id) DO UPDATE
                      SET analysis_metadata =
                              omnix_audiobook_speakers.analysis_metadata
                              || EXCLUDED.analysis_metadata,
                          status = omnix_audiobook_speakers.status
                    """,
                    (
                        speaker_id, context.workspace_id, project_id, name, name,
                        canonical_json(metadata),
                    ),
                )
            touched += 1

            if existing is not None and str(existing[1]) == "rejected":
                continue

            for alias in discovery.aliases:
                alias_name = display_speaker_name(alias)
                alias_normalized = normalize_speaker_name(alias_name)
                if (
                    not alias_normalized
                    or alias_normalized == normalized
                    or alias_normalized == resolved_canonical
                ):
                    continue
                canonical_conflict = self.connection.execute(
                    """SELECT 1
                         FROM omnix_audiobook_speakers
                        WHERE workspace_id = %s AND project_id = %s
                          AND id <> %s::uuid
                          AND status IN ('active', 'proposed')
                          AND lower(regexp_replace(
                              trim(canonical_name), '\\s+', ' ', 'g'
                          )) = %s
                        LIMIT 1""",
                    (
                        context.workspace_id, project_id, speaker_id,
                        alias_normalized,
                    ),
                ).fetchone()
                if canonical_conflict is not None:
                    continue
                alias_conflict = self.connection.execute(
                    """SELECT 1
                         FROM omnix_audiobook_speaker_aliases
                        WHERE workspace_id = %s AND project_id = %s
                          AND status = 'confirmed'
                          AND speaker_id <> %s::uuid
                          AND lower(regexp_replace(trim(alias), '\\s+', ' ', 'g')) = %s
                        LIMIT 1""",
                    (
                        context.workspace_id, project_id, speaker_id,
                        alias_normalized,
                    ),
                ).fetchone()
                if alias_conflict is not None:
                    continue
                # An alias the user unconfirmed (or rejected) for this speaker is not suggested again;
                # suggestions are confirmed automatically, so it would otherwise come straight back.
                declined = self.connection.execute(
                    """SELECT 1
                         FROM omnix_audiobook_speaker_aliases
                        WHERE workspace_id = %s AND project_id = %s
                          AND status = 'rejected' AND speaker_id = %s::uuid
                          AND lower(regexp_replace(trim(alias), '\\s+', ' ', 'g')) = %s
                        LIMIT 1""",
                    (
                        context.workspace_id, project_id, speaker_id,
                        alias_normalized,
                    ),
                ).fetchone()
                if declined is not None:
                    continue
                alias_id = f"ab:alias:{text_hash(f'{speaker_id}:{alias_normalized}')}"
                self.connection.execute(
                    """
                    INSERT INTO omnix_audiobook_speaker_aliases
                        (id, workspace_id, project_id, speaker_id, alias,
                         provenance, status)
                    VALUES (%s, %s, %s, %s::uuid, %s, %s::jsonb, 'proposed')
                    ON CONFLICT (id) DO UPDATE
                      SET alias = EXCLUDED.alias,
                          provenance = EXCLUDED.provenance
                    """,
                    (
                        alias_id, context.workspace_id, project_id, speaker_id,
                        alias_name,
                        canonical_json({"mode": "classifier_discovery"}),
                    ),
                )
        return touched

    def prepare_review(
        self, context: TenantContext, *, project_id: str, source_revision_id: str,
        annotations: dict[str, SpanAnnotation] | None = None,
        classifier: dict[str, Any] | None = None,
        chapter_id: str | None = None,
        finalize: bool = True,
        force_reclassify: bool = False,
        coverage_spans: Sequence[SourceSpan] | None = None,
        dialogue_target_ids: set[str] | None = None,
    ) -> dict[str, int]:
        project = self.connection.execute(
            """
            SELECT current_source_revision_id FROM omnix_audiobook_projects
             WHERE workspace_id = %s AND id = %s FOR UPDATE
            """, (context.workspace_id, project_id),
        ).fetchone()
        if project is None or str(project[0]) != source_revision_id:
            raise ValueError("analysis requires the current canonical source revision")
        narrator = narrator_id(project_id)
        self.connection.execute(
            """
            INSERT INTO omnix_audiobook_speakers
                (id, workspace_id, project_id, canonical_name, display_name, kind)
            VALUES (%s::uuid, %s, %s, 'Narrator', 'Narrator', 'narrator')
            ON CONFLICT (id) DO NOTHING
            """, (narrator, context.workspace_id, project_id),
        )
        spans = self.connection.execute(
            """
            SELECT s.id, s.structural_kind, s.chapter_id, s.ordinal,
                   s.start_offset, s.end_offset, s.source_text, s.source_hash,
                   s.detector_version
              FROM omnix_audiobook_spans AS s
              JOIN omnix_audiobook_chapters AS c
                ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
             WHERE c.workspace_id = %s AND c.source_revision_id = %s
               AND (%s::text IS NULL OR c.id = %s)
             ORDER BY c.ordinal, s.ordinal
            """, (context.workspace_id, source_revision_id, chapter_id, chapter_id),
        ).fetchall()
        raw_coverage_spans = [
            SourceSpan(
                str(row[0]), str(row[2]), int(row[3]), int(row[4]),
                int(row[5]), str(row[6]), str(row[7]), str(row[1]), str(row[8]),
            )
            for row in spans
        ]
        coverage_by_id = {
            span.id: span for span in (coverage_spans or raw_coverage_spans)
        }
        coverage_findings = audit_dialogue_coverage([
            coverage_by_id.get(span.id, span) for span in raw_coverage_spans
        ])
        issues = 0
        for span_id, kind, *_ in spans:
            interpreted = (annotations or {}).get(str(span_id))
            coverage_evidence = coverage_findings.get(str(span_id))
            previous = None
            if force_reclassify:
                previous = self.connection.execute(
                    """SELECT id, revision, review_status, role, speaker_id, delivery, evidence
                         FROM omnix_audiobook_annotations
                        WHERE workspace_id = %s AND span_id = %s
                        ORDER BY revision DESC LIMIT 1""",
                    (context.workspace_id, span_id),
                ).fetchone()
                # An explicit user decision is authoritative across classifier
                # reruns. The latest annotation remains the effective one.
                if previous is not None and str(previous[2]) == "user_resolved":
                    continue
            revision = int(previous[1]) + 1 if previous is not None else 1
            annotation_id = (
                f"ab:an:{uuid4().hex}" if force_reclassify
                else f"ab:an:{text_hash(f'{span_id}:1')}"
            )
            review_reason = interpreted.review_reason if interpreted else None
            if review_reason is None and coverage_evidence:
                review_reason = "POSSIBLE_MISSED_DIALOGUE"
            elif (
                review_reason is None
                and kind == "dialogue"
                and interpreted is None
                and (
                    dialogue_target_ids is None
                    or str(span_id) in dialogue_target_ids
                )
            ):
                review_reason = "FALLBACK_NARRATOR"
            review_required = review_reason is not None
            status = "review_required" if review_required else "confident"
            candidate = (" ".join(interpreted.speaker_candidate.strip().split())
                         if interpreted and interpreted.speaker_candidate else "")
            # Keep unknown classifier names durable and visible to the operator.
            # They remain proposed (and therefore cannot be cast) until the user
            # confirms them through the cast view.
            if (candidate and kind == "dialogue" and interpreted is not None
                    and interpreted.speaker_id == narrator
                    and candidate.casefold() != "narrator"):
                self.connection.execute(
                    """
                    INSERT INTO omnix_audiobook_speakers
                        (id, workspace_id, project_id, canonical_name, display_name,
                         kind, status)
                    VALUES (%s::uuid, %s, %s, %s, %s, 'character', 'proposed')
                    ON CONFLICT (id) DO UPDATE
                      SET status = omnix_audiobook_speakers.status
                    """,
                    (proposed_speaker_id(project_id, candidate), context.workspace_id,
                     project_id, candidate, candidate),
                )
            if force_reclassify and previous is not None:
                self.connection.execute(
                    """UPDATE omnix_audiobook_review_issues
                          SET status = 'superseded',
                              resolution = %s::jsonb,
                              resolved_at = CURRENT_TIMESTAMP
                        WHERE workspace_id = %s AND annotation_id = %s
                          AND status = 'open'""",
                    (canonical_json({"mode": "reclassified", "user_id": context.user_id}),
                     context.workspace_id, str(previous[0])),
                )
            role = interpreted.role if interpreted else kind
            speaker_id = interpreted.speaker_id if interpreted else narrator
            delivery = interpreted.delivery if interpreted else ""
            carried = _carried_direction(previous, role=role, speaker_id=speaker_id, delivery=delivery)
            self.connection.execute(
                """
                INSERT INTO omnix_audiobook_annotations
                    (id, workspace_id, span_id, revision, role, speaker_id,
                     speaker_candidate, delivery, evidence, classifier, review_status)
                VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s, %s::jsonb, %s::jsonb, %s)
                """ + ("" if force_reclassify else "ON CONFLICT (span_id, revision) DO NOTHING"),
                (annotation_id, context.workspace_id, span_id, revision,
                 role, speaker_id,
                 candidate or None,
                 carried or delivery,
                 canonical_json({"detector_role": kind, "source_text_untouched": True,
                                 **(interpreted.evidence if interpreted else {}),
                                 **({"dialogue_coverage": coverage_evidence}
                                    if coverage_evidence else {}),
                                 **({"delivery_source": "performance_director",
                                     "delivery_carried_from_revision": int(previous[1])}
                                    if carried else {})}),
                 canonical_json(classifier or {"mode": "deterministic_fallback", "version": "audiobook-analysis-v1"}),
                 status),
            )
            if review_required:
                issues += 1
                self.connection.execute(
                    """
                    INSERT INTO omnix_audiobook_review_issues
                        (id, workspace_id, annotation_id, reason, evidence)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (f"ab:ri:{uuid4().hex}" if force_reclassify else f"ab:ri:{text_hash(annotation_id)}", context.workspace_id,
                     annotation_id, review_reason,
                     canonical_json({"reason": review_reason,
                                     "speaker_candidate": candidate or None,
                                     **(coverage_evidence or {})})),
                )
        if not finalize:
            return {"spans": len(spans), "review_issues": issues}
        return self.finalize_review(context, project_id=project_id,
                                    source_revision_id=source_revision_id)

    def finalize_review(
        self, context: TenantContext, *, project_id: str, source_revision_id: str,
    ) -> dict[str, int]:
        project = self.connection.execute(
            """SELECT current_source_revision_id FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s FOR UPDATE""",
            (context.workspace_id, project_id),
        ).fetchone()
        if project is None or str(project[0]) != source_revision_id:
            raise ValueError("analysis requires the current canonical source revision")
        coverage = self.connection.execute(
            """SELECT count(s.id), count(a.id)
                 FROM omnix_audiobook_spans s
                 JOIN omnix_audiobook_chapters c
                   ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
                 LEFT JOIN LATERAL (
                   SELECT id
                     FROM omnix_audiobook_annotations
                    WHERE workspace_id = s.workspace_id AND span_id = s.id
                    ORDER BY revision DESC LIMIT 1
                 ) a ON TRUE
                WHERE c.workspace_id = %s AND c.source_revision_id = %s""",
            (context.workspace_id, source_revision_id),
        ).fetchone()
        if int(coverage[0]) != int(coverage[1]):
            raise ValueError("analysis cannot finish before all spans are annotated")
        issue_total = self.connection.execute(
            """SELECT count(i.id)
                 FROM omnix_audiobook_review_issues i
                 JOIN omnix_audiobook_annotations a
                   ON a.workspace_id = i.workspace_id AND a.id = i.annotation_id
                 JOIN omnix_audiobook_spans s
                   ON s.workspace_id = a.workspace_id AND s.id = a.span_id
                 JOIN omnix_audiobook_chapters c
                   ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
                WHERE c.workspace_id = %s AND c.source_revision_id = %s
                  AND i.status = 'open' AND NOT """ + chapter_removed_sql("c", "%s"),
            (context.workspace_id, source_revision_id, project_id),
        ).fetchone()[0]
        self.connection.execute(
            """
            UPDATE omnix_audiobook_projects
               SET state = %s, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s
            """, ("review_required" if issue_total else "ready_to_render", context.workspace_id, project_id),
        )
        return {"spans": int(coverage[0]), "review_issues": int(issue_total)}

    def list_review_issues(self, context: TenantContext, project_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT i.id, i.reason, i.evidence, i.status, a.span_id, a.speaker_id,
                   s.source_text, c.id, c.title, c.ordinal, a.speaker_candidate,
                   s.structural_kind
              FROM omnix_audiobook_review_issues AS i
              JOIN omnix_audiobook_annotations AS a
                ON a.workspace_id = i.workspace_id AND a.id = i.annotation_id
              JOIN omnix_audiobook_spans AS s
                ON s.workspace_id = a.workspace_id AND s.id = a.span_id
              JOIN omnix_audiobook_chapters AS c
                ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
              JOIN omnix_audiobook_source_revisions AS r
                ON r.workspace_id = c.workspace_id AND r.id = c.source_revision_id
              JOIN omnix_audiobook_projects AS p
                ON p.workspace_id = r.workspace_id AND p.id = r.project_id
               AND p.current_source_revision_id = r.id
             WHERE i.workspace_id = %s AND r.project_id = %s AND i.status = 'open'
               AND NOT """ + chapter_removed_sql("c", "r.project_id") + """
             ORDER BY c.ordinal, s.ordinal
            """, (context.workspace_id, project_id),
        ).fetchall()
        return [{"id": str(row[0]), "reason": str(row[1]), "evidence": dict(row[2]),
                 "status": str(row[3]), "span_id": str(row[4]), "speaker_id": str(row[5]) if row[5] else None,
                 "source_text": str(row[6]), "chapter_id": str(row[7]),
                 "chapter_title": str(row[8]), "chapter_ordinal": int(row[9]),
                 "speaker_candidate": str(row[10]) if row[10] else None,
                 "structural_kind": str(row[11])}
                for row in rows]
