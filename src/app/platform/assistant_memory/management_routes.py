"""Internal browser-facing memory management routes."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query

from app.runtime.pagination import MAX_PAGE_SIZE

from app.conversation.contracts import TranscriptReader

from .controls import (
    MemoryExportResponse,
    MemoryResetResponse,
    RecentAutomaticMemoryResponse,
    export_owner_memory,
    recent_automatic_memories,
    reset_owner_memory,
    set_memory_archived,
    undo_automatic_memory,
)
from .management import (
    CandidateCleanupRequest,
    CandidateResolutionRequest,
    CreateManagedMemoryRequest,
    ForgetCandidateResponse,
    ForgetMemoryResponse,
    MemoryCandidateListResponse,
    MemoryListResponse,
    MoveManagedMemoryRequest,
    RevisionedMemoryRequest,
    UpdateManagedMemoryRequest,
    candidates_for_session,
    is_candidate_visible,
    records_for_session,
    require_memory_write,
    resolve_session_scope,
    session_record,
)
from app.conversation.memory_contracts import MemoryCandidate, MemoryCategory, MemoryRecord, MemoryScope
from .observability import MemoryUsageResponse, memory_usage_snapshot
from .paging import iter_records
from .repository import MemoryConflictError, MemoryNotFoundError
from .owner_defaults import default_memory_service
from .service import MemoryPolicyError, MemoryService


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _translate_memory_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MemoryNotFoundError):
        return _not_found("memory entity not found")
    if isinstance(exc, MemoryConflictError):
        return HTTPException(status_code=409, detail={"code": "memory_revision_conflict", "message": str(exc)})
    if isinstance(exc, MemoryPolicyError):
        return HTTPException(status_code=403, detail={"code": "memory_policy_rejected", "message": str(exc)})
    return HTTPException(status_code=400, detail=str(exc))


def register_memory_management_routes(
    router: APIRouter,
    *,
    chat_store_factory: Callable[[], TranscriptReader],
    memory_service_factory: Callable[[], MemoryService] = default_memory_service,
) -> None:
    names = {getattr(route, "name", "") for route in router.routes}

    def read_context(session_id: str):
        store = chat_store_factory()
        session, context = resolve_session_scope(store, session_id)
        if session is None or context is None:
            raise _not_found("chat session not found")
        return store, session, context

    def write_context(session_id: str):
        store, session, context = read_context(session_id)
        try:
            require_memory_write(session)
        except Exception as exc:
            raise _translate_memory_error(exc) from exc
        return store, session, context

    if "assistant_memory_list_endpoint" not in names:

        @router.get(
            "/api/assistant/memory",
            response_model=MemoryListResponse,
            name="assistant_memory_list_endpoint",
        )
        def assistant_memory_list_endpoint(
            session_id: str,
            scope: MemoryScope | None = None,
            category: MemoryCategory | None = None,
            pinned_only: bool = False,
            query: str | None = None,
            limit: int = Query(default=100, ge=0, le=MAX_PAGE_SIZE),
            offset: int = Query(default=0, ge=0),
        ) -> MemoryListResponse:
            result = records_for_session(
                chat_store_factory(),
                memory_service_factory(),
                session_id,
                scope=scope,
                category=category,
                pinned_only=pinned_only,
                query=query,
                limit=limit,
                offset=offset,
            )
            if result is None:
                raise _not_found("chat session not found")
            return result

        @router.post(
            "/api/assistant/memory",
            response_model=MemoryRecord,
            name="assistant_memory_create_endpoint",
        )
        def assistant_memory_create_endpoint(request: CreateManagedMemoryRequest) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return memory_service_factory().create_explicit_memory(
                    context,
                    scope=request.scope,
                    category=request.category,
                    sensitivity=request.sensitivity,
                    content=request.content,
                    provenance_id=request.session_id,
                    pinned=request.pinned,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.get(
            "/api/assistant/memory/archived",
            response_model=MemoryListResponse,
            name="assistant_memory_archived_endpoint",
        )
        def assistant_memory_archived_endpoint(session_id: str) -> MemoryListResponse:
            _, _, context = read_context(session_id)
            service = memory_service_factory()
            records: list[MemoryRecord] = []
            for scope in ("global", "workspace", "project", "session"):
                scope_id = {
                    "global": context.profile_id,
                    "workspace": context.workspace_id,
                    "project": context.project_id,
                    "session": context.session_id,
                }[scope]
                if scope_id is None:
                    continue
                records.extend(
                    iter_records(
                        service.repository,
                        owner_type=context.owner_type,
                        owner_id=context.owner_id,
                        scope=scope,
                        scope_id=scope_id,
                        status="archived",
                    )
                )
            records.sort(key=lambda item: (item.scope, item.category, item.id))
            return MemoryListResponse(records=records, total=len(records), session_id=session_id)

        @router.get(
            "/api/assistant/memory/recent-automatic",
            response_model=RecentAutomaticMemoryResponse,
            name="assistant_memory_recent_automatic_endpoint",
        )
        def assistant_memory_recent_automatic_endpoint(
            session_id: str,
            limit: int = Query(default=5, ge=0, le=20),
        ) -> RecentAutomaticMemoryResponse:
            _, session, context = read_context(session_id)
            message_ids = {message.id for message in session.messages if message.role == "user"}
            return RecentAutomaticMemoryResponse(
                session_id=session_id,
                records=recent_automatic_memories(
                    memory_service_factory(),
                    context,
                    message_ids,
                    limit=limit,
                ),
            )

        @router.get(
            "/api/assistant/memory/usage",
            response_model=MemoryUsageResponse,
            name="assistant_memory_usage_endpoint",
        )
        def assistant_memory_usage_endpoint(session_id: str) -> MemoryUsageResponse:
            read_context(session_id)
            return memory_usage_snapshot(session_id)

        @router.get(
            "/api/assistant/memory/export",
            response_model=MemoryExportResponse,
            name="assistant_memory_export_endpoint",
        )
        def assistant_memory_export_endpoint(session_id: str) -> MemoryExportResponse:
            _, _, context = read_context(session_id)
            return export_owner_memory(memory_service_factory(), context)

        @router.post(
            "/api/assistant/memory/reset",
            response_model=MemoryResetResponse,
            name="assistant_memory_reset_endpoint",
        )
        def assistant_memory_reset_endpoint(session_id: str) -> MemoryResetResponse:
            store, _, context = write_context(session_id)
            try:
                return reset_owner_memory(store, memory_service_factory(), context)
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.get(
            "/api/assistant/memory/candidates/pending",
            response_model=MemoryCandidateListResponse,
            name="assistant_memory_candidates_endpoint",
        )
        def assistant_memory_candidates_endpoint(
            session_id: str,
            limit: int = Query(default=100, ge=0, le=MAX_PAGE_SIZE),
        ) -> MemoryCandidateListResponse:
            result = candidates_for_session(
                chat_store_factory(),
                memory_service_factory(),
                session_id,
                limit=limit,
            )
            if result is None:
                raise _not_found("chat session not found")
            return result

        @router.get(
            "/api/assistant/memory/{memory_id}",
            response_model=MemoryRecord,
            name="assistant_memory_read_endpoint",
        )
        def assistant_memory_read_endpoint(memory_id: str, session_id: str) -> MemoryRecord:
            session_found, record = session_record(
                chat_store_factory(),
                memory_service_factory(),
                session_id,
                memory_id,
            )
            if not session_found:
                raise _not_found("chat session not found")
            if record is None:
                raise _not_found("memory record not found")
            return record

        @router.patch(
            "/api/assistant/memory/{memory_id}",
            response_model=MemoryRecord,
            name="assistant_memory_update_endpoint",
        )
        def assistant_memory_update_endpoint(
            memory_id: str,
            request: UpdateManagedMemoryRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return memory_service_factory().edit_memory(
                    context,
                    memory_id,
                    content=request.content,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.delete(
            "/api/assistant/memory/{memory_id}",
            response_model=ForgetMemoryResponse,
            name="assistant_memory_forget_endpoint",
        )
        def assistant_memory_forget_endpoint(
            memory_id: str,
            session_id: str,
            expected_revision: int = Query(ge=1),
        ) -> ForgetMemoryResponse:
            _, _, context = write_context(session_id)
            try:
                memory_service_factory().forget_memory(
                    context,
                    memory_id,
                    expected_revision=expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc
            return ForgetMemoryResponse(memory_id=memory_id)

        @router.post(
            "/api/assistant/memory/{memory_id}/pin",
            response_model=MemoryRecord,
            name="assistant_memory_pin_endpoint",
        )
        def assistant_memory_pin_endpoint(
            memory_id: str,
            request: RevisionedMemoryRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return memory_service_factory().set_pinned(
                    context,
                    memory_id,
                    pinned=True,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.post(
            "/api/assistant/memory/{memory_id}/unpin",
            response_model=MemoryRecord,
            name="assistant_memory_unpin_endpoint",
        )
        def assistant_memory_unpin_endpoint(
            memory_id: str,
            request: RevisionedMemoryRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return memory_service_factory().set_pinned(
                    context,
                    memory_id,
                    pinned=False,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.post(
            "/api/assistant/memory/{memory_id}/move",
            response_model=MemoryRecord,
            name="assistant_memory_move_endpoint",
        )
        def assistant_memory_move_endpoint(
            memory_id: str,
            request: MoveManagedMemoryRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return memory_service_factory().move_memory(
                    context,
                    memory_id,
                    target_scope=request.target_scope,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.post(
            "/api/assistant/memory/{memory_id}/archive",
            response_model=MemoryRecord,
            name="assistant_memory_archive_endpoint",
        )
        def assistant_memory_archive_endpoint(
            memory_id: str,
            request: RevisionedMemoryRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return set_memory_archived(
                    memory_service_factory(),
                    context,
                    memory_id,
                    archived=True,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.post(
            "/api/assistant/memory/{memory_id}/restore",
            response_model=MemoryRecord,
            name="assistant_memory_restore_endpoint",
        )
        def assistant_memory_restore_endpoint(
            memory_id: str,
            request: RevisionedMemoryRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return set_memory_archived(
                    memory_service_factory(),
                    context,
                    memory_id,
                    archived=False,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.post(
            "/api/assistant/memory/{memory_id}/undo",
            response_model=ForgetMemoryResponse,
            name="assistant_memory_undo_endpoint",
        )
        def assistant_memory_undo_endpoint(
            memory_id: str,
            request: RevisionedMemoryRequest,
        ) -> ForgetMemoryResponse:
            _, session, context = write_context(request.session_id)
            message_ids = {message.id for message in session.messages if message.role == "user"}
            try:
                undo_automatic_memory(
                    memory_service_factory(),
                    context,
                    message_ids,
                    memory_id,
                    expected_revision=request.expected_revision,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc
            return ForgetMemoryResponse(memory_id=memory_id)

        @router.post(
            "/api/assistant/memory/candidates/{candidate_id}/approve",
            response_model=MemoryRecord,
            name="assistant_memory_candidate_approve_endpoint",
        )
        def assistant_memory_candidate_approve_endpoint(
            candidate_id: str,
            request: CandidateResolutionRequest,
        ) -> MemoryRecord:
            _, _, context = write_context(request.session_id)
            try:
                return memory_service_factory().approve_candidate(
                    context,
                    candidate_id,
                    pinned=request.pinned,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.post(
            "/api/assistant/memory/candidates/{candidate_id}/reject",
            response_model=MemoryCandidate,
            name="assistant_memory_candidate_reject_endpoint",
        )
        def assistant_memory_candidate_reject_endpoint(
            candidate_id: str,
            request: CandidateResolutionRequest,
        ) -> MemoryCandidate:
            _, _, context = write_context(request.session_id)
            candidate = memory_service_factory().repository.get_candidate(candidate_id)
            if candidate is None:
                raise _not_found("memory candidate not found")
            if (candidate.owner_type, candidate.owner_id) != (context.owner_type, context.owner_id):
                raise HTTPException(status_code=403, detail={"code": "candidate_scope_mismatch"})
            if not is_candidate_visible(candidate, context):
                raise HTTPException(status_code=403, detail={"code": "candidate_scope_mismatch"})
            try:
                return memory_service_factory().reject_candidate(candidate_id)
            except Exception as exc:
                raise _translate_memory_error(exc) from exc

        @router.delete(
            "/api/assistant/memory/candidates/{candidate_id}",
            response_model=ForgetCandidateResponse,
            name="assistant_memory_candidate_forget_endpoint",
        )
        def assistant_memory_candidate_forget_endpoint(
            candidate_id: str,
            request: CandidateCleanupRequest,
        ) -> ForgetCandidateResponse:
            _, _, context = write_context(request.session_id)
            try:
                memory_service_factory().delete_resolved_candidate(
                    context,
                    candidate_id,
                    expected_status=request.expected_status,
                )
            except Exception as exc:
                raise _translate_memory_error(exc) from exc
            return ForgetCandidateResponse(candidate_id=candidate_id)
