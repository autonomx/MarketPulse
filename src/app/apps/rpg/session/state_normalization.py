from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, cast

from app.apps.rpg.narration.ai.grounding_settings import normalize_grounding_settings
from app.apps.rpg.rules.economy.currency import currency_to_copper_value, normalize_currency
from app.apps.rpg.rules.items.inventory_state import add_inventory_items, normalize_inventory_state
from app.apps.rpg.rules.items.world_items import ensure_world_item_state
from app.apps.rpg.world.memory.actor_memory_state import ensure_actor_memory_state
from app.apps.rpg.world.memory.memory_state import ensure_memory_state
from app.apps.rpg.world.memory.world_memory_state import ensure_world_memory_state
from app.apps.rpg.rules.player import ensure_player_party, ensure_player_state
from app.apps.rpg.rules.player.player_progression_state import ensure_player_progression_state
from app.apps.rpg.narration.presentation.personality_state import ensure_personality_state
from app.apps.rpg.narration.presentation.visual_state import ensure_visual_state
from app.apps.rpg.world.conversation_settings import normalize_conversation_settings
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_DEFAULT_STORY_POLICY = {
    "save_load_stable": True,
    "strict_replay": False,
    "record_replay_artifacts": False,
}
_ALLOWED_IDLE_SECONDS = (15, 30, 60, 300, 600)
_ALLOWED_REACTION_STYLES = ("minimal", "normal", "lively")
_FAST_TURN_DEFAULTS = {
    "enable_action_advisory": True,
    "enable_semantic_action_advisory": True,
    "enable_live_narration_llm": True,
    "enable_narration_retry": False,
    "enable_fast_live_narrator_mode": True,
    "enable_continuity_grounding": True,
    "compact_save": True,
}
_MAX_NPC_REACTION_RECORDS = 64
_MAX_INTERACTION_REACTION_STATE = 16

DEFAULT_NPC_PROFILE_SETTINGS: dict[str, Any] = {
    "auto_create_on_introduction": True,
    "allow_manual_create": True,
    "draft_with_llm_on_create": False,
}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _copy_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _merge_stepped_simulation_state(
    authoritative_state: dict[str, Any],
    stepped_state: dict[str, Any],
) -> dict[str, Any]:
    """Merge stepped world state over the authoritative turn state."""

    authoritative_state = _ensure_simulation_state(_safe_dict(authoritative_state))
    stepped_state = _safe_dict(stepped_state)
    if not stepped_state:
        return authoritative_state

    merged_state = deepcopy(authoritative_state)
    for key, value in stepped_state.items():
        merged_state[key] = deepcopy(value)
    return _ensure_simulation_state(merged_state)


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    try:
        return bool(value)
    except Exception:
        return default


def npc_profile_settings(settings: dict[str, Any]) -> dict[str, Any]:
    configured = _safe_dict(settings.get("npc_profile_generation"))
    return {
        "auto_create_on_introduction": bool(
            configured.get("auto_create_on_introduction", True)
        ),
        "allow_manual_create": bool(
            configured.get("allow_manual_create", True)
        ),
        "draft_with_llm_on_create": bool(
            configured.get("draft_with_llm_on_create", False)
        ),
    }


def _normalize_final_narration_text(text: str) -> str:
    text = _safe_str(text).strip()
    if not text:
        return ""

    normalized_lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(_safe_str(raw_line).split()).strip()
        if line:
            normalized_lines.append(line)
        elif normalized_lines and normalized_lines[-1] != "":
            normalized_lines.append("")

    text = "\n".join(normalized_lines).strip()
    if text and not text.endswith("...") and text[-1] not in ".!?\"'":
        text += "."
    return text


