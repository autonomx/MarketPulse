from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str
from collections.abc import Iterable


def _norm(value: Any) -> str:
    value = _safe_str(value).lower().strip()
    value = re.sub(r"^(the|a|an|my|this|that)\s+", "", value)
    value = re.sub(r"[^a-z0-9:_ -]+", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _candidate_names(entity: Dict[str, Any]) -> List[str]:
    names = [
        _safe_str(entity.get("id")),
        _safe_str(entity.get("entity_id")),
        _safe_str(entity.get("npc_id")),
        _safe_str(entity.get("item_id")),
        _safe_str(entity.get("object_id")),
        _safe_str(entity.get("name")),
        _safe_str(entity.get("title")),
        _safe_str(entity.get("label")),
    ]
    names.extend(_safe_list(entity.get("aliases")))
    return [_norm(name) for name in names if _norm(name)]


def _score_candidate(target_ref: str, entity: Dict[str, Any]) -> int:
    target = _norm(target_ref)
    if not target:
        return 0

    names = _candidate_names(entity)
    if not names:
        return 0

    singular_target = target[:-1] if target.endswith("s") else target

    score = 0
    for name in names:
        if target == name:
            score = max(score, 100)
        elif target in name or name in target:
            score = max(score, 75)
        elif singular_target and singular_target == name:
            score = max(score, 95)
        elif singular_target and (singular_target in name or name in singular_target):
            score = max(score, 70)
        else:
            target_tokens = set(target.split())
            name_tokens = set(name.split())
            overlap = len(target_tokens & name_tokens)
            if overlap:
                score = max(score, 20 + overlap * 10)

    return score


def _entity_id(entity: Dict[str, Any]) -> str:
    for key in ("id", "entity_id", "npc_id", "item_id", "object_id"):
        value = _safe_str(entity.get(key))
        if value:
            return value
    return ""


def _entity_type(entity: Dict[str, Any]) -> str:
    explicit = _safe_str(entity.get("entity_type") or entity.get("type"))
    if explicit:
        return explicit
    if _safe_str(entity.get("npc_id")):
        return "npc"
    if _safe_str(entity.get("item_id")):
        return "item"
    if _safe_str(entity.get("object_id")):
        return "object"
    return "entity"


def _definition_id(entity: Dict[str, Any]) -> str:
    raw = _safe_dict(entity.get("raw"))
    return _safe_str(
        entity.get("definition_id")
        or raw.get("definition_id")
    )


def collect_interaction_entities(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect bounded target candidates from current deterministic state."""
    entities: List[Dict[str, Any]] = []

    sim = _safe_dict(simulation_state)
    player_state = _safe_dict(sim.get("player_state"))
    location_id = (
        _safe_str(player_state.get("location_id"))
        or _safe_str(sim.get("location_id"))
        or _safe_str(sim.get("current_location_id"))
    )

    # Active party companions.
    party_state = _safe_dict(player_state.get("party_state"))
    for companion in _safe_list(party_state.get("companions")):
        companion = _safe_dict(companion)
        npc_id = _safe_str(companion.get("npc_id"))
        if not npc_id:
            continue
        entities.append({
            "entity_id": npc_id,
            "npc_id": npc_id,
            "entity_type": "npc",
            "name": _safe_str(companion.get("name")) or npc_id.replace("npc:", ""),
            "location_id": _safe_str(companion.get("location_id")) or location_id,
            "source": "party_state",
            "raw": deepcopy(companion),
        })

    # Present NPC state.
    present_state = _safe_dict(sim.get("present_npc_state"))
    by_location = _safe_dict(present_state.get("by_location"))
    for loc_entry in by_location.values():
        loc_entry = _safe_dict(loc_entry)
        for npc in _safe_list(loc_entry.get("present_npcs")):
            npc = _safe_dict(npc)
            npc_id = _safe_str(npc.get("npc_id"))
            if not npc_id:
                continue
            entities.append({
                "entity_id": npc_id,
                "npc_id": npc_id,
                "entity_type": "npc",
                "name": _safe_str(npc.get("name")) or npc_id.replace("npc:", ""),
                "location_id": _safe_str(npc.get("location_id")) or _safe_str(loc_entry.get("location_id")),
                "source": "present_npc_state",
                "raw": deepcopy(npc),
            })

    # Scene/location objects from several possible state shapes.
    for container_key in ("scene_objects", "location_objects", "world_objects"):
        objects = sim.get(container_key)
        if isinstance(objects, dict):
            iterable: Iterable[Any] = objects.values()
        else:
            iterable = _safe_list(objects)

        for obj in iterable:
            obj = _safe_dict(obj)
            object_id = _safe_str(obj.get("object_id") or obj.get("id") or obj.get("entity_id"))
            if not object_id:
                continue
            entities.append({
                "entity_id": object_id,
                "object_id": object_id,
                "entity_type": "object",
                "name": _safe_str(obj.get("name") or obj.get("title") or object_id.replace("obj:", "")),
                "location_id": _safe_str(obj.get("location_id")) or location_id,
                "aliases": _safe_list(obj.get("aliases")),
                "source": container_key,
                "raw": deepcopy(obj),
            })

    # Loose item candidates from location/world state.
    for container_key in ("scene_items", "location_items", "world_items"):
        items = sim.get(container_key)
        if isinstance(items, dict):
            iterable = items.values()
        else:
            iterable = _safe_list(items)

        for item in iterable:
            item = _safe_dict(item)
            item_id = _safe_str(item.get("item_id") or item.get("id") or item.get("entity_id"))
            if not item_id:
                continue
            entities.append({
                "entity_id": item_id,
                "item_id": item_id,
                "entity_type": "item",
                "name": _safe_str(item.get("name") or item_id.replace("item:", "")),
                "location_id": _safe_str(item.get("location_id")) or location_id,
                "aliases": _safe_list(item.get("aliases")),
                "source": container_key,
                "raw": deepcopy(item),
            })

    # Player inventory candidates if already present.
    inventory = _safe_dict(player_state.get("inventory"))
    inventory_items = _safe_list(inventory.get("items"))
    for item in inventory_items:
        item = _safe_dict(item)
        item_id = _safe_str(item.get("item_id") or item.get("id") or item.get("entity_id"))
        if not item_id:
            continue
        entities.append({
            "entity_id": item_id,
            "item_id": item_id,
            "entity_type": "item",
            "name": _safe_str(item.get("name") or item_id.replace("item:", "")),
            "location_id": "inventory",
            "aliases": _safe_list(item.get("aliases")),
            "source": "player_inventory",
            "raw": deepcopy(item),
        })

    # Stable de-dupe by entity_id + source.
    seen = set()
    unique = []
    for entity in entities:
        entity_id = _entity_id(entity)
        key = (entity_id, _safe_str(entity.get("source")))
        if not entity_id or key in seen:
            continue
        seen.add(key)
        unique.append(entity)

    return unique


def resolve_target_ref(
    simulation_state: Dict[str, Any],
    *,
    target_ref: str,
    expected_types: List[str] | None = None,
    allowed_sources: List[str] | None = None,
) -> Dict[str, Any]:
    target_ref = _safe_str(target_ref)
    expected = set(expected_types or [])
    allowed = set(allowed_sources or [])

    if not target_ref:
        return {
            "resolved": False,
            "reason": "missing_target_ref",
            "target_ref": "",
            "source": "deterministic_target_resolver",
        }

    candidates = []
    for entity in collect_interaction_entities(simulation_state):
        entity = _safe_dict(entity)
        entity_type = _entity_type(entity)
        if expected and entity_type not in expected:
            continue

        entity_source = _safe_str(entity.get("source"))
        if allowed and entity_source not in allowed:
            continue

        score = _score_candidate(target_ref, entity)
        if score > 0:
            candidates.append((score, _entity_id(entity), entity_type, entity))

    if not candidates:
        return {
            "resolved": False,
            "reason": "target_not_found",
            "target_ref": target_ref,
            "source": "deterministic_target_resolver",
        }

    candidates.sort(key=lambda item: (-item[0], item[1]))

    best_score = candidates[0][0]
    best = [item for item in candidates if item[0] == best_score]

    if len(best) > 1:
        # Multiple systems can expose the same NPC/item through different
        # projections, for example present_npc_state and party_state. If the
        # best candidates point to the same stable entity, this is not a real
        # ambiguity.
        unique_best = {}
        for item in best:
            entity = _safe_dict(item[3])
            key = (_entity_type(entity), _entity_id(entity))
            if not key[0] or not key[1]:
                continue
            if key not in unique_best:
                unique_best[key] = item

        if len(unique_best) == 1:
            item = next(iter(unique_best.values()))
            entity = _safe_dict(item[3])
            return {
                "resolved": True,
                "reason": "duplicate_entity_candidate_resolved",
                "target_ref": target_ref,
                "target_id": _entity_id(entity),
                "target_type": _entity_type(entity),
                "score": item[0],
                "entity": deepcopy(entity),
                "duplicate_candidates": [
                    {
                        "entity_id": candidate[1],
                        "entity_type": candidate[2],
                        "score": candidate[0],
                        "name": _safe_str(_safe_dict(candidate[3]).get("name")),
                    }
                    for candidate in best[:8]
                ],
                "source": "deterministic_target_resolver",
            }

        best_entities = [_safe_dict(item[3]) for item in best]
        best_types = {_entity_type(entity) for entity in best_entities}
        best_defs = {
            _definition_id(entity)
            for entity in best_entities
            if _definition_id(entity)
        }

        if best_types == {"item"} and len(best_defs) == 1:
            # Equivalent stackable item candidates. Resolve to the first stable
            # candidate so inventory runtime can take from that stack.
            entity = _safe_dict(best[0][3])
            return {
                "resolved": True,
                "reason": "equivalent_item_stack_resolved",
                "target_ref": target_ref,
                "target_id": _entity_id(entity),
                "target_type": _entity_type(entity),
                "score": best[0][0],
                "entity": deepcopy(entity),
                "equivalent_stack_candidates": [
                    {
                        "entity_id": item[1],
                        "entity_type": item[2],
                        "score": item[0],
                        "name": _safe_str(_safe_dict(item[3]).get("name")),
                        "definition_id": _definition_id(_safe_dict(item[3])),
                    }
                    for item in best[:8]
                ],
                "source": "deterministic_target_resolver",
            }

        return {
            "resolved": False,
            "reason": "ambiguous_target",
            "target_ref": target_ref,
            "candidates": [
                {
                    "entity_id": item[1],
                    "entity_type": item[2],
                    "score": item[0],
                    "name": _safe_str(_safe_dict(item[3]).get("name")),
                }
                for item in best[:8]
            ],
            "source": "deterministic_target_resolver",
        }

    entity = _safe_dict(candidates[0][3])
    return {
        "resolved": True,
        "target_ref": target_ref,
        "target_id": _entity_id(entity),
        "target_type": _entity_type(entity),
        "score": candidates[0][0],
        "entity": deepcopy(entity),
        "source": "deterministic_target_resolver",
    }


def expected_target_types_for_action(action_kind: str) -> List[str]:
    if action_kind in {"talk", "attack"}:
        return ["npc"]
    if action_kind == "give":
        return ["item"]
    if action_kind in {"take", "drop", "equip", "unequip", "put", "repair", "consume"}:
        return ["item"]
    if action_kind in {"open", "close", "inspect", "repair", "use"}:
        return ["object", "item", "npc"]
    return []
