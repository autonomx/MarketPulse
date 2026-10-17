"""Scene weaver — builds short NPC-to-NPC scene threads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def build_scene_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_context = _safe_dict(player_context)

    npc_index = _safe_dict(simulation_state.get("npc_index"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    opening_runtime = _safe_dict(runtime_state.get("opening_runtime"))
    player_loc = _safe_str(player_context.get("player_location"))
    nearby_ids = set(_safe_list(player_context.get("nearby_npc_ids")))
    recent_incidents = _safe_list(player_context.get("recent_incidents"))

    candidates: List[Dict[str, Any]] = []
    opening_npc_ids = set(_safe_list(opening_runtime.get("present_npc_ids")))

    for speaker_id in sorted(npc_index.keys()):
        s_info = _safe_dict(npc_index.get(speaker_id))
        s_loc = _safe_str(s_info.get("location_id"))
        if speaker_id not in nearby_ids and s_loc != player_loc:
            continue

        s_beliefs = _safe_dict(_safe_dict(npc_minds.get(speaker_id)).get("beliefs"))

        for target_id in sorted(npc_index.keys()):
            if target_id == speaker_id:
                continue

            t_info = _safe_dict(npc_index.get(target_id))
            t_loc = _safe_str(t_info.get("location_id"))
            if t_loc != player_loc or s_loc != player_loc:
                continue

            belief = _safe_dict(s_beliefs.get(target_id))
            trust = float(belief.get("trust", 0) or 0)
            hostility = float(belief.get("hostility", 0) or 0)

            scene_kind = ""
            topic = ""
            priority = 0.0
            player_pull = "overhear"

            if hostility > 0.35:
                scene_kind = "argument"
                topic = "tension"
                priority = 0.55 + min(hostility, 1.0) * 0.15
            elif trust > 0.2 and recent_incidents:
                scene_kind = "rumor_scene"
                topic = "recent_incident"
                priority = 0.42
            elif speaker_id in opening_npc_ids or target_id in opening_npc_ids:
                scene_kind = "npc_exchange"
                topic = "opening_conflict"
                priority = 0.50

            if not scene_kind:
                continue

            if topic == "opening_conflict":
                player_pull = "address_player"

            candidates.append({
                "scene_kind": scene_kind,
                "scene_id": f"scene:{speaker_id}:{target_id}:{topic}",
                "location_id": player_loc,
                "topic": topic,
                "participants": [speaker_id, target_id],
                "primary_speaker_id": speaker_id,
                "secondary_speaker_id": target_id,
                "beats": 2,
                "priority": min(priority, 1.0),
                "interrupt": priority >= 0.7,
                "opening_tied": topic == "opening_conflict",
                "player_pull": player_pull,
            })

    candidates.sort(
        key=lambda c: (
            -float(c.get("priority", 0) or 0),
            _safe_str(c.get("scene_kind")),
            _safe_str(c.get("primary_speaker_id")),
            _safe_str(c.get("secondary_speaker_id")),
        )
    )
    return candidates[:12]


def select_scene_candidate(
    candidates: List[Dict[str, Any]],
    runtime_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    runtime_state = _safe_dict(runtime_state)
    recent_scene_ids = set(_safe_list(_safe_dict(runtime_state.get("scene_runtime")).get("recent_scene_ids")))
    recent_scene_pairs = set(_safe_list(_safe_dict(runtime_state.get("scene_runtime")).get("recent_scene_pairs")))

    for c in candidates:
        scene_id = _safe_str(c.get("scene_id"))
        pair_key = f"{_safe_str(c.get('primary_speaker_id'))}:{_safe_str(c.get('secondary_speaker_id'))}"
        if scene_id in recent_scene_ids:
            continue
        if pair_key in recent_scene_pairs:
            continue
        return c
    return None


def apply_scene_cooldowns(
    runtime_state: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    runtime_state = dict(_safe_dict(runtime_state))
    scene_runtime = dict(_safe_dict(runtime_state.get("scene_runtime")))
    recent_scene_ids = list(_safe_list(scene_runtime.get("recent_scene_ids")))
    recent_scene_pairs = list(_safe_list(scene_runtime.get("recent_scene_pairs")))

    scene_id = _safe_str(candidate.get("scene_id"))
    pair_key = f"{_safe_str(candidate.get('primary_speaker_id'))}:{_safe_str(candidate.get('secondary_speaker_id'))}"

    recent_scene_ids.append(scene_id)
    recent_scene_pairs.append(pair_key)

    scene_runtime["recent_scene_ids"] = recent_scene_ids[-16:]
    scene_runtime["recent_scene_pairs"] = recent_scene_pairs[-16:]
    runtime_state["scene_runtime"] = scene_runtime
    return runtime_state


def build_scene_beats(
    scene_candidate: Dict[str, Any],
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    scene_candidate = _safe_dict(scene_candidate)
    simulation_state = _safe_dict(simulation_state)

    npc_index = _safe_dict(simulation_state.get("npc_index"))
    speaker_id = _safe_str(scene_candidate.get("primary_speaker_id"))
    target_id = _safe_str(scene_candidate.get("secondary_speaker_id"))
    speaker_name = _safe_str(_safe_dict(npc_index.get(speaker_id)).get("name") or speaker_id)
    target_name = _safe_str(_safe_dict(npc_index.get(target_id)).get("name") or target_id)
    topic = _safe_str(scene_candidate.get("topic"))
    scene_id = _safe_str(scene_candidate.get("scene_id"))
    location_id = _safe_str(scene_candidate.get("location_id"))
    priority = float(scene_candidate.get("priority", 0) or 0)

    if topic == "opening_conflict":
        first = f"{speaker_name} keeps their voice low. \"We cannot keep delaying this.\""
        second = f"{target_name} snaps back. \"Then stop pretending this is under control.\""
    elif topic == "recent_incident":
        first = f"{speaker_name} mutters, \"Did you hear what happened?\""
        second = f"{target_name} answers, \"I heard enough to know it’s getting worse.\""
    else:
        first = f"{speaker_name} says something tense under their breath."
        second = f"{target_name} replies without much patience."

    beats = [
        {
            "kind": "npc_to_npc",
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
            "target_id": target_id,
            "target_name": target_name,
            "text_hint": first,
            "reason": topic,
            "priority": priority,
            "location_id": location_id,
            "scene_id": scene_id,
            "scene_kind": _safe_str(scene_candidate.get("scene_kind")),
            "beat_index": 0,
        },
        {
            "kind": "npc_to_npc",
            "speaker_id": target_id,
            "speaker_name": target_name,
            "target_id": speaker_id,
            "target_name": speaker_name,
            "text_hint": second,
            "reason": topic,
            "priority": max(0.0, priority - 0.04),
            "location_id": location_id,
            "scene_id": scene_id,
            "scene_kind": _safe_str(scene_candidate.get("scene_kind")),
            "beat_index": 1,
        },
    ]

    if _safe_str(scene_candidate.get("player_pull")) == "address_player":
        beats.append({
            "kind": "npc_to_player",
            "speaker_id": target_id,
            "speaker_name": target_name,
            "target_id": "player",
            "target_name": "you",
            "text_hint": f"{target_name} finally turns toward you. \"You heard that, didn’t you?\"",
            "reason": f"{topic}_pull_player",
            "priority": max(0.0, priority - 0.02),
            "location_id": location_id,
            "scene_id": scene_id,
            "scene_kind": _safe_str(scene_candidate.get("scene_kind")),
            "beat_index": 2,
        })

    return beats[:4]