"""PostgreSQL-backed chat runtime services."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from app.caching.bounded_cache import bounded_lru_cache
from typing import Any, cast

from app.platform.chat.assistant_turns import default_assistant_turn_coordinator
from app.platform.chat.character_store import (
    _CharacterSessionMixin,
    _find_idempotent_user_turn,
    _start_assistant_turn,
    _store_database,
)
from app.platform.chat.compaction import ConversationSummary
from app.persistence.document_schemas import register_document_schema
from app.platform.chat.history_search import HistorySearchResult, HistorySearchStatus
from app.platform.chat.memory_port import parse_memory_command
from app.platform.chat.models import ChatMessage, ChatSession, ChatSessionListResponse, SendChatMessageRequest
from app.platform.chat.prompt_assembly import PromptHistoryItem
from app.platform.chat.prompt_store import ChatSessionStore as _PromptChatSessionStore
from app.platform.chat.prompt_store import turn_completed_event
from app.platform.chat.retention_policy import transcript_retention_allowed
from app.platform.chat.store import _context_source_summaries
from app.conversation.contracts import (
    AcceptedChatActivityRecorder,
    LIVE_VOICE_ROUTE_METADATA_KEY,
    LiveVoiceChatPort,
)
from app.observability.tts_stream_diagnostics import stream_log
from app.persistence.database import PostgresDatabase, default_database
from app.persistence.document_store import PostgresDocumentStore
from app.persistence.transaction_binding import after_commit, share_transaction
from app.persistence.unit_of_work import unit_of_work
from app.security.tenant_context import RequestTenant

from .chat_store import PostgresChatRepositoryAdapter


@contextmanager
def _durable_session_mutation(store, session_id):
    adapter = store._repository
    with unit_of_work(adapter.database) as work:
        started = time.perf_counter()
        work.connection.execute(
            'SELECT id FROM omnix_chat_sessions WHERE id = %s AND workspace_id = %s FOR UPDATE',
            (session_id, adapter.context.workspace_id),
        )
        with share_transaction(work):
            yield (time.perf_counter() - started) * 1000
        work.commit()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _assistant_message_id(session_id: str, user_message_id: str) -> str:
    """Return one stable assistant message ID for an originating user turn."""
    identity = f"omnix-live-assistant:{session_id}:{user_message_id}"
    return f"msg:{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


def _load_single_session(store: Any, session_id: str) -> ChatSession | None:
    """Load only the requested session, paging through its complete transcript."""
    adapter = store._repository
    with unit_of_work(adapter.database) as work:
        record = work.chats.get_session(adapter.context, session_id)
        if record is None:
            work.rollback()
            return None
        messages = adapter._list_all_messages(work, session_id)
        session = adapter._to_session(record, messages)
        work.rollback()
    return session


def _load_session_window(store: Any, session_id: str) -> ChatSession | None:
    """Load only the newest part of the transcript a prompt can read (WP-5.7)."""
    return store._repository.get_session_window(session_id)


def _persist_user_turn(store: Any, session: ChatSession, message: ChatMessage) -> bool:
    """Update active session routing fields and append exactly one user message."""
    adapter = store._repository
    with unit_of_work(adapter.database) as work:
        updated = work.connection.execute(
            """
            UPDATE omnix_chat_sessions
               SET title = %s,
                   provider_id = %s,
                   model_id = %s
             WHERE id = %s
               AND workspace_id = %s
               AND status = 'active'
            RETURNING id
            """,
            (
                session.title,
                session.provider_id,
                session.model_id,
                session.id,
                adapter.context.workspace_id,
            ),
        ).fetchone()
        if updated is None:
            work.rollback()
            return False
        work.chats.append_message(
            adapter.context,
            session.id,
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
                "metadata": dict(message.metadata),
            },
        )
        work.commit()
    return True


def _persist_assistant_completion(
    store: Any,
    session: ChatSession,
    user_message: ChatMessage,
    *,
    content: str,
    metadata: dict[str, Any],
    assistant_turn_id: str,
    generation_status: str,
    assistant_turn_payload: dict[str, Any] | None,
) -> tuple[bool, bool]:
    """Persist user terminal metadata and at most one assistant reply atomically."""
    adapter = store._repository
    user_metadata = dict(user_message.metadata)
    user_metadata["generation_status"] = generation_status
    if assistant_turn_payload is not None:
        user_metadata["assistant_turn"] = assistant_turn_payload

    assistant_metadata = {
        **metadata,
        "segment_id": session.active_segment_id,
        "generation_status": generation_status,
    }
    if assistant_turn_id:
        assistant_metadata["assistant_turn_id"] = assistant_turn_id
    if generation_status == "interrupted":
        assistant_metadata["delivery_status"] = "interrupted"

    assistant_id = _assistant_message_id(session.id, user_message.id)
    generated = content.strip()
    allow_transcript = getattr(store, 'transcript_retention_allowed', transcript_retention_allowed)(session)
    with unit_of_work(adapter.database) as work:
        updated = work.connection.execute(
            """
            UPDATE omnix_chat_messages
               SET metadata = %s::jsonb
             WHERE id = %s
               AND workspace_id = %s
               AND session_id = %s
               AND role = 'user'
            RETURNING id
            """,
            (
                _json(user_metadata),
                user_message.id,
                adapter.context.workspace_id,
                session.id,
            ),
        ).fetchone()
        if updated is None:
            work.rollback()
            return False, False

        existing = work.connection.execute(
            """
            SELECT id
              FROM omnix_chat_messages
             WHERE workspace_id = %s
               AND session_id = %s
               AND (
                    id = %s
                    OR (
                        role = 'assistant'
                        AND metadata->>'assistant_turn_id' = %s
                    )
               )
             LIMIT 1
            """,
            (
                adapter.context.workspace_id,
                session.id,
                assistant_id,
                assistant_turn_id,
            ),
        ).fetchone()
        assistant_already_present = existing is not None
        assistant_appended = False
        if generated and allow_transcript and not assistant_already_present:
            work.chats.append_message(
                adapter.context,
                session.id,
                {
                    "id": assistant_id,
                    "role": "assistant",
                    "content": generated,
                    "created_at": _utcnow(),
                    "metadata": assistant_metadata,
                },
            )
            assistant_appended = True
        else:
            work.connection.execute(
                """
                UPDATE omnix_chat_sessions
                   SET revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s
                   AND workspace_id = %s
                   AND status = 'active'
                """,
                (session.id, adapter.context.workspace_id),
            )
        work.commit()
    return assistant_appended, assistant_already_present


def _completed_session_snapshot(
    session: ChatSession,
    user_message: ChatMessage,
    *,
    content: str,
    metadata: dict[str, Any],
    assistant_turn_id: str,
    generation_status: str,
    assistant_turn_payload: dict[str, Any] | None,
    assistant_appended: bool,
) -> ChatSession:
    """Update the already-loaded session for the normal append-success path."""
    user_metadata = dict(user_message.metadata)
    user_metadata["generation_status"] = generation_status
    if assistant_turn_payload is not None:
        user_metadata["assistant_turn"] = assistant_turn_payload
    user_message.metadata = user_metadata

    if assistant_appended:
        assistant_metadata = {
            **metadata,
            "segment_id": session.active_segment_id,
            "generation_status": generation_status,
        }
        if assistant_turn_id:
            assistant_metadata["assistant_turn_id"] = assistant_turn_id
        if generation_status == "interrupted":
            assistant_metadata["delivery_status"] = "interrupted"
        assistant_message = ChatMessage(
            id=_assistant_message_id(session.id, user_message.id),
            role="assistant",
            content=content.strip(),
            created_at=_utcnow(),
            metadata=assistant_metadata,
        )
        session.messages.append(assistant_message)
        session.message_count = (
            session.message_count + 1 if session.transcript_is_window else len(session.messages)
        )
        session.updated_at = assistant_message.created_at
    return session


def _begin_user_message_fast(
    self: PostgresCharacterChatSessionStore,
    session_id: str,
    request: SendChatMessageRequest,
    *,
    context_items: list[dict[str, Any]] | None = None,
    context_diagnostics: dict[str, Any] | None = None,
    route_metadata: dict[str, Any] | None = None,
    start_streaming: bool = False,
) -> tuple[ChatSession, ChatMessage] | None:
    started = time.perf_counter()
    load_started = time.perf_counter()
    session = _load_session_window(self, session_id)
    load_ms = (time.perf_counter() - load_started) * 1000.0
    if session is None:
        return None

    existing = _find_idempotent_user_turn(session, request.user_turn_id)
    if (
        existing is None
        and request.user_turn_id
        and session.transcript_is_window
        and self._repository.find_user_turn(session_id, request.user_turn_id) is not None
    ):
        # A retry of a turn older than the window: answer with the whole transcript.
        existing = _find_idempotent_user_turn(_load_single_session(self, session_id), request.user_turn_id)
    if existing is not None:
        stream_log(
            "gateway-live-chat-first-token",
            "runtime",
            "live_chat_user_turn_fast_path_idempotent",
            load_ms=round(load_ms, 3),
            total_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )
        return existing

    now = _utcnow()
    turn_context = context_items or []
    context_sources = _context_source_summaries(turn_context)
    message_metadata: dict[str, Any] = {
        "generation_status": "running",
        "agent_mode": request.agent_mode,
        "coding_approval_policy": request.coding_approval_policy,
    }
    if route_metadata is not None:
        message_metadata[LIVE_VOICE_ROUTE_METADATA_KEY] = dict(route_metadata)
    if request.image_data_urls:
        message_metadata["image_data_urls"] = list(request.image_data_urls)
        message_metadata["image_data_url"] = request.image_data_urls[0]
    if request.text_attachment:
        message_metadata["text_attachment"] = request.text_attachment.model_dump()
    if request.research_mode is not None:
        message_metadata["research_mode"] = request.research_mode
    if request.workspace_root:
        message_metadata["workspace_root"] = request.workspace_root
    if context_sources:
        message_metadata["context_sources"] = context_sources
    if context_diagnostics:
        message_metadata["context_diagnostics"] = context_diagnostics

    message = ChatMessage(
        id=f"msg:{uuid.uuid4().hex}",
        role="user",
        content=request.content.strip(),
        created_at=now,
        metadata=message_metadata,
    )
    command = parse_memory_command(message.content)
    if command is not None:
        message.metadata["memory_command"] = command.model_dump(mode="json")

    coordinator_started = time.perf_counter()
    database = _store_database(self)
    if database is None:
        _start_assistant_turn(session, message, request, streaming=start_streaming)
    else:
        _start_assistant_turn(
            session,
            message,
            request,
            database=database,
            streaming=start_streaming,
        )
    message.metadata["segment_id"] = session.active_segment_id
    coordinator_ms = (time.perf_counter() - coordinator_started) * 1000.0

    session.messages.append(message)
    appended_count = session.message_count + 1 if session.transcript_is_window else len(session.messages)
    if route_metadata is not None:
        session.provider_id = route_metadata["provider_id"]
        session.model_id = route_metadata["model_id"]
    else:
        session.provider_id = request.provider_id or session.provider_id
        session.model_id = request.model_id or session.model_id
    session.message_count = appended_count
    if session.title.strip().lower() in {"new chat", "new chat..."}:
        session.title = message.content[:48] or "New chat"
    session.updated_at = now

    persist_started = time.perf_counter()
    try:
        persisted = _persist_user_turn(self, session, message)
    except Exception as exc:
        stream_log(
            "gateway-live-chat-first-token",
            "runtime",
            "live_chat_user_turn_fast_path_failed",
            error_type=type(exc).__name__,
            load_ms=round(load_ms, 3),
            coordinator_ms=round(coordinator_ms, 3),
            total_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )
        raise
    persist_ms = (time.perf_counter() - persist_started) * 1000.0
    if not persisted:
        return None

    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_user_turn_fast_path_completed",
        load_ms=round(load_ms, 3),
        coordinator_ms=round(coordinator_ms, 3),
        persist_ms=round(persist_ms, 3),
        total_ms=round((time.perf_counter() - started) * 1000.0, 3),
        session_message_count=session.message_count,
    )
    return session, message


def _complete_streamed_reply_fast(
    self: PostgresCharacterChatSessionStore,
    session_id: str,
    user_message_id: str,
    content: str,
    metadata: dict[str, Any],
    *,
    lock_wait_ms: float = 0.0,
) -> ChatSession | None:
    """Complete one streamed reply without a workspace-wide compatibility save."""
    started = time.perf_counter()
    stage = "load_session"
    try:
        session = _load_session_window(self, session_id)
        if session is None:
            return None
        user_message = next(
            (message for message in session.messages if message.id == user_message_id),
            None,
        )
        if user_message is None and session.transcript_is_window:
            session = _load_single_session(self, session_id)
            user_message = next(
                (message for message in session.messages if message.id == user_message_id),
                None,
            ) if session is not None else None
            if session is None:
                return None
        if user_message is None:
            stream_log(
                "gateway-live-chat-completion",
                "runtime",
                "live_chat_assistant_completion_missing_user",
                session_found=True,
            )
            return session

        assistant_turn_id = str(
            user_message.metadata.get("assistant_turn_id")
            or metadata.get("assistant_turn_id")
            or ""
        ).strip()
        database = _store_database(self)
        coordinator = (
            default_assistant_turn_coordinator(database)
            if database is not None
            else default_assistant_turn_coordinator()
        )
        turn = coordinator.get(assistant_turn_id) if assistant_turn_id else None
        if turn is not None and not turn.terminal:
            coordinator.try_complete(assistant_turn_id)
            turn = coordinator.get(assistant_turn_id)
        if turn is not None and turn.lifecycle == "interrupted":
            generation_status = "interrupted"
            coordinator.mark_provider_cancelled(assistant_turn_id)
            turn = coordinator.get(assistant_turn_id)
        elif turn is not None and turn.lifecycle == "failed":
            generation_status = "failed"
        else:
            generation_status = "completed"

        stage = "persist_completion"
        persist_started = time.perf_counter()
        assistant_appended, assistant_already_present = _persist_assistant_completion(
            self,
            session,
            user_message,
            content=content,
            metadata=dict(metadata),
            assistant_turn_id=assistant_turn_id,
            generation_status=generation_status,
            assistant_turn_payload=(
                turn.model_dump(mode="json") if turn is not None else None
            ),
        )
        persist_ms = (time.perf_counter() - persist_started) * 1000.0
        if assistant_already_present:
            stage = "reload_session"
            completed = _load_session_window(self, session_id)
            if completed is None:
                return None
        else:
            completed = _completed_session_snapshot(
                session,
                user_message,
                content=content,
                metadata=metadata,
                assistant_turn_id=assistant_turn_id,
                generation_status=generation_status,
                assistant_turn_payload=(
                    turn.model_dump(mode="json") if turn is not None else None
                ),
                assistant_appended=assistant_appended,
            )
        if generation_status == "completed":
            stage = "post_turn_maintenance"
            maintenance_started = time.perf_counter()
            after_commit(self._repository.database, lambda: self._run_post_turn_maintenance(completed, user_message_id))
            maintenance_ms = (time.perf_counter() - maintenance_started) * 1000.0
        else:
            maintenance_ms = 0.0

        stream_log(
            "gateway-live-chat-completion",
            "runtime",
            "live_chat_assistant_completion_fast_path_completed",
            assistant_appended=assistant_appended,
            assistant_already_present=assistant_already_present,
            content_chars=len(content.strip()),
            generation_status=generation_status,
            lock_wait_ms=round(lock_wait_ms, 3),
            persist_ms=round(persist_ms, 3),
            post_turn_maintenance_ms=round(maintenance_ms, 3),
            total_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )
        return completed
    except Exception as exc:
        stream_log(
            "gateway-live-chat-completion",
            "runtime",
            "live_chat_assistant_completion_fast_path_failed",
            stage=stage,
            error_type=type(exc).__name__,
            content_chars=len(content.strip()),
            total_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )
        raise


_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")


class PostgresConversationSummaryRepository:
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.documents = PostgresDocumentStore(database)

    def save(self, summary: ConversationSummary) -> ConversationSummary:
        existing = self._by_through(summary.session_id, summary.through_message_id)
        if existing is not None:
            return existing
        latest = self.latest(summary.session_id)
        stored = summary.model_copy(update={"revision": (latest.revision if latest else 0) + 1})
        self.documents.write(
            stored.model_dump(mode="json"),
            module="chat",
            record_type="conversation-summary",
            record_id=stored.id,
        )
        return stored

    def latest(self, session_id: str) -> ConversationSummary | None:
        records = [
            ConversationSummary.model_validate(payload)
            for _, payload, _ in self.documents.list_for_session(
                module="chat", record_type="conversation-summary", session_id=session_id
            )
            if isinstance(payload, dict)
        ]
        records.sort(key=lambda item: (item.revision, item.created_at, item.id), reverse=True)
        return records[0] if records else None

    def _by_through(self, session_id: str, through_message_id: str) -> ConversationSummary | None:
        for _, payload, _ in self.documents.list_for_session(
            module="chat", record_type="conversation-summary", session_id=session_id
        ):
            if not isinstance(payload, dict):
                continue
            if payload.get("through_message_id") == through_message_id:
                return ConversationSummary.model_validate(payload)
        return None


class PostgresHistorySearchService:
    context = RequestTenant()
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def ensure_index(self) -> HistorySearchStatus:
        with self.database.connection() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM omnix_chat_messages WHERE workspace_id = %s",
                    (self.context.workspace_id,),
                ).fetchone()[0]
            )
        return HistorySearchStatus(available=True, reason="postgresql_ready", indexed_messages=count)

    def sync_index(self) -> HistorySearchStatus:
        return self.ensure_index()

    def search(
        self,
        query: str,
        *,
        profile_id: str,
        workspace_id: str,
        project_id: str | None,
        exclude_session_id: str | None = None,
        limit: int = 6,
    ) -> HistorySearchResult:
        terms = list(dict.fromkeys(term.casefold() for term in _TERM_PATTERN.findall(query)))[:12]
        # No per-search COUNT(*) of the workspace (WP-5.7); ensure_index()
        # still reports the count when asked for status.
        status = HistorySearchStatus(available=True, reason="postgresql_full_text")
        if not terms or workspace_id != self.context.workspace_id:
            return HistorySearchResult(items=[], query_terms=terms, status=status)
        clauses = [
            "message.workspace_id = %s",
            "session.profile_id = %s",
            "COALESCE(session.project_id, '') = %s",
            "message.role IN ('user', 'assistant')",
        ]
        params: list[Any] = [self.context.workspace_id, profile_id, project_id or ""]
        if exclude_session_id:
            clauses.append("message.session_id <> %s")
            params.append(exclude_session_id)
        # Word-prefix match on the full-text index (migration 0110). Terms are
        # [A-Za-z0-9_] only, so they cannot carry tsquery operators.
        clauses.append("to_tsvector('simple', message.content) @@ to_tsquery('simple', %s)")
        params.append(" | ".join(f"{term}:*" for term in terms))
        params.append(max(0, min(int(limit), 50)))
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT message.id, message.session_id, message.role,
                       message.content, message.created_at
                  FROM omnix_chat_messages AS message
                  JOIN omnix_chat_sessions AS session ON session.id = message.session_id
                 WHERE """
                + " AND ".join(clauses)
                + " ORDER BY message.created_at DESC, message.id ASC LIMIT %s",
                tuple(params),
            ).fetchall()
        return HistorySearchResult(
            items=[
                PromptHistoryItem(
                    session_id=str(row[1]),
                    message_id=str(row[0]),
                    role=cast(Any, str(row[2])),
                    content=str(row[3]),
                    created_at=row[4].isoformat(),
                )
                for row in rows
            ],
            query_terms=terms,
            status=status,
        )


