from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _apply_starting_resources_to_player_state as _apply_starting_resources_to_player_state, _ensure_simulation_state as _ensure_simulation_state,
    _normalize_runtime_settings as _normalize_runtime_settings, _safe_bool as _safe_bool, _safe_dict as _safe_dict, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.genesis.creator.world_player_actions import (
    ESCALATE_CONFLICT as ESCALATE_CONFLICT,
    INTERVENE_THREAD as INTERVENE_THREAD,
    SUPPORT_FACTION as SUPPORT_FACTION,
)
from app.apps.rpg.narration.ai.world_scene_narrator import (
    narrate_scene as narrate_scene,
)
from app.apps.rpg.narration.presentation.visual_state import (
    ensure_visual_state as ensure_visual_state,
)
from app.apps.rpg.session.combat_intent import (
    _apply_grounded_scene_overlay as _apply_grounded_scene_overlay, _derive_grounded_scene_context as _derive_grounded_scene_context, _utc_now_iso as _utc_now_iso,
)
from app.apps.rpg.narration.presentation.speaker_cards import (
    build_nearby_npc_cards as build_nearby_npc_cards,
)
from app.apps.rpg.session.player_activity_runtime import (
    _build_transaction_menus_for_state as _build_transaction_menus_for_state, _derive_transaction_providers as _derive_transaction_providers,
)
from app.apps.rpg.narration.presentation.memory_inspector import (
    build_memory_ui_summary as build_memory_ui_summary,
)
from app.apps.rpg.narration.presentation.runtime_bridge_n1222 import (
    build_runtime_presentation_payload as build_runtime_presentation_payload,
)
from app.apps.rpg.genesis.creator.defaults import (
    apply_adventure_defaults as apply_adventure_defaults,
)
from app.apps.rpg.genesis.creator.world_simulation import (
    build_initial_simulation_state as build_initial_simulation_state,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _SCHEMA_VERSION as _SCHEMA_VERSION,
)
from app.apps.rpg.rules.combat.state import (
    build_empty_combat_state as build_empty_combat_state,
)
from app.apps.rpg.rules.player.player_progression_state import (
    award_player_xp as award_player_xp, award_skill_xp as award_skill_xp, ensure_player_progression_state as ensure_player_progression_state,
    resolve_level_ups as resolve_level_ups, resolve_skill_level_ups as resolve_skill_level_ups,
)
from app.apps.rpg.rules.player.player_xp_rules import (
    compute_action_player_xp as compute_action_player_xp, compute_action_skill_xp as compute_action_skill_xp, compute_stat_influence_bonus as compute_stat_influence_bonus,
)
from app.apps.rpg.session.service_runtime import (
    service_authoritative_result as service_authoritative_result,
)
from app.apps.rpg.rules.economy.transactions import (
    build_transaction_metadata as build_transaction_metadata, enrich_action_with_registry_price as enrich_action_with_registry_price,
)
from app.apps.rpg.session.inventory_runtime import (
    drop_item_action as drop_item_action, equip_item_action as equip_item_action, pickup_item_action as pickup_item_action, unequip_item_action as unequip_item_action,
)
from app.apps.rpg.rules.action_resolver import (
    resolve_player_action as resolve_player_action,
)
from app.apps.rpg.rules.economy.transaction_effects import (
    apply_transaction_effects as apply_transaction_effects,
)
from app.apps.rpg.rules.items.inventory_state import (
    normalize_inventory_state as normalize_inventory_state,
)
from app.apps.rpg.rules.economy.currency import (
    can_afford as can_afford, currency_delta as currency_delta, currency_to_copper_value as currency_to_copper_value, normalize_currency as normalize_currency,
    subtract_currency_cost as subtract_currency_cost,
)
from app.apps.rpg.rules.items.item_effects import (
    apply_item_effects as apply_item_effects,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)


