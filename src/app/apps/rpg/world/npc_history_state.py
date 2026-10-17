from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_HISTORY_ENTRIES_PER_NPC = 20
DEFAULT_HISTORY_TTL_TICKS = 1000


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_npc_history_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = _safe_dict(simulation_state.get("npc_history_state"))
    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["npc_history_state"] = state
    return state


def prune_npc_history_state(
    simulation_state: Dict[str, Any],
    *,
    current_tick: int,
    max_entries_per_npc: int = MAX_HISTORY_ENTRIES_PER_NPC,
) -> Dict[str, Any]:
    state = ensure_npc_history_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    expired_ids: List[str] = []

    for npc_id, npc_state in list(by_npc.items()):
        npc_state = _safe_dict(npc_state)
        entries = []
        for entry in _safe_list(npc_state.get("entries")):
            entry = _safe_dict(entry)
            history_id = _safe_str(entry.get("history_id"))
            expires_tick = _safe_int(entry.get("expires_tick"), 0)
            if expires_tick and int(current_tick or 0) >= expires_tick:
                if history_id:
                    expired_ids.append(history_id)
                continue
            entries.append(entry)

        entries.sort(
            key=lambda item: (
                _safe_int(_safe_dict(item).get("importance"), 0),
                _safe_int(_safe_dict(item).get("tick"), 0),
                _safe_str(_safe_dict(item).get("history_id")),
            ),
            reverse=True,
        )
        npc_state["entries"] = entries[: max(1, int(max_entries_per_npc or MAX_HISTORY_ENTRIES_PER_NPC))]
        by_npc[npc_id] = npc_state

    state["by_npc"] = by_npc
    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_prune_tick": int(current_tick or 0),
        "expired_history_ids": expired_ids,
        "source": "deterministic_npc_history_runtime",
    }
    return {
        "expired_history_ids": expired_ids,
        "source": "deterministic_npc_history_runtime",
    }


def add_npc_history_entry(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    kind: str,
    summary: str,
    tick: int,
    topic_id: str = "",
    importance: int = 1,
    ttl_ticks: int = DEFAULT_HISTORY_TTL_TICKS,
    source: str = "deterministic_npc_history_runtime",
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    summary = _safe_str(summary).strip()
    kind = _safe_str(kind).strip() or "interaction"
    if not npc_id.startswith("npc:") or not summary:
        return {"created": False, "reason": "invalid_history_entry"}

    state = ensure_npc_history_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))
    npc_state = _safe_dict(by_npc.get(npc_id))
    entries = _safe_list(npc_state.get("entries"))

    current_tick = int(tick or 0)
    history_id = f"hist:{npc_id}:{current_tick}:{kind}:{abs(hash(summary)) % 100000}"

    entry = {
        "history_id": history_id,
        "npc_id": npc_id,
        "kind": kind,
        "summary": summary[:280],
        "topic_id": _safe_str(topic_id),
        "importance": max(1, min(5, _safe_int(importance, 1))),
        "tick": current_tick,
        "expires_tick": current_tick + max(1, int(ttl_ticks or DEFAULT_HISTORY_TTL_TICKS)),
        "source": source,
    }

    # Deduplicate recent identical summaries.
    deduped = []
    for existing in entries:
        existing = _safe_dict(existing)
        if (
            _safe_str(existing.get("summary")) == entry["summary"]
            and _safe_str(existing.get("kind")) == entry["kind"]
            and _safe_int(existing.get("tick"), 0) == current_tick
        ):
            continue
        deduped.append(existing)

    deduped.append(entry)
    deduped.sort(
        key=lambda item: (
            _safe_int(_safe_dict(item).get("importance"), 0),
            _safe_int(_safe_dict(item).get("tick"), 0),
        ),
        reverse=True,
    )

    npc_state["entries"] = deduped[:MAX_HISTORY_ENTRIES_PER_NPC]
    by_npc[npc_id] = npc_state
    state["by_npc"] = by_npc
    simulation_state["npc_history_state"] = state

    return {
        "created": True,
        "entry": deepcopy(entry),
        "source": source,
    }


def recent_npc_history(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    state = ensure_npc_history_state(simulation_state)
    npc_state = _safe_dict(_safe_dict(state.get("by_npc")).get(_safe_str(npc_id)))
    entries = _safe_list(npc_state.get("entries"))
    entries = sorted(
        [_safe_dict(entry) for entry in entries],
        key=lambda item: _safe_int(item.get("tick"), 0),
        reverse=True,
    )
    return deepcopy(entries[: max(0, int(limit or 0))])
