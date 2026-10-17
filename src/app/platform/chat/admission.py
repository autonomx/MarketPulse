"""Accept Chat turns once, for the job route and the streaming route (WP-8.5).

``admit_chat_turn`` is the one admission path: a repeated submission returns
its existing turn, a new one interrupts older active turns and is recorded
as a ``chat.generate`` job. The job route then hands the turn to the
dispatcher; the streaming route runs it with ``stream_chat_turn`` on the
request's own stream.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.jobs import CancelJobRequest
from app.jobs.models import CreateJobRequest, JobRecord, JobStatus, ResourceClass

from .generation_jobs import (
    _commit_chat_reply,
    _drop_job_cancel_event,
    _fail_job,
    _job_cancel_event,
    cancel_chat_generation_job,
    chat_submission_lock,
    existing_chat_generation_turn,
    find_chat_generation_job,
    interrupt_active_chat_generation_jobs,
    mark_chat_acceptance_failed,
)
from .models import ChatMessage, ChatSession, SendChatMessageRequest


@dataclass(frozen=True)
class ChatAdmission:
    """One accepted Chat turn; ``existing`` when the submission was already accepted."""

    session: ChatSession
    user_message: ChatMessage
    job: JobRecord
    existing: bool


class ChatSubmissionConflict(RuntimeError):
    """An accepted submission no longer has its user message."""


class ChatAcceptanceFailed(RuntimeError):
    """The turn was appended but its job could not be created."""


def admit_chat_turn_for_http(
    chat_store: Any, job_store: Any, session_id: str, request: SendChatMessageRequest, **options: Any,
) -> ChatAdmission:
    """``admit_chat_turn`` for a route: refusals become HTTP errors (409, 503, 404)."""
    try:
        admission = admit_chat_turn(chat_store, job_store, session_id, request, **options)
    except ChatSubmissionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ChatAcceptanceFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if admission is None:
        raise HTTPException(status_code=404, detail="chat session not found")
    return admission


def admit_chat_turn(
    chat_store: Any,
    job_store: Any,
    session_id: str,
    request: SendChatMessageRequest,
    *,
    begin_user_message: Callable[[str, SendChatMessageRequest], Any] | None = None,
    job_payload: dict[str, Any] | None = None,
    contract: str = "chat_session_v1",
) -> ChatAdmission | None:
    """Accept one Chat submission exactly once, for the job and streaming routes.

    Under the submission lock, a repeated ``user_turn_id`` returns the turn it
    already created; otherwise older active turns are interrupted, the user
    message is appended and a ``chat.generate`` job records the turn. Returns
    ``None`` when the session does not exist. A feature that admits Chat turns
    (assistant context) adds its own ``job_payload`` fields and ``contract``.
    """
    begin = begin_user_message or chat_store.begin_user_message
    with chat_submission_lock(
        session_id, request.user_turn_id, job_store=job_store, chat_store=chat_store
    ):
        existing_job = find_chat_generation_job(
            job_store, session_id=session_id, submission_id=request.user_turn_id,
        )
        if existing_job is not None:
            existing_turn = existing_chat_generation_turn(chat_store, existing_job)
            if existing_turn is None:
                raise ChatSubmissionConflict("accepted chat submission is missing its user message")
            session, user_message = existing_turn
            return ChatAdmission(session, user_message, existing_job, existing=True)
        interrupt_active_chat_generation_jobs(
            chat_store, job_store, session_id=session_id,
            reason="Interrupted by a newer Chat prompt.",
        )
        appended = begin(session_id, request)
        if appended is None:
            return None
        session, user_message = appended
        try:
            job = job_store.create_job(
                CreateJobRequest(
                    module="chatbot",
                    type="chat.generate",
                    resource_class=ResourceClass.GPU_LLM,
                    input_ref={"session_id": session.id, "message_id": user_message.id},
                    input_payload={
                        "session_id": session.id,
                        "message_id": user_message.id,
                        "submission_id": request.user_turn_id,
                        "provider_id": request.provider_id or session.provider_id,
                        "model_id": request.model_id or session.model_id,
                        "request": request.model_dump(mode="json"),
                        **(job_payload or {}),
                    },
                    compat={"contract": contract, "inline_execution": True},
                )
            )
        except Exception as exc:
            mark_chat_acceptance_failed(
                chat_store, session_id=session.id, message_id=user_message.id, error=exc,
            )
            raise ChatAcceptanceFailed("chat generation could not be queued") from exc
    return ChatAdmission(session, user_message, job, existing=False)


def stream_chat_turn(
    chat_store: Any,
    job_store: Any,
    admission: ChatAdmission,
    request: SendChatMessageRequest,
    *,
    context_items: list[dict[str, Any]] | None = None,
    annotate_reply: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Generate an admitted turn on the caller's stream and finish its job.

    Yields the provider's events. A newer submission (or a job cancel)
    interrupts the stream between events, exactly as it interrupts a
    dispatched turn; the reply is committed with the job at the provider's
    completion event, before that event is yielded. ``context_items`` are
    passed to the provider; ``annotate_reply`` adds to the reply's metadata
    before it is committed.
    """
    job = admission.job
    session, user_message = admission.session, admission.user_message
    cancel = _job_cancel_event(job.id, create=True)
    try:
        try_start = getattr(job_store, 'try_start_chat_job', job_store.mark_running)
        started = try_start(job.id)
        if started is None or started.status in {
            JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STALE,
            JobStatus.CANCEL_REQUESTED,
        }:
            yield {"type": "interrupted", "job_id": job.id}
            return
        content = ""
        metadata: dict[str, Any] = {"generation_status": "completed"}
        committed = False
        completed = None
        extra = {} if context_items is None else {"context_items": context_items}
        events = chat_store.stream_provider_reply_chunks(
            session,
            user_message,
            provider_id=request.provider_id or session.provider_id,
            model_id=request.model_id or session.model_id,
            **extra,
        )
        try:
            for event in events:
                if cancel is not None and cancel.is_set():
                    yield {"type": "interrupted", "job_id": job.id}
                    return
                if event.get("type") == "complete":
                    content = str(event.get("content") or "").strip()
                    if isinstance(event.get("metadata"), dict):
                        metadata = event["metadata"]
                    if annotate_reply is not None:
                        annotate_reply(metadata)
                    # Commit at the provider completion boundary: live voice
                    # playback can outlast generation and a later barge-in may
                    # close the HTTP body before the session event is sent.
                    completed = _commit_chat_reply(
                        chat_store, job_store, job, session_id=session.id,
                        message_id=user_message.id, content=content, metadata=metadata,
                    )
                    committed = True
                    if completed is None:
                        yield {"type": "interrupted", "job_id": job.id}
                        return
                yield event
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()
        if not committed:
            completed = _commit_chat_reply(
                chat_store, job_store, job, session_id=session.id,
                message_id=user_message.id, content=content, metadata=metadata,
            )
            if completed is None:
                yield {"type": "interrupted", "job_id": job.id}
                return
        # Both paths above return when the commit finds the job gone.
        assert completed is not None
        yield {"type": "session", "session": completed.model_dump(mode="json")}
    except GeneratorExit:
        # The client went away mid-turn: the turn ends like an interruption.
        _cancel_streamed_turn(chat_store, job_store, job)
        raise
    except Exception as exc:
        _fail_job(chat_store, job_store, job, exc, session_id=session.id, message_id=user_message.id)
        raise
    finally:
        _drop_job_cancel_event(job.id)


def _cancel_streamed_turn(chat_store: Any, job_store: Any, job: JobRecord) -> None:
    current = job_store.get_job(job.id)
    if current is not None and current.status not in {
        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STALE,
    }:
        cancel_chat_generation_job(
            chat_store, job_store, job.id, CancelJobRequest(reason="The Chat stream was closed."),
        )
