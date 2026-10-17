"""Living-world NPC ambient dialogue engine.

Builds, selects, and prepares ambient dialogue candidates.
All selection is deterministic — sorted ordering, stable salience, stable tie-breaking.
No uncontrolled randomness.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


# ── Candidate types ───────────────────────────────────────────────────────

CANDIDATE_KINDS = (
    "npc_to_player",
    "npc_to_npc",
    "npc_reaction",
    "companion_comment",
    "warning",
    "demand",
    "taunt",
    "gossip",
    "follow_reaction",
    "caution_reaction",
    "assist_reaction",
    "idle_check_in",
)

# ── Cooldown constants ────────────────────────────────────────────────────

_DEFAULT_SPEAKER_COOLDOWN = 3   # ticks before same NPC can speak again
_DEFAULT_KIND_COOLDOWN = 2      # ticks before same kind fires again
_DEFAULT_PAIR_COOLDOWN = 5      # ticks before same speaker→target pair fires again


def _is_on_cooldown(cooldowns: Dict[str, Any], key: str, current_tick: int, cooldown_ticks: int) -> bool:
    """Check if a cooldown key is still active."""
    last_tick = int(cooldowns.get(key, -999) or -999)
    return (current_tick - last_tick) < cooldown_ticks


def _set_cooldown(cooldowns: Dict[str, Any], key: str, current_tick: int) -> None:
    """Record a cooldown activation."""
    cooldowns[key] = current_tick


# ── Build candidates ──────────────────────────────────────────────────────

def _build_player_reaction_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build reaction candidates from player's last action context."""
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_context = _safe_dict(player_context)
    settings = _safe_dict(runtime_state.get("settings"))
    action_ctx = _safe_dict(runtime_state.get("last_player_action_context"))

    candidates: List[Dict[str, Any]] = []
    if not action_ctx:
        return candidates

    movement_intent = _safe_str(action_ctx.get("movement_intent"))
    risk_level = _safe_str(action_ctx.get("risk_level"))
    urgency = _safe_str(action_ctx.get("urgency"))
    player_loc = _safe_str(player_context.get("player_location"))
    nearby_ids = set(_safe_list(player_context.get("nearby_npc_ids")))
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_ids = set(_safe_list(player_state.get("party_npc_ids")))
    tick = int(simulation_state.get("tick", 0) or 0)

    follow_reactions_enabled = bool(settings.get("follow_reactions_enabled", True))

    for npc_id in sorted(npc_index.keys()):
        npc_info = _safe_dict(npc_index.get(npc_id))
        npc_name = _safe_str(npc_info.get("name") or npc_id)
        npc_loc = _safe_str(npc_info.get("location_id"))

        if npc_id not in nearby_ids and npc_loc != player_loc:
            continue

        mind = _safe_dict(npc_minds.get(npc_id))
        beliefs = _safe_dict(mind.get("beliefs"))
        player_belief = _safe_dict(beliefs.get("player"))
        trust = float(player_belief.get("trust", 0) or 0)
        hostility = float(player_belief.get("hostility", 0) or 0)

        role = _safe_str(npc_info.get("role")).lower()
        is_companion = (
            role in ("companion", "ally", "follower", "support", "guard", "scout", "party_member")
            or npc_id in party_ids
            or bool(npc_info.get("is_companion"))
        )

        # Follow reactions for rush/advance/retreat
        if follow_reactions_enabled and is_companion and movement_intent in ("rush", "advance", "retreat", "approach"):
            if movement_intent == "rush":
                text_hint = f"{npc_name} hurries after you, breathing harder now."
                salience = 0.75
            elif movement_intent == "retreat":
                text_hint = f"{npc_name} falls back with you."
                salience = 0.7
            elif movement_intent == "approach":
                text_hint = f"{npc_name} follows closely behind you."
                salience = 0.6
            else:
                text_hint = f"{npc_name} keeps pace with you."
                salience = 0.55
            candidates.append({
                "lane": "reaction",
                "kind": "follow_reaction",
                "reaction_type": "follow",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "salience": salience,
                "interrupt": False,
                "location_id": npc_loc,
                "text_hint": text_hint,
                "tick": tick,
            })

        # Caution reactions for risky actions
        if movement_intent in ("rush", "attack") and risk_level in ("medium", "high"):
            if trust > 0.2 and not is_companion:
                text_hint = f"{npc_name} calls after you, asking whether this is really wise."
                salience = 0.65
                candidates.append({
                    "lane": "reaction",
                    "kind": "caution_reaction",
                    "reaction_type": "caution",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": "player",
                    "target_name": "you",
                    "salience": salience,
                    "interrupt": False,
                    "location_id": npc_loc,
                    "text_hint": text_hint,
                    "tick": tick,
                })
            elif is_companion and role in ("guard", "scout"):
                text_hint = f"{npc_name} covers your flank as you push forward."
                candidates.append({
                    "lane": "reaction",
                    "kind": "assist_reaction",
                    "reaction_type": "assist",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": "player",
                    "target_name": "you",
                    "salience": 0.6,
                    "interrupt": False,
                    "location_id": npc_loc,
                    "text_hint": text_hint,
                    "tick": tick,
                })

        # Assist reactions for inspect
        if movement_intent == "inspect":
            personality = _safe_str(npc_info.get("personality")).lower() if isinstance(npc_info.get("personality"), str) else _safe_str(_safe_dict(npc_info.get("personality")).get("style")).lower()
            if personality in ("scholarly", "curious", "wise", "learned") or role in ("scholar", "sage", "mage"):
                text_hint = f"{npc_name} leans in to inspect beside you."
                candidates.append({
                    "lane": "reaction",
                    "kind": "assist_reaction",
                    "reaction_type": "assist",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": "player",
                    "target_name": "you",
                    "salience": 0.55,
                    "interrupt": False,
                    "location_id": npc_loc,
                    "text_hint": text_hint,
                    "tick": tick,
                })
            elif personality in ("nervous", "fearful", "cautious", "timid"):
                text_hint = f"{npc_name} hangs back and warns you not to touch it."
                candidates.append({
                    "lane": "reaction",
                    "kind": "caution_reaction",
                    "reaction_type": "caution",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": "player",
                    "target_name": "you",
                    "salience": 0.5,
                    "interrupt": False,
                    "location_id": npc_loc,
                    "text_hint": text_hint,
                    "tick": tick,
                })

        # Hostile NPC reactions (taunt/challenge)
        if hostility > 0.5 and movement_intent in ("rush", "approach", "attack"):
            text_hint = f"{npc_name} snarls a challenge as you approach."
            candidates.append({
                "lane": "reaction",
                "kind": "taunt",
                "reaction_type": "hostile_reaction",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "salience": 0.7 + hostility * 0.2,
                "interrupt": hostility > 0.7,
                "location_id": npc_loc,
                "text_hint": text_hint,
                "tick": tick,
            })

    return candidates


