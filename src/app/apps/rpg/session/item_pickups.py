"""Deterministic scene item pickup helpers for RPG sessions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import merge_inventory_stack
from app.apps.rpg.session.item_rewards import generate_item_rewards
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_pickup_v1"
NODE_KEYS = ("item_nodes", "pickup_nodes", "resource_nodes")


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _positive_int(value: Any, fallback: int = 1) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return fallback


def _node_id(node: dict[str, Any], ordinal: int) -> str:
    return _text(node.get("node_id") or node.get("id") or node.get("source_id"), f"scene_node_{ordinal + 1}")


def _node_remaining(node: dict[str, Any]) -> int:
    if node.get("depleted") is True:
        return 0
    for key in ("remaining", "uses", "quantity", "count"):
        value = node.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
    return 1


def _state_nodes(state: dict[str, Any]) -> tuple[dict[str, Any], str, list[Any]]:
    scene_state = _safe_dict(state.get("scene_state"))
    state["scene_state"] = scene_state
    for key in NODE_KEYS:
        if isinstance(scene_state.get(key), list):
            return scene_state, key, scene_state[key]
    scene_state["item_nodes"] = []
    return scene_state, "item_nodes", scene_state["item_nodes"]


def list_scene_item_nodes(state: dict[str, Any]) -> list[dict[str, Any]]:
    _container, _key, nodes = _state_nodes(state)
    listed: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(_safe_list(nodes)):
        node = deepcopy(_safe_dict(raw))
        node["node_id"] = _node_id(node, ordinal)
        node["remaining"] = _node_remaining(node)
        node["depleted"] = node["remaining"] <= 0
        listed.append(node)
    return listed


def _find_node(nodes: list[Any], node_id: str | None) -> tuple[int, dict[str, Any] | None]:
    wanted = _norm(node_id)
    if not wanted:
        return -1, None
    for index, raw in enumerate(nodes):
        node = _safe_dict(raw)
        names = [node.get("node_id"), node.get("id"), node.get("source_id"), node.get("name")]
        if any(_norm(name) == wanted for name in names):
            return index, node
    for index, raw in enumerate(nodes):
        node = _safe_dict(raw)
        names = [node.get("node_id"), node.get("id"), node.get("source_id"), node.get("name")]
        if any(wanted in _norm(name) for name in names):
            return index, node
    return -1, None


def _explicit_outputs(node: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = node.get("outputs") or node.get("items")
    return [deepcopy(_safe_dict(item)) for item in _safe_list(outputs) if _safe_dict(item)]


def _player_inventory(state: dict[str, Any]) -> list[dict[str, Any]]:
    player = _safe_dict(state.get("player"))
    state["player"] = player
    inventory = _safe_list(player.get("inventory"))
    player["inventory"] = inventory
    return inventory


def _record_trace(state: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    traces = _safe_list(mechanics.get("pickup_traces"))
    traces.insert(0, trace)
    mechanics["pickup_traces"] = traces[:50]
    item_traces = _safe_list(mechanics.get("item_traces"))
    item_traces.insert(0, trace)
    mechanics["item_traces"] = item_traces[:50]
    state["mechanics"] = mechanics
    return trace


def apply_scene_item_pickup(state: dict[str, Any], node_id: str | None, *, seed: str | int | None = None) -> dict[str, Any]:
    container, key, nodes = _state_nodes(state)
    index, node = _find_node(nodes, node_id)
    if node is None or index < 0:
        return {"ok": False, "error": "pickup_node_not_found", "node_id": node_id, "outputs": []}

    remaining = _node_remaining(node)
    resolved_node_id = _node_id(node, index)
    if remaining <= 0:
        return {"ok": False, "error": "pickup_node_depleted", "node_id": resolved_node_id, "outputs": []}

    outputs = _explicit_outputs(node)
    reward_trace: dict[str, Any] = {}
    if not outputs:
        table_id = _text(node.get("reward_table") or node.get("source_id") or node.get("table_id"), "generic_cache")
        reward_result = generate_item_rewards(table_id, seed=seed or resolved_node_id, context={"node_id": resolved_node_id})
        if not reward_result.get("ok"):
            return {"ok": False, "error": reward_result.get("error") or "pickup_rewards_failed", "node_id": resolved_node_id, "outputs": []}
        outputs = [deepcopy(_safe_dict(item)) for item in _safe_list(reward_result.get("outputs")) if _safe_dict(item)]
        reward_trace = _safe_dict(reward_result.get("trace"))

    inventory = _player_inventory(state)
    added = [deepcopy(merge_inventory_stack(inventory, item)) for item in outputs]

    updated_node = deepcopy(node)
    updated_node["node_id"] = resolved_node_id
    updated_node["remaining"] = max(0, remaining - 1)
    updated_node["depleted"] = updated_node["remaining"] <= 0
    nodes[index] = updated_node
    container[key] = nodes

    trace = {
        "event": "scene_item_picked_up",
        "node_id": resolved_node_id,
        "node_name": _text(node.get("name"), resolved_node_id),
        "remaining": updated_node["remaining"],
        "depleted": updated_node["depleted"],
        "outputs": [
            {
                "item_id": _text(item.get("item_id") or item.get("id")),
                "name": _text(item.get("name")),
                "quantity": _positive_int(item.get("quantity"), 1),
                "item_type": _text(item.get("item_type") or item.get("type")),
            }
            for item in outputs
        ],
        "reward_trace": reward_trace,
        "mechanics_source": MECHANICS_SOURCE,
    }
    _record_trace(state, trace)
    return {"ok": True, "node_id": resolved_node_id, "outputs": outputs, "added": added, "trace": trace}
