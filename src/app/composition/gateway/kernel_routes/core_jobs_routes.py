"""Core jobs routes with explicitly injected store factories."""

from __future__ import annotations

import logging

import asyncio
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.platform.chat.generation_jobs import cancel_chat_generation_job
from app.jobs.models import (
    CancelJobRequest,
    CreateJobRequest,
    JobListResponse,
    JobRecord,
)
from app.jobs.projections import summarize_job
from app.runtime.pagination import MAX_PAGE_SIZE, InvalidCursor
from app.composition.gateway.kernel_routes.live_event_stream import (
    _sse_event,
    committed_event_stream,
    legacy_event_id,
    live_event_start_id,
    resilient_live_job_event_stream,
)
from .internal_jobs_routes import register_internal_jobs_routes


class RetiredWorkerProtocolResponse(BaseModel):
    detail: str


def register_core_jobs_routes(router: APIRouter, state, *, get_chat_store, get_job_store):
    register_internal_jobs_routes(router, get_job_store=get_job_store)
    @router.post("/api/jobs", response_model=JobRecord, tags=["jobs"])
    def create_job(request: CreateJobRequest) -> JobRecord:
        if request.type in {"chat.generate", "rpg.turn.foreground_record"} or (
            {"record_only", "inline_execution", "execution_owner", "foreground_record", "direct_foreground_route"}
            & request.compat.keys()
        ):
            raise HTTPException(status_code=422, detail="job_execution_authority_is_server_owned")
        registry = getattr(state, "job_handler_registry", None)
        if registry is not None:
            try:
                request = registry.validate_submission(request)
            except ValueError as exc:
                # Includes pydantic validation of the job input model.
                raise HTTPException(status_code=422, detail=f"job_input_invalid:{type(exc).__name__}") from exc
        from app.persistence.module_states import ModuleNotAcceptingWork

        job_store = get_job_store()
        idempotency_key = str((request.compat or {}).get("idempotency_key") or "").strip()
        try:
            if idempotency_key and hasattr(job_store, "create_job_once"):
                return job_store.create_job_once(request, idempotency_key=idempotency_key)
            return job_store.create_job(request)
        except ModuleNotAcceptingWork as exc:
            raise HTTPException(status_code=503, detail=f"module_{exc.state.state}", headers={"Retry-After": "30"}) from exc

    @router.get("/api/jobs", response_model=JobListResponse, tags=["jobs"])
    def list_jobs(
        limit: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
        full: bool = False,
        status: str | None = Query(default=None, max_length=40),
        job_type: Annotated[str | None, Query(alias="type", max_length=200)] = None,
        module: str | None = Query(default=None, max_length=100),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> JobListResponse:
        """One page of jobs, newest first; follow ``next_cursor`` (WP-5.5)."""
        try:
            page = get_job_store().list_job_page(
                limit=limit,
                status=status,
                job_types=(job_type,) if job_type else None,
                modules=(module,) if module else None,
                cursor=cursor,
            )
        except InvalidCursor as error:
            raise HTTPException(status_code=400, detail="invalid_cursor") from error
        if full:
            return page
        return page.model_copy(update={"jobs": [summarize_job(job) for job in page.jobs]})

    @router.get(
        "/events",
        response_model=None,
        responses={
            200: {
                "description": "Committed job lifecycle events as a resumable Server-Sent Events stream.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
        tags=["jobs"],
    )
    async def events(
        # An opaque cursor: "<tx_id>:<id>" (WP-5.4) or a legacy integer id.
        after_id: str | None = Query(default=None, max_length=64),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        job_store = await asyncio.to_thread(get_job_store)
        database = getattr(job_store, "database", None)
        if database is not None:
            # One reader per process and workspace, woken by NOTIFY (WP-5.4).
            from app.events.event_reader import EventCursor, EventReaders
            from app.runtime.tenant_context import current_tenant

            readers = getattr(state, "event_readers", None)
            if readers is None:
                readers = state.event_readers = EventReaders(database)
            start = EventCursor.parse(last_event_id) or EventCursor.parse(after_id)
            return StreamingResponse(
                committed_event_stream(readers.for_tenant(current_tenant()), start),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        start_id = await asyncio.to_thread(
            live_event_start_id,
            job_store,
            after_id=legacy_event_id(after_id),
            last_event_id=last_event_id,
        )
        return StreamingResponse(
            resilient_live_job_event_stream(job_store, after_id=start_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @router.get(
        "/api/jobs/events",
        response_model=None,
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "Job lifecycle events as Server-Sent Events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
        tags=["jobs"],
    )
    def job_events(after_id: int = 0, limit: int = 100) -> StreamingResponse:
        events = get_job_store().list_events(after_id=after_id, limit=limit)

        def generate():
            for event in events:
                yield _sse_event(
                    event.event_type, event.model_dump(mode="json"), event_id=event.id
                )

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/api/jobs/{job_id}", response_model=JobRecord, tags=["jobs"])
    def get_job(job_id: str) -> JobRecord:
        job = get_job_store().get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return job

    @router.post(
        "/api/jobs/claim",
        response_model=RetiredWorkerProtocolResponse,
        status_code=410,
        tags=["jobs"],
        deprecated=True,
    )
    @router.post(
        "/api/jobs/{job_id}/complete",
        response_model=RetiredWorkerProtocolResponse,
        status_code=410,
        tags=["jobs"],
        deprecated=True,
    )
    @router.post(
        "/api/jobs/{job_id}/fail",
        response_model=RetiredWorkerProtocolResponse,
        status_code=410,
        tags=["jobs"],
        deprecated=True,
    )
    def retired_worker_protocol(job_id: str | None = None) -> RetiredWorkerProtocolResponse:
        logging.getLogger(__name__).warning("Retired public job worker endpoint requested")
        return RetiredWorkerProtocolResponse(detail="worker_protocol_moved_to_internal_jobs")

    @router.post("/api/jobs/{job_id}/cancel", response_model=JobRecord, tags=["jobs"])
    def cancel_job(job_id: str, request: CancelJobRequest) -> JobRecord:
        job_store = get_job_store()
        current = job_store.get_job(job_id)
        if current is not None and current.type == "chat.generate":
            job = cancel_chat_generation_job(
                get_chat_store(),
                job_store,
                job_id,
                request,
            )
        else:
            job = job_store.cancel_job(job_id, request)
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return job
