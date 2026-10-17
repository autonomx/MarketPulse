"""Credential-protected worker protocol, separate from browser job routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.jobs.models import ClaimJobRequest, ClaimJobResponse, CompleteJobRequest, FailJobRequest, JobRecord
from app.persistence.execution_repositories import JobClaimConflict
from app.security.service_token import require_service_token


def register_internal_jobs_routes(router: APIRouter, *, get_job_store):
    internal_router = APIRouter(
        prefix="/internal/jobs", tags=["internal-jobs"],
        dependencies=[Depends(require_service_token)],
    )

    @internal_router.post("/claim", response_model=ClaimJobResponse)
    def claim_job(request: ClaimJobRequest) -> ClaimJobResponse:
        return get_job_store().claim_next(request)

    def finalize(job_id, request, method):
        if not request.worker_id or not request.lease_token:
            raise HTTPException(status_code=422, detail="worker_lease_credentials_required")
        try:
            job = method(job_id, request)
        except JobClaimConflict as exc:
            raise HTTPException(status_code=409, detail="job_lease_conflict") from exc
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return job

    @internal_router.post("/{job_id}/complete", response_model=JobRecord)
    def complete_job(job_id: str, request: CompleteJobRequest) -> JobRecord:
        return finalize(job_id, request, get_job_store().complete_job)

    @internal_router.post("/{job_id}/fail", response_model=JobRecord)
    def fail_job(job_id: str, request: FailJobRequest) -> JobRecord:
        return finalize(job_id, request, get_job_store().fail_job)

    router.include_router(internal_router)
