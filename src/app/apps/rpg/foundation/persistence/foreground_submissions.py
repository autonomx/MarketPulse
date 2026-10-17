"""PostgreSQL compatibility facade for foreground RPG submission ownership."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.persistence.database import PostgresDatabase, default_database
from app.runtime.tenant_context import RequestTenant
from app.persistence.unit_of_work import unit_of_work

_TERMINAL = {"completed", "failed"}


@dataclass(frozen=True)
class ForegroundSubmissionClaim:
    session_id: str
    submission_id: str
    status: str
    owner: bool
    claim_token: str | None = None
    job_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    lease_expires_at: str | None = None
    execution_started_at: str | None = None


class PostgresForegroundSubmissionStoreAdapter:
    """Preserve the foreground-store contract over PostgreSQL authority."""
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def claim(
        self,
        session_id: str,
        submission_id: str,
        *,
        lease_seconds: float = 30.0,
        now: Any | None = None,
    ) -> ForegroundSubmissionClaim:
        del now
        with unit_of_work(self.database) as work:
            record = work.foreground_submissions.claim(
                self.context,
                session_id=session_id,
                submission_id=submission_id,
                lease_seconds=max(1, int(lease_seconds)),
            )
            work.commit()
        return self._claim(record)

    def get(self, session_id: str, submission_id: str) -> ForegroundSubmissionClaim | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT session_id, submission_id, status, claim_token, job_id,
                       response, error, lease_expires_at, execution_started_at
                  FROM omnix_rpg_foreground_submissions
                 WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
                """,
                (self.context.workspace_id, session_id, submission_id),
            ).fetchone()
        return self._row(row) if row is not None else None

    def renew(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        *,
        lease_seconds: float = 30.0,
        now: Any | None = None,
    ) -> bool:
        del now
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE omnix_rpg_foreground_submissions
                   SET lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND session_id = %s AND submission_id = %s
                   AND claim_token = %s AND status = 'claimed'
                   AND execution_started_at IS NULL
                   AND lease_expires_at > CURRENT_TIMESTAMP
                """,
                (
                    max(1, min(int(lease_seconds), 600)),
                    self.context.workspace_id,
                    session_id,
                    submission_id,
                    claim_token,
                ),
            )
        return cursor.rowcount == 1

    def attach_job(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        job_id: str,
        *,
        now: Any | None = None,
    ) -> bool:
        del now
        with unit_of_work(self.database) as work:
            attached = work.foreground_submissions.attach_job(
                self.context,
                session_id=session_id,
                submission_id=submission_id,
                claim_token=claim_token,
                job_id=job_id,
            )
            work.commit()
        return attached

    def mark_execution_started(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        *,
        now: Any | None = None,
    ) -> bool:
        del now
        with unit_of_work(self.database) as work:
            started = work.foreground_submissions.start_execution(
                self.context,
                session_id=session_id,
                submission_id=submission_id,
                claim_token=claim_token,
            )
            work.commit()
        return started

    def complete(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        result: dict[str, Any],
    ) -> bool:
        current = self.get(session_id, submission_id)
        if current is not None and current.status == "completed":
            return current.result == result
        with unit_of_work(self.database) as work:
            completed = work.foreground_submissions.complete(
                self.context,
                session_id=session_id,
                submission_id=submission_id,
                claim_token=claim_token,
                interaction_id=str(result.get("interaction_id") or ""),
                response=result,
            )
            work.commit()
        return completed

    def fail(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        error: str,
    ) -> bool:
        current = self.get(session_id, submission_id)
        if current is not None and current.status == "failed":
            return True
        if current is not None and current.status == "completed":
            return False
        with unit_of_work(self.database) as work:
            failed = work.foreground_submissions.fail(
                self.context,
                session_id=session_id,
                submission_id=submission_id,
                claim_token=claim_token,
                error=error,
            )
            work.commit()
        return failed

    def wait_for_terminal_or_claim(
        self,
        session_id: str,
        submission_id: str,
        *,
        timeout_seconds: float,
        lease_seconds: float = 30.0,
        poll_seconds: float = 0.02,
    ) -> ForegroundSubmissionClaim:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            claim = self.claim(
                session_id,
                submission_id,
                lease_seconds=lease_seconds,
            )
            if claim.owner or claim.status in _TERMINAL or time.monotonic() >= deadline:
                return claim
            time.sleep(max(0.001, poll_seconds))

    def wait_for_terminal(
        self,
        session_id: str,
        submission_id: str,
        *,
        timeout_seconds: float,
        poll_seconds: float = 0.02,
    ) -> ForegroundSubmissionClaim:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            claim = self.get(session_id, submission_id)
            if claim is None:
                raise RuntimeError("foreground submission claim disappeared")
            if claim.status in _TERMINAL or time.monotonic() >= deadline:
                return claim
            time.sleep(max(0.001, poll_seconds))

    @staticmethod
    def _claim(record: dict[str, Any]) -> ForegroundSubmissionClaim:
        return ForegroundSubmissionClaim(
            session_id=record["session_id"],
            submission_id=record["submission_id"],
            status=record["status"],
            owner=bool(record.get("owner")),
            claim_token=record.get("claim_token"),
            job_id=record.get("job_id"),
            result=record.get("response"),
            error=record.get("error"),
            lease_expires_at=record.get("lease_expires_at"),
            execution_started_at=record.get("execution_started_at"),
        )

    @staticmethod
    def _row(row: Any) -> ForegroundSubmissionClaim:
        return ForegroundSubmissionClaim(
            session_id=str(row[0]),
            submission_id=str(row[1]),
            status=str(row[2]),
            owner=False,
            claim_token=None,
            job_id=str(row[4]) if row[4] is not None else None,
            result=dict(row[5]) if row[5] is not None else None,
            error=str(row[6]) if row[6] is not None else None,
            lease_expires_at=row[7].isoformat() if row[7] is not None else None,
            execution_started_at=row[8].isoformat() if row[8] is not None else None,
        )


_DEFAULT_STORE: PostgresForegroundSubmissionStoreAdapter | None = None


def default_postgres_foreground_submission_store() -> PostgresForegroundSubmissionStoreAdapter:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = PostgresForegroundSubmissionStoreAdapter()
    return _DEFAULT_STORE


def submission_store_for_job_store(job_store: Any) -> PostgresForegroundSubmissionStoreAdapter:
    del job_store
    return default_postgres_foreground_submission_store()