class PostgresChatSessionStore(_PromptChatSessionStore):
    """Preserve chat orchestration while making PostgreSQL the transcript authority."""

    _durable_chat_mutations = True

    def _publish_turn_completed(self, session: ChatSession, user_message_id: str) -> None:
        """Append ``chat.turn.completed`` to the outbox, once per user message (PA-3.4).

        Called after the turn's own transaction commits, as the memory call it
        replaces was; the outbox then delivers it to every consumer.
        """
        from app.platform.chat.turn_events import CHAT_TURN_COMPLETED, turn_completed_event_key

        context = self._repository.context
        event = turn_completed_event(session, user_message_id, user_id=context.user_id)
        with unit_of_work(self._repository.database) as work:
            work.outbox.append(
                context,
                aggregate_type="chat_session",
                aggregate_id=session.id,
                event_type=CHAT_TURN_COMPLETED,
                payload=event.model_dump(mode="json"),
                event_key=turn_completed_event_key(user_message_id),
                skip_existing=True,
            )
            work.commit()

    def __init__(
        self,
        path: Any = None,
        *,
        memory_service_factory: Callable[[], Any] | None = None,
        memory_settings_factory: Callable[[], Any] | None = None,
        history_search_factory: Callable[[], PostgresHistorySearchService] = PostgresHistorySearchService,
        summary_repository_factory: Callable[[], PostgresConversationSummaryRepository] = PostgresConversationSummaryRepository,
        job_service: Any | None = None,
        live_voice_chat_port: LiveVoiceChatPort | None = None,
        live_agent_planner: Any | None = None,
        accepted_chat_activity_recorder: AcceptedChatActivityRecorder | None = None,
    ) -> None:
        if path is not None:
            raise RuntimeError("file-backed chat authority is retired; use the legacy importer")
        self.path = None
        self.memory_service_factory = memory_service_factory or _missing_memory_service
        self.memory_settings_factory = memory_settings_factory or _missing_memory_settings
        self.history_search_factory = history_search_factory
        self.summary_repository_factory = summary_repository_factory
        self.job_service = job_service
        self.live_voice_chat_port = live_voice_chat_port
        if live_agent_planner is None:
            from app.platform.chat.live_agent_store import default_live_agent_planner

            live_agent_planner = default_live_agent_planner()
        self.live_agent_planner = live_agent_planner
        self.accepted_chat_activity_recorder = accepted_chat_activity_recorder
        self._repository = PostgresChatRepositoryAdapter()
        self._initialize_prompt_context_cache()

    def transcript_retention_allowed(self, session):
        from app.platform.chat.retention_policy import transcript_retention_allowed

        return transcript_retention_allowed(
            session,
            settings=self.memory_settings_factory(),
        )

    def list_sessions(
        self, *, limit: int = 100, cursor: str | None = None
    ) -> ChatSessionListResponse:
        sessions, next_cursor = self._repository.list_session_summaries(
            limit=limit,
            cursor=cursor,
        )
        return ChatSessionListResponse(
            sessions=sessions,
            next_cursor=next_cursor,
        )

    def get_session(self, session_id: str) -> ChatSession | None:
        return self._repository.get_session(session_id)

    def get_session_window(self, session_id: str, *, through_message_id: str | None = None) -> ChatSession | None:
        """The newest part of the transcript a prompt can read (WP-5.7)."""
        return self._repository.get_session_window(session_id, through_message_id=through_message_id)

    def _save_session(self, session: ChatSession) -> None:
        self._repository.save_session(session)

    def clear_memory_snapshots_for_owner(self, owner_type: str, owner_id: str) -> int:
        return self._repository.clear_memory_snapshots_for_owner(owner_type, owner_id)

    def _save_created_session(self, session: ChatSession) -> None:
        self._repository.create_session(session)

    def delete_session(self, session_id: str) -> bool:
        return self._repository.delete_session(session_id)

    def update_delivery_metadata(
        self,
        *,
        session_id: str,
        assistant_turn_id: str,
        metadata: dict[str, object],
    ) -> bool:
        return self._repository.update_delivery_metadata(
            session_id=session_id,
            assistant_turn_id=assistant_turn_id,
            metadata=metadata,
        )

    def update_user_message_metadata(
        self,
        *,
        session_id: str,
        message_id: str,
        metadata: dict[str, object],
    ) -> bool:
        return self._repository.update_user_message_metadata(
            session_id=session_id,
            message_id=message_id,
            metadata=metadata,
        )

    def update_message_metadata(
        self,
        *,
        session_id: str,
        message_id: str,
        metadata: dict[str, object],
    ) -> bool:
        return self._repository.update_message_metadata(
            session_id=session_id,
            message_id=message_id,
            metadata=metadata,
        )

    def delete_messages(self, session_id: str, message_ids: list[str]) -> int:
        return self._repository.delete_messages(session_id, message_ids)

    def remove_assistant_reply(
        self, session_id: str, user_message_id: str
    ) -> ChatSession | None:
        session = self.get_session(session_id)
        if session is None:
            return None
        self._repository.remove_assistant_reply(session_id, user_message_id)
        return self.get_session(session_id)

    def _sessions_for_history_search(self):
        return None


