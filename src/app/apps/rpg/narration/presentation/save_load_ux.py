"""Product Layer A5 — Save/load UX presentation helpers.

Read-only save slot summaries and rewind preview metadata.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _sort_key_slot(slot: Dict[str, Any]) -> tuple[int, str]:
    return (
        _safe_int(slot.get("tick"), 0),
        _safe_str(slot.get("save_id")),
    )


def build_save_load_ux_payload(
    save_snapshots: List[Dict[str, Any]] | None = None,
    current_tick: int = 0,
) -> Dict[str, Any]:
    """Build save/load UX payload with sorted slots and rewind preview."""
    save_snapshots = [
        _safe_dict(v)
        for v in _safe_list(save_snapshots)
        if isinstance(v, dict)
    ]
    normalized_slots: List[Dict[str, Any]] = []
    for item in save_snapshots:
        normalized_slots.append({
            "save_id": _safe_str(item.get("save_id")),
            "tick": max(0, _safe_int(item.get("tick"), 0)),
            "version": max(1, _safe_int(item.get("version"), 1)),
            "label": _safe_str(item.get("label")) or _safe_str(item.get("save_id")),
            "integrity_hash": _safe_str(item.get("integrity_hash")),
        })
    normalized_slots = sorted(normalized_slots, key=_sort_key_slot, reverse=True)

    rewind_preview = []
    for slot in normalized_slots[:5]:
        rewind_preview.append({
            "save_id": slot["save_id"],
            "tick": slot["tick"],
            "tick_delta": max(0, _safe_int(current_tick) - slot["tick"]),
        })

    return {
        "save_load_ux": {
            "save_slots": normalized_slots,
            "rewind_preview": rewind_preview,
            "can_rewind": bool(rewind_preview),
        }
    }