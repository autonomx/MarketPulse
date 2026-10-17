from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.apps.rpg.rules.interactions.inventory import add_item_to_player_inventory, player_has_item
from app.apps.rpg.rules.interactions.objects import (
    find_object_id_from_text,
    item_id_from_text,
    set_world_object,
    world_objects_from_state,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def detect_interaction_intent(player_input: str) -> Dict[str, Any]:
    text = _safe_str(player_input).strip().lower()

    action_type = ""
    if any(word in text for word in ["unlock", "use key", "use the key"]):
        action_type = "unlock"
    elif any(word in text for word in ["open"]):
        action_type = "open"
    elif any(word in text for word in ["take", "grab", "loot"]):
        action_type = "take"
    elif any(word in text for word in ["close"]):
        action_type = "close"

    if not action_type:
        return {}

    return {
        "detected": True,
        "action_type": action_type,
    }


def resolve_general_interaction(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_input: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    simulation_state = dict(_safe_dict(simulation_state))
    runtime_state = dict(_safe_dict(runtime_state))
    intent = detect_interaction_intent(player_input)

    if not intent:
        return simulation_state, {
            "resolved": False,
            "action_type": "",
            "reason": "no_interaction_intent",
        }

    action_type = _safe_str(intent.get("action_type"))
    object_id = find_object_id_from_text(simulation_state, player_input)
    objects = world_objects_from_state(simulation_state)
    obj = dict(_safe_dict(objects.get(object_id)))

    if not object_id or not obj:
        return simulation_state, _interaction_result(
            action_type=action_type,
            resolved=False,
            reason="target_not_found",
            target_id=object_id,
        )

    if not obj.get("visible", True):
        return simulation_state, _interaction_result(
            action_type=action_type,
            resolved=False,
            reason="target_not_visible",
            target_id=object_id,
            target_name=obj.get("name", object_id),
        )

    if not obj.get("reachable", True):
        return simulation_state, _interaction_result(
            action_type=action_type,
            resolved=False,
            reason="target_not_reachable",
            target_id=object_id,
            target_name=obj.get("name", object_id),
        )

    if action_type == "unlock":
        return _resolve_unlock(simulation_state, obj, player_input)

    if action_type == "open":
        return _resolve_open(simulation_state, obj)

    if action_type == "close":
        return _resolve_close(simulation_state, obj)

    if action_type == "take":
        return _resolve_take(simulation_state, obj, player_input)

    return simulation_state, _interaction_result(
        action_type=action_type,
        resolved=False,
        reason="unsupported_interaction",
        target_id=object_id,
        target_name=obj.get("name", object_id),
    )


def _resolve_unlock(
    simulation_state: Dict[str, Any],
    obj: Dict[str, Any],
    player_input: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    object_id = _safe_str(obj.get("object_id"))
    required_key_id = _safe_str(obj.get("required_key_id")).strip()
    requested_item_id = item_id_from_text(player_input)

    if not obj.get("locked"):
        return simulation_state, _interaction_result(
            action_type="unlock",
            resolved=False,
            reason="already_unlocked",
            target_id=object_id,
            target_name=obj.get("name", object_id),
        )

    if required_key_id:
        key_to_check = required_key_id
    else:
        key_to_check = requested_item_id

    if key_to_check and not player_has_item(simulation_state, key_to_check):
        return simulation_state, _interaction_result(
            action_type="unlock",
            resolved=False,
            reason="missing_required_item",
            target_id=object_id,
            target_name=obj.get("name", object_id),
            required_item_id=key_to_check,
            forbidden_narration=[
                "Do not say the object unlocks.",
                "Do not say the object opens.",
                "Do not imply the player has the required key.",
            ],
        )

    obj["locked"] = False
    simulation_state = set_world_object(simulation_state, object_id, obj)

    return simulation_state, _interaction_result(
        action_type="unlock",
        resolved=True,
        reason="unlocked",
        target_id=object_id,
        target_name=obj.get("name", object_id),
        required_item_id=key_to_check,
        state_changes=[
            {
                "kind": "object_unlocked",
                "object_id": object_id,
            }
        ],
    )


def _resolve_open(
    simulation_state: Dict[str, Any],
    obj: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    object_id = _safe_str(obj.get("object_id"))
    if obj.get("locked"):
        return simulation_state, _interaction_result(
            action_type="open",
            resolved=False,
            reason="target_locked",
            target_id=object_id,
            target_name=obj.get("name", object_id),
            required_item_id=obj.get("required_key_id", ""),
            forbidden_narration=[
                "Do not say the object opens.",
                "Do not reveal contents as accessible.",
            ],
        )

    if obj.get("open"):
        return simulation_state, _interaction_result(
            action_type="open",
            resolved=False,
            reason="already_open",
            target_id=object_id,
            target_name=obj.get("name", object_id),
        )

    obj["open"] = True
    simulation_state = set_world_object(simulation_state, object_id, obj)

    return simulation_state, _interaction_result(
        action_type="open",
        resolved=True,
        reason="opened",
        target_id=object_id,
        target_name=obj.get("name", object_id),
        revealed_contents=list(_safe_list(obj.get("contents"))),
        state_changes=[
            {
                "kind": "object_opened",
                "object_id": object_id,
            }
        ],
    )


def _resolve_close(
    simulation_state: Dict[str, Any],
    obj: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    object_id = _safe_str(obj.get("object_id"))
    if not obj.get("open"):
        return simulation_state, _interaction_result(
            action_type="close",
            resolved=False,
            reason="already_closed",
            target_id=object_id,
            target_name=obj.get("name", object_id),
        )

    obj["open"] = False
    simulation_state = set_world_object(simulation_state, object_id, obj)

    return simulation_state, _interaction_result(
        action_type="close",
        resolved=True,
        reason="closed",
        target_id=object_id,
        target_name=obj.get("name", object_id),
        state_changes=[
            {
                "kind": "object_closed",
                "object_id": object_id,
            }
        ],
    )


def _resolve_take(
    simulation_state: Dict[str, Any],
    obj: Dict[str, Any],
    player_input: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    object_id = _safe_str(obj.get("object_id"))

    if obj.get("kind") == "container" and not obj.get("open"):
        return simulation_state, _interaction_result(
            action_type="take",
            resolved=False,
            reason="container_closed",
            target_id=object_id,
            target_name=obj.get("name", object_id),
            forbidden_narration=[
                "Do not add items to inventory.",
                "Do not say the player takes contents from a closed container.",
            ],
        )

    contents = list(_safe_list(obj.get("contents")))
    if not contents:
        return simulation_state, _interaction_result(
            action_type="take",
            resolved=False,
            reason="nothing_to_take",
            target_id=object_id,
            target_name=obj.get("name", object_id),
        )

    taken_items: List[Dict[str, Any]] = []
    for item in contents:
        item = dict(_safe_dict(item))
        simulation_state, add_result = add_item_to_player_inventory(simulation_state, item)
        if add_result.get("added"):
            taken_items.append(item)

    obj["contents"] = []
    simulation_state = set_world_object(simulation_state, object_id, obj)

    return simulation_state, _interaction_result(
        action_type="take",
        resolved=True,
        reason="items_taken",
        target_id=object_id,
        target_name=obj.get("name", object_id),
        taken_items=taken_items,
        state_changes=[
            {
                "kind": "container_looted",
                "object_id": object_id,
                "items": taken_items,
            }
        ],
    )


def _interaction_result(
    *,
    action_type: str,
    resolved: bool,
    reason: str,
    target_id: str = "",
    target_name: str = "",
    required_item_id: str = "",
    revealed_contents: List[Dict[str, Any]] | None = None,
    taken_items: List[Dict[str, Any]] | None = None,
    state_changes: List[Dict[str, Any]] | None = None,
    forbidden_narration: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "source": "general_interaction_resolver",
        "action_type": action_type,
        "resolved": resolved,
        "reason": reason,
        "target_id": target_id,
        "target_name": target_name,
        "required_item_id": required_item_id,
        "revealed_contents": list(revealed_contents or []),
        "taken_items": list(taken_items or []),
        "state_changes": list(state_changes or []),
        "forbidden_narration": list(forbidden_narration or []),
    }