def _build_idle_conversation_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build idle conversation candidates — used when player is truly idle."""
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_context = _safe_dict(player_context)
    settings = _safe_dict(runtime_state.get("settings"))

    candidates: List[Dict[str, Any]] = []
    player_loc = _safe_str(player_context.get("player_location"))
    nearby_ids = set(_safe_list(player_context.get("nearby_npc_ids")))
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_ids = set(_safe_list(player_state.get("party_npc_ids")))
    tick = int(simulation_state.get("tick", 0) or 0)

    idle_npc_to_player = bool(settings.get("idle_npc_to_player_enabled", True))
    idle_npc_to_npc = bool(settings.get("idle_npc_to_npc_enabled", True))

    for npc_id in sorted(npc_index.keys()):
        npc_info = _safe_dict(npc_index.get(npc_id))
        npc_name = _safe_str(npc_info.get("name") or npc_id)
        npc_loc = _safe_str(npc_info.get("location_id"))

        if npc_id not in nearby_ids and npc_loc != player_loc:
            continue

        mind = _safe_dict(npc_minds.get(npc_id))
        beliefs = _safe_dict(mind.get("beliefs"))
        goals = _safe_list(mind.get("goals"))

        player_belief = _safe_dict(beliefs.get("player"))
        trust = float(player_belief.get("trust", 0) or 0)

        role = _safe_str(npc_info.get("role")).lower()
        is_companion = (
            role in ("companion", "ally", "follower", "support", "guard", "scout", "party_member")
            or npc_id in party_ids
            or bool(npc_info.get("is_companion"))
        )

        # Companion idle check-in
        if is_companion and idle_npc_to_player:
            candidates.append({
                "lane": "idle",
                "kind": "idle_check_in",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "salience": 0.45,
                "text_hint": f"{npc_name} breaks the silence and checks in with you.",
                "emotion": "thoughtful",
                "location_id": npc_loc,
                "tick": tick,
            })

        # NPC-to-NPC idle talk
        if idle_npc_to_npc:
            action_ctx = _safe_dict(runtime_state.get("last_player_action_context"))
            risk_level = _safe_str(action_ctx.get("risk_level"))
            
            for other_id in sorted(npc_index.keys()):
                if other_id == npc_id:
                    continue
                if other_id not in nearby_ids:
                    continue
                
                other_info = _safe_dict(npc_index.get(other_id))
                other_loc = _safe_str(other_info.get("location_id"))
                
                # Only generate for NPCs at same location as player
                if npc_loc != player_loc or other_loc != player_loc:
                    continue
                
                other_name = _safe_str(other_info.get("name") or other_id)
                
                # Generate deterministic dialogue based on roles and context
                text_content = ""
                if risk_level == "high":
                    text_content = f'{npc_name} says quietly to {other_name}, "Something about this place feels wrong."'
                elif role in ("merchant", "vendor", "trader"):
                    text_content = f'{npc_name} mentions to {other_name}, "Business has been slow lately. Travelers are few."'
                elif role in ("guard", "captain", "soldier"):
                    text_content = f'{npc_name} warns {other_name}, "Keep your eyes open. We don\'t know who might be watching."'
                elif is_companion:
                    text_content = f'{npc_name} says quietly to {other_name}, "We should keep an eye on this one."'
                elif trust > 0.3:
                    text_content = f'{npc_name} remarks to {other_name}, "Did you notice that? Something is off today."'
                else:
                    text_content = f'{npc_name} says quietly to {other_name}, "We should keep an eye on this."'
                
                # Boost salience when 2+ NPCs are present so it wins over gossip
                npc_count = len([x for x in nearby_ids if x != npc_id])
                base_salience = 0.52 if npc_count >= 2 else 0.45
                
                if npc_loc != player_loc or other_loc != player_loc:
                    continue
                other_belief = _safe_dict(beliefs.get(other_id))
                other_trust = float(other_belief.get("trust", 0) or 0)
                if other_trust > 0.2:
                    other_name = _safe_str(other_info.get("name") or other_id)
                    candidates.append({
                        "lane": "idle",
                        "kind": "npc_to_npc",
                        "speaker_id": npc_id,
                        "speaker_name": npc_name,
                        "target_id": other_id,
                        "target_name": other_name,
                        "salience": 0.3 + other_trust * 0.2,
                        "text_hint": f"{npc_name} turns to speak with {other_name}.",
                        "emotion": "neutral",
                        "location_id": npc_loc,
                        "tick": tick,
                    })
                    # Only one NPC↔NPC candidate per speaker per idle tick
                    break

        # Gossip from NPCs with goals
        if goals and idle_npc_to_npc:
            candidates.append({
                "lane": "idle",
                "kind": "gossip",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "",
                "target_name": "",
                "salience": 0.2,
                "text_hint": f"{npc_name} mutters something about their affairs.",
                "emotion": "neutral",
                "location_id": npc_loc,
                "tick": tick,
            })

    return candidates


def build_ambient_dialogue_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
    *,
    lane: str = "idle",
) -> List[Dict[str, Any]]:
    """Build a list of possible ambient dialogue candidates from simulation state.
    
    lane: "reaction" | "idle" | "all"
    """
    if lane == "reaction":
        return _build_player_reaction_candidates(simulation_state, runtime_state, player_context)
    if lane == "idle":
        # Idle includes both new idle-specific candidates and original candidates
        result = _build_idle_conversation_candidates(simulation_state, runtime_state, player_context)
        result.extend(_build_original_candidates(simulation_state, runtime_state, player_context))
        return result
    # "all" — combine both
    result = _build_player_reaction_candidates(simulation_state, runtime_state, player_context)
    result.extend(_build_idle_conversation_candidates(simulation_state, runtime_state, player_context))
    # Also include the original candidates for backward compatibility
    result.extend(_build_original_candidates(simulation_state, runtime_state, player_context))
    return result


def _build_original_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build a list of possible ambient dialogue candidates from simulation state.
    
    Considers NPC minds, decisions, relationships, and player context
    to generate candidates for NPC speech.
    """
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_context = _safe_dict(player_context)
    
    candidates: List[Dict[str, Any]] = []
    
    player_loc = _safe_str(player_context.get("player_location"))
    nearby_ids = set(_safe_list(player_context.get("nearby_npc_ids")))
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    npc_decisions = _safe_dict(simulation_state.get("npc_decisions"))
    tick = int(simulation_state.get("tick", 0) or 0)
    
    # Active encounter check: suppress chatter during active combat unless urgent
    encounter_active = bool(simulation_state.get("encounter_active") or simulation_state.get("active_encounter"))
    
    for npc_id in sorted(npc_index.keys()):
        npc_info = _safe_dict(npc_index.get(npc_id))
        npc_name = _safe_str(npc_info.get("name") or npc_id)
        npc_loc = _safe_str(npc_info.get("location_id"))
        
        # Only consider nearby NPCs or those at player location
        if npc_id not in nearby_ids and npc_loc != player_loc:
            continue
        
        mind = _safe_dict(npc_minds.get(npc_id))
        beliefs = _safe_dict(mind.get("beliefs"))
        goals = _safe_list(mind.get("goals"))
        decision = _safe_dict(npc_decisions.get(npc_id))
        
        player_belief = _safe_dict(beliefs.get("player"))
        trust = float(player_belief.get("trust", 0) or 0)
        hostility = float(player_belief.get("hostility", 0) or 0)
        
        # NPC → Player speech candidates
        if trust > 0.3:
            candidates.append({
                "kind": "npc_to_player",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "salience": 0.5 + trust * 0.3,
                "text_hint": f"{npc_name} wants to speak with you.",
                "emotion": "friendly",
                "location_id": npc_loc,
                "tick": tick,
            })
        
        if hostility > 0.5 and not encounter_active:
            kind = "warning" if hostility < 0.8 else "taunt"
            candidates.append({
                "kind": kind,
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "salience": 0.6 + hostility * 0.3,
                "text_hint": f"{npc_name} eyes you {'warily' if kind == 'warning' else 'with menace'}.",
                "emotion": "hostile",
                "location_id": npc_loc,
                "tick": tick,
                "interrupt": hostility > 0.7,
            })
        
        # NPC → NPC speech candidates (between nearby NPCs)
        for other_id in sorted(npc_index.keys()):
            if other_id == npc_id:
                continue
            other_info = _safe_dict(npc_index.get(other_id))
            other_loc = _safe_str(other_info.get("location_id"))
            # Both must be at player's location
            if npc_loc != player_loc or other_loc != player_loc:
                continue
            
            other_belief = _safe_dict(beliefs.get(other_id))
            other_trust = float(other_belief.get("trust", 0) or 0)
            
            if other_trust > 0.2:
                other_name = _safe_str(other_info.get("name") or other_id)
                candidates.append({
                    "kind": "npc_to_npc",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": other_id,
                    "target_name": other_name,
                    "salience": 0.3 + other_trust * 0.2,
                    "text_hint": f"{npc_name} turns to speak with {other_name}.",
                    "emotion": "neutral",
                    "location_id": npc_loc,
                    "tick": tick,
                })
                break  # One NPC-to-NPC candidate per speaker to prevent chatter spam
        
        # Companion commentary
        if _safe_str(npc_info.get("role")).lower() in ("companion", "ally", "follower"):
            candidates.append({
                "kind": "companion_comment",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "salience": 0.4,
                "text_hint": f"{npc_name} has something to say.",
                "emotion": "thoughtful",
                "location_id": npc_loc,
                "tick": tick,
            })
        
        # Gossip (low priority, from NPCs with goals)
        if goals and not encounter_active:
            candidates.append({
                "kind": "gossip",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "",
                "target_name": "",
                "salience": 0.2,
                "text_hint": f"{npc_name} mutters something about their affairs.",
                "emotion": "neutral",
                "location_id": npc_loc,
                "tick": tick,
            })
    
    return candidates


