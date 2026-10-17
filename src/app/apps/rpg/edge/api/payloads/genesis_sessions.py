"""Gateway compatibility bridge for Campaign Genesis v2 launches."""

from __future__ import annotations

from typing import Any

from app.apps.rpg.edge.api.payloads.sessions import get_rpg_session_payload as _legacy_get_rpg_session_payload
from app.apps.rpg.genesis.forge.promoted_launch import create_promoted_new_game
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict
from app.apps.rpg.session.service import SESSION_CAMPAIGN_SESSIONS


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def get_rpg_session_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Promote new-game requests into genesis before falling back to compat."""

    payload = _safe_dict(data)
    action = _safe_str(payload.get("action")).strip()
    request = _safe_dict(payload.get("request") or payload)
    if action == "new_game" and request:
        return create_promoted_new_game(payload, sessions=SESSION_CAMPAIGN_SESSIONS)
    return _legacy_get_rpg_session_payload(data)
