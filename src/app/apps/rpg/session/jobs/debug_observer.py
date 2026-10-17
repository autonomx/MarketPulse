"""Optional, privacy-bounded lifecycle diagnostics for RPG jobs."""
from __future__ import annotations

from typing import Any

from app.jobs.models import JobRecord


class RpgJobDebugObserver:
    """Write lifecycle transitions through the RPG debug logging owner."""

    def on_created(self, job: JobRecord) -> None:
        self._record("created", job)

    def on_started(self, job: JobRecord) -> None:
        self._record("started", job)

    def on_completed(self, job: JobRecord) -> None:
        self._record("completed", job)

    def on_failed(self, job: JobRecord) -> None:
        self._record("failed", job)

    @staticmethod
    def _record(transition: str, job: JobRecord) -> None:
        if not job.type.startswith("rpg."):
            return
        from app.apps.rpg.foundation.debug_logging import log_rpg_event

        payload: dict[str, Any] = job.input_payload or {}
        status = getattr(job.status, "value", job.status)
        session_id = str(payload.get("session_id") or "").strip() or None
        turn_id = str(
            payload.get("turn_id") or payload.get("submission_id") or ""
        ).strip() or None
        log_rpg_event(
            f"job.{transition}",
            category="job",
            level="error" if transition == "failed" else "info",
            session_id=session_id,
            turn_id=turn_id,
            trace_id=job.id,
            fields={
                "job_id": job.id,
                "job_type": job.type,
                "status": str(status),
            },
        )


def rpg_debug_job_observer() -> RpgJobDebugObserver | None:
    from app.apps.rpg.foundation.debug_logging import rpg_debug_logging_enabled

    if not rpg_debug_logging_enabled():
        return None
    return RpgJobDebugObserver()
