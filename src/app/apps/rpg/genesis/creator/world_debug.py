"""Phase 7 — Creator / GM Debug Tools.

Collect compact inspector/debug views from simulation state
and provide explainability surfaces.

Rules:
- Avoid putting this logic inside routes
- Bounded outputs for debug payloads
- Deterministic ordering everywhere
"""

from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import list_copy as _safe_list


_MAX_ITEMS = 12


def summarize_tick_changes(before_state: Dict[str, Any], after_state: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize what changed between two simulation states."""
    before_state = before_state or {}
    after_state = after_state or {}

    before_events = _safe_list(before_state.get("events"))
    after_events = _safe_list(after_state.get("events"))
    before_consequences = _safe_list(before_state.get("consequences"))
    after_consequences = _safe_list(after_state.get("consequences"))

    new_events = after_events[len(before_events):]
    new_consequences = after_consequences[len(before_consequences):]

    return {
        "tick_before": int(before_state.get("tick", 0) or 0),
        "tick_after": int(after_state.get("tick", 0) or 0),
        "new_events": [dict(item) for item in new_events[:12] if isinstance(item, dict)],
        "new_consequences": [dict(item) for item in new_consequences[:12] if isinstance(item, dict)],
    }