# ── Cooldown-aware selection ──────────────────────────────────────────────

def select_ambient_dialogue_candidate(
    candidates: List[Dict[str, Any]],
    runtime_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Select the best ambient dialogue candidate respecting cooldowns.
    
    Selection is deterministic: sorted by salience descending, then by
    speaker_id for stable tie-breaking. Cooldowns suppress spam.
    """
    runtime_state = _safe_dict(runtime_state)
    cooldowns = _safe_dict(runtime_state.get("ambient_cooldowns"))
    current_tick = int(runtime_state.get("tick", 0) or 0)
    
    # Sort deterministically: highest salience first, then by speaker_id
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (
            -float(c.get("salience", 0) or 0),
            0 if _safe_str(c.get("lane")) == "reaction" else 1,
            0 if _safe_str(c.get("target_id")) == "player" else 1,
            _safe_str(c.get("speaker_id")),
            _safe_str(c.get("target_id")),
            _safe_str(c.get("kind")),
        ),
    )
    
    for candidate in sorted_candidates:
        candidate = _safe_dict(candidate)
        speaker_id = _safe_str(candidate.get("speaker_id"))
        kind = _safe_str(candidate.get("kind"))
        target_id = _safe_str(candidate.get("target_id"))
        
        # Check cooldowns
        if _is_on_cooldown(cooldowns, f"speaker:{speaker_id}", current_tick, _DEFAULT_SPEAKER_COOLDOWN):
            continue
        if _is_on_cooldown(cooldowns, f"kind:{kind}", current_tick, _DEFAULT_KIND_COOLDOWN):
            continue
        if target_id and _is_on_cooldown(cooldowns, f"pair:{speaker_id}:{target_id}", current_tick, _DEFAULT_PAIR_COOLDOWN):
            continue
        
        # This candidate passes all cooldowns — select it
        return candidate
    
    return None


def apply_dialogue_cooldowns(runtime_state: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Apply cooldowns after a dialogue candidate is selected."""
    runtime_state = _safe_dict(runtime_state)
    cooldowns = dict(_safe_dict(runtime_state.get("ambient_cooldowns")))
    current_tick = int(runtime_state.get("tick", 0) or 0)
    
    speaker_id = _safe_str(candidate.get("speaker_id"))
    kind = _safe_str(candidate.get("kind"))
    target_id = _safe_str(candidate.get("target_id"))
    
    _set_cooldown(cooldowns, f"speaker:{speaker_id}", current_tick)
    _set_cooldown(cooldowns, f"kind:{kind}", current_tick)
    if target_id:
        _set_cooldown(cooldowns, f"pair:{speaker_id}:{target_id}", current_tick)
    
    reaction_type = _safe_str(candidate.get("reaction_type"))
    if reaction_type:
        _set_cooldown(cooldowns, f"reaction_type:{speaker_id}:{reaction_type}", current_tick)
    
    runtime_state["ambient_cooldowns"] = cooldowns
    return runtime_state


def build_ambient_dialogue_request(candidate: Dict[str, Any], session_context: Dict[str, Any]) -> Dict[str, Any]:
    """Build an ambient dialogue request payload from a selected candidate.
    
    This creates the data structure used to either generate LLM dialogue
    or produce templated fallback text.
    """
    candidate = _safe_dict(candidate)
    session_context = _safe_dict(session_context)
    
    return {
        "kind": _safe_str(candidate.get("kind")),
        "speaker_id": _safe_str(candidate.get("speaker_id")),
        "speaker_name": _safe_str(candidate.get("speaker_name")),
        "target_id": _safe_str(candidate.get("target_id")),
        "target_name": _safe_str(candidate.get("target_name")),
        "text_hint": _safe_str(candidate.get("text_hint")),
        "emotion": _safe_str(candidate.get("emotion")),
        "location_id": _safe_str(candidate.get("location_id")),
        "tick": int(candidate.get("tick", 0) or 0),
        "scene_id": _safe_str(session_context.get("scene_id")),
        "world_context": _safe_str(session_context.get("world_summary")),
        "interrupt": bool(candidate.get("interrupt")),
    }
