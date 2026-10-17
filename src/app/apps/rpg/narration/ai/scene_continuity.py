"""Persistent multi-tick scene continuity for RPG ambient scenes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


_MAX_ACTIVE_SCENES = 4
_MAX_SCENE_HISTORY = 16


def ensure_scene_runtime_state(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    runtime_state = dict(_safe_dict(runtime_state))
    scene_runtime = dict(_safe_dict(runtime_state.get("scene_runtime")))
    scene_runtime.setdefault("active_scenes", [])
    scene_runtime["active_scenes"] = _safe_list(scene_runtime.get("active_scenes"))[-_MAX_ACTIVE_SCENES:]
    scene_runtime.setdefault("recent_scene_ids", [])
    scene_runtime["recent_scene_ids"] = _safe_list(scene_runtime.get("recent_scene_ids"))[-_MAX_SCENE_HISTORY:]
    scene_runtime.setdefault("recent_scene_pairs", [])
    scene_runtime["recent_scene_pairs"] = _safe_list(scene_runtime.get("recent_scene_pairs"))[-_MAX_SCENE_HISTORY:]
    scene_runtime["last_scene_tick"] = int(scene_runtime.get("last_scene_tick", -999) or -999)
    runtime_state["scene_runtime"] = scene_runtime
    return runtime_state


def start_persistent_scene(
    runtime_state: Dict[str, Any],
    selected_scene: Dict[str, Any],
    current_tick: int,
) -> Dict[str, Any]:
    runtime_state = ensure_scene_runtime_state(runtime_state)
    scene_runtime = dict(_safe_dict(runtime_state.get("scene_runtime")))
    active_scenes = list(_safe_list(scene_runtime.get("active_scenes")))
    selected_scene = dict(_safe_dict(selected_scene))
    new_scene_id = _safe_str(selected_scene.get("scene_id"))

    for existing in active_scenes:
        if _safe_str(_safe_dict(existing).get("scene_id")) == new_scene_id:
            return runtime_state

    scene = {
        "scene_id": _safe_str(selected_scene.get("scene_id")),
        "scene_kind": _safe_str(selected_scene.get("scene_kind")),
        "topic": _safe_str(selected_scene.get("topic")),
        "location_id": _safe_str(selected_scene.get("location_id")),
        "participants": [str(x) for x in _safe_list(selected_scene.get("participants")) if str(x).strip()],
        "primary_speaker_id": _safe_str(selected_scene.get("primary_speaker_id")),
        "secondary_speaker_id": _safe_str(selected_scene.get("secondary_speaker_id")),
        "player_pull": _safe_str(selected_scene.get("player_pull") or "overhear"),
        "opening_tied": bool(selected_scene.get("opening_tied")),
        "priority": float(selected_scene.get("priority", 0.0) or 0.0),
        "tension": float(selected_scene.get("priority", 0.0) or 0.0),
        "beat_cursor": 0,
        "max_beats": min(max(int(selected_scene.get("beats", 2) or 2) + 1, 2), 4),
        "started_tick": int(current_tick),
        "last_advanced_tick": int(current_tick),
        "expiry_tick": int(current_tick) + 6,
        "status": "active",
        "consequence_emitted": False,
    }

    active_scenes.append(scene)
    scene_runtime["active_scenes"] = active_scenes[-_MAX_ACTIVE_SCENES:]
    runtime_state["scene_runtime"] = scene_runtime
    return runtime_state


def get_active_scene_candidates(
    runtime_state: Dict[str, Any],
    simulation_state: Dict[str, Any],
    current_tick: int,
) -> List[Dict[str, Any]]:
    runtime_state = ensure_scene_runtime_state(runtime_state)
    simulation_state = _safe_dict(simulation_state)
    scene_runtime = _safe_dict(runtime_state.get("scene_runtime"))
    active_scenes = _safe_list(scene_runtime.get("active_scenes"))
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    player_loc = _safe_str(player_state.get("location_id"))

    candidates: List[Dict[str, Any]] = []
    for scene in active_scenes:
        scene = _safe_dict(scene)
        if _safe_str(scene.get("status")) != "active":
            continue
        if int(scene.get("expiry_tick", -999) or -999) < current_tick:
            continue
        if _safe_str(scene.get("location_id")) != player_loc:
            continue

        participants = [p for p in _safe_list(scene.get("participants")) if _safe_str(p)]
        if not participants:
            continue

        everyone_present = True
        for npc_id in participants:
            npc = _safe_dict(npc_index.get(npc_id))
            if _safe_str(npc.get("location_id")) != player_loc:
                everyone_present = False
                break
        if not everyone_present:
            continue

        candidates.append(scene)

    candidates.sort(
        key=lambda s: (
            -float(s.get("tension", 0.0) or 0.0),
            -float(s.get("priority", 0.0) or 0.0),
            _safe_str(s.get("scene_id")),
        )
    )
    return candidates


def select_continuing_scene(
    runtime_state: Dict[str, Any],
    simulation_state: Dict[str, Any],
    current_tick: int,
) -> Optional[Dict[str, Any]]:
    candidates = get_active_scene_candidates(runtime_state, simulation_state, current_tick)
    for scene in candidates:
        last_tick = int(scene.get("last_advanced_tick", -999) or -999)
        if (current_tick - last_tick) >= 1:
            return scene
    return None


def advance_scene(
    runtime_state: Dict[str, Any],
    scene_id: str,
    current_tick: int,
    *,
    player_ignored: bool = True,
) -> Dict[str, Any]:
    runtime_state = ensure_scene_runtime_state(runtime_state)
    scene_runtime = dict(_safe_dict(runtime_state.get("scene_runtime")))
    active_scenes = []

    for scene in _safe_list(scene_runtime.get("active_scenes")):
        scene = dict(_safe_dict(scene))
        if _safe_str(scene.get("scene_id")) != scene_id:
            active_scenes.append(scene)
            continue

        scene["beat_cursor"] = int(scene.get("beat_cursor", 0) or 0) + 1
        scene["last_advanced_tick"] = int(current_tick)
        tension = float(scene.get("tension", 0.0) or 0.0)
        if player_ignored:
            tension = min(1.0, tension + 0.08)
        scene["tension"] = tension

        if scene["beat_cursor"] >= int(scene.get("max_beats", 2) or 2):
            scene["status"] = "resolved"
        elif int(scene.get("expiry_tick", -999) or -999) < current_tick:
            scene["status"] = "expired"

        active_scenes.append(scene)

    scene_runtime["active_scenes"] = active_scenes[-_MAX_ACTIVE_SCENES:]
    runtime_state["scene_runtime"] = scene_runtime
    return runtime_state


def compact_finished_scenes(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    runtime_state = ensure_scene_runtime_state(runtime_state)
    scene_runtime = dict(_safe_dict(runtime_state.get("scene_runtime")))
    recent_ids = list(_safe_list(scene_runtime.get("recent_scene_ids")))
    recent_pairs = list(_safe_list(scene_runtime.get("recent_scene_pairs")))
    still_active = []

    for scene in _safe_list(scene_runtime.get("active_scenes")):
        scene = _safe_dict(scene)
        status = _safe_str(scene.get("status"))
        if status == "active":
            still_active.append(scene)
            continue

        scene_id = _safe_str(scene.get("scene_id"))
        pair_key = f"{_safe_str(scene.get('primary_speaker_id'))}:{_safe_str(scene.get('secondary_speaker_id'))}"
        if scene_id:
            recent_ids.append(scene_id)
        if pair_key != ":":
            recent_pairs.append(pair_key)

    scene_runtime["recent_scene_ids"] = recent_ids[-_MAX_SCENE_HISTORY:]
    scene_runtime["recent_scene_pairs"] = recent_pairs[-_MAX_SCENE_HISTORY:]
    scene_runtime["active_scenes"] = still_active[-_MAX_ACTIVE_SCENES:]
    runtime_state["scene_runtime"] = scene_runtime
    return runtime_state


def build_continuation_beats(
    scene: Dict[str, Any],
    simulation_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    scene = _safe_dict(scene)
    simulation_state = _safe_dict(simulation_state)
    npc_index = _safe_dict(simulation_state.get("npc_index"))

    participants = [p for p in _safe_list(scene.get("participants")) if _safe_str(p)]
    if len(participants) < 2:
        return []

    a_id = _safe_str(scene.get("primary_speaker_id") or participants[0])
    b_id = _safe_str(scene.get("secondary_speaker_id") or participants[1])
    a_name = _safe_str(_safe_dict(npc_index.get(a_id)).get("name") or a_id)
    b_name = _safe_str(_safe_dict(npc_index.get(b_id)).get("name") or b_id)
    topic = _safe_str(scene.get("topic"))
    scene_kind = _safe_str(scene.get("scene_kind"))
    scene_id = _safe_str(scene.get("scene_id"))
    location_id = _safe_str(scene.get("location_id"))
    beat_cursor = int(scene.get("beat_cursor", 0) or 0)
    tension = float(scene.get("tension", 0.0) or 0.0)

    text_a = f"{a_name} keeps pressing the point."
    text_b = f"{b_name} does not look convinced."

    if topic == "opening_conflict":
        if beat_cursor == 1:
            text_a = f"{a_name} glances around. \"We are running out of time.\""
            text_b = f"{b_name} folds their arms. \"Then stop acting like this can wait.\""
        elif beat_cursor >= 2:
            text_a = f"{a_name}'s voice tightens. \"If this goes wrong, it will be on all of us.\""
            text_b = f"{b_name} turns sharply. \"Then maybe the player deserves the truth.\""
    elif topic == "recent_incident":
        if beat_cursor == 1:
            text_a = f"{a_name} lowers their voice. \"People are already talking.\""
            text_b = f"{b_name} answers, \"And by tonight, it will be worse.\""
        elif beat_cursor >= 2:
            text_a = f"{a_name} looks unsettled. \"This is spreading faster than anyone expected.\""
            text_b = f"{b_name} mutters, \"Then we act now, or we lose control.\""
    elif scene_kind == "argument":
        if beat_cursor == 1:
            text_a = f"{a_name} snaps, \"You are not listening.\""
            text_b = f"{b_name} fires back, \"I listened. I just disagree.\""
        elif beat_cursor >= 2:
            text_a = f"{a_name} steps closer, tension visible in every word."
            text_b = f"{b_name} refuses to back down."

    beats = [
        {
            "kind": "npc_to_npc",
            "speaker_id": a_id,
            "speaker_name": a_name,
            "target_id": b_id,
            "target_name": b_name,
            "text_hint": text_a,
            "reason": f"{topic}_continuation",
            "priority": min(1.0, float(scene.get("priority", 0.0) or 0.0) + tension * 0.1),
            "location_id": location_id,
            "scene_id": scene_id,
            "scene_kind": scene_kind,
            "beat_index": beat_cursor * 2,
        },
        {
            "kind": "npc_to_npc",
            "speaker_id": b_id,
            "speaker_name": b_name,
            "target_id": a_id,
            "target_name": a_name,
            "text_hint": text_b,
            "reason": f"{topic}_continuation",
            "priority": max(0.0, float(scene.get("priority", 0.0) or 0.0) - 0.03 + tension * 0.08),
            "location_id": location_id,
            "scene_id": scene_id,
            "scene_kind": scene_kind,
            "beat_index": beat_cursor * 2 + 1,
        },
    ]

    if _safe_str(scene.get("player_pull")) == "address_player" and beat_cursor >= 1:
        beats.append(
            {
                "kind": "npc_to_player",
                "speaker_id": b_id,
                "speaker_name": b_name,
                "target_id": "player",
                "target_name": "you",
                "text_hint": f"{b_name} turns toward you. \"Well? You heard enough. Say something.\"",
                "reason": f"{topic}_player_pull",
                "priority": min(1.0, float(scene.get("priority", 0.0) or 0.0) + 0.08),
                "location_id": location_id,
                "scene_id": scene_id,
                "scene_kind": scene_kind,
                "beat_index": beat_cursor * 2 + 2,
            }
        )

    return beats[:3]


def maybe_build_scene_consequence(
    scene: Dict[str, Any],
    simulation_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    scene = _safe_dict(scene)
    if _safe_str(scene.get("status")) != "resolved":
        return None
    if bool(scene.get("consequence_emitted")):
        return None

    topic = _safe_str(scene.get("topic"))
    location_id = _safe_str(scene.get("location_id"))
    tension = float(scene.get("tension", 0.0) or 0.0)
    if tension < 0.65:
        return None

    event_type = "public_disturbance"
    text = "The argument leaves a visible strain in the air."
    if topic == "opening_conflict":
        event_type = "quest_prompt"
        text = "The unresolved dispute finally spills toward the player."
    elif topic == "recent_incident":
        event_type = "rumor_spread"
        text = "What began as a quiet exchange now spreads into open concern."

    return {
        "kind": "world_event",
        "priority": min(1.0, 0.55 + tension * 0.2),
        "interrupt": False,
        "speaker_id": "",
        "speaker_name": "",
        "target_id": "",
        "target_name": "",
        "location_id": location_id,
        "text": text,
        "structured": {
            "event_type": event_type,
            "from_scene_id": _safe_str(scene.get("scene_id")),
            "topic": topic,
        },
        "source_event_ids": [],
        "source": "scene_continuity",
    }