class PostgresCharacterChatSessionStore(_CharacterSessionMixin, PostgresChatSessionStore):
    def get_session(self, session_id: str) -> ChatSession | None:
        session = _load_single_session(self, session_id)
        if session is not None:
            from app.platform.chat.live_chat_speculation import prime_live_speculation_session

            prime_live_speculation_session(session)
        return session

    def begin_user_message(
        self,
        session_id: str,
        request: SendChatMessageRequest,
        *,
        context_items: list[dict[str, Any]] | None = None,
        context_diagnostics: dict[str, Any] | None = None,
        start_streaming: bool = False,
    ) -> tuple[ChatSession, ChatMessage] | None:
        from app.platform.chat.live_chat_speculation import prime_live_speculation_session

        with _durable_session_mutation(self, session_id):
            persist = lambda routed_request, route_metadata: _begin_user_message_fast(
                self,
                session_id,
                routed_request,
                context_items=context_items,
                context_diagnostics=context_diagnostics,
                route_metadata=route_metadata,
                start_streaming=start_streaming,
            )
            live_voice = getattr(self, "live_voice_chat_port", None)
            if live_voice is None:
                result = super().begin_user_message(
                    session_id,
                    request,
                    context_items=context_items,
                    context_diagnostics=context_diagnostics,
                    start_streaming=start_streaming,
                )
            else:
                result = live_voice.begin_routed_user_message(
                    self,
                    session_id,
                    request,
                    persist=persist,
                )
        if result is not None:
            prime_live_speculation_session(result[0])
        return result

    def begin_streaming_user_message(
        self,
        session_id: str,
        request: SendChatMessageRequest,
    ) -> tuple[ChatSession, ChatMessage] | None:
        """Begin a streamed turn with running state in the first durable write."""
        return self.begin_user_message(session_id, request, start_streaming=True)

    def complete_streamed_reply(
        self,
        session_id: str,
        user_message_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> ChatSession | None:
        from app.platform.chat.live_chat_speculation import prime_live_speculation_session

        with _durable_session_mutation(self, session_id) as lock_wait_ms:
            session = _complete_streamed_reply_fast(
                self,
                session_id,
                user_message_id,
                content,
                metadata,
                lock_wait_ms=lock_wait_ms,
            )
        if session is not None:
            prime_live_speculation_session(session)
        return session


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_history_search_service() -> PostgresHistorySearchService:
    """Reuse readiness-checked history search state across chat turns."""
    return PostgresHistorySearchService()


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_chat_store(
    *,
    store_class: type[PostgresCharacterChatSessionStore] | None = None,
    memory_service_factory: Callable[[], Any] | None = None,
    memory_settings_factory: Callable[[], Any] | None = None,
    job_service: Any | None = None,
    live_voice_chat_port: LiveVoiceChatPort | None = None,
    live_agent_planner: Any | None = None,
    accepted_chat_activity_recorder: AcceptedChatActivityRecorder | None = None,
) -> PostgresCharacterChatSessionStore:
    """Reuse the authoritative chat store instead of re-running startup checks per request."""
    store_kwargs: dict[str, Any] = {
        "history_search_factory": default_history_search_service,
        "memory_service_factory": memory_service_factory,
        "memory_settings_factory": memory_settings_factory,
        "job_service": job_service,
        "live_agent_planner": live_agent_planner,
    }
    if live_voice_chat_port is not None:
        store_kwargs["live_voice_chat_port"] = live_voice_chat_port
    if accepted_chat_activity_recorder is not None:
        store_kwargs["accepted_chat_activity_recorder"] = accepted_chat_activity_recorder
    return (store_class or PostgresCharacterChatSessionStore)(**store_kwargs)


def _missing_memory_service() -> Any:
    raise RuntimeError("assistant-memory service was not supplied by the composition root")


def _missing_memory_settings() -> Any:
    raise RuntimeError("assistant-memory settings were not supplied by the composition root")


def reset_default_chat_runtime_caches() -> None:
    """Clear process-resident defaults for isolated tests and controlled restarts."""
    default_chat_store.cache_clear()
    default_history_search_service.cache_clear()


# Document shapes (WP-5.9).
register_document_schema("chat", "conversation-summary", ConversationSummary)


def production_summary_repository() -> PostgresConversationSummaryRepository:
    from app.persistence.repository_registry import register_feature_repositories

    register_feature_repositories("chat")
    return PostgresConversationSummaryRepository()
