from __future__ import annotations

import json
from typing import Any


MAX_RPG_NARRATION_EVENTS_PER_SESSION = 512
MAX_RPG_NARRATION_EVENT_PAGE_SIZE = 128
MAX_RPG_NARRATION_EVENT_BYTES = 60 * 1024


class PostgresRpgNarrationEventRepository:
    """Durable cross-replica event feed for RPG narration SSE clients."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def append(self, session_id: str, event: dict[str, Any]) -> int:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_RPG_NARRATION_EVENT_BYTES:
            raise ValueError("narration event exceeds the storage limit")
        event_id = int(
            self.connection.execute(
                """
                INSERT INTO omnix_rpg_narration_events (session_id, payload)
                VALUES (%s, %s::jsonb)
                RETURNING event_id
                """,
                (session_id, payload),
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            DELETE FROM omnix_rpg_narration_events
             WHERE event_id IN (
                 SELECT event_id
                   FROM omnix_rpg_narration_events
                  WHERE session_id = %s
                  ORDER BY event_id DESC
                 OFFSET %s
             )
            """,
            (session_id, MAX_RPG_NARRATION_EVENTS_PER_SESSION),
        )
        return event_id

    def latest_event_id(self, session_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(event_id), 0)
              FROM omnix_rpg_narration_events
             WHERE session_id = %s
            """,
            (str(session_id or "").strip(),),
        ).fetchone()
        return int(row[0])

    def list_after(
        self,
        session_id: str,
        after_event_id: int,
        *,
        limit: int = 32,
    ) -> list[tuple[int, dict[str, Any]]]:
        resolved_limit = max(1, min(int(limit), MAX_RPG_NARRATION_EVENT_PAGE_SIZE))
        rows = self.connection.execute(
            """
            SELECT event_id, payload
              FROM omnix_rpg_narration_events
             WHERE session_id = %s
               AND event_id > %s
               AND created_at >= CURRENT_TIMESTAMP - INTERVAL '1 day'
             ORDER BY event_id
             LIMIT %s
            """,
            (str(session_id or "").strip(), max(0, int(after_event_id)), resolved_limit),
        ).fetchall()
        return [(int(row[0]), dict(row[1])) for row in rows]
