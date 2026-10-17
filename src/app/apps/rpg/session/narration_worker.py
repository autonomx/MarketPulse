"""Narration events for SSE delivery.

Deferred narration runs as ``rpg.narration`` jobs (``app.apps.rpg.session.jobs.narration_job``,
WP-5.6); the in-process worker thread and its wake-up registry are gone.
Events go to PostgreSQL so an SSE connection on any replica reads them.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def publish_narration_event(session_id: str, event: dict[str, Any]) -> int:
    """Append an event to PostgreSQL for delivery to SSE connections on any replica."""
    session_id = str(session_id or "")
    if not session_id:
        return 0
    from app.persistence.database import default_database
    from app.apps.rpg.foundation.persistence.narration_event_repository import PostgresRpgNarrationEventRepository

    try:
        with default_database().transaction() as connection:
            PostgresRpgNarrationEventRepository(connection).append(session_id, event)
    except Exception:
        logger.exception("Failed to persist RPG narration event")
        return 0
    return 1
