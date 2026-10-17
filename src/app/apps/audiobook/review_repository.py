"""Explicit speaker, casting, and review decisions by immutable IDs."""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.persistence.tenant import TenantContext

from .annotation import display_speaker_name, normalize_speaker_name
from .hashing import canonical_json
from .speech_exclusions import chapter_removed_sql


class PostgresAudiobookReviewRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def _cancel_render_jobs(self, context: TenantContext, render_run_id: str) -> None:
        from app.persistence.job_repository import PostgresJobRepository

        jobs = PostgresJobRepository(self.connection).query_jobs(
            context,
            module="audiobook",
            job_types=("audiobook.render-chapter", "audiobook.assemble-chapter"),
            input_fields=(("render_run_id", render_run_id),),
            statuses=("queued", "waiting", "retrying", "leased", "running", "paused", "cancel_requested"),
            limit=500,
            for_update=True,
        )
        repository = PostgresJobRepository(self.connection)
        for job in jobs:
            repository.request_cancel(context, str(job["id"]))

    def _promote_proposed_speaker(
        self, context: TenantContext, *, project_id: str, speaker_id: str,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT id, canonical_name, status
              FROM omnix_audiobook_speakers
             WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid
             FOR UPDATE
            """, (context.workspace_id, project_id, speaker_id),
        ).fetchone()
        if row is None:
            raise KeyError(speaker_id)
        if str(row[2]) == "active":
            return {"id": str(row[0]), "canonical_name": str(row[1]), "status": "active",
                    "promoted": False, "reconciled_spans": 0}
        if str(row[2]) != "proposed":
            raise ValueError("speaker is not a proposed candidate")
        self.connection.execute(
            """UPDATE omnix_audiobook_speakers SET status = 'active'
                WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid""",
            (context.workspace_id, project_id, speaker_id),
        )
        candidates = self.connection.execute(
            """
            SELECT a.id, a.span_id, a.revision, a.role, a.delivery,
                   a.evidence, a.classifier, a.speaker_candidate
              FROM omnix_audiobook_spans AS s
              JOIN omnix_audiobook_chapters AS ch
                ON ch.workspace_id = s.workspace_id AND ch.id = s.chapter_id
              JOIN omnix_audiobook_projects AS p
                ON p.workspace_id = ch.workspace_id
               AND p.current_source_revision_id = ch.source_revision_id
              JOIN LATERAL (
                  SELECT id, span_id, revision, role, delivery, evidence,
                         classifier, speaker_candidate, speaker_id, review_status
                    FROM omnix_audiobook_annotations
                   WHERE workspace_id = s.workspace_id AND span_id = s.id
                   ORDER BY revision DESC LIMIT 1
              ) AS a ON TRUE
             WHERE s.workspace_id = %s AND p.id = %s
               AND lower(regexp_replace(trim(COALESCE(a.speaker_candidate, '')), '\\s+', ' ', 'g')) = %s
               AND a.review_status <> 'user_resolved'
               AND a.speaker_id IS DISTINCT FROM %s::uuid
             ORDER BY ch.ordinal, s.ordinal
            """, (
                context.workspace_id, project_id,
                normalize_speaker_name(str(row[1])), speaker_id,
            ),
        ).fetchall()
        reconciled = 0
        for previous_id, span_id, revision, role, delivery, evidence, classifier, candidate in candidates:
            annotation_id = f"ab:an:{uuid4().hex}"
            merged_evidence = dict(evidence or {})
            merged_evidence.update({
                "previous_annotation_id": str(previous_id),
                "confirmed_candidate": str(candidate or row[1]),
                "resolved_speaker_id": speaker_id,
            })
            self.connection.execute(
                """
                INSERT INTO omnix_audiobook_annotations
                    (id, workspace_id, span_id, revision, role, speaker_id,
                     speaker_candidate, delivery, evidence, classifier, review_status)
                VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s,
                        %s::jsonb, %s::jsonb, 'user_resolved')
                """, (annotation_id, context.workspace_id, span_id, int(revision) + 1,
                       role, speaker_id, candidate, delivery,
                       canonical_json(merged_evidence), canonical_json(classifier or {})),
            )
            self.connection.execute(
                """UPDATE omnix_audiobook_review_issues
                      SET status = 'resolved', resolution = %s::jsonb,
                          resolved_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND annotation_id = %s AND status = 'open'""",
                (canonical_json({"mode": "confirmed_candidate", "speaker_id": speaker_id,
                                 "user_id": context.user_id}), context.workspace_id, previous_id),
            )
            reconciled += 1

        if reconciled:
            active = self.connection.execute(
                """SELECT settings->>'current_render_run_id'
                     FROM omnix_audiobook_projects
                    WHERE workspace_id = %s AND id = %s FOR UPDATE""",
                (context.workspace_id, project_id),
            ).fetchone()
            if active and active[0]:
                self._cancel_render_jobs(context, str(active[0]))

        remaining = int(self.connection.execute(
            """
            SELECT count(*)
              FROM omnix_audiobook_review_issues AS i
              JOIN omnix_audiobook_annotations AS a
                ON a.workspace_id = i.workspace_id AND a.id = i.annotation_id
              JOIN omnix_audiobook_spans AS s
                ON s.workspace_id = a.workspace_id AND s.id = a.span_id
              JOIN omnix_audiobook_chapters AS ch
                ON ch.workspace_id = s.workspace_id AND ch.id = s.chapter_id
              JOIN omnix_audiobook_projects AS p
                ON p.workspace_id = ch.workspace_id
               AND p.current_source_revision_id = ch.source_revision_id
             WHERE i.workspace_id = %s AND p.id = %s AND i.status = 'open'
               AND NOT """ + chapter_removed_sql("ch", "p.id"), (context.workspace_id, project_id),
        ).fetchone()[0])
        if reconciled:
            self.connection.execute(
                """UPDATE omnix_audiobook_projects
                      SET state = %s,
                          settings = settings - 'current_render_run_id',
                          settings_revision = settings_revision + 1,
                          updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND id = %s""",
                ("review_required" if remaining else "ready_to_render",
                 context.workspace_id, project_id),
            )
        else:
            self.connection.execute(
                """UPDATE omnix_audiobook_projects
                      SET state = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND id = %s""",
                ("review_required" if remaining else "ready_to_render",
                 context.workspace_id, project_id),
            )
        return {"id": str(row[0]), "canonical_name": str(row[1]), "status": "active",
                "promoted": True, "reconciled_spans": reconciled}

    def add_speaker(
        self, context: TenantContext, *, project_id: str, canonical_name: str,
    ) -> dict[str, Any]:
        name = display_speaker_name(canonical_name)
        normalized_name = normalize_speaker_name(name)
        if not name:
            raise ValueError("speaker name is required")
        project = self.connection.execute(
            "SELECT id FROM omnix_audiobook_projects WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, project_id),
        ).fetchone()
        if project is None:
            raise KeyError(project_id)
        existing = self.connection.execute(
            """SELECT id, canonical_name, status
                 FROM omnix_audiobook_speakers
                WHERE workspace_id = %s AND project_id = %s
                  AND lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g')) = %s
                ORDER BY CASE WHEN status = 'active' THEN 0
                                  WHEN status = 'proposed' THEN 1 ELSE 2 END
                LIMIT 1 FOR UPDATE""",
            (context.workspace_id, project_id, normalized_name),
        ).fetchone()
        alias_conflict = self.connection.execute(
            """SELECT a.speaker_id
                 FROM omnix_audiobook_speaker_aliases AS a
                 JOIN omnix_audiobook_speakers AS s
                   ON s.workspace_id = a.workspace_id
                  AND s.project_id = a.project_id
                  AND s.id = a.speaker_id
                WHERE a.workspace_id = %s AND a.project_id = %s
                  AND a.status = 'confirmed'
                  AND s.status IN ('active', 'proposed')
                  AND lower(regexp_replace(trim(a.alias), '\\s+', ' ', 'g')) = %s
                LIMIT 1""",
            (context.workspace_id, project_id, normalized_name),
        ).fetchone()
        if (
            alias_conflict is not None
            and (existing is None or str(alias_conflict[0]) != str(existing[0]))
        ):
            raise ValueError(
                "speaker name is already a confirmed alias of another speaker"
            )
        if existing is not None:
            status = str(existing[2])
            if status == "active":
                return {"id": str(existing[0]), "canonical_name": str(existing[1]),
                        "status": "active", "promoted": False,
                        "reconciled_spans": 0}
            if status == "rejected":
                # Explicit user creation is allowed to reverse a classifier/user
                # rejection when the name is not owned as a confirmed alias.
                self.connection.execute(
                    """UPDATE omnix_audiobook_speakers SET status = 'proposed'
                        WHERE workspace_id = %s AND project_id = %s
                          AND id = %s::uuid""",
                    (context.workspace_id, project_id, str(existing[0])),
                )
            return self._promote_proposed_speaker(
                context, project_id=project_id, speaker_id=str(existing[0]),
            )
        speaker_id = str(uuid4())
        row = self.connection.execute(
            """
            INSERT INTO omnix_audiobook_speakers
                (id, workspace_id, project_id, canonical_name, display_name, kind)
            VALUES (%s::uuid, %s, %s, %s, %s, 'character')
            RETURNING id, canonical_name
            """, (speaker_id, context.workspace_id, project_id, name, name),
        ).fetchone()
        return {"id": str(row[0]), "canonical_name": str(row[1]),
                "status": "active", "promoted": False, "reconciled_spans": 0}

    def reject_proposed_speaker(
        self, context: TenantContext, *, project_id: str, speaker_id: str,
    ) -> dict[str, object]:
        UUID(speaker_id)
        speaker = self.connection.execute(
            """SELECT canonical_name, status, kind
                 FROM omnix_audiobook_speakers
                WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid
                FOR UPDATE""",
            (context.workspace_id, project_id, speaker_id),
        ).fetchone()
        if speaker is None:
            raise KeyError(speaker_id)
        if str(speaker[1]) != "proposed" or str(speaker[2]) == "narrator":
            raise ValueError("only proposed character speakers can be rejected")
        normalized = normalize_speaker_name(str(speaker[0]))
        dependencies = int(self.connection.execute(
            """SELECT count(*)
                 FROM omnix_audiobook_spans sp
                 JOIN omnix_audiobook_chapters ch
                   ON ch.workspace_id = sp.workspace_id AND ch.id = sp.chapter_id
                 JOIN omnix_audiobook_projects p
                   ON p.workspace_id = ch.workspace_id
                  AND p.current_source_revision_id = ch.source_revision_id
                 JOIN LATERAL (
                     SELECT speaker_id, speaker_candidate
                       FROM omnix_audiobook_annotations
                      WHERE workspace_id = sp.workspace_id AND span_id = sp.id
                      ORDER BY revision DESC LIMIT 1
                 ) a ON TRUE
                WHERE sp.workspace_id = %s AND p.id = %s
                  AND (
                    a.speaker_id = %s::uuid
                    OR lower(regexp_replace(
                        trim(COALESCE(a.speaker_candidate, '')), '\\s+', ' ', 'g'
                    )) = %s
                  )""",
            (context.workspace_id, project_id, speaker_id, normalized),
        ).fetchone()[0])
        if dependencies:
            raise ValueError(
                "reassign or resolve this speaker's spans before rejecting it"
            )
        self.connection.execute(
            """UPDATE omnix_audiobook_speakers
                  SET status = 'rejected'
                WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid""",
            (context.workspace_id, project_id, speaker_id),
        )
        self.connection.execute(
            """UPDATE omnix_audiobook_speaker_aliases
                  SET status = 'rejected'
                WHERE workspace_id = %s AND project_id = %s
                  AND speaker_id = %s::uuid
                  AND status IN ('proposed', 'confirmed')""",
            (context.workspace_id, project_id, speaker_id),
        )
        return {
            "id": speaker_id,
            "canonical_name": str(speaker[0]),
            "status": "rejected",
        }

    def list_speakers(self, context: TenantContext, project_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT s.id, s.canonical_name, s.display_name, s.kind, s.status,
                   c.id, c.voice_profile_id, c.voice_revision_hash, c.revision,
                   COALESCE(uses.span_count, 0), s.analysis_metadata
              FROM omnix_audiobook_speakers AS s
              LEFT JOIN LATERAL (
                  SELECT id, voice_profile_id, voice_revision_hash, revision
                    FROM omnix_audiobook_castings
                   WHERE workspace_id = s.workspace_id AND speaker_id = s.id
                   ORDER BY revision DESC LIMIT 1
              ) AS c ON TRUE
              LEFT JOIN LATERAL (
                  SELECT count(*) AS span_count
                    FROM omnix_audiobook_spans AS sp
                    JOIN omnix_audiobook_chapters AS ch
                      ON ch.workspace_id = sp.workspace_id AND ch.id = sp.chapter_id
                    JOIN omnix_audiobook_projects AS p
                      ON p.workspace_id = ch.workspace_id
                     AND p.current_source_revision_id = ch.source_revision_id
                    JOIN LATERAL (
                        SELECT speaker_id, speaker_candidate, role
                          FROM omnix_audiobook_annotations
                         WHERE workspace_id = sp.workspace_id AND span_id = sp.id
                         ORDER BY revision DESC LIMIT 1
                    ) AS a ON TRUE
                   WHERE sp.workspace_id = s.workspace_id AND p.id = s.project_id
                     AND (
                         (s.status = 'active' AND a.speaker_id = s.id
                          AND NOT (
                              s.kind = 'narrator'
                              AND a.role = 'dialogue'
                              AND a.speaker_candidate IS NOT NULL
                          ))
                         OR
                         (s.status = 'proposed'
                          AND (
                              a.speaker_id = s.id
                              OR lower(regexp_replace(
                                  trim(COALESCE(a.speaker_candidate, '')), '\\s+', ' ', 'g'
                              )) = lower(regexp_replace(
                                  trim(s.canonical_name), '\\s+', ' ', 'g'
                              ))
                          ))
                     )
              ) AS uses ON TRUE
             WHERE s.workspace_id = %s AND s.project_id = %s
               AND s.status IN ('active', 'proposed')
             ORDER BY CASE WHEN s.kind = 'narrator' THEN 0 WHEN s.status = 'proposed' THEN 1 ELSE 2 END,
                      s.canonical_name
            """, (context.workspace_id, project_id),
        ).fetchall()
        speakers = [{"id": str(row[0]), "canonical_name": str(row[1]),
                 "display_name": str(row[2]), "kind": str(row[3]),
                 "status": str(row[4]), "occurrence_count": int(row[9]),
                 "analysis_metadata": dict(row[10] or {}),
                 "casting": ({"id": str(row[5]), "voice_profile_id": str(row[6]),
                              "voice_revision_hash": str(row[7]), "revision": int(row[8])}
                             if row[5] else None), "aliases": [],
                 "proposed_aliases": []} for row in rows]
        alias_rows = self.connection.execute(
            """SELECT speaker_id, alias, status FROM omnix_audiobook_speaker_aliases
                WHERE workspace_id = %s AND project_id = %s
                  AND status IN ('confirmed', 'proposed')
                ORDER BY CASE WHEN status = 'confirmed' THEN 0 ELSE 1 END, alias""",
            (context.workspace_id, project_id),
        ).fetchall()
        by_id = {speaker["id"]: speaker for speaker in speakers}
        for speaker_id, alias, status in alias_rows:
            speaker = by_id.get(str(speaker_id))
            if speaker is None:
                continue
            if str(status) == "confirmed":
                speaker["aliases"].append(str(alias))
            else:
                speaker["proposed_aliases"].append(str(alias))
        return speakers

    def confirm_alias(
        self, context: TenantContext, *, project_id: str,
        speaker_id: str, alias: str, mode: str = "user_confirmed",
    ) -> dict[str, object]:
        """Make an alias authoritative for a speaker (``mode`` records whether the user or auto-confirmation did).

        The alias's provenance records the suggestions and detected speakers it displaced, so
        ``unconfirm_alias`` can restore them.
        """
        UUID(speaker_id)
        name = display_speaker_name(alias)
        normalized_name = normalize_speaker_name(name)
        if not name or len(name) > 128:
            raise ValueError("alias must be non-empty and at most 128 characters")
        speaker = self.connection.execute(
            """SELECT id, status, canonical_name
                 FROM omnix_audiobook_speakers
                WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid
                FOR UPDATE""", (context.workspace_id, project_id, speaker_id),
        ).fetchone()
        if speaker is None:
            raise KeyError(speaker_id)
        if str(speaker[1]) not in {"active", "proposed"}:
            raise ValueError("speaker is not available for alias confirmation")
        if normalize_speaker_name(str(speaker[2])) == normalized_name:
            raise ValueError("alias must differ from the speaker canonical name")
        conflict = self.connection.execute(
            """SELECT id FROM omnix_audiobook_speakers
                WHERE workspace_id = %s AND project_id = %s
                  AND status = 'active'
                  AND lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g')) = %s
                  AND id <> %s::uuid""",
            (context.workspace_id, project_id, normalized_name, speaker_id),
        ).fetchone()
        if conflict:
            raise ValueError("alias matches another active speaker's canonical name")
        alias_rows = self.connection.execute(
            """SELECT id, speaker_id, status
                 FROM omnix_audiobook_speaker_aliases
                WHERE workspace_id = %s AND project_id = %s
                  AND lower(regexp_replace(trim(alias), '\\s+', ' ', 'g')) = %s
                  AND status IN ('confirmed', 'proposed')
                ORDER BY CASE WHEN status = 'confirmed' THEN 0 ELSE 1 END, id
                FOR UPDATE""",
            (context.workspace_id, project_id, normalized_name),
        ).fetchall()
        confirmed = next(
            (row for row in alias_rows if str(row[2]) == "confirmed"), None
        )
        if confirmed is not None and str(confirmed[1]) != speaker_id:
            raise ValueError("alias is already assigned to another speaker")

        matching_proposed = next(
            (
                row for row in alias_rows
                if str(row[2]) == "proposed" and str(row[1]) == speaker_id
            ),
            None,
        )
        if confirmed is not None:
            alias_id = str(confirmed[0])
        elif matching_proposed is not None:
            alias_id = str(matching_proposed[0])
            self.connection.execute(
                """UPDATE omnix_audiobook_speaker_aliases
                      SET alias = %s, status = 'confirmed',
                          provenance = %s::jsonb,
                          confirmed_by_user_id = %s
                    WHERE workspace_id = %s AND project_id = %s AND id = %s""",
                (
                    name, canonical_json({"mode": mode}),
                    context.user_id, context.workspace_id, project_id, alias_id,
                ),
            )
        else:
            alias_id = f"ab:alias:{uuid4().hex}"
            self.connection.execute(
                """INSERT INTO omnix_audiobook_speaker_aliases
                    (id, workspace_id, project_id, speaker_id, alias, provenance,
                     status, confirmed_by_user_id)
                   VALUES (%s, %s, %s, %s::uuid, %s, %s::jsonb, 'confirmed', %s)""",
                (
                    alias_id, context.workspace_id, project_id, speaker_id, name,
                    canonical_json({"mode": mode}), context.user_id,
                ),
            )

        # Once one identity is authoritative, competing classifier suggestions
        # for the same normalized alias must no longer remain actionable.
        superseded_aliases = [str(row[0]) for row in self.connection.execute(
            """UPDATE omnix_audiobook_speaker_aliases
                  SET status = 'rejected'
                WHERE workspace_id = %s AND project_id = %s
                  AND id <> %s AND status = 'proposed'
                  AND lower(regexp_replace(trim(alias), '\\s+', ' ', 'g')) = %s
            RETURNING id""",
            (context.workspace_id, project_id, alias_id, normalized_name),
        ).fetchall()]
        # A candidate that is confirmed as an alias is no longer a separate
        # cast member; hide it from the proposed-candidate list.
        superseded_speakers = [str(row[0]) for row in self.connection.execute(
            """UPDATE omnix_audiobook_speakers
                  SET status = 'rejected'
                WHERE workspace_id = %s AND project_id = %s
                  AND id <> %s::uuid AND status = 'proposed'
                  AND lower(regexp_replace(trim(canonical_name), '\\s+', ' ', 'g')) = %s
            RETURNING id""",
            (context.workspace_id, project_id, speaker_id, normalized_name),
        ).fetchall()]
        if superseded_aliases or superseded_speakers:
            provenance = dict(self.connection.execute(
                """SELECT provenance FROM omnix_audiobook_speaker_aliases
                    WHERE workspace_id = %s AND project_id = %s AND id = %s""",
                (context.workspace_id, project_id, alias_id),
            ).fetchone()[0] or {})
            for key, ids in (("superseded_alias_ids", superseded_aliases),
                             ("superseded_speaker_ids", superseded_speakers)):
                provenance[key] = sorted({*provenance.get(key, []), *ids})
            self.connection.execute(
                """UPDATE omnix_audiobook_speaker_aliases SET provenance = %s::jsonb
                    WHERE workspace_id = %s AND project_id = %s AND id = %s""",
                (canonical_json(provenance), context.workspace_id, project_id, alias_id),
            )

        # A confirmed alias is an interpretation dependency, not merely display
        # metadata. Reconcile only the current source's latest unresolved
        # classifier candidates that exactly match the alias. Explicit
        # user-resolved annotations remain authoritative.
        candidates = self.connection.execute(
            """
            SELECT a.id, a.span_id, a.revision, a.role, a.delivery, a.evidence,
                   a.speaker_candidate
              FROM omnix_audiobook_spans AS s
              JOIN omnix_audiobook_chapters AS ch
                ON ch.workspace_id = s.workspace_id AND ch.id = s.chapter_id
              JOIN omnix_audiobook_projects AS p
                ON p.workspace_id = ch.workspace_id
               AND p.current_source_revision_id = ch.source_revision_id
              JOIN LATERAL (
                  SELECT id, span_id, revision, role, delivery, evidence,
                         speaker_candidate, speaker_id, review_status
                    FROM omnix_audiobook_annotations
                   WHERE workspace_id = s.workspace_id AND span_id = s.id
                   ORDER BY revision DESC LIMIT 1
              ) AS a ON TRUE
             WHERE s.workspace_id = %s AND p.id = %s
               AND lower(regexp_replace(trim(COALESCE(a.speaker_candidate, '')), '\\s+', ' ', 'g')) = %s
               AND a.review_status <> 'user_resolved'
               AND a.speaker_id IS DISTINCT FROM %s::uuid
             ORDER BY ch.ordinal, s.ordinal
            """,
            (context.workspace_id, project_id, normalized_name, speaker_id),
        ).fetchall()
        reconciled = 0
        for previous_id, span_id, revision, role, delivery, evidence, candidate in candidates:
            annotation_id = f"ab:an:{uuid4().hex}"
            merged_evidence = dict(evidence or {})
            merged_evidence.update({
                "previous_annotation_id": str(previous_id),
                "confirmed_alias": name,
                "resolved_speaker_id": speaker_id,
            })
            self.connection.execute(
                """INSERT INTO omnix_audiobook_annotations
                    (id, workspace_id, span_id, revision, role, speaker_id,
                     speaker_candidate, delivery, evidence, classifier, review_status)
                   VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s,
                           %s::jsonb, %s::jsonb, 'user_resolved')""",
                (annotation_id, context.workspace_id, span_id, int(revision) + 1,
                 role, speaker_id, candidate, delivery,
                 canonical_json(merged_evidence),
                 canonical_json({"mode": "confirmed_alias",
                                 "user_id": context.user_id,
                                 "alias": name})),
            )
            self.connection.execute(
                """UPDATE omnix_audiobook_review_issues
                      SET status = 'resolved',
                          resolution = %s::jsonb,
                          resolved_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND annotation_id = %s
                      AND status = 'open'""",
                (canonical_json({"mode": "confirmed_alias",
                                 "speaker_id": speaker_id, "alias": name}),
                 context.workspace_id, previous_id),
            )
            reconciled += 1

        if reconciled:
            self._after_alias_attribution_change(context, project_id)
        return {"id": alias_id, "speaker_id": speaker_id, "alias": name,
                "reconciled_spans": reconciled}

    def _after_alias_attribution_change(self, context: TenantContext, project_id: str) -> None:
        """Span speakers changed: the active render is stale, and the review state follows the open issues."""
        active = self.connection.execute(
            """SELECT settings->>'current_render_run_id'
                 FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s FOR UPDATE""",
            (context.workspace_id, project_id),
        ).fetchone()
        if active and active[0]:
            self._cancel_render_jobs(context, str(active[0]))
        remaining = int(self.connection.execute(
            """SELECT count(*)
                 FROM omnix_audiobook_review_issues AS i
                 JOIN omnix_audiobook_annotations AS a
                   ON a.workspace_id = i.workspace_id AND a.id = i.annotation_id
                 JOIN omnix_audiobook_spans AS s
                   ON s.workspace_id = a.workspace_id AND s.id = a.span_id
                 JOIN omnix_audiobook_chapters AS ch
                   ON ch.workspace_id = s.workspace_id AND ch.id = s.chapter_id
                 JOIN omnix_audiobook_projects AS p
                   ON p.workspace_id = ch.workspace_id
                  AND p.current_source_revision_id = ch.source_revision_id
                WHERE i.workspace_id = %s AND p.id = %s
                  AND i.status = 'open' AND NOT """ + chapter_removed_sql("ch", "p.id"),
            (context.workspace_id, project_id),
        ).fetchone()[0])
        self.connection.execute(
            """UPDATE omnix_audiobook_projects
                  SET state = %s,
                      settings = settings - 'current_render_run_id',
                      settings_revision = settings_revision + 1,
                      updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = %s AND id = %s""",
            ("review_required" if remaining else "ready_to_render",
             context.workspace_id, project_id),
        )

    def confirm_suggested_aliases(self, context: TenantContext, *, project_id: str) -> dict[str, int]:
        """Confirm every classifier-suggested alias (the user can unconfirm any of them).

        A suggestion that conflicts with another speaker's name or alias stays suggested.
        """
        suggestions = self.connection.execute(
            """SELECT a.id, a.speaker_id, a.alias
                 FROM omnix_audiobook_speaker_aliases AS a
                 JOIN omnix_audiobook_speakers AS s
                   ON s.workspace_id = a.workspace_id AND s.project_id = a.project_id
                  AND s.id = a.speaker_id
                WHERE a.workspace_id = %s AND a.project_id = %s
                  AND a.status = 'proposed' AND s.status IN ('active', 'proposed')
                ORDER BY a.alias, a.id""",
            (context.workspace_id, project_id),
        ).fetchall()
        confirmed = skipped = reconciled = 0
        for alias_id, speaker_id, alias in suggestions:
            # An earlier confirmation in this pass may have displaced this suggestion or its speaker.
            still_suggested = self.connection.execute(
                """SELECT 1
                     FROM omnix_audiobook_speaker_aliases AS a
                     JOIN omnix_audiobook_speakers AS s
                       ON s.workspace_id = a.workspace_id AND s.project_id = a.project_id
                      AND s.id = a.speaker_id
                    WHERE a.workspace_id = %s AND a.project_id = %s AND a.id = %s
                      AND a.status = 'proposed' AND s.status IN ('active', 'proposed')""",
                (context.workspace_id, project_id, alias_id),
            ).fetchone()
            if still_suggested is None:
                continue
            try:
                result = self.confirm_alias(
                    context, project_id=project_id, speaker_id=str(speaker_id),
                    alias=str(alias), mode="auto_confirmed",
                )
            except ValueError:
                # confirm_alias validates before it writes, so a conflict leaves nothing half-done.
                skipped += 1
                continue
            confirmed += 1
            reconciled += int(result["reconciled_spans"])
        return {"confirmed": confirmed, "skipped": skipped, "reconciled_spans": reconciled}

    def unconfirm_alias(
        self, context: TenantContext, *, project_id: str, speaker_id: str, alias: str,
    ) -> dict[str, object]:
        """Undo a confirmed alias: reject it (so it is not suggested again) and restore what confirming it changed.

        The spans its confirmation reattributed get their previous interpretation back (and their review
        issues reopen), and the suggestions and detected speakers it displaced return. Spans the user has
        since resolved by hand keep the user's choice.
        """
        UUID(speaker_id)
        normalized_name = normalize_speaker_name(display_speaker_name(alias))
        row = self.connection.execute(
            """SELECT id, alias, provenance
                 FROM omnix_audiobook_speaker_aliases
                WHERE workspace_id = %s AND project_id = %s AND speaker_id = %s::uuid
                  AND status = 'confirmed'
                  AND lower(regexp_replace(trim(alias), '\\s+', ' ', 'g')) = %s
                FOR UPDATE""",
            (context.workspace_id, project_id, speaker_id, normalized_name),
        ).fetchone()
        if row is None:
            raise KeyError(alias)
        alias_id, name, provenance = str(row[0]), str(row[1]), dict(row[2] or {})
        provenance["unconfirmed_by_user_id"] = context.user_id
        self.connection.execute(
            """UPDATE omnix_audiobook_speaker_aliases
                  SET status = 'rejected', provenance = %s::jsonb
                WHERE workspace_id = %s AND project_id = %s AND id = %s""",
            (canonical_json(provenance), context.workspace_id, project_id, alias_id),
        )
        restored_aliases = self.connection.execute(
            """UPDATE omnix_audiobook_speaker_aliases SET status = 'proposed'
                WHERE workspace_id = %s AND project_id = %s
                  AND id = ANY(%s) AND status = 'rejected'""",
            (context.workspace_id, project_id, list(provenance.get("superseded_alias_ids", []))),
        ).rowcount
        restored_speakers = self.connection.execute(
            """UPDATE omnix_audiobook_speakers SET status = 'proposed'
                WHERE workspace_id = %s AND project_id = %s
                  AND id = ANY(%s::uuid[]) AND status = 'rejected'""",
            (context.workspace_id, project_id, list(provenance.get("superseded_speaker_ids", []))),
        ).rowcount
        # The current source's spans whose latest interpretation is this alias's reconciliation.
        reattributed = self.connection.execute(
            """
            SELECT a.span_id, a.evidence->>'previous_annotation_id'
              FROM omnix_audiobook_spans AS s
              JOIN omnix_audiobook_chapters AS ch
                ON ch.workspace_id = s.workspace_id AND ch.id = s.chapter_id
              JOIN omnix_audiobook_projects AS p
                ON p.workspace_id = ch.workspace_id
               AND p.current_source_revision_id = ch.source_revision_id
              JOIN LATERAL (
                  SELECT span_id, speaker_id, evidence, classifier
                    FROM omnix_audiobook_annotations
                   WHERE workspace_id = s.workspace_id AND span_id = s.id
                   ORDER BY revision DESC LIMIT 1
              ) AS a ON TRUE
             WHERE s.workspace_id = %s AND p.id = %s
               AND a.classifier->>'mode' = 'confirmed_alias'
               AND a.speaker_id = %s::uuid
               AND lower(regexp_replace(trim(COALESCE(a.classifier->>'alias', '')), '\\s+', ' ', 'g')) = %s
            """,
            (context.workspace_id, project_id, speaker_id, normalized_name),
        ).fetchall()
        restored_spans = 0
        for span_id, previous_id in reattributed:
            previous = self.connection.execute(
                """SELECT role, speaker_id, speaker_candidate, delivery, evidence,
                          classifier, review_status
                     FROM omnix_audiobook_annotations
                    WHERE workspace_id = %s AND id = %s""",
                (context.workspace_id, previous_id),
            ).fetchone()
            if previous is None:
                continue
            role, previous_speaker, candidate, delivery, evidence, classifier, review_status = previous
            restored_evidence = dict(evidence or {})
            restored_evidence.update({"restored_annotation_id": str(previous_id), "unconfirmed_alias": name})
            next_revision = int(self.connection.execute(
                """SELECT COALESCE(MAX(revision), 0) + 1 FROM omnix_audiobook_annotations
                    WHERE workspace_id = %s AND span_id = %s""",
                (context.workspace_id, span_id),
            ).fetchone()[0])
            self.connection.execute(
                """INSERT INTO omnix_audiobook_annotations
                    (id, workspace_id, span_id, revision, role, speaker_id,
                     speaker_candidate, delivery, evidence, classifier, review_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
                (f"ab:an:{uuid4().hex}", context.workspace_id, span_id, next_revision, role,
                 previous_speaker, candidate, delivery, canonical_json(restored_evidence),
                 canonical_json(dict(classifier or {})), review_status),
            )
            # The issue the confirmation resolved is open again (it belongs to the restored interpretation).
            self.connection.execute(
                """UPDATE omnix_audiobook_review_issues
                      SET status = 'open', resolution = NULL, resolved_at = NULL
                    WHERE workspace_id = %s AND annotation_id = %s AND status = 'resolved'
                      AND resolution->>'mode' = 'confirmed_alias'""",
                (context.workspace_id, previous_id),
            )
            restored_spans += 1
        if restored_spans:
            self._after_alias_attribution_change(context, project_id)
        return {"id": alias_id, "speaker_id": speaker_id, "alias": name, "status": "rejected",
                "restored_spans": restored_spans, "restored_suggestions": int(restored_aliases or 0),
                "restored_speakers": int(restored_speakers or 0)}

    def assign_voice(
        self, context: TenantContext, *, project_id: str, speaker_id: str,
        voice_profile_id: str, voice_revision_hash: str,
    ) -> dict[str, Any]:
        UUID(speaker_id)
        if len(voice_revision_hash) != 64:
            raise ValueError("voice revision hash is invalid")
        speaker = self.connection.execute(
            """
            SELECT id, status FROM omnix_audiobook_speakers
             WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid FOR UPDATE
            """, (context.workspace_id, project_id, speaker_id),
        ).fetchone()
        if speaker is None:
            raise KeyError(speaker_id)
        if str(speaker[1]) == "proposed":
            self._promote_proposed_speaker(
                context, project_id=project_id, speaker_id=speaker_id,
            )
        elif str(speaker[1]) != "active":
            raise ValueError("speaker is not available for casting")
        current = self.connection.execute(
            """
            SELECT revision, voice_profile_id, voice_revision_hash
              FROM omnix_audiobook_castings
             WHERE workspace_id = %s AND speaker_id = %s::uuid
             ORDER BY revision DESC LIMIT 1
            """, (context.workspace_id, speaker_id),
        ).fetchone()
        if current and (str(current[1]), str(current[2])) == (voice_profile_id, voice_revision_hash):
            return {"speaker_id": speaker_id, "revision": int(current[0]),
                    "voice_profile_id": voice_profile_id, "voice_revision_hash": voice_revision_hash}
        revision = int(current[0]) + 1 if current else 1
        casting_id = f"ab:cast:{uuid4().hex}"
        self.connection.execute(
            """
            INSERT INTO omnix_audiobook_castings
                (id, workspace_id, speaker_id, revision, voice_profile_id,
                 voice_revision_hash, provenance)
            VALUES (%s, %s, %s::uuid, %s, %s, %s, %s::jsonb)
            """, (casting_id, context.workspace_id, speaker_id, revision,
                  voice_profile_id, voice_revision_hash,
                  canonical_json({"confirmed_by_user_id": context.user_id})),
        )
        active_runs = self.connection.execute(
            """
            SELECT settings->>'current_render_run_id', state
              FROM omnix_audiobook_projects
             WHERE workspace_id = %s AND id = %s FOR UPDATE
            """, (context.workspace_id, project_id),
        ).fetchone()
        if active_runs and active_runs[1] in {"rendering", "mastering"} and active_runs[0]:
            self._cancel_render_jobs(context, str(active_runs[0]))
        self.connection.execute(
            """
            UPDATE omnix_audiobook_projects
               SET state = CASE WHEN state IN ('rendering', 'mastering', 'rendered', 'ready_to_export', 'exported')
                                THEN 'ready_to_render' ELSE state END,
                   settings = settings - 'current_render_run_id',
                   settings_revision = settings_revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s
            """, (context.workspace_id, project_id),
        )
        return {"id": casting_id, "speaker_id": speaker_id, "revision": revision,
                "voice_profile_id": voice_profile_id, "voice_revision_hash": voice_revision_hash}

    def resolve_issue(
        self, context: TenantContext, *, project_id: str, issue_id: str,
        speaker_id: str, role: str, delivery: str = "",
    ) -> dict[str, Any]:
        if role not in {"narration", "dialogue", "heading", "other"}:
            raise ValueError("invalid annotation role")
        UUID(speaker_id)
        row = self.connection.execute(
            """
            SELECT i.annotation_id, a.span_id, a.revision, s.structural_kind
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
             WHERE i.workspace_id = %s AND i.id = %s AND i.status = 'open'
               AND r.project_id = %s
             FOR UPDATE OF i
            """, (context.workspace_id, issue_id, project_id),
        ).fetchone()
        if row is None:
            raise KeyError(issue_id)
        speaker = self.connection.execute(
            "SELECT id, status FROM omnix_audiobook_speakers WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid",
            (context.workspace_id, project_id, speaker_id),
        ).fetchone()
        if speaker is None:
            raise KeyError(speaker_id)
        if str(speaker[1]) != "active":
            raise ValueError("confirm the detected speaker before resolving a review issue")
        if role == "dialogue" and row[3] != "dialogue":
            raise ValueError("narrative source cannot be assigned a dialogue voice")
        next_revision = int(self.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM omnix_audiobook_annotations WHERE workspace_id = %s AND span_id = %s",
            (context.workspace_id, row[1]),
        ).fetchone()[0])
        annotation_id = f"ab:an:{uuid4().hex}"
        self.connection.execute(
            """
            INSERT INTO omnix_audiobook_annotations
                (id, workspace_id, span_id, revision, role, speaker_id, delivery,
                 evidence, classifier, review_status)
            VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s::jsonb, %s::jsonb, 'user_resolved')
            """, (annotation_id, context.workspace_id, row[1], next_revision,
                  role, speaker_id, delivery,
                  canonical_json({"review_issue_id": issue_id}),
                  canonical_json({"mode": "user_decision", "user_id": context.user_id})),
        )
        self.connection.execute(
            """
            UPDATE omnix_audiobook_review_issues
               SET status = 'resolved', resolution = %s::jsonb, resolved_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s
            """, (canonical_json({"annotation_id": annotation_id,
                                    "speaker_id": speaker_id, "role": role,
                                    "user_id": context.user_id}),
                  context.workspace_id, issue_id),
        )
        remaining = int(self.connection.execute(
            """
            SELECT count(*) FROM omnix_audiobook_review_issues AS i
              JOIN omnix_audiobook_annotations AS a ON a.workspace_id = i.workspace_id AND a.id = i.annotation_id
              JOIN omnix_audiobook_spans AS s ON s.workspace_id = a.workspace_id AND s.id = a.span_id
              JOIN omnix_audiobook_chapters AS c ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
              JOIN omnix_audiobook_source_revisions AS r ON r.workspace_id = c.workspace_id AND r.id = c.source_revision_id
              JOIN omnix_audiobook_projects AS p ON p.workspace_id = r.workspace_id AND p.id = r.project_id AND p.current_source_revision_id = r.id
             WHERE i.workspace_id = %s AND i.status = 'open' AND r.project_id = %s
               AND NOT """ + chapter_removed_sql("c", "r.project_id"), (context.workspace_id, project_id),
        ).fetchone()[0])
        if remaining == 0:
            self.connection.execute(
                "UPDATE omnix_audiobook_projects SET state = 'ready_to_render', updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s",
                (context.workspace_id, project_id),
            )
        return {"annotation_id": annotation_id, "span_id": str(row[1]),
                "revision": next_revision, "remaining_review_issues": remaining}

    def apply_performance_directions(
        self, context: TenantContext, *, project_id: str,
        directions: dict[str, str], director: dict[str, Any],
    ) -> dict[str, Any]:
        """Write generated delivery directions as new annotation revisions.

        Each revision copies its predecessor (role, speaker, review status,
        classifier and evidence) and changes only the delivery, so user
        decisions and confirmed aliases keep their meaning. A span is left
        alone when it is not dialogue, has an open review issue, or carries a
        delivery the user wrote (one not marked as directed).
        """
        project = self.connection.execute(
            """SELECT settings->>'current_render_run_id' FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s FOR UPDATE""",
            (context.workspace_id, project_id),
        ).fetchone()
        if project is None:
            raise KeyError(project_id)
        changed: list[str] = []
        kept_user_delivery = 0
        for span_id, delivery in directions.items():
            row = self.connection.execute(
                """SELECT a.revision, a.role, a.speaker_id, a.speaker_candidate, a.delivery,
                          a.evidence, a.classifier, a.review_status,
                          EXISTS (SELECT 1 FROM omnix_audiobook_review_issues i
                                    JOIN omnix_audiobook_annotations ia
                                      ON ia.workspace_id = i.workspace_id AND ia.id = i.annotation_id
                                   WHERE i.workspace_id = a.workspace_id AND ia.span_id = a.span_id
                                     AND i.status = 'open')
                     FROM omnix_audiobook_spans s
                     JOIN omnix_audiobook_chapters c
                       ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
                     JOIN omnix_audiobook_projects p
                       ON p.workspace_id = c.workspace_id
                      AND p.current_source_revision_id = c.source_revision_id
                     JOIN LATERAL (
                        SELECT * FROM omnix_audiobook_annotations
                         WHERE workspace_id = s.workspace_id AND span_id = s.id
                         ORDER BY revision DESC LIMIT 1
                     ) a ON TRUE
                    WHERE s.workspace_id = %s AND p.id = %s AND s.id = %s""",
                (context.workspace_id, project_id, span_id),
            ).fetchone()
            if row is None:
                continue
            revision, role, speaker_id, candidate, current, evidence, classifier, review_status, open_issue = row
            evidence = dict(evidence or {})
            if role != "dialogue" or open_issue or str(current) == delivery:
                continue
            if str(current) and evidence.get("delivery_source") != "performance_director":
                kept_user_delivery += 1
                continue
            self.connection.execute(
                """INSERT INTO omnix_audiobook_annotations
                    (id, workspace_id, span_id, revision, role, speaker_id,
                     speaker_candidate, delivery, evidence, classifier, review_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
                (f"ab:an:{uuid4().hex}", context.workspace_id, span_id, int(revision) + 1, role,
                 speaker_id, candidate, delivery,
                 canonical_json({**evidence, "previous_revision": int(revision),
                                 "delivery_source": "performance_director"}),
                 canonical_json({**dict(classifier or {}), "performance_direction": director}),
                 review_status),
            )
            changed.append(span_id)
        if changed:
            if project[0]:
                self._cancel_render_jobs(context, str(project[0]))
            self.connection.execute(
                """UPDATE omnix_audiobook_projects
                      SET state = CASE WHEN state IN ('rendering', 'mastering', 'rendered',
                                                    'ready_to_export', 'exported')
                                       THEN 'ready_to_render' ELSE state END,
                          settings = settings - 'current_render_run_id',
                          settings_revision = settings_revision + 1,
                          updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND id = %s""",
                (context.workspace_id, project_id),
            )
        return {"changed_span_ids": changed, "kept_user_delivery": kept_user_delivery}

    def revise_span(
        self, context: TenantContext, *, project_id: str, span_id: str,
        speaker_id: str, role: str, delivery: str = "",
    ) -> dict[str, Any]:
        """Append a user decision for a current-source span; never edit source text."""
        if role not in {"narration", "dialogue", "heading", "other"}:
            raise ValueError("invalid annotation role")
        UUID(speaker_id)
        if len(delivery) > 512:
            raise ValueError("delivery instruction is too long")
        project = self.connection.execute(
            """SELECT settings->>'current_render_run_id' FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s FOR UPDATE""",
            (context.workspace_id, project_id),
        ).fetchone()
        if project is None:
            raise KeyError(project_id)
        row = self.connection.execute(
            """SELECT s.structural_kind, a.revision, a.role, a.speaker_id, a.delivery
                 FROM omnix_audiobook_spans s
                 JOIN omnix_audiobook_chapters c
                   ON c.workspace_id = s.workspace_id AND c.id = s.chapter_id
                 JOIN omnix_audiobook_projects p
                   ON p.workspace_id = c.workspace_id
                  AND p.current_source_revision_id = c.source_revision_id
                 LEFT JOIN LATERAL (
                    SELECT revision, role, speaker_id, delivery
                      FROM omnix_audiobook_annotations
                     WHERE workspace_id = s.workspace_id AND span_id = s.id
                     ORDER BY revision DESC LIMIT 1
                 ) a ON TRUE
                WHERE s.workspace_id = %s AND p.id = %s AND s.id = %s""",
            (context.workspace_id, project_id, span_id),
        ).fetchone()
        if row is None:
            raise KeyError(span_id)
        if role == "dialogue" and row[0] != "dialogue":
            raise ValueError("narrative source cannot be assigned a dialogue voice")
        speaker = self.connection.execute(
            """SELECT id, status FROM omnix_audiobook_speakers
                WHERE workspace_id = %s AND project_id = %s AND id = %s::uuid""",
            (context.workspace_id, project_id, speaker_id),
        ).fetchone()
        if speaker is None:
            raise KeyError(speaker_id)
        if str(speaker[1]) != "active":
            raise ValueError("confirm the detected speaker before editing a span")
        unresolved = self.connection.execute(
            """SELECT 1 FROM omnix_audiobook_review_issues i
                 JOIN omnix_audiobook_annotations a
                   ON a.workspace_id = i.workspace_id AND a.id = i.annotation_id
                WHERE i.workspace_id = %s AND a.span_id = %s AND i.status = 'open'
                LIMIT 1""",
            (context.workspace_id, span_id),
        ).fetchone()
        if unresolved:
            raise ValueError("resolve the open review issue for this span first")
        if row[1] is not None and (str(row[2]), str(row[3]) if row[3] else None, str(row[4])) == (
            role, speaker_id, delivery,
        ):
            return {"span_id": span_id, "revision": int(row[1]), "changed": False}
        previous_revision = int(row[1] or 0)
        revision = previous_revision + 1
        annotation_id = f"ab:an:{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO omnix_audiobook_annotations
                (id, workspace_id, span_id, revision, role, speaker_id, delivery,
                 evidence, classifier, review_status)
               VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s::jsonb, %s::jsonb,
                       'user_resolved')""",
            (annotation_id, context.workspace_id, span_id, revision, role,
             speaker_id, delivery,
             canonical_json({"previous_revision": previous_revision}),
             canonical_json({"mode": "user_decision", "user_id": context.user_id})),
        )
        if project[0]:
            self._cancel_render_jobs(context, str(project[0]))
        self.connection.execute(
            """UPDATE omnix_audiobook_projects
                  SET state = CASE WHEN state IN ('rendering', 'mastering', 'rendered',
                                                'ready_to_export', 'exported')
                                   THEN 'ready_to_render' ELSE state END,
                      settings = settings - 'current_render_run_id',
                      settings_revision = settings_revision + 1,
                      updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = %s AND id = %s""",
            (context.workspace_id, project_id),
        )
        return {"annotation_id": annotation_id, "span_id": span_id,
                "revision": revision, "changed": True}
