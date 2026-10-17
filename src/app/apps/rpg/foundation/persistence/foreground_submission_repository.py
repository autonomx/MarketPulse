"""PostgreSQL ownership of foreground RPG submissions: claim, execute, complete (moved from kernel persistence, PA-2.2)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.persistence.tenant import TenantContext


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PostgresForegroundSubmissionRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def claim(
        self,
        context: TenantContext,
        *,
        session_id: str,
        submission_id: str,
        lease_seconds: int = 30,
    ) -> dict[str, Any]:
        token = uuid.uuid4().hex
        lease_seconds = max(1, min(int(lease_seconds), 600))
        inserted = self.connection.execute(
            """
            INSERT INTO omnix_rpg_foreground_submissions
                (workspace_id, session_id, submission_id, claim_token, lease_expires_at)
            VALUES (%s, %s, %s, %s,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'))
            ON CONFLICT DO NOTHING
            RETURNING workspace_id, session_id, submission_id, status, claim_token,
                      job_id, interaction_id, response, error, lease_expires_at,
                      execution_started_at, created_at, updated_at
            """,
            (context.workspace_id, session_id, submission_id, token, lease_seconds),
        ).fetchone()
        owner = inserted is not None
        if inserted is None:
            reclaimed = self.connection.execute(
                """
                UPDATE omnix_rpg_foreground_submissions
                   SET claim_token = %s,
                       lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                       updated_at = CURRENT_TIMESTAMP, error = NULL
                 WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
                   AND status = 'claimed' AND execution_started_at IS NULL
                   AND lease_expires_at <= CURRENT_TIMESTAMP
                RETURNING workspace_id, session_id, submission_id, status, claim_token,
                          job_id, interaction_id, response, error, lease_expires_at,
                          execution_started_at, created_at, updated_at
                """,
                (
                    token,
                    lease_seconds,
                    context.workspace_id,
                    session_id,
                    submission_id,
                ),
            ).fetchone()
            if reclaimed is not None:
                inserted = reclaimed
                owner = True
        row = inserted or self.connection.execute(
            """
            SELECT workspace_id, session_id, submission_id, status, claim_token,
                   job_id, interaction_id, response, error, lease_expires_at,
                   execution_started_at, created_at, updated_at
              FROM omnix_rpg_foreground_submissions
             WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
            """,
            (context.workspace_id, session_id, submission_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("foreground submission claim was not persisted")
        return self._record(row, owner=owner, owner_token=token if owner else None)

    def attach_job(
        self,
        context: TenantContext,
        *,
        session_id: str,
        submission_id: str,
        claim_token: str,
        job_id: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_rpg_foreground_submissions
               SET job_id = %s, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
               AND claim_token = %s AND status = 'claimed'
               AND execution_started_at IS NULL
               AND lease_expires_at > CURRENT_TIMESTAMP
            """,
            (
                job_id,
                context.workspace_id,
                session_id,
                submission_id,
                claim_token,
            ),
        )
        return cursor.rowcount == 1

    def start_execution(
        self,
        context: TenantContext,
        *,
        session_id: str,
        submission_id: str,
        claim_token: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_rpg_foreground_submissions
               SET execution_started_at = COALESCE(execution_started_at, CURRENT_TIMESTAMP),
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
               AND claim_token = %s AND status = 'claimed'
               AND lease_expires_at > CURRENT_TIMESTAMP
            """,
            (context.workspace_id, session_id, submission_id, claim_token),
        )
        return cursor.rowcount == 1

    def complete(
        self,
        context: TenantContext,
        *,
        session_id: str,
        submission_id: str,
        claim_token: str,
        interaction_id: str,
        response: dict[str, Any],
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_rpg_foreground_submissions
               SET status = 'completed', interaction_id = %s,
                   response = %s::jsonb, error = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
               AND claim_token = %s AND status = 'claimed'
               AND execution_started_at IS NOT NULL
            """,
            (
                interaction_id,
                _json(response),
                context.workspace_id,
                session_id,
                submission_id,
                claim_token,
            ),
        )
        return cursor.rowcount == 1

    def fail(
        self,
        context: TenantContext,
        *,
        session_id: str,
        submission_id: str,
        claim_token: str,
        error: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_rpg_foreground_submissions
               SET status = 'failed', error = %s, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
               AND claim_token = %s AND status = 'claimed'
            """,
            (error[:2000], context.workspace_id, session_id, submission_id, claim_token),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _record(row: Any, *, owner: bool, owner_token: str | None) -> dict[str, Any]:
        return {
            "workspace_id": str(row[0]),
            "session_id": str(row[1]),
            "submission_id": str(row[2]),
            "status": str(row[3]),
            "owner": owner,
            "claim_token": owner_token,
            "job_id": str(row[5]) if row[5] is not None else None,
            "interaction_id": str(row[6]) if row[6] is not None else None,
            "response": dict(row[7]) if row[7] is not None else None,
            "error": str(row[8]) if row[8] is not None else None,
            "lease_expires_at": row[9].isoformat(),
            "execution_started_at": row[10].isoformat() if row[10] is not None else None,
            "created_at": row[11].isoformat(),
            "updated_at": row[12].isoformat(),
        }
