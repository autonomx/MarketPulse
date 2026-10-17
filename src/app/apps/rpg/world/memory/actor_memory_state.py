"""Phase 14.1 — Actor Memory Integration.

Provides bounded, deterministic actor-specific memory lanes (short-term / long-term).
Memory is scoped, bounded, and observational — it influences presentation/reasoning
surfaces but does not silently mutate simulation truth outside explicit reducers/routes.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_MAX_ACTOR_SHORT_TERM = 10
_MAX_ACTOR_LONG_TERM = 20
_MAX_ACTORS = 64


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _normalize_entry(value: Any) -> Dict[str, Any]:
    data = _safe_dict(value)
    return {
        "id": _safe_str(data.get("id")).strip(),
        "summary": _safe_str(data.get("summary")).strip(),
        "kind": _safe_str(data.get("kind")).strip() or "fact",
        "tick": _safe_int(data.get("tick"), 0),
        "source": _safe_str(data.get("source")).strip(),
    }


def _normalize_actor_memory(value: Any) -> Dict[str, Any]:
    data = _safe_dict(value)
    return {
        "short_term": [
            _normalize_entry(item)
            for item in _safe_list(data.get("short_term"))
            if isinstance(item, dict)
        ][-_MAX_ACTOR_SHORT_TERM:],
        "long_term": [
            _normalize_entry(item)
            for item in _safe_list(data.get("long_term"))
            if isinstance(item, dict)
        ][-_MAX_ACTOR_LONG_TERM:],
    }


def ensure_actor_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure actor memory state exists in simulation state, normalized and bounded."""
    if not isinstance(simulation_state, dict):
        simulation_state = {}

    memory_state = simulation_state.get("memory_state")
    if not isinstance(memory_state, dict):
        memory_state = {}
        simulation_state["memory_state"] = memory_state

    actor_memory_in = memory_state.get("actor_memory")
    if not isinstance(actor_memory_in, dict):
        actor_memory_in = {}

    actor_memory_out: Dict[str, Any] = {}
    for actor_id in sorted(actor_memory_in.keys(), key=lambda v: _safe_str(v)):
        actor_memory_out[_safe_str(actor_id)] = _normalize_actor_memory(actor_memory_in.get(actor_id))

    limited_items = list(actor_memory_out.items())[:_MAX_ACTORS]
    memory_state["actor_memory"] = dict(limited_items)
    return simulation_state


def get_actor_memory(
    simulation_state: Dict[str, Any],
    actor_id: str,
) -> Dict[str, Any]:
    """Retrieve normalized actor-specific memory state."""
    simulation_state = ensure_actor_memory_state(simulation_state)
    memory_state = _safe_dict(simulation_state.get("memory_state"))
    actor_memory = _safe_dict(memory_state.get("actor_memory"))
    return _normalize_actor_memory(actor_memory.get(actor_id))