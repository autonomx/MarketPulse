"""Scene grounding for narration prompts: location names and actors resolved from state.

Moved out of ``session.combat_intent`` (R-3) so the narrator's prompt builder
does not reach into the turn pipeline; the session imports these back.
"""
from __future__ import annotations

from typing import Any, cast

from app.apps.rpg.foundation.safe_values import (
    dict_copy as _copy_dict,
    safe_dict as _safe_dict,
    safe_list as _safe_list,
    safe_str as _safe_str,
)


def _stable_unique_strs(values: list[Any]) -> list[str]:
    seen = set()
    out: list[str] = []
    for raw in values:
        value = _safe_str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_prompt_location_name(value: str, grounded_fallback: str) -> str:
    value = _safe_str(value).strip()
    if not value:
        return grounded_fallback
    if value.startswith("scene:tick:"):
        return grounded_fallback
    return value


def _location_key(value: str) -> str:
    return _safe_str(value).strip().replace(":", "_").replace("-", "_").lower()


def _is_location_id_text(name: str, location_id: str) -> bool:
    """A "name" that is only the id (``loc_tavern``) is not a display name."""
    return bool(name) and _location_key(name) in {_location_key(location_id), _location_key(location_id.split(":", 1)[-1])}


def _humanized_location_id(location_id: str) -> str:
    words = _location_key(location_id).split("_")
    if words and words[0] in {"loc", "location"}:
        words = words[1:]
    return " ".join(words).title()


def _resolve_location_name(
    simulation_state: dict[str, Any],
    location_id: str,
    fallback_name: str = "",
) -> str:
    """The display name of a location: the simulation's entry, the location registry,
    a stored name, then a readable form of the id -- never the raw id."""
    simulation_state = _safe_dict(simulation_state)
    location_id = _safe_str(location_id).strip()
    fallback_name = _safe_str(fallback_name).strip()
    if not location_id:
        return fallback_name
    normalized_id = _location_key(location_id)

    # Modern format: locations is an object keyed by location_id; legacy: a list.
    entries = [
        _safe_dict(entry)
        for key, entry in _safe_dict(simulation_state.get("locations")).items()
        if _location_key(key) == normalized_id
    ] + [
        _safe_dict(entry)
        for entry in _safe_list(simulation_state.get("locations"))
        if _location_key(cast(Any, _safe_dict(entry).get("location_id") or _safe_dict(entry).get("id"))) == normalized_id
    ]
    for entry in entries:
        name = _safe_str(entry.get("name") or entry.get("title")).strip()
        if name:
            return name

    from app.apps.rpg.rules.location_registry import get_location

    registered = _safe_str(get_location(location_id).get("name")).strip()
    if registered:
        return registered
    if fallback_name and not _is_location_id_text(fallback_name, location_id):
        return fallback_name
    return _humanized_location_id(location_id) or "Current Location"


def _resolve_actor_names(simulation_state: dict[str, Any], actor_ids: list[str]) -> list[str]:
    simulation_state = _safe_dict(simulation_state)
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    names: list[str] = []
    for actor_id in _stable_unique_strs(actor_ids):
        npc = _safe_dict(npc_index.get(actor_id))
        names.append(_safe_str(npc.get("name") or actor_id))
    return names


def _derive_grounded_scene_context(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    turn_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    turn_result = _safe_dict(turn_result)

    opening_text = _safe_str(runtime_state.get("opening"))
    current_scene = _safe_dict(runtime_state.get("current_scene"))
    player_state = _safe_dict(simulation_state.get("player_state"))

    player_loc_id = _safe_str(player_state.get("location_id")).strip()
    nearby_ids = _safe_list(player_state.get("nearby_npc_ids"))
    present_scene_ids = _safe_list(current_scene.get("present_npc_ids"))
    actor_objs = _safe_list(current_scene.get("actors"))
    actor_obj_ids = [
        _safe_str(_safe_dict(a).get("id") or _safe_dict(a).get("npc_id") or _safe_dict(a).get("name"))
        for a in actor_objs
    ]

    location_id = (
        player_loc_id
        or _safe_str(current_scene.get("location_id"))
        or _safe_str(turn_result.get("location_id"))
    ).strip()
    location_name = _resolve_location_name(
        simulation_state,
        location_id,
        _safe_str(current_scene.get("location_name")).strip(),
    )

    present_actor_ids = _stable_unique_strs(nearby_ids + present_scene_ids + actor_obj_ids)
    present_actor_names = _resolve_actor_names(simulation_state, present_actor_ids)

    scene_title = (
        _safe_str(current_scene.get("title")).strip()
        or _safe_str(current_scene.get("scene_title")).strip()
        or location_name
        or "Current Scene"
    )
    scene_summary = (
        _safe_str(current_scene.get("summary")).strip()
        or _safe_str(current_scene.get("scene")).strip()
        or opening_text.strip()
        or "Your adventure continues."
    )

    return {
        "scene_title": scene_title,
        "location_id": location_id,
        "location_name": location_name or "Current Location",
        "scene_summary": scene_summary,
        "present_actor_ids": present_actor_ids,
        "present_actor_names": present_actor_names,
    }


def _apply_grounded_scene_overlay(scene: dict[str, Any], grounded: dict[str, Any]) -> dict[str, Any]:
    scene = _copy_dict(_safe_dict(scene))
    grounded = _safe_dict(grounded)

    scene["title"] = _safe_str(scene.get("title")).strip() or _safe_str(grounded.get("scene_title")) or "Current Scene"
    scene["location_id"] = _safe_str(scene.get("location_id")).strip() or _safe_str(grounded.get("location_id"))
    stored_name = _safe_str(scene.get("location_name")).strip()
    if _is_location_id_text(stored_name, _safe_str(scene.get("location_id"))):
        stored_name = ""
    scene["location_name"] = stored_name or _safe_str(grounded.get("location_name")) or "Current Location"
    scene["summary"] = _safe_str(scene.get("summary")).strip() or _safe_str(grounded.get("scene_summary")) or "Your adventure continues."

    actor_names = _safe_list(grounded.get("present_actor_names"))
    if actor_names:
        scene["actors"] = actor_names

    present_ids = _safe_list(grounded.get("present_actor_ids"))
    if present_ids and not _safe_list(scene.get("present_npc_ids")):
        scene["present_npc_ids"] = present_ids

    return scene
