"""RPG inspection compatibility helpers for gateway bridge routes."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.foundation.analytics import (
    build_tick_diff,
    build_timeline_row_diff,
    build_timeline_summary,
    build_world_events_view,
    get_timeline_tick,
    inspect_npc_reasoning,
)
from app.apps.rpg.foundation.persistence.save_schema import CURRENT_RPG_SCHEMA_VERSION
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _simulation_state_from_setup(setup_payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _safe_dict(_safe_dict(setup_payload).get("metadata"))
    return _safe_dict(metadata.get("simulation_state"))


def inspect_timeline_payload(data: dict[str, Any]) -> dict[str, Any]:
    setup_payload = _safe_dict(_safe_dict(data).get("setup_payload"))
    timeline_summary = build_timeline_summary(_simulation_state_from_setup(setup_payload))
    ticks = _safe_dict(timeline_summary.get("timeline")).get("ticks") or []
    latest_diff = build_timeline_row_diff(ticks[-2], ticks[-1]) if len(ticks) >= 2 else {}
    return {
        "ok": True,
        "schema_version": CURRENT_RPG_SCHEMA_VERSION,
        "timeline": timeline_summary,
        "latest_diff": latest_diff,
    }


def inspect_timeline_tick_payload(data: dict[str, Any]) -> dict[str, Any]:
    request = _safe_dict(data)
    setup_payload = _safe_dict(request.get("setup_payload"))
    tick = int(request.get("tick", 0) or 0)
    return {
        "ok": True,
        "tick_view": get_timeline_tick(_simulation_state_from_setup(setup_payload), tick),
    }


def inspect_tick_diff_payload(data: dict[str, Any]) -> dict[str, Any]:
    request = _safe_dict(data)
    return {
        "ok": True,
        "tick_diff": build_tick_diff(
            _safe_dict(request.get("before_state")),
            _safe_dict(request.get("after_state")),
        ),
    }


def inspect_npc_reasoning_payload(data: dict[str, Any]) -> dict[str, Any]:
    request = _safe_dict(data)
    setup_payload = _safe_dict(request.get("setup_payload"))
    npc_id = str(request.get("npc_id") or "")
    return {
        "ok": True,
        "npc_reasoning": inspect_npc_reasoning(
            _simulation_state_from_setup(setup_payload),
            npc_id,
        ),
    }


def inspect_world_events_payload(data: dict[str, Any]) -> dict[str, Any]:
    request = _safe_dict(data)
    setup_payload = _safe_dict(request.get("setup_payload"))
    return {
        "ok": True,
        "world_events": build_world_events_view(
            _simulation_state_from_setup(setup_payload),
            _safe_dict(request.get("runtime_state")),
        ),
    }
