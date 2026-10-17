from __future__ import annotations

from typing import Any

from app.apps.rpg.edge.hermes.mode_routing import omnix_mode_route
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _bounded_context(value: Any, *, limit: int = 12) -> dict[str, Any]:
    data = _safe_dict(value)
    bounded: dict[str, Any] = {}
    for index, (key, item) in enumerate(data.items()):
        if index >= limit:
            break
        bounded[_safe_str(key)[:80]] = item
    return bounded


def hermes_adapter_preview_payload(request: dict[str, Any]) -> dict[str, Any]:
    data = _safe_dict(request)
    mode = _safe_str(data.get("mode")).strip()
    intent = _safe_str(data.get("intent")).strip() or "preview"
    if not mode:
        return {"ok": False, "error": "missing_mode", "source": "hermes_adapter"}

    try:
        route = omnix_mode_route(mode)
    except KeyError:
        return {"ok": False, "error": "unknown_mode", "mode": mode, "source": "hermes_adapter"}

    return {
        "ok": True,
        "source": "hermes_adapter",
        "mode": route["mode"],
        "intent": intent,
        "route": route,
        "request": {
            "mode": route["mode"],
            "intent": intent,
            "context": _bounded_context(data.get("context")),
            "metadata": _bounded_context(data.get("metadata"), limit=8),
        },
        "response_contract": {
            "kind": route["hermes_role"],
            "items": [],
            "review_required": route["requires_approval"],
            "owner": route["execution_owner"],
        },
    }