def _check_opening_resolution(
    session: dict[str, Any],
) -> dict[str, Any]:
    """Check simple rule-based conditions for opening resolution.

    Returns updated opening_runtime dict.
    """
    session = _safe_dict(session)
    runtime = _safe_dict(session.get("runtime_state"))
    sim = _safe_dict(session.get("simulation_state"))
    opening_rt = _safe_dict(runtime.get("opening_runtime"))

    if not opening_rt.get("active") or opening_rt.get("opening_resolved"):
        return opening_rt

    opening_rt = dict(opening_rt)

    # Rule: player has acted on opening conflict (tick > 3 means some engagement)
    tick = int(sim.get("tick", 0) or 0)
    player_turns = len(_safe_list(runtime.get("turn_history")))

    # Simple heuristics for opening resolution
    resolved = False

    # Player engaged with key opening NPCs (at least 2 turns)
    if player_turns >= 2:
        # Check if player interacted with opening NPCs
        turn_history = _safe_list(runtime.get("turn_history"))
        opening_npcs = set(_safe_list(opening_rt.get("present_npc_ids")))
        engaged_opening_npcs = set()
        for turn in turn_history:
            turn = _safe_dict(turn)
            action = _safe_dict(turn.get("action"))
            target = _safe_str(action.get("target_id") or action.get("npc_id"))
            if target in opening_npcs:
                engaged_opening_npcs.add(target)
        if engaged_opening_npcs:
            resolved = True

    # Player left opening location
    player_state = _safe_dict(sim.get("player_state"))
    player_loc = _safe_str(player_state.get("location_id"))
    opening_loc = _safe_str(_safe_dict(_safe_dict(session.get("setup_payload")).get("opening")).get("location_id"))
    if opening_loc and player_loc and player_loc != opening_loc and player_turns >= 1:
        resolved = True

    # Tick-based fallback: after tick 10, opening bias decays
    if tick >= 10:
        resolved = True

    if resolved:
        opening_rt["opening_resolved"] = True
        opening_rt["active"] = False

    return opening_rt


# ── Known NPC tracking ─────────────────────────────────────────────────────

_MAX_KNOWN_NPC_IDS = 64

def _update_known_npc_ids(runtime_state: dict[str, Any], simulation_state: dict[str, Any]) -> dict[str, Any]:
    """Update known NPC list from current player presence.

    Adds nearby NPCs to known list (append only, never remove).
    Maintains hard cap to prevent unbounded growth.
    Present NPCs are always drawn from current simulation state, not known list.
    """
    runtime_state = dict(runtime_state) if isinstance(runtime_state, dict) else {}
    simulation_state = _safe_dict(simulation_state)

    known = _safe_list(runtime_state.get("known_npc_ids", []))
    known_set = set(known)

    # Add currently nearby NPCs
    player = _safe_dict(simulation_state.get("player_state"))
    nearby = _safe_list(player.get("nearby_npc_ids", []))

    for npc_id in nearby:
        npc_id = _safe_str(npc_id).strip()
        if npc_id and npc_id not in known_set:
            known.append(npc_id)
            known_set.add(npc_id)

    # Cap to maximum size (keep most recent entries)
    if len(known) > _MAX_KNOWN_NPC_IDS:
        known = known[-_MAX_KNOWN_NPC_IDS:]

    runtime_state["known_npc_ids"] = known
    return runtime_state


def _build_opening_text(generated: dict[str, Any]) -> str:
    opening_situation = _safe_dict(generated.get("opening_situation"))
    parts: list[str] = []
    summary = _safe_str(opening_situation.get("summary")).strip()
    location = _safe_str(opening_situation.get("location")).strip()
    present_actors = [str(v) for v in _safe_list(opening_situation.get("present_actors")) if str(v).strip()]
    if summary:
        parts.append(summary)
    if location:
        parts.append(f"You find yourself in {location}.")
    if present_actors:
        parts.append(f"Present: {', '.join(present_actors)}.")
    return " ".join(parts).strip() or "Your adventure begins…"


def _build_world_payload(setup: dict[str, Any], generated: dict[str, Any], canon_summary: dict[str, Any]) -> dict[str, Any]:
    world_frame = _safe_dict(generated.get("world_frame"))
    return {
        "title": _safe_str(setup.get("title") or world_frame.get("title")),
        "genre": _safe_str(setup.get("genre")),
        "setting": _safe_str(setup.get("setting")),
        "premise": _safe_str(setup.get("premise")),
        "summary": _safe_str(canon_summary.get("summary")),
    }


