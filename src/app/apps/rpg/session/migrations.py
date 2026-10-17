"""Versioned migration layer for durable RPG session payloads.

The upcasters and the campaign envelope live in ``campaign_state`` (WP-5.6).
"""
from __future__ import annotations

from typing import Any

from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

_CURRENT_SAVE_VERSION = "1.0"


def migrate_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate wrapped or direct session payloads to the current schema.

    Durable files use ``{"save_version": ..., "session": {...}}`` while several
    callers pass the session dictionary directly. Both shapes are migrated so a
    legacy on-disk transcript cannot bypass interaction conversion.
    """

    from app.apps.rpg.session.campaign_state import upcast_campaign_document

    payload = _safe_dict(payload)
    nested = payload.get("session")
    if isinstance(nested, dict):
        payload["session"] = upcast_campaign_document(nested)
    return upcast_campaign_document(payload)
