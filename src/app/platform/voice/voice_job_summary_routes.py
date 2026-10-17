"""Memory-bounded Voice Studio job list route."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from fastapi import APIRouter, Query

from app.jobs import JobListResponse, default_job_store

from app.jobs.projections import voice_job_projections

_ROUTE_SENTINEL = "_omnix_voice_job_summaries_registered"
VOICE_JOB_SUMMARIES_PATH = "/api/jobs/voice-summaries"
VOICE_JOB_MODULES = ("voice", "voice-cloning")
DEFAULT_VOICE_JOB_LIMIT = 40
MAX_VOICE_JOB_LIMIT = 100


def register_voice_job_summary_routes(router: APIRouter, state: Any) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)
    """Register a bounded list projection for the Voice Studio browser view."""

    @router.get(VOICE_JOB_SUMMARIES_PATH, response_model=JobListResponse)
    def voice_job_summaries(
        limit: int = Query(default=DEFAULT_VOICE_JOB_LIMIT, ge=1, le=MAX_VOICE_JOB_LIMIT),
    ) -> JobListResponse:
        jobs = _recent_voice_jobs(default_job_store(), limit)
        return JobListResponse(jobs=voice_job_projections(jobs))


def _recent_voice_jobs(store: Any, limit: int) -> list[Any]:
    """Read a bounded projection through the active job-store contract."""
    return store.list_jobs(limit=limit, modules=VOICE_JOB_MODULES)
