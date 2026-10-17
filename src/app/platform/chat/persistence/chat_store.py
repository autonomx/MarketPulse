from __future__ import annotations

import json
import base64
from typing import Any

from app.platform.chat.models import ChatMessage, ChatSession, ChatSessionSummary
from app.runtime.pagination import MAX_PAGE_SIZE
from app.platform.chat.retention_policy import transcript_retention_allowed

from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant
from app.persistence.unit_of_work import unit_of_work
from app.persistence.repository_registry import install_repository_specs
from app.platform.chat.persistence.repository_specs import CHAT_REPOSITORY_SPECS


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# The repository's page maximum: a shorter page is the last one.
_MESSAGE_PAGE_SIZE = MAX_PAGE_SIZE


def transcript_window_complete(
    messages: list[ChatMessage],
    *,
    active_segment_id: str | None,
    through_message_id: str | None = None,
) -> bool:
    """Whether the newest ``messages`` hold everything a prompt can read (WP-5.7).

    A prompt reads at most the newest ``MAX_RECENT_MESSAGE_LIMIT`` eligible
    messages (user and assistant turns of the active segment) before the turn's
    own message, plus a deterministic summary that keeps only the last
    ``SUMMARY_TEXT_CHARS`` characters of the older ones. Once the loaded older
    messages add more than that, any older message cannot change the prompt.
    """
    from app.platform.chat.compaction import SUMMARY_TEXT_CHARS, summary_line_length
    from app.platform.chat.prompt_window import MAX_RECENT_MESSAGE_LIMIT

    if through_message_id is not None:
        index = next((i for i, message in enumerate(messages) if message.id == through_message_id), None)
        if index is None:
            return False
        messages = messages[:index]
    eligible = [
        message
        for message in messages
        if message.role in {"user", "assistant"}
        and (not active_segment_id or message.metadata.get("segment_id") == active_segment_id)
    ]
    if len(eligible) <= MAX_RECENT_MESSAGE_LIMIT:
        return False
    older = eligible[:-MAX_RECENT_MESSAGE_LIMIT]
    return sum(summary_line_length(message) for message in older) > SUMMARY_TEXT_CHARS


