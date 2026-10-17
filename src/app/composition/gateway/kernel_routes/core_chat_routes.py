"""Core chat routes with explicitly injected store factories."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

from app.platform.chat.generation_jobs import start_chat_generation_job
from app.platform.chat.models import (
    ChatSession,
    ChatSessionListResponse,
    CreateChatSessionRequest,
    DeleteChatSessionResponse,
    SendChatMessageRequest,
    SendChatMessageResponse,
)
from app.platform.live_voice.transport.sse import OmnixStreamingResponse

if TYPE_CHECKING:
    from app.platform.chat.admission import ChatAdmission


def _chat_message_image_data_urls(metadata: object) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    values: list[str] = []
    raw = metadata.get("image_data_urls")
    if isinstance(raw, list):
        values.extend(value for value in raw if isinstance(value, str) and value)
    legacy = metadata.get("image_data_url")
    if isinstance(legacy, str) and legacy:
        values.insert(0, legacy)
    return list(dict.fromkeys(values))


def _admit(chat_store, job_store, session_id: str, request: SendChatMessageRequest,
           *, begin_user_message=None) -> "ChatAdmission":
    """Shared admission for the job and streaming routes, as HTTP errors."""
    # Imported on first use: admission is not needed to compose the gateway.
    from app.platform.chat.admission import admit_chat_turn_for_http

    return admit_chat_turn_for_http(
        chat_store, job_store, session_id, request, begin_user_message=begin_user_message,
    )


def register_core_chat_routes(router: APIRouter, *, get_chat_store, get_job_store):
    @router.get(
        "/api/chat/sessions", response_model=ChatSessionListResponse, tags=["chat"]
    )
    def chat_sessions(
        limit: int = Query(default=100, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> ChatSessionListResponse:
        try:
            return get_chat_store().list_sessions(limit=limit, cursor=cursor)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/chat/sessions", response_model=ChatSession, tags=["chat"])
    def create_chat_session(request: CreateChatSessionRequest) -> ChatSession:
        return get_chat_store().create_session(request)

    @router.get(
        "/api/chat/sessions/{session_id}", response_model=ChatSession, tags=["chat"]
    )
    def chat_session(
        session_id: str,
        include_attachments: bool = Query(default=True),
    ) -> ChatSession:
        chat_store = get_chat_store()
        if include_attachments:
            session = chat_store.get_session(session_id)
        else:
            get_session_without_attachments = getattr(
                chat_store, "get_session_without_attachments", None
            )
            session = (
                get_session_without_attachments(session_id)
                if callable(get_session_without_attachments)
                else chat_store.get_session(session_id)
            )
        if session is None:
            raise HTTPException(status_code=404, detail="chat session not found")
        return session

    @router.get(
        "/api/chat/sessions/{session_id}/attachments",
        response_model=dict[str, list[str]],
        tags=["chat"],
    )
    def chat_session_attachments(session_id: str) -> dict[str, list[str]]:
        chat_store = get_chat_store()
        get_session_attachments = getattr(chat_store, "get_session_attachments", None)
        if callable(get_session_attachments):
            attachments = get_session_attachments(session_id)
        else:
            session = chat_store.get_session(session_id)
            attachments = (
                {
                    message.id: _chat_message_image_data_urls(message.metadata)
                    for message in session.messages
                    if _chat_message_image_data_urls(message.metadata)
                }
                if session is not None
                else None
            )
        if attachments is None:
            raise HTTPException(status_code=404, detail="chat session not found")
        return attachments

    @router.delete(
        "/api/chat/sessions/{session_id}",
        response_model=DeleteChatSessionResponse,
        tags=["chat"],
    )
    def delete_chat_session(session_id: str) -> DeleteChatSessionResponse:
        if not get_chat_store().delete_session(session_id):
            raise HTTPException(status_code=404, detail="chat session not found")
        return DeleteChatSessionResponse(session_id=session_id)

    @router.post(
        "/api/chat/sessions/{session_id}/messages",
        response_model=SendChatMessageResponse,
        tags=["chat"],
    )
    def send_chat_message(
        session_id: str, request: SendChatMessageRequest
    ) -> SendChatMessageResponse:
        chat_store = get_chat_store()
        job_store = get_job_store()
        admission = _admit(chat_store, job_store, session_id, request)
        session, user_message, job = admission.session, admission.user_message, admission.job
        if admission.existing:
            return SendChatMessageResponse(session=session, user_message=user_message, job=job)
        job = start_chat_generation_job(
            chat_store=chat_store,
            job_store=job_store,
            job=job,
            request=request,
        )
        return SendChatMessageResponse(
            session=session, user_message=user_message, job=job
        )

    @router.post(
        "/api/chat/sessions/{session_id}/messages/stream",
        response_model=None,
        response_class=OmnixStreamingResponse,
        responses={
            200: {
                "description": "Chat generation events as Server-Sent Events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
        tags=["chat"],
    )
    async def stream_chat_message(
        session_id: str, request: SendChatMessageRequest
    ) -> OmnixStreamingResponse:
        chat_store = get_chat_store()
        job_store = get_job_store()
        begin_streaming = getattr(chat_store, "begin_streaming_user_message", None)
        admission = await asyncio.to_thread(
            _admit, chat_store, job_store, session_id, request,
            begin_user_message=begin_streaming if callable(begin_streaming) else None,
        )
        user_message = admission.user_message

        def event_line(event: dict[str, Any]) -> str:
            return f"data: {json.dumps(event, sort_keys=True)}\n\n"

        from app.platform.chat.admission import stream_chat_turn

        def generate():
            yield event_line({"type": "user_message", "message": user_message.model_dump(mode="json")})
            yield event_line({"type": "job", "job": admission.job.model_dump(mode="json")})
            if admission.existing:
                # A repeated submission reports the turn it already started.
                yield event_line({"type": "session", "session": admission.session.model_dump(mode="json")})
                yield event_line({"type": "done"})
                return
            try:
                for event in stream_chat_turn(chat_store, job_store, admission, request):
                    yield event_line(event)
                    if event.get("type") == "interrupted":
                        return
                yield event_line({"type": "done"})
            except Exception as exc:
                yield event_line({"type": "error", "message": str(exc) or "Chat stream failed."})

        user_turn_id = str(request.user_turn_id or "").strip() or None
        speech_segment_id = str(request.speech_segment_id or "").strip() or None
        voice_turn_id = (
            user_turn_id.removeprefix("voice-user-turn:")
            if user_turn_id and user_turn_id.startswith("voice-user-turn:")
            else None
        )
        return OmnixStreamingResponse(
            generate(),
            media_type="text/event-stream",
            eager_sync=True,
            diagnostic_context={
                "route_path": "/api/chat/sessions/{session_id}/messages/stream",
                "session_id": session_id,
                "user_turn_id": user_turn_id,
                "speech_segment_id": speech_segment_id,
                "voice_turn_id": voice_turn_id,
            },
        )
