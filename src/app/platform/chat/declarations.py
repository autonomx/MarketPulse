"""What the kernel reads about chat without loading it (ADR-0016): kernel imports only."""
from __future__ import annotations

from typing import Any

from app.persistence.declarations import LegacyImport


def _restore_conversation_segments(work: Any, context: Any, _stable_id: str, item: dict[str, Any]) -> None:
    """A legacy character aggregate's conversation segments (chat owns segments, PA-2.2)."""
    for segment in list(item.get("conversation_segments") or []):
        segment_id = str(segment.get("id") or "").strip()
        session_id = str(segment.get("session_id") or "").strip()
        if not segment_id or not session_id:
            continue
        work.connection.execute(
            """
            INSERT INTO omnix_conversation_segments (
                id, workspace_id, session_id, interaction_mode, character_id,
                character_version, transcript_policy, read_memory, write_memory,
                shared_memory_access, carryover_summary, started_at, ended_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s::timestamptz
            ) ON CONFLICT (id) DO NOTHING
            """,
            (
                segment_id,
                context.workspace_id,
                session_id,
                segment.get("interaction_mode", "system"),
                segment.get("character_id"),
                segment.get("character_version"),
                segment.get("transcript_policy", "persistent"),
                bool(segment.get("read_memory", False)),
                bool(segment.get("write_memory", False)),
                segment.get("shared_memory_access", "none"),
                segment.get("carryover_summary"),
                segment.get("started_at"),
                segment.get("ended_at"),
            ),
        )


LEGACY_IMPORTS = (LegacyImport("chat.conversation_segments", _restore_conversation_segments),)