def _build_npc_cards(generated: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for npc in _safe_list(generated.get("seed_npcs")):
        npc = _safe_dict(npc)
        if not npc:
            continue
        cards.append({
            "id": _safe_str(npc.get("npc_id")),
            "name": _safe_str(npc.get("name") or "Unknown"),
            "role": _safe_str(npc.get("role")),
            "description": _safe_str(npc.get("description")),
            "faction_id": npc.get("faction_id"),
            "location_id": npc.get("location_id"),
        })
    return cards


def _get_player_location_id(simulation_state: dict[str, Any], runtime_state: dict[str, Any]) -> str:
    player_state = _safe_dict(simulation_state.get("player_state"))
    current_scene = _safe_dict(runtime_state.get("current_scene"))
    return (
        _safe_str(player_state.get("location_id")).strip()
        or _safe_str(current_scene.get("location_id")).strip()
        or _safe_str(current_scene.get("scene_id")).strip()
    )




def select_primary_action(simulation_state: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    return candidates[0] if candidates else {"action_type": "investigate"}


def _structured_action_prompt(action: dict[str, Any]) -> str:
    action = _safe_dict(action)
    npc_name = _safe_str(action.get("npc_name")).strip()
    npc_id = _safe_str(action.get("npc_id") or action.get("target_id")).strip()
    label = npc_name or npc_id or "them"
    action_type = _safe_str(action.get("action_type")).strip()
    legacy_action = _safe_str(action.get("action")).strip().lower()

    if legacy_action == "talk" or action_type == "persuade":
        return f"Talk to {label}"
    if legacy_action == "threaten" or action_type == "intimidate":
        return f"Threaten {label}"
    if label and action_type:
        return f"{action_type.replace('_', ' ').title()} {label}"
    if action_type:
        return action_type.replace("_", " ").title()
    return ""


















def _use_item_action(
    simulation_state: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    item_id = _safe_str(action.get("item_id")).strip()
    result = apply_item_effects(simulation_state, item_id)
    return {
        "simulation_state": _safe_dict(result.get("simulation_state")),
        "result": _safe_dict(result.get("result")),
    }


SPEND_ACTION_TYPES = {
    "buy",
    "purchase",
    "trade",
    "pay",
    "bribe",
    "rent_room",
    "rent_bed",
    "hire",
    "use_service",
    "shop_purchase",
}


def _should_apply_action_cost(action: dict[str, Any]) -> bool:
    action = _safe_dict(action)

    action_type = _safe_str(action.get("action_type") or action.get("type")).strip().lower()

    if action.get("apply_cost") is True:
        return action_type in SPEND_ACTION_TYPES

    return action_type in SPEND_ACTION_TYPES


def _extract_action_cost(action: dict[str, Any]) -> dict[str, int]:
    action = _safe_dict(action)

    cost = _safe_dict(action.get("cost"))
    currency_cost = _safe_dict(action.get("currency_cost"))
    price = _safe_dict(action.get("price"))

    if cost:
        return normalize_currency(cost)

    if currency_cost:
        return normalize_currency(currency_cost)

    if price:
        return normalize_currency(price)

    # Legacy compatibility
    if action.get("gold_cost") is not None:
        return normalize_currency({"gold": action.get("gold_cost", 0)})
    if action.get("requires_gold") is not None:
        return normalize_currency({"gold": action.get("requires_gold", 0)})

    return normalize_currency({})


def _apply_action_resource_requirements(
    simulation_state: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    simulation_state = _ensure_simulation_state(simulation_state)
    action = _safe_dict(action)

    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    currency = normalize_currency(_safe_dict(inventory_state.get("currency")))

    if not _should_apply_action_cost(action):
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "result": {
                "blocked": False,
                "blocked_reason": "",
                "resource_changes": {
                    "currency": {
                        "gold": 0,
                        "silver": 0,
                        "copper": 0,
                    },
                },
                "player_resources": {
                    "currency": currency,
                    "gold": int(currency.get("gold", 0) or 0),
                },
                "requirements": {},
            },
        }

    cost = _extract_action_cost(action)

    if currency_to_copper_value(cost) <= 0:
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "result": {
                "blocked": False,
                "blocked_reason": "",
                "resource_changes": {
                    "currency": {
                        "gold": 0,
                        "silver": 0,
                        "copper": 0,
                    },
                },
                "player_resources": {
                    "currency": currency,
                    "gold": int(currency.get("gold", 0) or 0),
                },
                "requirements": {},
            },
        }

    if not can_afford(currency, cost):
        return {
            "ok": False,
            "simulation_state": simulation_state,
            "result": {
                "action_type": _safe_str(action.get("action_type") or action.get("type")),
                "outcome": "blocked",
                "blocked": True,
                "blocked_reason": "insufficient_currency",
                "failure_kind": "resource_requirement",
                "requirements": {
                    "currency": cost,
                },
                "resource_changes": {
                    "currency": {
                        "gold": 0,
                        "silver": 0,
                        "copper": 0,
                    },
                },
                "player_resources": {
                    "currency": currency,
                    "gold": int(currency.get("gold", 0) or 0),
                },
            },
        }

    updated_currency = subtract_currency_cost(currency, cost)
    delta = currency_delta(currency, updated_currency)

    inventory_state["currency"] = updated_currency
    player_state["inventory_state"] = inventory_state
    simulation_state["player_state"] = player_state

    return {
        "ok": True,
        "simulation_state": simulation_state,
        "result": {
            "blocked": False,
            "blocked_reason": "",
            "resource_changes": {
                "currency": delta,
            },
            "player_resources": {
                "currency": updated_currency,
                "gold": int(updated_currency.get("gold", 0) or 0),
            },
            "requirements": {
                "currency": cost,
            },
        },
    }


def _is_action_provider_available(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    action: dict[str, Any],
) -> bool:
    action = _safe_dict(action)
    provider_id = _safe_str(action.get("provider_id"))
    if not provider_id:
        return True  # Backward compatible for old actions

    providers = _derive_transaction_providers(simulation_state, runtime_state)
    available_ids = {_safe_str(p.get("provider_id")) for p in providers}
    return provider_id in available_ids








def _apply_authoritative_action(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    action_type = _safe_str(action.get("action_type")).strip()

    if action_type in {"service_inquiry", "service_purchase"}:
        return service_authoritative_result(simulation_state, action)

    action = enrich_action_with_registry_price(action)

    if not _is_action_provider_available(simulation_state, runtime_state, action):
        return {
            "simulation_state": simulation_state,
            "result": {
                "action_type": action_type,
                "outcome": "blocked",
                "blocked": True,
                "blocked_reason": "provider_not_available",
                "failure_kind": "provider_requirement",
                "requirements": {},
                "player_resources": {},
                "resource_changes": {
                    "currency": {
                        "gold": 0,
                        "silver": 0,
                        "copper": 0,
                    },
                },
                "effect_result": {
                    "items_added": [],
                    "service_effects": {},
                },
                "action_metadata": {
                    "provider_id": _safe_str(action.get("provider_id")),
                    "provider_name": _safe_str(action.get("provider_name")),
                },
            },
        }

    if action_type == "pickup_item":
        return pickup_item_action(simulation_state, action)
    if action_type == "drop_item":
        return drop_item_action(
            simulation_state,
            action,
            location_id=_get_player_location_id(simulation_state, runtime_state),
        )
    if action_type == "equip_item":
        return equip_item_action(simulation_state, action)
    if action_type == "unequip_item":
        return unequip_item_action(simulation_state, action)
    if action_type == "use_item":
        return _use_item_action(simulation_state, action)

    gated = _apply_action_resource_requirements(simulation_state, action)
    gated_state = _safe_dict(gated.get("simulation_state")) or simulation_state
    gated_result = _safe_dict(gated.get("result"))

    if gated.get("ok") is False:
        blocked_result = dict(gated_result)
        transaction_metadata = build_transaction_metadata(action)
        if transaction_metadata:
            merged_action_metadata = _safe_dict(blocked_result.get("action_metadata"))
            merged_action_metadata.update(transaction_metadata)
            blocked_result["action_metadata"] = merged_action_metadata
        blocked_result["effect_result"] = {
            "items_added": [],
            "service_effects": {},
            }
        return {
            "simulation_state": gated_state,
            "result": blocked_result,
        }

    from app.apps.rpg.foundation.core.determinism import rng_for

    session_seed = gated_state.get("rng_seed")
    if not isinstance(session_seed, int) or isinstance(session_seed, bool):
        raise ValueError("RPG action resolution requires a persisted session RNG seed")
    turn_index = gated_state.get("turn_index")
    if not isinstance(turn_index, int) or isinstance(turn_index, bool):
        turn_history = runtime_state.get("turn_history")
        turn_index = len(turn_history) if isinstance(turn_history, list) else 0
    resolved = resolve_player_action(
        gated_state,
        action,
        rng_for(session_seed, turn_index, "player_action"),
    )
    next_state = _safe_dict(resolved.get("simulation_state")) or gated_state
    next_state["turn_index"] = turn_index + 1
    result = _safe_dict(resolved.get("result"))

    transaction_metadata = build_transaction_metadata(action)
    if transaction_metadata:
        merged_action_metadata = _safe_dict(result.get("action_metadata"))
        merged_action_metadata.update(transaction_metadata)
        result["action_metadata"] = merged_action_metadata

    effect_out = apply_transaction_effects(
        next_state,
        action,
        _safe_dict(result.get("action_metadata")),
    )
    next_state = _safe_dict(effect_out.get("simulation_state")) or next_state
    effect_result = _safe_dict(effect_out.get("effect_result"))

    if effect_result:
        result["effect_result"] = effect_result

    if gated_result:
        merged_resource_changes = _safe_dict(gated_result.get("resource_changes"))
        merged_player_resources = _safe_dict(gated_result.get("player_resources"))
        merged_requirements = _safe_dict(gated_result.get("requirements"))

        if merged_resource_changes:
            result["resource_changes"] = merged_resource_changes
        if merged_player_resources:
            result["player_resources"] = merged_player_resources
        if merged_requirements:
            result["requirements"] = merged_requirements

        if "blocked" in gated_result:
            result["blocked"] = bool(gated_result.get("blocked"))
        if "blocked_reason" in gated_result:
            result["blocked_reason"] = _safe_str(gated_result.get("blocked_reason"))
        if "failure_kind" in gated_result:
            result["failure_kind"] = _safe_str(gated_result.get("failure_kind"))

    return {
        "simulation_state": next_state,
        "result": result,
    }


def _award_progression(
    simulation_state: dict[str, Any],
    resolved_result: dict[str, Any],
) -> dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    player_state = ensure_player_progression_state(player_state)

    explicit_player_xp = int(_safe_dict(resolved_result.get("xp_result")).get("player_xp", 0) or 0)
    computed_player_xp = int(compute_action_player_xp(resolved_result) or 0)
    action_xp = max(0, explicit_player_xp + computed_player_xp)
    stat_bonus = int(compute_stat_influence_bonus(player_state, resolved_result) or 0) if action_xp > 0 else 0
    total_player_xp = max(0, action_xp + stat_bonus)

    explicit_awards = _safe_dict(_safe_dict(resolved_result.get("skill_xp_result")).get("awards"))
    computed_skill_awards = {}

    if not explicit_awards:
        computed_skill_awards = compute_action_skill_xp(resolved_result)

    skill_xp_awards = dict(explicit_awards)
    for skill_id, amount in computed_skill_awards.items():
        skill_xp_awards[skill_id] = int(skill_xp_awards.get(skill_id, 0) or 0) + int(amount or 0)

    if total_player_xp > 0:
        player_state = award_player_xp(
            player_state,
            total_player_xp,
            source=_safe_str(resolved_result.get("action_type")),
        )

    for skill_id, amount in skill_xp_awards.items():
        if int(amount or 0) > 0:
            player_state = award_skill_xp(
                player_state,
                skill_id,
                int(amount),
                source=_safe_str(resolved_result.get("action_type")),
            )

    player_state = resolve_level_ups(player_state)
    level_ups = list(player_state.pop("_level_ups", []) or [])
    player_state = resolve_skill_level_ups(player_state)
    skill_level_ups = list(player_state.pop("_skill_level_ups", []) or [])

    simulation_state["player_state"] = player_state
    return {
        "simulation_state": simulation_state,
        "xp_result": {
            "player_xp": total_player_xp,
            "base_player_xp": action_xp,
            "explicit_player_xp": explicit_player_xp,
            "computed_player_xp": computed_player_xp,
            "stat_bonus": stat_bonus,
        },
        "skill_xp_result": {
            "awards": skill_xp_awards,
        },
        "level_up": level_ups,
        "skill_level_ups": skill_level_ups,
    }


def _initial_scene_state(generated: dict[str, Any]) -> dict[str, Any]:
    opening = _safe_dict(generated.get("opening_situation"))
    anchor = _safe_dict(generated.get("initial_scene_anchor"))
    scene_id = _safe_str(anchor.get("scene_id") or anchor.get("anchor_id") or "scene:opening")
    location_id = _safe_str(anchor.get("location_id") or opening.get("location_id"))
    location_name = _safe_str(anchor.get("location_name") or opening.get("location"))
    body = _safe_str(anchor.get("summary") or opening.get("summary"))
    present_actors = _safe_list(opening.get("present_actors"))
    return {
        "scene_id": scene_id,
        "scene": body or "Your adventure begins…",
        "summary": body or "Your adventure begins…",
        "location_id": location_id,
        "location_name": location_name,
        "actors": [{"id": _safe_str(name), "name": _safe_str(name)} for name in present_actors if _safe_str(name)],
        "options": [],
        "meta": {"origin": "adventure_start"},
        "metadata": {"origin": "adventure_start"},
    }


def build_session_from_start_result(setup_payload: dict[str, Any], start_result: dict[str, Any]) -> dict[str, Any]:
    setup = apply_adventure_defaults(dict(setup_payload or {}))
    generated = _safe_dict(start_result.get("generated"))
    canon_summary = _safe_dict(start_result.get("canon_summary"))
    setup_id = _safe_str(setup.get("setup_id")).strip() or f"adventure_{_utc_now_iso()}"
    now = _utc_now_iso()

    metadata = _safe_dict(setup.get("metadata"))
    simulation_state = _safe_dict(metadata.get("simulation_state"))
    if not simulation_state:
        simulation_state = build_initial_simulation_state(setup)
        simulation_state = _apply_starting_resources_to_player_state(simulation_state, setup)
        metadata["simulation_state"] = simulation_state
        setup["metadata"] = metadata
    else:
        simulation_state = _apply_starting_resources_to_player_state(simulation_state, setup)
        metadata["simulation_state"] = simulation_state

    simulation_state = _ensure_simulation_state(simulation_state)
    world = _build_world_payload(setup, generated, canon_summary)
    npcs = _build_npc_cards(generated)
    opening = _build_opening_text(generated)
    current_scene = _initial_scene_state(generated)

    session: dict[str, Any] = {
        "manifest": {
            "session_id": setup_id,
            "schema_version": _SCHEMA_VERSION,
            "title": _safe_str(setup.get("title") or world.get("title") or "Untitled Adventure"),
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "source_pack_id": "",
            "source_template_id": _safe_str(metadata.get("template_name")),
        },
        "setup_payload": setup,
        "simulation_state": simulation_state,
        "runtime_state": {
            "tick": int(simulation_state.get("tick", 0) or 0),
            "opening": opening,
            "world": world,
            "npcs": npcs,
            "current_scene": current_scene,
            "last_turn_result": {},
            "turn_history": [],
            "voice_assignments": {},
            "settings": {
                "enable_turn_contract": True,
                "response_length": "short",
                "idle_conversation_seconds": 15,
                "idle_conversations_enabled": True,
                "idle_npc_to_player_enabled": True,
                "idle_npc_to_npc_enabled": True,
                "follow_reactions_enabled": True,
                "reaction_style": "normal",
                "console_debug_enabled": False,
                "world_events_panel_enabled": True,
                "interaction_duration_mode": "until_next_command",
                "interaction_duration_ticks": 5,
                # 4C-F: NPC conversation settings
                "ambient_conversations_enabled": True,
                "ambient_delay_after_player_turn": 15,
                "max_concurrent_ambient_threads": 3,
                "max_beats_per_ambient_thread": 5,
                "allow_npc_address_player": True,
                "allow_conversation_world_signals": True,
                "conversation_frequency": "normal",
                "combat_suppression": True,
                "stealth_suppression": True,
            },
            # Living-world ambient state (Phase 0.2)
            "ambient_queue": [],
            "ambient_seq": 0,
            "last_idle_tick_at": "",
            "last_player_turn_at": "",
            "idle_streak": 0,
            "ambient_cooldowns": {},
            "recent_ambient_ids": [],
            "pending_interrupt": None,
            "subscription_state": {"last_polled_seq": 0},
            "ambient_metrics": {"emitted": 0, "suppressed": 0, "coalesced": 0},
            "last_real_player_activity_at": "",
            "last_player_action_context": {},
            "idle_debug_trace": {},
            "recent_world_event_rows": [],
            "combat_state": build_empty_combat_state(),
            # 4C-E: Conversation world signals
            "conversation_world_signals": {
                "pending": [],
                "applied": [],
                "total_emitted": 0,
            },
        },
    }
    session["simulation_state"] = _ensure_simulation_state(_safe_dict(session.get("simulation_state")))
    session["session_id"] = setup_id
    return session


def build_frontend_bootstrap_payload(session: dict[str, Any]) -> dict[str, Any]:
    session = _safe_dict(session)
    manifest = _safe_dict(session.get("manifest"))
    runtime_state = _safe_dict(session.get("runtime_state"))
    simulation_state = _safe_dict(session.get("simulation_state"))
    simulation_state = ensure_visual_state(simulation_state)
    npcs = _safe_list(runtime_state.get("npcs"))
    opening = _safe_str(runtime_state.get("opening"))
    turn_result = _safe_dict(session.get("turn_result"))
    player_state = _safe_dict(simulation_state.get("player_state"))

    # Ensure grounded scene context is always available
    if not runtime_state.get("grounded_scene_context"):
        grounded = _derive_grounded_scene_context(simulation_state, runtime_state)
        current_scene = _safe_dict(runtime_state.get("current_scene"))
        current_scene = _apply_grounded_scene_overlay(current_scene, grounded)
        runtime_state["grounded_scene_context"] = grounded
        runtime_state["current_scene"] = current_scene

    current_scene = _safe_dict(runtime_state.get("current_scene"))
    narration = _safe_str(turn_result.get("narration")) or opening
    nearby_npcs = build_nearby_npc_cards(simulation_state, current_scene)

    inventory_state = _safe_dict(player_state.get("inventory_state"))
    equipment = _safe_dict(inventory_state.get("equipment"))

    transaction_menus = _build_transaction_menus_for_state(simulation_state, runtime_state)

    presentation_state = _safe_dict(simulation_state.get("presentation_state"))
    visual_state = _safe_dict(presentation_state.get("visual_state"))

    return {
        "success": True,
        "session_id": _safe_str(manifest.get("id")) or _safe_str(session.get("id")),
        "title": _safe_str(manifest.get("title")),
        "opening": opening,
        "narration": narration,
        "player": {
            "stats": _safe_dict(player_state.get("stats")),
            "skills": _safe_dict(player_state.get("skills")),
            "level": int(player_state.get("level", 1) or 1),
            "xp": int(player_state.get("xp", 0) or 0),
            "xp_to_next": int(player_state.get("xp_to_next", 100) or 100),
            "inventory_state": inventory_state,
            "equipment": equipment,
            "currency": _safe_dict(inventory_state.get("currency")),
            "inventory_items": _safe_list(inventory_state.get("items")),
            "nearby_npc_ids": _safe_list(player_state.get("nearby_npc_ids")),
            "available_checks": _safe_list(player_state.get("available_checks")),
        },
        "nearby_npcs": nearby_npcs,
        "known_npcs": npcs,
        "scene": {
            "scene_id": _safe_str(current_scene.get("scene_id")),
            "items": _safe_list(current_scene.get("items")),
            "available_checks": _safe_list(current_scene.get("available_checks")),
            "present_npc_ids": _safe_list(current_scene.get("present_npc_ids")),
        },
        "memory_summary": build_memory_ui_summary(simulation_state),
        "combat_result": _safe_dict(turn_result.get("combat_result")),
        "xp_result": _safe_dict(turn_result.get("xp_result")),
        "skill_xp_result": _safe_dict(turn_result.get("skill_xp_result")),
        "level_up": _safe_list(turn_result.get("level_up")),
        "skill_level_ups": _safe_list(turn_result.get("skill_level_ups")),
        "resource_changes": _safe_dict(turn_result.get("resource_changes")),
        "player_resources": _safe_dict(turn_result.get("player_resources")),
        "effect_result": _safe_dict(turn_result.get("effect_result")),
        "presentation": build_runtime_presentation_payload(simulation_state),
        "visual_state": visual_state,
        "settings": _normalize_runtime_settings(_safe_dict(runtime_state.get("runtime_settings"))),
        "world_events_summary": {
            "recent_world_event_rows": _safe_list(runtime_state.get("recent_world_event_rows"))[-12:],
        },
        "grounded_scene_context": _safe_dict(runtime_state.get("grounded_scene_context")),
        "transaction_menus": transaction_menus,
        "campaign_calendar": _safe_dict(runtime_state.get("campaign_calendar")),
        "player_journal": _safe_dict(runtime_state.get("player_journal")),
    }


def _find_target_by_name(bucket: dict[str, Any], text: str) -> str:
    text_lc = text.lower()
    for entity_id, entity in sorted(bucket.items()):
        entity = _safe_dict(entity)
        candidates = [
            _safe_str(entity_id),
            _safe_str(entity.get("name")),
            _safe_str(entity.get("title")),
            _safe_str(entity.get("summary")),
        ]
        for candidate in candidates:
            candidate = candidate.strip().lower()
            if candidate and candidate in text_lc:
                return _safe_str(entity_id)
    return ""


def derive_player_action(simulation_state: dict[str, Any], player_input: str) -> dict[str, Any]:
    text = _safe_str(player_input).strip()
    text_lc = text.lower()
    threads = _safe_dict(simulation_state.get("threads"))
    factions = _safe_dict(simulation_state.get("factions"))

    if not text:
        return {}

    if any(token in text_lc for token in ("help", "intervene", "stop", "de-escalate", "defuse")):
        target_id = _find_target_by_name(threads, text)
        if target_id:
            return {
                "type": INTERVENE_THREAD,
                "target_id": target_id,
                "action_id": f"action:{int(simulation_state.get('tick', 0) or 0)}:{target_id}:intervene",
            }

    if any(token in text_lc for token in ("support", "aid", "ally with", "back ")) or text_lc.startswith("support "):
        target_id = _find_target_by_name(factions, text)
        if target_id:
            return {
                "type": SUPPORT_FACTION,
                "target_id": target_id,
                "action_id": f"action:{int(simulation_state.get('tick', 0) or 0)}:{target_id}:support",
            }

    if any(token in text_lc for token in ("attack", "escalate", "strike", "provoke")):
        target_id = _find_target_by_name(threads, text)
        if target_id:
            return {
                "type": ESCALATE_CONFLICT,
                "target_id": target_id,
                "action_id": f"action:{int(simulation_state.get('tick', 0) or 0)}:{target_id}:escalate",
            }

    return {}

__all__ = ['SPEND_ACTION_TYPES', '_MAX_KNOWN_NPC_IDS', '_SCHEMA_VERSION', '_apply_action_resource_requirements', '_apply_authoritative_action', '_award_progression', '_build_npc_cards', '_build_opening_text', '_build_world_payload', '_check_opening_resolution', '_extract_action_cost', '_find_target_by_name', '_get_player_location_id', '_initial_scene_state', '_is_action_provider_available', '_should_apply_action_cost', '_structured_action_prompt', '_update_known_npc_ids', '_use_item_action', 'build_frontend_bootstrap_payload', 'build_session_from_start_result', 'derive_player_action', 'select_primary_action']
