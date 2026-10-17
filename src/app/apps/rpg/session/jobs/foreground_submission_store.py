"""Foreground RPG submission ownership compatibility boundary.

PostgreSQL is the production authority. Provider-free tests use a process-local
in-memory double keyed by the requested path; cross-process correctness is
covered by PostgreSQL integration tests.
"""

from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    claims: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def _state(path: str | Path | None) -> _State:
    key = str(path or ":memory:foreground")
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryForegroundSubmissionStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(":memory:")
        self._state = _state(db_path)

    def claim(
        self,
        session_id: str,
        submission_id: str,
        *,
        lease_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> ForegroundSubmissionClaim:
        instant = now or _utcnow()
        key = (session_id, submission_id)
        with self._state.lock:
            current = self._state.claims.get(key)
            if current is not None:
                if current["status"] in {"completed", "failed"}:
                    return self._claim(current, owner=False)
                expires = datetime.fromisoformat(current["lease_expires_at"])
                if expires > instant or current.get("execution_started_at"):
                    return self._claim(current, owner=False)
            token = uuid.uuid4().hex
            record = {
                "session_id": session_id,
                "submission_id": submission_id,
                "status": "claimed",
                "claim_token": token,
                "job_id": current.get("job_id") if current else None,
                "result": None,
                "error": None,
                "lease_expires_at": (
                    instant + timedelta(seconds=max(1.0, float(lease_seconds)))
                ).isoformat(),
                "execution_started_at": None,
            }
            self._state.claims[key] = record
            return self._claim(record, owner=True)

    def get(self, session_id: str, submission_id: str) -> ForegroundSubmissionClaim | None:
        with self._state.lock:
            current = self._state.claims.get((session_id, submission_id))
            return self._claim(current, owner=False) if current is not None else None

    def renew(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        *,
        lease_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> bool:
        instant = now or _utcnow()
        with self._state.lock:
            current = self._state.claims.get((session_id, submission_id))
            if (
                current is None
                or current["status"] != "claimed"
                or current["claim_token"] != claim_token
                or current.get("execution_started_at")
            ):
                return False
            current["lease_expires_at"] = (
                instant + timedelta(seconds=max(1.0, float(lease_seconds)))
            ).isoformat()
            return True

    def attach_job(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        del now
        with self._state.lock:
            current = self._state.claims.get((session_id, submission_id))
            if current is None or current["claim_token"] != claim_token or current["status"] != "claimed":
                return False
            current["job_id"] = job_id
            return True

    def mark_execution_started(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        instant = now or _utcnow()
        with self._state.lock:
            current = self._state.claims.get((session_id, submission_id))
            if current is None or current["claim_token"] != claim_token or current["status"] != "claimed":
                return False
            current["execution_started_at"] = instant.isoformat()
            return True

    def complete(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        result: dict[str, Any],
    ) -> bool:
        with self._state.lock:
            current = self._state.claims.get((session_id, submission_id))
            if current is None or current["claim_token"] != claim_token:
                return False
            if current["status"] == "completed":
                return current.get("result") == result
            if current["status"] != "claimed":
                return False
            current.update(status="completed", result=deepcopy(result), error=None)
            return True

    def fail(
        self,
        session_id: str,
        submission_id: str,
        claim_token: str,
        error: str,
    ) -> bool:
        with self._state.lock:
            current = self._state.claims.get((session_id, submission_id))
            if current is None or current["claim_token"] != claim_token:
                return False
            if current["status"] == "failed":
                return True
            if current["status"] != "claimed":
                return False
            current.update(status="failed", result=None, error=str(error))
            return True

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
            claim = self.claim(session_id, submission_id, lease_seconds=lease_seconds)
            if claim.owner or claim.status in {"completed", "failed"} or time.monotonic() >= deadline:
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
            if claim.status in {"completed", "failed"} or time.monotonic() >= deadline:
                return claim
            time.sleep(max(0.001, poll_seconds))

    @staticmethod
    def _claim(record: dict[str, Any], *, owner: bool) -> ForegroundSubmissionClaim:
        return ForegroundSubmissionClaim(
            session_id=str(record["session_id"]),
            submission_id=str(record["submission_id"]),
            status=str(record["status"]),
            owner=owner,
            claim_token=str(record["claim_token"]) if owner else None,
            job_id=str(record["job_id"]) if record.get("job_id") else None,
            result=deepcopy(record.get("result")),
            error=str(record["error"]) if record.get("error") else None,
            lease_expires_at=str(record["lease_expires_at"]),
            execution_started_at=(
                str(record["execution_started_at"])
                if record.get("execution_started_at")
                else None
            ),
        )


RpgForegroundSubmissionStore = InMemoryForegroundSubmissionStore


def submission_store_for_job_store(job_store: Any) -> Any:
    if hasattr(job_store, 'database'):
        from app.apps.rpg.foundation.persistence.foreground_submissions import submission_store_for_job_store as factory
        return factory(job_store)
    return InMemoryForegroundSubmissionStore(getattr(job_store, "db_path", None))


