"""Conversation segments in PostgreSQL (``omnix_conversation_segments``, owned by chat)."""
from __future__ import annotations

from typing import Any, cast

from app.platform.chat.segments import MAX_SESSION_SEGMENTS, ConversationSegment, check_segment_identity, new_segment_id
from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant

_COLUMNS = """id, session_id, interaction_mode, character_id, character_version, transcript_policy,
              read_memory, write_memory, shared_memory_access, carryover_summary, started_at, ended_at"""


class PostgresConversationSegmentRepository:
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def create_segment(self, *, session_id: str, interaction_mode: Any, character_id: str | None,
                       profile_version: int | None, transcript_policy: Any, read_memory: bool, write_memory: bool,
                       shared_memory_access: Any, carryover_summary: str | None = None) -> ConversationSegment:
        check_segment_identity(interaction_mode, character_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""INSERT INTO omnix_conversation_segments (
                        id, workspace_id, session_id, interaction_mode, character_id,
                        character_version, transcript_policy, read_memory, write_memory,
                        shared_memory_access, carryover_summary
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}""",
                (new_segment_id(), self.context.workspace_id, session_id, interaction_mode, character_id,
                 profile_version, transcript_policy, read_memory, write_memory, shared_memory_access,
                 carryover_summary),
            ).fetchone()
        return _segment(row)

    def close_segment(self, segment_id: str) -> ConversationSegment | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""UPDATE omnix_conversation_segments
                       SET ended_at = COALESCE(ended_at, CURRENT_TIMESTAMP)
                     WHERE id = %s AND workspace_id = %s
                    RETURNING {_COLUMNS}""",
                (segment_id, self.context.workspace_id),
            ).fetchone()
        return _segment(row) if row is not None else None

    def segments(self, session_id: str) -> list[ConversationSegment]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""SELECT * FROM (
                        SELECT {_COLUMNS}
                          FROM omnix_conversation_segments
                         WHERE workspace_id = %s AND session_id = %s
                         ORDER BY started_at DESC, id DESC LIMIT %s
                    ) AS newest ORDER BY started_at ASC, id ASC""",
                # The newest segments of a session, oldest first (WP-5.5).
                (self.context.workspace_id, session_id, MAX_SESSION_SEGMENTS),
            ).fetchall()
        return [_segment(row) for row in rows]


def _segment(row: Any) -> ConversationSegment:
    return ConversationSegment(
        id=str(row[0]),
        session_id=str(row[1]),
        interaction_mode=cast(Any, str(row[2])),
        character_id=str(row[3]) if row[3] is not None else None,
        profile_version=int(row[4]) if row[4] is not None else None,
        transcript_policy=cast(Any, str(row[5])),
        read_memory=bool(row[6]),
        write_memory=bool(row[7]),
        shared_memory_access=cast(Any, str(row[8])),
        carryover_summary=str(row[9]) if row[9] is not None else None,
        started_at=row[10].isoformat(),
        ended_at=row[11].isoformat() if row[11] is not None else None,
    )
