"""PostgreSQL persistence for the coding quality controller.

Quality state is deliberately separate from AgentRunStatus. A worker crash can
therefore recover the run lifecycle and the exact quality stage independently.
"""
from __future__ import annotations

import json
from typing import Any, cast

from app.persistence.tenant import TenantContext

from .contracts import (
    ReviewAttempt,
    ReviewResult,
    ReviewSnapshot,
    SelfReviewResult,
    ValidationResult,
    WorkspaceState,
)
from collections.abc import Sequence


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


class PostgresCodingQualityRepository:
    def __init__(self, connection: Any, context: TenantContext) -> None:
        self.connection = connection
        self.context = context

    def _effective_stage(
        self,
        run_id: str,
        *,
        stage: str,
        task_revision_id: str | None,
    ) -> str:
        """Preserve the broad Pi-owned working stage supplied by the service.

        Omnix no longer infers inspect/planning/implementation subphases from
        Pi's tool stream.  ``implementing`` therefore represents Pi's entire
        autonomous coding loop for both initial and revised tasks.
        """

        del run_id, task_revision_id
        return stage

    def set_stage(
        self,
        run_id: str,
        *,
        stage: str,
        attempt: int,
        task_revision_id: str | None,
        workspace_state_id: str | None = None,
    ) -> dict[str, Any]:
        stage = self._effective_stage(
            run_id,
            stage=stage,
            task_revision_id=task_revision_id,
        )
        row = self.connection.execute(
            """
            INSERT INTO omnix_agent_coding_quality_state (
                workspace_id, run_id, stage, attempt, task_revision_id,
                workspace_state_id, stage_started_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (workspace_id, run_id) DO UPDATE
               SET stage = EXCLUDED.stage,
                   attempt = EXCLUDED.attempt,
                   task_revision_id = EXCLUDED.task_revision_id,
                   workspace_state_id = EXCLUDED.workspace_state_id,
                   stage_started_at = CASE
                       WHEN omnix_agent_coding_quality_state.stage IS DISTINCT FROM EXCLUDED.stage
                         OR omnix_agent_coding_quality_state.attempt IS DISTINCT FROM EXCLUDED.attempt
                       THEN CURRENT_TIMESTAMP
                       ELSE omnix_agent_coding_quality_state.stage_started_at
                   END,
                   updated_at = CURRENT_TIMESTAMP
            RETURNING stage, attempt, task_revision_id, workspace_state_id,
                      stage_started_at, updated_at
            """,
            (
                self.context.workspace_id,
                run_id,
                stage,
                attempt,
                task_revision_id,
                workspace_state_id,
            ),
        ).fetchone()
        return {
            "stage": str(row[0]),
            "attempt": int(row[1]),
            "task_revision_id": str(row[2]) if row[2] else None,
            "workspace_state_id": str(row[3]) if row[3] else None,
            "stage_started_at": row[4],
            "updated_at": row[5],
        }

    def get_stage(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT stage, attempt, task_revision_id, workspace_state_id,
                   stage_started_at, updated_at
              FROM omnix_agent_coding_quality_state
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "stage": str(row[0]),
            "attempt": int(row[1]),
            "task_revision_id": str(row[2]) if row[2] else None,
            "workspace_state_id": str(row[3]) if row[3] else None,
            "stage_started_at": row[4],
            "updated_at": row[5],
        }

    def add_workspace_state(self, state: WorkspaceState) -> WorkspaceState:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_workspace_states (
                workspace_id, run_id, state_id, task_revision_id, base_commit_sha,
                tracked_diff_sha256, untracked_file_manifest_sha256,
                modified_paths, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (workspace_id, run_id, state_id) DO NOTHING
            """,
            (
                self.context.workspace_id,
                state.run_id,
                state.state_id,
                state.task_revision_id,
                state.base_commit_sha,
                state.tracked_diff_sha256,
                state.untracked_file_manifest_sha256,
                _json(state.modified_paths),
                state.created_at,
            ),
        )
        return state

    def get_workspace_state(self, run_id: str, state_id: str) -> WorkspaceState | None:
        row = self.connection.execute(
            """
            SELECT task_revision_id, base_commit_sha, tracked_diff_sha256,
                   untracked_file_manifest_sha256, modified_paths, created_at
              FROM omnix_agent_workspace_states
             WHERE workspace_id = %s AND run_id = %s AND state_id = %s
            """,
            (self.context.workspace_id, run_id, state_id),
        ).fetchone()
        if row is None:
            return None
        return WorkspaceState(
            state_id=state_id,
            run_id=run_id,
            task_revision_id=str(row[0]) if row[0] else None,
            base_commit_sha=str(row[1]),
            tracked_diff_sha256=str(row[2]),
            untracked_file_manifest_sha256=str(row[3]),
            modified_paths=list(row[4] or []),
            created_at=row[5],
        )

    def latest_workspace_state(
        self,
        run_id: str,
        *,
        task_revision_id: str | None = None,
    ) -> WorkspaceState | None:
        if task_revision_id is None:
            row = self.connection.execute(
                """
                SELECT state_id
                  FROM omnix_agent_workspace_states
                 WHERE workspace_id = %s AND run_id = %s
                 ORDER BY created_at DESC, state_id DESC
                 LIMIT 1
                """,
                (self.context.workspace_id, run_id),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT state_id
                  FROM omnix_agent_workspace_states
                 WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
                 ORDER BY created_at DESC, state_id DESC
                 LIMIT 1
                """,
                (self.context.workspace_id, run_id, task_revision_id),
            ).fetchone()
        return self.get_workspace_state(run_id, str(row[0])) if row else None

    def add_validation_result(self, result: ValidationResult) -> ValidationResult:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_validation_results (
                workspace_id, run_id, result_id, validation_id, kind,
                task_revision_id, workspace_state_id, command, exit_code,
                success, outcome, output_digest, covers_requirement_ids, started_at, finished_at, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, run_id, result_id) DO NOTHING
            """,
            (
                self.context.workspace_id,
                result.run_id,
                result.result_id,
                result.validation_id,
                result.kind,
                result.task_revision_id,
                result.workspace_state_id,
                result.command,
                result.exit_code,
                result.success,
                result.outcome,
                result.output_digest,
                _json(result.covers_requirement_ids),
                result.started_at,
                result.finished_at,
                _json(result.metadata),
            ),
        )
        return result

    def list_validation_results(
        self,
        run_id: str,
        *,
        task_revision_id: str | None = None,
    ) -> list[ValidationResult]:
        if task_revision_id is None:
            rows = self.connection.execute(
                """
                SELECT result_id, validation_id, kind, task_revision_id,
                       workspace_state_id, command, exit_code, success, outcome,
                       output_digest, covers_requirement_ids, started_at, finished_at, metadata
                  FROM omnix_agent_validation_results
                 WHERE workspace_id = %s AND run_id = %s
                 ORDER BY finished_at, result_id
                """,
                (self.context.workspace_id, run_id),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT result_id, validation_id, kind, task_revision_id,
                       workspace_state_id, command, exit_code, success, outcome,
                       output_digest, covers_requirement_ids, started_at, finished_at, metadata
                  FROM omnix_agent_validation_results
                 WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
                 ORDER BY finished_at, result_id
                """,
                (self.context.workspace_id, run_id, task_revision_id),
            ).fetchall()
        return [
            ValidationResult(
                result_id=str(row[0]),
                run_id=run_id,
                validation_id=str(row[1]),
                kind=cast(Any, str(row[2])),
                task_revision_id=str(row[3]) if row[3] else None,
                workspace_state_id=str(row[4]),
                command=str(row[5]),
                exit_code=int(row[6]) if row[6] is not None else None,
                success=bool(row[7]),
                outcome=cast(Any, str(row[8])),
                output_digest=str(row[9]),
                covers_requirement_ids=list(row[10] or []),
                started_at=row[11],
                finished_at=row[12],
                metadata=dict(row[13] or {}),
            )
            for row in rows
        ]

    def add_self_review_result(self, result: SelfReviewResult) -> SelfReviewResult:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_self_review_results (
                workspace_id, run_id, self_review_result_id, task_revision_id, workspace_state_id,
                verdict, requirements, findings, missing_tests, residual_risks, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
            ON CONFLICT (workspace_id, run_id, self_review_result_id) DO NOTHING
            """,
            (
                self.context.workspace_id,
                result.run_id,
                result.self_review_result_id,
                result.task_revision_id,
                result.workspace_state_id,
                result.verdict,
                _json(result.requirements),
                _json(result.findings),
                _json(result.missing_tests),
                _json(result.residual_risks),
                result.created_at,
            ),
        )
        return result

    def list_self_review_results(
        self,
        run_id: str,
        *,
        task_revision_id: str | None = None,
    ) -> list[SelfReviewResult]:
        where = "WHERE workspace_id = %s AND run_id = %s"
        args: tuple[object, ...] = (self.context.workspace_id, run_id)
        if task_revision_id is not None:
            where += " AND task_revision_id = %s"
            args = (*args, task_revision_id)
        rows = self.connection.execute(
            f"""
            SELECT self_review_result_id, task_revision_id, workspace_state_id, verdict,
                   requirements, findings, missing_tests, residual_risks, created_at
              FROM omnix_agent_self_review_results {where}
             ORDER BY created_at, self_review_result_id
            """,
            args,
        ).fetchall()
        return [
            SelfReviewResult(
                self_review_result_id=str(row[0]),
                run_id=run_id,
                task_revision_id=str(row[1]) if row[1] else None,
                workspace_state_id=str(row[2]),
                verdict=cast(Any, str(row[3])),
                requirements=list(row[4] or []),
                findings=list(row[5] or []),
                missing_tests=list(row[6] or []),
                residual_risks=list(row[7] or []),
                created_at=row[8],
            )
            for row in rows
        ]

    def add_review_snapshot(self, snapshot: ReviewSnapshot) -> ReviewSnapshot:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_review_snapshots (
                workspace_id, run_id, snapshot_id, task_revision_id,
                workspace_state_id, base_commit_sha, patch_checksum,
                patch_storage_ref, run_change_set_id, workspace_root, subject_paths, context_paths, relevant_files,
                validation_result_ids, repository_guidance_digest, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
            ON CONFLICT (workspace_id, run_id, snapshot_id) DO NOTHING
            """,
            (
                self.context.workspace_id,
                snapshot.run_id,
                snapshot.snapshot_id,
                snapshot.task_revision_id,
                snapshot.workspace_state_id,
                snapshot.base_commit_sha,
                snapshot.patch_checksum,
                snapshot.patch_storage_ref,
                snapshot.run_change_set_id,
                snapshot.workspace_root,
                _json(snapshot.subject_paths),
                _json(snapshot.context_paths),
                _json(snapshot.relevant_files),
                _json(snapshot.validation_result_ids),
                snapshot.repository_guidance_digest,
                snapshot.created_at,
            ),
        )
        return snapshot

    def get_review_snapshot(self, run_id: str, snapshot_id: str) -> ReviewSnapshot | None:
        row = self.connection.execute(
            """
            SELECT task_revision_id, workspace_state_id, base_commit_sha,
                   patch_checksum, patch_storage_ref, run_change_set_id, workspace_root,
                   subject_paths, context_paths, relevant_files, validation_result_ids,
                   repository_guidance_digest, created_at
              FROM omnix_agent_review_snapshots
             WHERE workspace_id = %s AND run_id = %s AND snapshot_id = %s
            """,
            (self.context.workspace_id, run_id, snapshot_id),
        ).fetchone()
        if row is None:
            return None
        return ReviewSnapshot(
            snapshot_id=snapshot_id,
            run_id=run_id,
            task_revision_id=str(row[0]) if row[0] else None,
            workspace_state_id=str(row[1]),
            base_commit_sha=str(row[2]),
            patch_checksum=str(row[3]),
            patch_storage_ref=str(row[4]) if row[4] else None,
            run_change_set_id=str(row[5]) if row[5] else None,
            workspace_root=str(row[6]),
            subject_paths=list(row[7] or []),
            context_paths=list(row[8] or []),
            relevant_files=list(row[9] or []),
            validation_result_ids=list(row[10] or []),
            repository_guidance_digest=str(row[11]) if row[11] else None,
            created_at=row[12],
        )

    def latest_review_snapshot(
        self,
        run_id: str,
        *,
        task_revision_id: str | None,
        workspace_state_id: str,
    ) -> ReviewSnapshot | None:
        row = self.connection.execute(
            """
            SELECT snapshot_id
              FROM omnix_agent_review_snapshots
             WHERE workspace_id = %s AND run_id = %s
               AND task_revision_id IS NOT DISTINCT FROM %s
               AND workspace_state_id = %s
             ORDER BY created_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (self.context.workspace_id, run_id, task_revision_id, workspace_state_id),
        ).fetchone()
        return self.get_review_snapshot(run_id, str(row[0])) if row else None

    def add_review_attempt(self, attempt: ReviewAttempt) -> ReviewAttempt:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_review_attempts (
                workspace_id, run_id, review_attempt_id, reviewer_run_id,
                review_snapshot_id, task_revision_id, workspace_state_id,
                reviewer_slot, runtime_attempt, protocol_version,
                model_provider_id, model_id, reasoning_effort, status,
                failure_class, failure_reason, retryable, started_at,
                finished_at, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (workspace_id, run_id, review_attempt_id) DO UPDATE
               SET status = EXCLUDED.status,
                   failure_class = EXCLUDED.failure_class,
                   failure_reason = EXCLUDED.failure_reason,
                   retryable = EXCLUDED.retryable,
                   finished_at = EXCLUDED.finished_at
            """,
            (
                self.context.workspace_id,
                attempt.run_id,
                attempt.review_attempt_id,
                attempt.reviewer_run_id,
                attempt.review_snapshot_id,
                attempt.task_revision_id,
                attempt.workspace_state_id,
                attempt.reviewer_slot,
                attempt.runtime_attempt,
                attempt.protocol_version,
                attempt.model_provider_id,
                attempt.model_id,
                attempt.reasoning_effort,
                attempt.status,
                attempt.failure_class,
                attempt.failure_reason,
                attempt.retryable,
                attempt.started_at,
                attempt.finished_at,
                attempt.created_at,
            ),
        )
        return attempt

    def get_review_attempt_by_reviewer(self, reviewer_run_id: str) -> ReviewAttempt | None:
        row = self.connection.execute(
            """
            SELECT run_id, review_attempt_id, reviewer_run_id, review_snapshot_id,
                   task_revision_id, workspace_state_id, reviewer_slot,
                   runtime_attempt, protocol_version, model_provider_id,
                   model_id, reasoning_effort, status, failure_class,
                   failure_reason, retryable, started_at, finished_at, created_at
              FROM omnix_agent_review_attempts
             WHERE workspace_id = %s AND reviewer_run_id = %s
            """,
            (self.context.workspace_id, reviewer_run_id),
        ).fetchone()
        return self._review_attempt_from_row(row) if row else None

    def list_review_attempts(
        self,
        run_id: str,
        *,
        review_snapshot_id: str | None = None,
        task_revision_id: str | None = None,
    ) -> list[ReviewAttempt]:
        clauses = ["workspace_id = %s", "run_id = %s"]
        args: list[object] = [self.context.workspace_id, run_id]
        if review_snapshot_id is not None:
            clauses.append("review_snapshot_id = %s")
            args.append(review_snapshot_id)
        if task_revision_id is not None:
            clauses.append("task_revision_id IS NOT DISTINCT FROM %s")
            args.append(task_revision_id)
        rows = self.connection.execute(
            f"""
            SELECT run_id, review_attempt_id, reviewer_run_id, review_snapshot_id,
                   task_revision_id, workspace_state_id, reviewer_slot,
                   runtime_attempt, protocol_version, model_provider_id,
                   model_id, reasoning_effort, status, failure_class,
                   failure_reason, retryable, started_at, finished_at, created_at
              FROM omnix_agent_review_attempts
             WHERE {' AND '.join(clauses)}
             ORDER BY reviewer_slot, runtime_attempt, created_at, review_attempt_id
            """,
            tuple(args),
        ).fetchall()
        return [self._review_attempt_from_row(row) for row in rows]

    @staticmethod
    def _review_attempt_from_row(row: Sequence[Any]) -> ReviewAttempt:
        values = list(row)
        return ReviewAttempt(
            run_id=str(values[0]),
            review_attempt_id=str(values[1]),
            reviewer_run_id=str(values[2]),
            review_snapshot_id=str(values[3]),
            task_revision_id=str(values[4]) if values[4] else None,
            workspace_state_id=str(values[5]),
            reviewer_slot=int(values[6]),
            runtime_attempt=int(values[7]),
            protocol_version=str(values[8]),
            model_provider_id=str(values[9]),
            model_id=str(values[10]),
            reasoning_effort=str(values[11]) if values[11] else None,
            status=cast(Any, str(values[12])),
            failure_class=cast(Any, str(values[13]) if values[13] else None),
            failure_reason=str(values[14]) if values[14] else None,
            retryable=bool(values[15]),
            started_at=values[16],
            finished_at=values[17],
            created_at=values[18],
        )

    def add_review_result(self, result: ReviewResult) -> ReviewResult:
        self.connection.execute(
            """
            INSERT INTO omnix_agent_review_results (
                workspace_id, run_id, review_result_id, reviewer_run_id,
                review_snapshot_id, task_revision_id, workspace_state_id,
                verdict, requirements, findings, missing_tests,
                residual_risks, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (workspace_id, run_id, review_result_id) DO NOTHING
            """,
            (
                self.context.workspace_id,
                result.run_id,
                result.review_result_id,
                result.reviewer_run_id,
                result.review_snapshot_id,
                result.task_revision_id,
                result.workspace_state_id,
                result.verdict,
                _json(result.requirements),
                _json(result.findings),
                _json(result.missing_tests),
                _json(result.residual_risks),
                result.created_at,
            ),
        )
        return result

    def list_review_results(
        self,
        run_id: str,
        *,
        task_revision_id: str | None = None,
    ) -> list[ReviewResult]:
        if task_revision_id is None:
            rows = self.connection.execute(
                """
                SELECT review_result_id, reviewer_run_id, review_snapshot_id,
                       task_revision_id, workspace_state_id, verdict,
                       requirements, findings, missing_tests, residual_risks,
                       created_at
                  FROM omnix_agent_review_results
                 WHERE workspace_id = %s AND run_id = %s
                 ORDER BY created_at, review_result_id
                """,
                (self.context.workspace_id, run_id),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT review_result_id, reviewer_run_id, review_snapshot_id,
                       task_revision_id, workspace_state_id, verdict,
                       requirements, findings, missing_tests, residual_risks,
                       created_at
                  FROM omnix_agent_review_results
                 WHERE workspace_id = %s AND run_id = %s AND task_revision_id = %s
                 ORDER BY created_at, review_result_id
                """,
                (self.context.workspace_id, run_id, task_revision_id),
            ).fetchall()
        return [
            ReviewResult(
                review_result_id=str(row[0]),
                run_id=run_id,
                reviewer_run_id=str(row[1]),
                review_snapshot_id=str(row[2]),
                task_revision_id=str(row[3]) if row[3] else None,
                workspace_state_id=str(row[4]),
                verdict=cast(Any, str(row[5])),
                requirements=list(row[6] or []),
                findings=list(row[7] or []),
                missing_tests=list(row[8] or []),
                residual_risks=list(row[9] or []),
                created_at=row[10],
            )
            for row in rows
        ]