def _normalize_runtime_settings(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    result: dict[str, Any] = {}
    result["mode"] = _safe_str(value.get("mode") or "live").strip().lower() or "live"
    interaction_duration_mode = _safe_str(
        value.get("interaction_duration_mode") or "until_next_command"
    ).strip().lower()
    if interaction_duration_mode not in {"ticks", "until_next_command"}:
        interaction_duration_mode = "ticks"

    interaction_duration_ticks = _safe_int(value.get("interaction_duration_ticks"), 5)
    if interaction_duration_ticks < 1:
        interaction_duration_ticks = 1
    if interaction_duration_ticks > 20:
        interaction_duration_ticks = 20

    result["interaction_duration_mode"] = interaction_duration_mode
    result["interaction_duration_ticks"] = interaction_duration_ticks
    result["interaction_trace"] = _safe_bool(value.get("interaction_trace"), True)
    rl = value.get("response_length")
    if isinstance(rl, str):
        rl_value = rl.strip().lower()
        result["response_length"] = rl_value if rl_value in ("short", "medium", "long") else "short"
    elif isinstance(rl, dict):
        fallback = str(rl.get("narrator_length") or rl.get("character_length") or "").strip().lower()
        result["response_length"] = fallback if fallback in ("short", "medium", "long") else "short"
    else:
        result["response_length"] = "short"
    ics = value.get("idle_conversation_seconds")
    try:
        ics = int(cast(Any, ics))
    except (TypeError, ValueError):
        ics = 15
    result["idle_conversation_seconds"] = ics if ics in _ALLOWED_IDLE_SECONDS else 15
    for bkey in (
        "idle_conversations_enabled",
        "idle_npc_to_player_enabled",
        "idle_npc_to_npc_enabled",
        "follow_reactions_enabled",
        "console_debug_enabled",
        "world_events_panel_enabled",
    ):
        result[bkey] = bool(value.get(bkey, True))
    rs = value.get("reaction_style")
    result["reaction_style"] = (
        rs if isinstance(rs, str) and rs.strip().lower() in _ALLOWED_REACTION_STYLES else "normal"
    )
    result["verbose_semantic_trace"] = _safe_bool(value.get("verbose_semantic_trace"), False)
    # Preserve and normalize nested conversation_settings so UI payloads survive the
    # normalization round-trip without being silently dropped.
    raw_conv = value.get("conversation_settings")
    if isinstance(raw_conv, dict):
        result["conversation_settings"] = normalize_conversation_settings(raw_conv)
    # Preserve npc_profile_generation settings.
    raw_npc_profile_gen = value.get("npc_profile_generation")
    if isinstance(raw_npc_profile_gen, dict):
        result["npc_profile_generation"] = npc_profile_settings(value)

    raw_grounding = value.get("grounding")
    result["grounding"] = normalize_grounding_settings(raw_grounding)

    return result


def _normalize_story_policy(runtime_state: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    raw = runtime_state.get("story_policy")
    if not isinstance(raw, dict):
        raw = {}
    result = dict(_DEFAULT_STORY_POLICY)
    for key in _DEFAULT_STORY_POLICY.keys():
        if raw.get(key) is not None:
            result[key] = bool(raw.get(key))
    return result


def _story_policy_record_replay_artifacts(runtime_state: dict[str, Any]) -> bool:
    return bool(_normalize_story_policy(runtime_state).get("record_replay_artifacts", False))


def _normalize_performance_settings(runtime_state: dict[str, Any]) -> dict[str, Any]:
    perf = {}
    if isinstance(runtime_state, dict):
        perf = dict(runtime_state.get("performance") or {})
    fast = bool(perf.get("fast_turn_mode", False))
    defaults = _FAST_TURN_DEFAULTS if fast else {
        "enable_action_advisory": True,
        "enable_semantic_action_advisory": True,
        "enable_live_narration_llm": True,
        "enable_narration_retry": False,
        "enable_fast_live_narrator_mode": False,
        "enable_continuity_grounding": True,
        "compact_save": False,
    }
    result: dict[str, Any] = {"fast_turn_mode": fast}
    for key, default_val in defaults.items():
        val = perf.get(key)
        result[key] = bool(val) if val is not None else default_val
    result["live_narrator_temperature"] = float(perf.get("live_narrator_temperature", 0.2) or 0.2)
    result["live_narrator_top_p"] = float(perf.get("live_narrator_top_p", 0.9) or 0.9)
    result["continuity_turn_window"] = int(perf.get("continuity_turn_window", 3) or 3)
    return result


def _ensure_semantic_action_runtime_state(runtime_state: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _copy_dict(runtime_state)
    runtime_state.setdefault("semantic_action_records", [])
    runtime_state.setdefault("semantic_action_index", {})
    return runtime_state


def _ensure_npc_reaction_runtime_state(runtime_state: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _copy_dict(runtime_state)
    runtime_state.setdefault("npc_reaction_records", [])
    runtime_state.setdefault("interaction_reaction_state", [])
    records = _safe_list(runtime_state.get("npc_reaction_records"))
    runtime_state["npc_reaction_records"] = records[-_MAX_NPC_REACTION_RECORDS:]
    state_rows = _safe_list(runtime_state.get("interaction_reaction_state"))
    runtime_state["interaction_reaction_state"] = state_rows[-_MAX_INTERACTION_REACTION_STATE:]
    return runtime_state


def _ensure_active_interactions(simulation_state: dict[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    interactions = _safe_list(simulation_state.get("active_interactions"))
    simulation_state["active_interactions"] = interactions
    return simulation_state


def _normalize_social_axes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in _safe_list(items)[:4]:
        item = _safe_dict(item)
        axis = _safe_str(item.get("axis")).strip().lower()
        delta = _safe_int(item.get("delta"), 0)
        if not axis or delta == 0:
            continue
        if delta > 2:
            delta = 2
        if delta < -2:
            delta = -2
        key = (axis, delta)
        if key in seen:
            continue
        seen.add(key)
        out.append({"axis": axis, "delta": delta})
    return out


def _normalize_structured_action(action: Any, player_input: str = "") -> dict[str, Any]:
    normalized = _safe_dict(action)
    if not normalized:
        raw_input = _safe_str(player_input).strip()
        if raw_input.startswith("{") and raw_input.endswith("}"):
            try:
                normalized = _safe_dict(json.loads(raw_input))
            except Exception:
                normalized = {}

    if not normalized:
        return {}

    if normalized.get("action_type"):
        action_type = _safe_str(normalized.get("action_type")).strip().lower()
        if action_type == "talk":
            normalized["action_type"] = "persuade"
        elif action_type == "threaten":
            normalized["action_type"] = "intimidate"
        elif action_type == "threat":
            normalized["action_type"] = "threat"
        elif action_type == "social":
            normalized["action_type"] = "social_activity"
        normalized.setdefault(
            "target_id",
            _safe_str(normalized.get("target_id") or normalized.get("npc_id")).strip(),
        )
        return normalized

    legacy_type = _safe_str(normalized.get("type")).strip().lower()
    legacy_action = _safe_str(normalized.get("action")).strip().lower()
    npc_id = _safe_str(normalized.get("npc_id")).strip()
    npc_name = _safe_str(normalized.get("npc_name")).strip()

    if legacy_type == "npc_action":
        if legacy_action == "talk":
            return {
                "action_type": "persuade",
                "npc_id": npc_id,
                "npc_name": npc_name,
                "target_id": npc_id,
                "interaction": "talk",
                "difficulty": "normal",
            }
        if legacy_action == "threaten":
            return {
                "action_type": "intimidate",
                "npc_id": npc_id,
                "npc_name": npc_name,
                "target_id": npc_id,
                "interaction": "threaten",
                "difficulty": "normal",
            }

    normalized.setdefault("target_id", _safe_str(normalized.get("target_id") or npc_id).strip())
    if normalized.get("type") and not normalized.get("action_type"):
        normalized["action_type"] = _safe_str(normalized.get("type")).strip()
    return normalized


def _coerce_starting_inventory_items(resources: dict[str, Any]) -> list[dict[str, Any]]:
    resources = _safe_dict(resources)
    items: list[dict[str, Any]] = []

    for key, raw_value in sorted(resources.items()):
        qty = int(raw_value or 0)
        if qty <= 0:
            continue

        resource_id = _safe_str(key).strip().lower()
        if not resource_id or resource_id == "gold":
            continue

        item_id = resource_id
        name = resource_id.replace("_", " ").title()
        items.append({
            "item_id": item_id,
            "qty": qty,
            "name": name,
        })

    return items


def _apply_starting_resources_to_player_state(
    simulation_state: dict[str, Any],
    setup_payload: dict[str, Any],
) -> dict[str, Any]:
    simulation_state = _copy_dict(simulation_state)
    setup_payload = _safe_dict(setup_payload)

    simulation_state = ensure_player_state(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    player_state = ensure_player_progression_state(player_state)

    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    currency = _safe_dict(inventory_state.get("currency"))
    items = _safe_list(inventory_state.get("items"))

    starting_resources = _safe_dict(setup_payload.get("starting_resources"))
    if not starting_resources:
        player_state["inventory_state"] = inventory_state
        simulation_state["player_state"] = player_state
        return simulation_state

    currency = normalize_currency(currency)

    current_currency_value = currency_to_copper_value(currency)
    starting_currency = normalize_currency({
        "gold": starting_resources.get("gold", 0),
        "silver": starting_resources.get("silver", 0),
        "copper": starting_resources.get("copper", 0),
    })

    if current_currency_value <= 0 and currency_to_copper_value(starting_currency) > 0:
        currency = starting_currency

    if not items:
        bootstrap_items = _coerce_starting_inventory_items(starting_resources)
        if bootstrap_items:
            inventory_state = add_inventory_items(inventory_state, bootstrap_items)

    inventory_state["currency"] = currency
    player_state["inventory_state"] = normalize_inventory_state(inventory_state)
    simulation_state["player_state"] = player_state
    return simulation_state


def _ensure_simulation_state(simulation_state: dict[str, Any]) -> dict[str, Any]:
    simulation_state = _copy_dict(simulation_state)
    simulation_state = ensure_player_state(simulation_state)
    simulation_state = ensure_player_party(simulation_state)
    simulation_state = ensure_memory_state(simulation_state)
    simulation_state = ensure_actor_memory_state(simulation_state)
    simulation_state = ensure_world_memory_state(simulation_state)
    simulation_state = ensure_personality_state(simulation_state)
    simulation_state = ensure_visual_state(simulation_state)
    simulation_state = ensure_world_item_state(simulation_state)
    simulation_state = _ensure_active_interactions(simulation_state)

    player_state = _safe_dict(simulation_state.get("player_state"))
    player_state = ensure_player_progression_state(player_state)
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    player_state["inventory_state"] = inventory_state
    simulation_state["player_state"] = player_state
    return simulation_state


def _normalize_active_interactions(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> list[dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)

    raw_items = _safe_list(simulation_state.get("active_interactions"))
    if not raw_items:
        single = _safe_dict(
            simulation_state.get("active_interaction")
            or runtime_state.get("active_interaction")
        )
        if single:
            raw_items = [single]

    out: list[dict[str, Any]] = []
    for item in raw_items:
        item = _safe_dict(item)
        if not item:
            continue
        interaction_id = _safe_str(item.get("id")) or _safe_str(item.get("interaction_id"))
        interaction_type = _safe_str(item.get("type")) or "interaction"
        interaction_subtype = _safe_str(item.get("subtype")) or interaction_type
        scene_id = _safe_str(item.get("scene_id"))
        location_id = _safe_str(item.get("location_id"))
        phase = _safe_str(item.get("phase"))
        resolved = bool(item.get("resolved"))
        winner = _safe_str(item.get("winner"))

        participants = [_safe_str(x) for x in _safe_list(item.get("participants")) if _safe_str(x)]
        if not participants:
            opponent_id = _safe_str(item.get("opponent_id")) or _safe_str(item.get("npc_id"))
            participants = ["player"] + ([opponent_id] if opponent_id else [])

        display_name = (
            _safe_str(item.get("opponent_name"))
            or _safe_str(item.get("npc_name"))
            or _safe_str(item.get("target_name"))
            or _safe_str(item.get("name"))
            or "your opponent"
        )

        state = _safe_dict(item.get("state"))
        if not state:
            state = {
                "player_progress": item.get("player_progress"),
                "opponent_progress": item.get("npc_progress"),
                "momentum": item.get("momentum_side"),
                "advantage": item.get("advantage"),
                "crowd_attention": item.get("crowd_attention"),
                "stakes": item.get("stakes"),
                "tone": item.get("tone"),
                "clue_found": item.get("clue_found"),
            }

        out.append(
            {
                "id": interaction_id or f"{interaction_type}:{interaction_subtype}:{scene_id or location_id or 'unknown'}",
                "type": interaction_type,
                "subtype": interaction_subtype,
                "scene_id": scene_id,
                "location_id": location_id,
                "phase": phase,
                "participants": participants,
                "display_name": display_name,
                "resolved": resolved,
                "winner": winner,
                "state": state,
            }
        )
    return out
