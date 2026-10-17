from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

REPUTATION_MIN = -5
REPUTATION_MAX = 5


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp(value: Any, default: int = 0) -> int:
    return max(REPUTATION_MIN, min(REPUTATION_MAX, _safe_int(value, default)))


def ensure_npc_reputation_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = _safe_dict(simulation_state.get("npc_reputation_state"))
    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["npc_reputation_state"] = state
    return state


def get_npc_reputation(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
) -> Dict[str, Any]:
    state = ensure_npc_reputation_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    entry = _safe_dict(by_npc.get(_safe_str(npc_id)))
    return {
        "npc_id": _safe_str(npc_id),
        "familiarity": _clamp(entry.get("familiarity"), 0),
        "trust": _clamp(entry.get("trust"), 0),
        "annoyance": _clamp(entry.get("annoyance"), 0),
        "fear": _clamp(entry.get("fear"), 0),
        "respect": _clamp(entry.get("respect"), 0),
        "last_updated_tick": _safe_int(entry.get("last_updated_tick"), 0),
        "source": "deterministic_npc_reputation_runtime",
    }


def update_npc_reputation(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    tick: int,
    familiarity_delta: int = 0,
    trust_delta: int = 0,
    annoyance_delta: int = 0,
    fear_delta: int = 0,
    respect_delta: int = 0,
    reason: str = "",
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    if not npc_id.startswith("npc:"):
        return {"updated": False, "reason": "invalid_npc_id"}

    state = ensure_npc_reputation_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    current = get_npc_reputation(simulation_state, npc_id=npc_id)

    updated = {
        "npc_id": npc_id,
        "familiarity": _clamp(cast(int, current.get("familiarity")) + int(familiarity_delta or 0)),
        "trust": _clamp(cast(int, current.get("trust")) + int(trust_delta or 0)),
        "annoyance": _clamp(cast(int, current.get("annoyance")) + int(annoyance_delta or 0)),
        "fear": _clamp(cast(int, current.get("fear")) + int(fear_delta or 0)),
        "respect": _clamp(cast(int, current.get("respect")) + int(respect_delta or 0)),
        "last_updated_tick": int(tick or 0),
        "last_reason": _safe_str(reason),
        "source": "deterministic_npc_reputation_runtime",
    }

    by_npc[npc_id] = updated
    state["by_npc"] = by_npc
    simulation_state["npc_reputation_state"] = state

    return {
        "updated": True,
        "entry": deepcopy(updated),
        "source": "deterministic_npc_reputation_runtime",
    }


def response_style_from_reputation(
    reputation: Dict[str, Any],
    *,
    fallback: str = "guarded",
) -> str:
    reputation = _safe_dict(reputation)
    trust = _safe_int(reputation.get("trust"), 0)
    annoyance = _safe_int(reputation.get("annoyance"), 0)
    fear = _safe_int(reputation.get("fear"), 0)
    respect = _safe_int(reputation.get("respect"), 0)

    if fear >= 3:
        return "evasive"
    if annoyance >= 3:
        return "annoyed"
    if trust >= 2 and respect >= 1:
        return "helpful"
    if trust >= 1:
        return "friendly"
    return fallback or "guarded"
