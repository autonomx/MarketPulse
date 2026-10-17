"""Phase 8 — Player-facing journal.

Automatically pulls recent events and consequences from simulation state
into a deduplicated, capped journal for the player to browse.

Bounds:
    - 200 entries maximum
    - entries are deduplicated by (type, title, text) tuple
"""
from __future__ import annotations

from typing import Any, Dict, List

from .player_scene_state import ensure_player_state
from app.apps.rpg.foundation.safe_values import list_copy as _safe_list, safe_str as _safe_str

_MAX_JOURNAL = 200


def update_journal_from_state(
    simulation_state: Dict[str, Any],
    scene: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Append recent events, consequences, and an optional scene summary
    to the player's journal, deduplicating and capping the list.

    Parameters
    ----------
    simulation_state :
        Authoritative simulation state (may contain ``events`` and
        ``consequences`` lists).
    scene :
        Optional scene dict to add a scene-level journal entry.

    Returns
    -------
    dict
        Updated simulation_state with refreshed journal_entries.
    """
    simulation_state = ensure_player_state(simulation_state)
    player_state = simulation_state["player_state"]
    tick = int(simulation_state.get("tick", 0) or 0)

    entries = _safe_list(player_state.get("journal_entries"))
    events = _safe_list(simulation_state.get("events"))
    consequences = _safe_list(simulation_state.get("consequences"))

    for item in events[-3:]:
        if not isinstance(item, dict):
            continue
        entries.append({
            "entry_id": f"journal:event:{tick}:{len(entries)}",
            "tick": tick,
            "type": "event",
            "title": _safe_str(item.get("type")) or "event",
            "text": _safe_str(item.get("summary")),
        })

    for item in consequences[-3:]:
        if not isinstance(item, dict):
            continue
        entries.append({
            "entry_id": f"journal:consequence:{tick}:{len(entries)}",
            "tick": tick,
            "type": "consequence",
            "title": _safe_str(item.get("type")) or "consequence",
            "text": _safe_str(item.get("summary")),
        })

    if isinstance(scene, dict) and scene:
        entries.append({
            "entry_id": f"journal:scene:{tick}:{len(entries)}",
            "tick": tick,
            "type": "scene",
            "title": _safe_str(scene.get("title")) or "scene",
            "text": _safe_str(scene.get("summary") or scene.get("description")),
        })

    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        key = (
            _safe_str(entry.get("type")),
            _safe_str(entry.get("title")),
            _safe_str(entry.get("text")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)

    player_state["journal_entries"] = deduped[-_MAX_JOURNAL:]
    return simulation_state