def _encode_session_cursor(record: dict[str, Any]) -> str:
    payload = json.dumps(
        [record["updated_at"], record["id"]], separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_session_cursor(cursor: str | None) -> tuple[str, str] | None:
    if not cursor:
        return None
    try:
        encoded = cursor.encode("ascii")
        payload = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        updated_at, session_id = json.loads(payload)
    except (UnicodeEncodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("chat session cursor is invalid") from error
    if not isinstance(updated_at, str) or not isinstance(session_id, str) or not session_id:
        raise ValueError("chat session cursor is invalid")
    return updated_at, session_id


class PostgresChatRepositoryAdapter:
    """Compatibility implementation for the current ChatStore contract.

    The public ChatStore can continue to operate while PostgreSQL remains the
    sole authority. Message history is append-only; an attempted transcript
    rewrite is rejected rather than implemented as delete/reinsert persistence.
    """
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        install_repository_specs(CHAT_REPOSITORY_SPECS)
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def list_session_summaries(
        self, *, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[ChatSessionSummary], str | None]:
        """List one bounded summary page without loading any transcript rows."""
        page_size = max(1, min(int(limit), 100))
        before = _decode_session_cursor(cursor)
        with unit_of_work(self.database) as work:
            records = work.chats.list_sessions(
                self.context,
                limit=page_size + 1,
                before_updated_at=before[0] if before else None,
                before_id=before[1] if before else None,
            )
            work.rollback()
        has_more = len(records) > page_size
        page = records[:page_size]
        next_cursor = _encode_session_cursor(page[-1]) if has_more and page else None
        return [self._to_summary(record) for record in page], next_cursor

    def get_session(self, session_id: str) -> ChatSession | None:
        """Load one transcript without hydrating the complete chat workspace."""

        with unit_of_work(self.database) as work:
            record = work.chats.get_session(self.context, session_id)
            if record is None:
                work.rollback()
                return None
            messages = self._list_all_messages(work, session_id)
            session = self._to_session(record, messages)
            work.rollback()
        return session

    def get_session_window(self, session_id: str, *, through_message_id: str | None = None) -> ChatSession | None:
        """Load the newest part of a transcript that a prompt can read (WP-5.7).

        Pages newest first until ``transcript_window_complete`` holds or the
        transcript starts. A session loaded this way says so
        (``transcript_is_window``) and saves only what follows its first message.
        """
        with unit_of_work(self.database) as work:
            record = work.chats.get_session(self.context, session_id)
            if record is None:
                work.rollback()
                return None
            session = self._to_session(record, [])
            loaded: list[dict[str, Any]] = []
            before: int | None = None
            complete = False
            while True:
                page = work.chats.list_messages_before(
                    self.context, session_id, before_position=before, limit=_MESSAGE_PAGE_SIZE
                )
                loaded[:0] = reversed(page)
                if len(page) < _MESSAGE_PAGE_SIZE:
                    break
                before = int(page[-1]["position"])
                session.messages = [self._to_message(message) for message in loaded]
                if transcript_window_complete(
                    session.messages,
                    active_segment_id=session.active_segment_id,
                    through_message_id=through_message_id,
                ):
                    complete = True
                    break
            work.rollback()
        session.messages = [self._to_message(message) for message in loaded]
        if complete and loaded:
            session._window_first_position = int(loaded[0]["position"])
        return session

    def find_user_turn(self, session_id: str, user_turn_id: str) -> ChatMessage | None:
        with unit_of_work(self.database) as work:
            record = work.chats.find_user_turn(self.context, session_id, user_turn_id)
            work.rollback()
        return self._to_message(record) if record is not None else None

    def save_session(self, session: ChatSession) -> None:
        """Persist one session and its changed message rows atomically.

        Chat history is append-only: existing message IDs must remain in order.
        Updating a message only writes that message row, while new messages are
        appended through the repository's row-locked append operation.
        """
        with unit_of_work(self.database) as work:
            session_row = work.connection.execute(
                """
                SELECT status, revision FROM omnix_chat_sessions
                 WHERE id = %s AND workspace_id = %s
                 FOR UPDATE
                """,
                (session.id, self.context.workspace_id),
            ).fetchone()
            if session_row is None:
                work.chats.create_session(self.context, self._session_payload(session))
                stored_messages: list[dict[str, Any]] = []
            else:
                if session_row[0] != "active":
                    raise RuntimeError(f"chat session {session.id} is not active")
                if int(session_row[1]) != session._revision:
                    raise RuntimeError(
                        f"chat session {session.id} changed after it was loaded"
                    )
                # A window saves against the stored messages from its first one on.
                stored_messages = self._list_all_messages(
                    work,
                    session.id,
                    after_position=(
                        (session._window_first_position or 0) - 1 if session.transcript_is_window else -1
                    ),
                )
                work.connection.execute(
                    """
                    UPDATE omnix_chat_sessions SET
                        title = %s,
                        provider_id = %s,
                        model_id = %s,
                        project_id = %s,
                        profile_id = %s,
                        interaction_mode = %s,
                        character_id = %s,
                        character_version = %s,
                        memory_enabled = %s,
                        memory_snapshot_id = %s,
                        settings = %s::jsonb,
                        transcript_policy = %s,
                        active_segment_id = %s,
                        revision = revision + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND id = %s AND status = 'active'
                    """,
                    (
                        session.title,
                        session.provider_id,
                        session.model_id,
                        session.project_id,
                        session.profile_id,
                        session.interaction_mode,
                        session.character_id,
                        session.character_profile_version,
                        session.memory_enabled,
                        session.memory_snapshot_id,
                        _json(self._settings(session)),
                        session.transcript_policy,
                        session.active_segment_id,
                        self.context.workspace_id,
                        session.id,
                    ),
                )

            stored_ids = [message["id"] for message in stored_messages]
            requested_prefix = [message.id for message in session.messages[: len(stored_ids)]]
            if stored_ids != requested_prefix:
                raise RuntimeError(
                    f"Chat transcript rewrite rejected for {session.id}; "
                    "PostgreSQL message history is append-only"
                )
            retained_messages = (
                session.messages
                if transcript_retention_allowed(session)
                else session.messages[: len(stored_ids)]
            )
            stored_by_id = {message["id"]: message for message in stored_messages}
            for message in retained_messages:
                payload = {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at,
                    "metadata": dict(message.metadata),
                }
                stored = stored_by_id.get(message.id)
                if stored is None:
                    work.chats.append_message(self.context, session.id, payload)
                elif any(
                    stored[field] != payload[field]
                    for field in ("content", "metadata", "created_at")
                ):
                    work.connection.execute(
                        """
                        UPDATE omnix_chat_messages
                           SET content = %s,
                               metadata = %s::jsonb,
                               created_at = %s::timestamptz
                         WHERE workspace_id = %s AND session_id = %s AND id = %s
                        """,
                        (
                            payload["content"],
                            _json(payload["metadata"]),
                            payload["created_at"],
                            self.context.workspace_id,
                            session.id,
                            message.id,
                        ),
                    )
            revision_row = work.connection.execute(
                """SELECT revision FROM omnix_chat_sessions
                     WHERE workspace_id = %s AND id = %s""",
                (self.context.workspace_id, session.id),
            ).fetchone()
            revision = int(revision_row[0])
            work.commit()
        session._revision = revision

    def create_session(self, session: ChatSession) -> None:
        """Insert one session and its retained greeting without rewriting neighbors."""
        with unit_of_work(self.database) as work:
            work.chats.create_session(self.context, self._session_payload(session))
            if transcript_retention_allowed(session):
                for message in session.messages:
                    work.chats.append_message(self.context, session.id, {
                        "id": message.id,
                        "role": message.role,
                        "content": message.content,
                        "created_at": message.created_at,
                        "metadata": dict(message.metadata),
                    })
            revision = work.connection.execute(
                """SELECT revision FROM omnix_chat_sessions
                     WHERE workspace_id = %s AND id = %s""",
                (self.context.workspace_id, session.id),
            ).fetchone()[0]
            work.commit()
        session._revision = int(revision)

    def delete_session(self, session_id: str) -> bool:
        """Soft-delete only the requested active session in the current workspace."""
        with unit_of_work(self.database) as work:
            cursor = work.connection.execute(
                """UPDATE omnix_chat_sessions
                      SET status = 'deleted', revision = revision + 1,
                          updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = %s AND id = %s AND status = 'active'""",
                (self.context.workspace_id, session_id),
            )
            changed = cursor.rowcount > 0
            work.commit()
        return changed

    def clear_memory_snapshots_for_owner(self, owner_type: str, owner_id: str) -> int:
        """Clear cached memory references for sessions owned by one memory scope."""
        if owner_type == "character":
            if not owner_id:
                return 0
            owner_clause = "character_id = %s"
            owner_params: tuple[str, ...] = (owner_id,)
        elif owner_type == "system" and owner_id == "system-assistant":
            owner_clause = "interaction_mode = 'system' AND character_id IS NULL"
            owner_params = ()
        else:
            return 0
        with unit_of_work(self.database) as work:
            result = work.connection.execute(
                f"""
                UPDATE omnix_chat_sessions
                   SET memory_enabled = FALSE,
                       memory_snapshot_id = NULL,
                       settings = settings - 'memory_snapshot_revision'
                                         - 'memory_record_count'
                                         - 'memory_last_refreshed_at',
                       revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND {owner_clause}
                """,
                (self.context.workspace_id, *owner_params),
            )
            changed = result.rowcount
            work.commit()
        return changed

    def _list_all_messages(self, work: Any, session_id: str, *, after_position: int = -1) -> list[dict[str, Any]]:
        """Load the append-only transcript (after ``after_position``) instead of truncating at 500 rows."""
        messages: list[dict[str, Any]] = []
        while True:
            page = work.chats.list_messages(
                self.context,
                session_id,
                limit=_MESSAGE_PAGE_SIZE,
                after_position=after_position,
            )
            if not page:
                return messages
            messages.extend(page)
            next_position = int(page[-1]["position"])
            if next_position <= after_position:
                raise RuntimeError(
                    f"Chat message pagination did not advance for session {session_id}"
                )
            after_position = next_position
            if len(page) < _MESSAGE_PAGE_SIZE:
                return messages

    def update_delivery_metadata(
        self,
        *,
        session_id: str,
        assistant_turn_id: str,
        metadata: dict[str, object],
    ) -> bool:
        """Patch delivery metadata without serializing the entire chat workspace."""
        if not metadata:
            return False
        with unit_of_work(self.database) as work:
            session = work.connection.execute(
                """SELECT id FROM omnix_chat_sessions
                     WHERE workspace_id = %s AND id = %s AND status = 'active'
                     FOR UPDATE""",
                (self.context.workspace_id, session_id),
            ).fetchone()
            if session is None:
                work.rollback()
                return False
            cursor = work.connection.execute(
                """
                UPDATE omnix_chat_messages
                   SET metadata = metadata || %s::jsonb
                 WHERE workspace_id = %s
                   AND session_id = %s
                   AND metadata ->> 'assistant_turn_id' = %s
                """,
                (
                    _json(metadata),
                    self.context.workspace_id,
                    session_id,
                    assistant_turn_id,
                ),
            )
            changed = cursor.rowcount > 0
            if changed:
                work.connection.execute(
                    """UPDATE omnix_chat_sessions
                          SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                        WHERE workspace_id = %s AND id = %s AND status = 'active'""",
                    (self.context.workspace_id, session_id),
                )
            work.commit()
        return changed

    def update_user_message_metadata(
        self,
        *,
        session_id: str,
        message_id: str,
        metadata: dict[str, object],
    ) -> bool:
        """Patch one accepted user turn without rewriting the chat workspace."""
        return self.update_message_metadata(
            session_id=session_id,
            message_id=message_id,
            metadata=metadata,
            role="user",
        )

    def update_message_metadata(
        self,
        *,
        session_id: str,
        message_id: str,
        metadata: dict[str, object],
        role: str | None = None,
    ) -> bool:
        """Patch one message row and bump only its owning session revision."""
        if not metadata:
            return False
        with unit_of_work(self.database) as work:
            session = work.connection.execute(
                """SELECT id FROM omnix_chat_sessions
                     WHERE workspace_id = %s AND id = %s AND status = 'active'
                     FOR UPDATE""",
                (self.context.workspace_id, session_id),
            ).fetchone()
            if session is None:
                work.rollback()
                return False
            role_clause = " AND role = %s" if role else ""
            params: tuple[Any, ...] = (
                _json(metadata), self.context.workspace_id, session_id, message_id,
                *((role,) if role else ()),
            )
            cursor = work.connection.execute(
                """UPDATE omnix_chat_messages
                      SET metadata = metadata || %s::jsonb
                    WHERE workspace_id = %s AND session_id = %s AND id = %s"""
                + role_clause,
                params,
            )
            changed = cursor.rowcount > 0
            if changed:
                work.connection.execute(
                    """UPDATE omnix_chat_sessions
                          SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                        WHERE workspace_id = %s AND id = %s AND status = 'active'""",
                    (self.context.workspace_id, session_id),
                )
            work.commit()
        return changed

    def delete_messages(self, session_id: str, message_ids: list[str]) -> int:
        """Delete selected transcript rows from one locked session."""
        ids = list(dict.fromkeys(message_ids))
        if not ids:
            return 0
        with unit_of_work(self.database) as work:
            session = work.connection.execute(
                """SELECT message_count FROM omnix_chat_sessions
                     WHERE workspace_id = %s AND id = %s AND status = 'active'
                     FOR UPDATE""",
                (self.context.workspace_id, session_id),
            ).fetchone()
            if session is None:
                work.rollback()
                return 0
            deleted = work.connection.execute(
                """DELETE FROM omnix_chat_messages
                     WHERE workspace_id = %s AND session_id = %s AND id = ANY(%s)""",
                (self.context.workspace_id, session_id, ids),
            ).rowcount
            if deleted:
                work.connection.execute(
                    """UPDATE omnix_chat_sessions
                          SET message_count = GREATEST(message_count - %s, 0),
                              revision = revision + 1,
                              updated_at = CURRENT_TIMESTAMP
                        WHERE workspace_id = %s AND id = %s AND status = 'active'""",
                    (deleted, self.context.workspace_id, session_id),
                )
            work.commit()
        return deleted

    def remove_assistant_reply(self, session_id: str, user_message_id: str) -> int:
        """Remove only assistant rows addressed to one user turn."""
        with unit_of_work(self.database) as work:
            session = work.connection.execute(
                """SELECT message_count FROM omnix_chat_sessions
                     WHERE workspace_id = %s AND id = %s AND status = 'active'
                     FOR UPDATE""",
                (self.context.workspace_id, session_id),
            ).fetchone()
            if session is None:
                work.rollback()
                return 0
            deleted = work.connection.execute(
                """DELETE FROM omnix_chat_messages
                     WHERE workspace_id = %s AND session_id = %s AND role = 'assistant'
                       AND metadata ->> 'reply_to_message_id' = %s""",
                (self.context.workspace_id, session_id, user_message_id),
            ).rowcount
            if deleted:
                work.connection.execute(
                    """UPDATE omnix_chat_sessions
                          SET message_count = GREATEST(message_count - %s, 0),
                              revision = revision + 1,
                              updated_at = CURRENT_TIMESTAMP
                        WHERE workspace_id = %s AND id = %s AND status = 'active'""",
                    (deleted, self.context.workspace_id, session_id),
                )
            work.commit()
        return deleted

    def _session_payload(self, session: ChatSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "title": session.title,
            "provider_id": session.provider_id,
            "model_id": session.model_id,
            "project_id": session.project_id,
            "profile_id": session.profile_id,
            "interaction_mode": session.interaction_mode,
            "character_id": session.character_id,
            "character_version": session.character_profile_version,
            "memory_enabled": session.memory_enabled,
            "memory_snapshot_id": session.memory_snapshot_id,
            "settings": self._settings(session),
            "transcript_policy": session.transcript_policy,
            "active_segment_id": session.active_segment_id,
        }

    @staticmethod
    def _settings(session: ChatSession) -> dict[str, Any]:
        return {
            "research_mode_override": session.research_mode_override,
            "memory_snapshot_revision": session.memory_snapshot_revision,
            "memory_record_count": session.memory_record_count,
            "memory_last_refreshed_at": session.memory_last_refreshed_at,
            "voice_asset_id": session.voice_asset_id,
            "read_memory": session.read_memory,
            "write_memory": session.write_memory,
            "shared_memory_access": session.shared_memory_access,
            "effective_identity_hash": session.effective_identity_hash,
        }

    @staticmethod
    def _summary_fields(record: dict[str, Any]) -> dict[str, Any]:
        settings = dict(record.get("settings") or {})
        return {
            "id": record["id"],
            "title": record["title"],
            "provider_id": record.get("provider_id"),
            "model_id": record.get("model_id"),
            "research_mode_override": settings.get("research_mode_override"),
            "profile_id": record.get("profile_id") or "profile:default",
            "workspace_id": record["workspace_id"],
            "project_id": record.get("project_id"),
            "memory_enabled": bool(record.get("memory_enabled")),
            "memory_snapshot_id": record.get("memory_snapshot_id"),
            "memory_snapshot_revision": settings.get("memory_snapshot_revision"),
            "memory_record_count": int(settings.get("memory_record_count") or 0),
            "memory_last_refreshed_at": settings.get("memory_last_refreshed_at"),
            "interaction_mode": record.get("interaction_mode") or "system",
            "character_id": record.get("character_id"),
            "voice_asset_id": settings.get("voice_asset_id"),
            "read_memory": bool(settings.get("read_memory")),
            "write_memory": bool(settings.get("write_memory")),
            "shared_memory_access": settings.get("shared_memory_access") or "none",
            "transcript_policy": record.get("transcript_policy") or "persistent",
            "active_segment_id": record.get("active_segment_id"),
            "character_profile_version": record.get("character_version"),
            "effective_identity_hash": settings.get("effective_identity_hash"),
            "message_count": int(record.get("message_count") or 0),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }

    @classmethod
    def _to_summary(cls, record: dict[str, Any]) -> ChatSessionSummary:
        return ChatSessionSummary(**cls._summary_fields(record))

    @staticmethod
    def _to_message(message: dict[str, Any]) -> ChatMessage:
        return ChatMessage(
            id=message["id"],
            role=message["role"],
            content=message["content"],
            created_at=message["created_at"],
            metadata=dict(message.get("metadata") or {}),
        )

    @classmethod
    def _to_session(
        cls,
        record: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> ChatSession:
        session = ChatSession(
            **cls._summary_fields(record),
            messages=[cls._to_message(message) for message in messages],
        )
        session._revision = int(record.get("revision") or 0)
        return session
