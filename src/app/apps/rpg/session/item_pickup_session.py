"""Session-level scene item pickup bridge for RPG item actions.

This module keeps scene-item transfer route-free while adding session context
to the deterministic pickup helper. Routes, autoplay, or loadout-style actions
can call it to mutate inventory/scene nodes and receive trace-ready output.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_pickups import apply_scene_item_pickup, list_scene_item_nodes
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

PICKUP_SESSION_SOURCE = "engine_item_pickup_session_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _turn(state: dict[str, Any]) -> int:
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _enrich_trace(state: dict[str, Any], trace: dict[str, Any], *, source: str) -> dict[str, Any]:
    trace["session_event"] = "scene_item_pickup_session_applied"
    trace["session_source"] = _text(source, "world_action")
    trace["turn"] = _turn(state)
    trace["timestamp"] = _utc_now()
    trace["mechanics_source"] = PICKUP_SESSION_SOURCE
    return trace


def _outputs_summary(outputs: list[Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for raw in outputs:
        item = _safe_dict(raw)
        summary.append(
            {
                "item_id": _text(item.get("item_id") or item.get("id")),
                "name": _text(item.get("name") or item.get("display_name")),
                "quantity": int(item.get("quantity") or 1),
                "item_type": _text(item.get("item_type") or item.get("type")),
            }
        )
    return summary


def available_scene_pickups_for_session(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-depleted scene transfer nodes with deterministic summaries."""

    nodes = []
    for node in list_scene_item_nodes(state):
        if node.get("depleted") is True:
            continue
        nodes.append(
            {
                "node_id": _text(node.get("node_id")),
                "name": _text(node.get("name") or node.get("node_id")),
                "remaining": int(node.get("remaining") or 0),
                "has_explicit_outputs": bool(_safe_list(node.get("outputs") or node.get("items"))),
                "reward_table": _text(node.get("reward_table") or node.get("source_id") or node.get("table_id")),
            }
        )
    return nodes


def apply_session_scene_item_pickup(
    state: dict[str, Any],
    node_id: str | None,
    *,
    seed: str | int | None = None,
    source: str = "world_action",
) -> dict[str, Any]:
    """Apply deterministic scene-item transfer to a mutable session state."""

    state = _safe_dict(state)
    result = apply_scene_item_pickup(state, node_id, seed=seed)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "scene_item_pickup_failed",
            "node_id": result.get("node_id") or node_id,
            "outputs": [],
        }

    trace = _enrich_trace(state, _safe_dict(result.get("trace")), source=source)
    mechanics = _mechanics(state)
    # The lower-level helper records this same trace object before returning it.
    # Reassigning the heads keeps the enriched trace visible even if future helper
    # changes return a copied trace.
    for key in ("pickup_traces", "item_traces"):
        traces = _safe_list(mechanics.get(key))
        if traces:
            traces[0] = trace
        else:
            traces.insert(0, trace)
        mechanics[key] = traces[:50]

    outputs = deepcopy(_safe_list(result.get("outputs")))
    node_name = _text(trace.get("node_name") or result.get("node_id") or node_id, "scene item")
    count = sum(int(item.get("quantity") or 1) for item in outputs if isinstance(item, dict)) or len(outputs)
    detail = f"Collected {count} item{'s' if count != 1 else ''} from {node_name}."
    return {
        "ok": True,
        "node_id": result.get("node_id"),
        "detail": detail,
        "outputs": outputs,
        "added": deepcopy(_safe_list(result.get("added"))),
        "output_summary": _outputs_summary(outputs),
        "mechanics_trace": trace,
    }
