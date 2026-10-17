from __future__ import annotations

from typing import Any


def hermes_rpg_request_guard(request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    session_ok = str(request.get("session_id") or "").strip() == str(context.get("session_id") or "").strip()
    hash_value = request.get("context_hash") or context.get("context_hash")
    hash_ok = not request.get("context_hash") or request.get("context_hash") == context.get("context_hash")
    ok = request.get("ok") is True and session_ok and hash_ok
    return {
        "ok": ok,
        "source": "hermes_rpg_request_guard",
        "session_ok": session_ok,
        "context_hash": hash_value,
        "context_ok": hash_ok,
        "command_text": request.get("command_text"),
        "state_changed": False,
    }
