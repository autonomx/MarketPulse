from __future__ import annotations

import json
from typing import Any

from app.persistence.errors import EntityNotFound, RevisionConflict
from app.runtime.pagination import page_limit
from app.persistence.tenant import TenantContext


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _session(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "owner_user_id": str(row[2]) if row[2] is not None else None,
        "title": str(row[3]),
        "provider_id": str(row[4]) if row[4] is not None else None,
        "model_id": str(row[5]) if row[5] is not None else None,
        "project_id": str(row[6]) if row[6] is not None else None,
        "profile_id": str(row[7]) if row[7] is not None else None,
        "interaction_mode": str(row[8]),
        "character_id": str(row[9]) if row[9] is not None else None,
        "character_version": int(row[10]) if row[10] is not None else None,
        "memory_enabled": bool(row[11]),
        "memory_snapshot_id": str(row[12]) if row[12] is not None else None,
        "settings": dict(row[13]),
        "transcript_policy": str(row[14]),
        "active_segment_id": str(row[15]) if row[15] is not None else None,
        "status": str(row[16]),
        "revision": int(row[17]),
        "message_count": int(row[18]),
        "created_at": row[19].isoformat(),
        "updated_at": row[20].isoformat(),
    }


_SESSION_COLUMNS = """
id, workspace_id, owner_user_id, title, provider_id, model_id, project_id,
profile_id, interaction_mode, character_id, character_version, memory_enabled,
memory_snapshot_id, settings, transcript_policy, active_segment_id, status,
revision, message_count, created_at, updated_at
"""


class PostgresChatRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_session(
        self, context: TenantContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_chat_sessions (
                id, workspace_id, owner_user_id, title, provider_id, model_id,
                project_id, profile_id, interaction_mode, character_id,
                character_version, memory_enabled, memory_snapshot_id, settings,
                transcript_policy, active_segment_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s, %s
            ) RETURNING {_SESSION_COLUMNS}
            """,
            (
                payload["id"],
                context.workspace_id,
                payload.get("owner_user_id") or context.user_id,
                payload.get("title") or "New chat",
                payload.get("provider_id"),
                payload.get("model_id"),
                payload.get("project_id"),
                payload.get("profile_id"),
                payload.get("interaction_mode", "system"),
                payload.get("character_id"),
                payload.get("character_version"),
                bool(payload.get("memory_enabled", False)),
                payload.get("memory_snapshot_id"),
                _json(payload.get("settings") or {}),
                payload.get("transcript_policy", "persistent"),
                payload.get("active_segment_id"),
            ),
        ).fetchone()
        return _session(row)

    def get_session(self, context: TenantContext, session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM omnix_chat_sessions "
            "WHERE id = %s AND workspace_id = %s",
            (session_id, context.workspace_id),
        ).fetchone()
        return _session(row) if row is not None else None

    def list_sessions(
        self,
        context: TenantContext,
        *,
        limit: int = 50,
        before_updated_at: str | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id = %s", "status = 'active'"]
        params: list[Any] = [context.workspace_id]
        if before_updated_at is not None and before_id is not None:
            clauses.append("(updated_at, id) < (%s::timestamptz, %s)")
            params.extend([before_updated_at, before_id])
        params.append(max(1, min(int(limit), 200)))
        rows = self.connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM omnix_chat_sessions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id DESC LIMIT %s",
            tuple(params),
        ).fetchall()
        return [_session(row) for row in rows]

    def update_session(
        self,
        context: TenantContext,
        *,
        session_id: str,
        expected_revision: int,
        title: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.get_session(context, session_id)
        if current is None:
            raise EntityNotFound(session_id)
        row = self.connection.execute(
            f"""
            UPDATE omnix_chat_sessions
               SET title = %s, settings = %s::jsonb,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s AND revision = %s
            RETURNING {_SESSION_COLUMNS}
            """,
            (
                title if title is not None else current["title"],
                _json(settings if settings is not None else current["settings"]),
                session_id,
                context.workspace_id,
                expected_revision,
            ),
        ).fetchone()
        if row is None:
            raise RevisionConflict(
                f"chat session {session_id} expected revision {expected_revision}; current {current['revision']}"
            )
        return _session(row)

    def append_message(
        self,
        context: TenantContext,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session = self.connection.execute(
            """
            SELECT message_count FROM omnix_chat_sessions
             WHERE id = %s AND workspace_id = %s AND status = 'active'
             FOR UPDATE
            """,
            (session_id, context.workspace_id),
        ).fetchone()
        if session is None:
            raise EntityNotFound(session_id)
        # Read the transcript watermark after acquiring the session lock. A
        # separate statement observes any appends committed while this writer
        # waited for the lock under PostgreSQL READ COMMITTED isolation.
        position = int(
            self.connection.execute(
                """SELECT COALESCE(MAX(position) + 1, 0)
                     FROM omnix_chat_messages
                    WHERE workspace_id = %s AND session_id = %s""",
                (context.workspace_id, session_id),
            ).fetchone()[0]
        )
        row = self.connection.execute(
            """
            INSERT INTO omnix_chat_messages
                (id, workspace_id, session_id, position, role, content, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))
            RETURNING id, session_id, position, role, content, metadata, created_at
            """,
            (
                payload["id"],
                context.workspace_id,
                session_id,
                position,
                payload["role"],
                payload["content"],
                _json(payload.get("metadata") or {}),
                payload.get("created_at"),
            ),
        ).fetchone()
        self.connection.execute(
            """
            UPDATE omnix_chat_sessions
               SET message_count = message_count + 1,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
            """,
            (session_id, context.workspace_id),
        )
        return {
            "id": str(row[0]),
            "session_id": str(row[1]),
            "position": int(row[2]),
            "role": str(row[3]),
            "content": str(row[4]),
            "metadata": dict(row[5]),
            "created_at": row[6].isoformat(),
        }

    def list_messages_before(
        self,
        context: TenantContext,
        session_id: str,
        *,
        before_position: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """One page of the transcript, newest first, before ``before_position`` (WP-5.7)."""
        rows = self.connection.execute(
            """
            SELECT id, session_id, position, role, content, metadata, created_at
              FROM omnix_chat_messages
             WHERE workspace_id = %s AND session_id = %s
               AND (%s::bigint IS NULL OR position < %s::bigint)
             ORDER BY position DESC, id DESC LIMIT %s
            """,
            (
                context.workspace_id,
                session_id,
                before_position,
                before_position,
                page_limit(limit, default=100),
            ),
        ).fetchall()
        return [_message_record(row) for row in rows]

    def find_user_turn(
        self,
        context: TenantContext,
        session_id: str,
        user_turn_id: str,
    ) -> dict[str, Any] | None:
        """The user message a client turn id already created (idx_omnix_chat_messages_user_turn)."""
        row = self.connection.execute(
            """
            SELECT id, session_id, position, role, content, metadata, created_at
              FROM omnix_chat_messages
             WHERE workspace_id = %s AND session_id = %s
               AND role = 'user' AND metadata ? 'user_turn_id'
               AND metadata->>'user_turn_id' = %s
             ORDER BY position ASC
             LIMIT 1
            """,
            (context.workspace_id, session_id, user_turn_id),
        ).fetchone()
        return _message_record(row) if row is not None else None

    def list_messages(
        self,
        context: TenantContext,
        session_id: str,
        *,
        limit: int = 100,
        after_position: int = -1,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, session_id, position, role, content, metadata, created_at
              FROM omnix_chat_messages
             WHERE workspace_id = %s AND session_id = %s AND position > %s
             ORDER BY position ASC, id ASC LIMIT %s
            """,
            (
                context.workspace_id,
                session_id,
                int(after_position),
                page_limit(limit, default=100),
            ),
        ).fetchall()
        return [
            {
                "id": str(row[0]),
                "session_id": str(row[1]),
                "position": int(row[2]),
                "role": str(row[3]),
                "content": str(row[4]),
                "metadata": dict(row[5]),
                "created_at": row[6].isoformat(),
            }
            for row in rows
        ]


def _message_record(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "session_id": str(row[1]),
        "position": int(row[2]),
        "role": str(row[3]),
        "content": str(row[4]),
        "metadata": dict(row[5]),
        "created_at": row[6].isoformat(),
    }
