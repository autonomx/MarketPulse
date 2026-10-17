"""Phase 14.2 — World Memory / Rumor Propagation.

Provides shared world memory / rumor memory entries with propagation metadata.
World memory is shared/public-ish memory, including rumors, public facts,
and propagated incidents. It is not a substitute for actor memory.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_MAX_RUMORS = 32


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _normalize_rumor(value: Any) -> Dict[str, Any]:
    """Normalize a rumor entry into bounded, deterministic state."""
    data = _safe_dict(value)
    return {
        "id": _safe_str(data.get("id")).strip(),
        "summary": _safe_str(data.get("summary")).strip(),
        "origin": _safe_str(data.get("origin")).strip(),
        "location": _safe_str(data.get("location")).strip(),
        "tick": _safe_int(data.get("tick"), 0),
        "reach": _safe_int(data.get("reach"), 0),
    }


def ensure_world_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure world memory state exists in simulation state, normalized and bounded."""
    if not isinstance(simulation_state, dict):
        simulation_state = {}

    memory_state = simulation_state.get("memory_state")
    if not isinstance(memory_state, dict):
        memory_state = {}
        simulation_state["memory_state"] = memory_state

    rumors = [
        _normalize_rumor(item)
        for item in _safe_list(memory_state.get("rumors"))
        if isinstance(item, dict)
    ][-_MAX_RUMORS:]

    rumors = sorted(
        rumors,
        key=lambda item: (
            item.get("tick", 0),
            _safe_str(item.get("summary")).lower(),
            _safe_str(item.get("id")),
        ),
    )[-_MAX_RUMORS:]

    memory_state["rumors"] = rumors
    return simulation_state


