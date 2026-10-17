"""NPC initiative engine — autonomous NPC action and speech candidates.

Builds, selects, and scores initiative candidates so NPCs act and speak
first based on beliefs, goals, tension, context, and opening-state relevance.
All selection is deterministic — sorted ordering, stable salience, stable
tie-breaking.  No uncontrolled randomness.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


# ── Candidate types ───────────────────────────────────────────────────────

CANDIDATE_KINDS = (
    "npc_to_player",
    "npc_to_npc",
    "warning",
    "demand",
    "taunt",
    "gossip",
    "companion_comment",
    "quest_prompt",
    "recruitment_offer",
    "plea_for_help",
    "follow_reaction",
    "caution_reaction",
    "assist_reaction",
)

# ── Cooldown constants ────────────────────────────────────────────────────

DEFAULT_SPEAKER_COOLDOWN = 3   # ticks before same NPC can speak again
DEFAULT_KIND_COOLDOWN = 2      # ticks before same kind fires again
DEFAULT_PAIR_COOLDOWN = 5      # ticks before same speaker→target pair fires again
DEFAULT_REASON_COOLDOWN = 4    # ticks before same reason fires again
DEFAULT_OPENING_COOLDOWN = 6   # ticks before same opening hook fires again

# ── Hard caps ─────────────────────────────────────────────────────────────

MAX_INITIATIVE_CANDIDATES = 32


# ── Cooldown helpers ──────────────────────────────────────────────────────

def _is_on_cooldown(
    cooldowns: Dict[str, Any],
    key: str,
    current_tick: int,
    cooldown_ticks: int,
) -> bool:
    """Check if a cooldown key is still active."""
    last_tick = int(cooldowns.get(key, -999) or -999)
    return (current_tick - last_tick) < cooldown_ticks


def _set_cooldown(
    cooldowns: Dict[str, Any],
    key: str,
    current_tick: int,
) -> None:
    """Record a cooldown activation."""
    cooldowns[key] = current_tick


def _personality_bias(npc_info: Dict[str, Any], kind: str) -> float:
    """Small deterministic personality bias for initiative kinds."""
    personality = _safe_str(_safe_dict(npc_info.get("personality")).get("style") or npc_info.get("personality")).lower()
    if kind in ("taunt", "demand") and personality in ("aggressive", "bold", "hotheaded"):
        return 0.08
    if kind in ("warning", "plea_for_help") and personality in ("cautious", "careful", "wary"):
        return 0.06
    if kind in ("npc_to_player", "companion_comment", "recruitment_offer") and personality in ("friendly", "warm", "helpful", "loyal"):
        return 0.06
    return 0.0


# ── Reaction-lane initiative builder ──────────────────────────────────────

def _build_reaction_initiative_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
    *,
    npc_index: Dict[str, Any],
    npc_minds: Dict[str, Any],
    nearby_ids: Set[str],
    player_loc: str,
    party_ids: Set[str],
    tick: int,
    encounter_active: bool,
) -> List[Dict[str, Any]]:
    """Build initiative candidates for the reaction lane — immediate responses to player action."""
    action_ctx = _safe_dict(runtime_state.get("last_player_action_context"))
    if not action_ctx:
        return []

    movement_intent = _safe_str(action_ctx.get("movement_intent"))
    risk_level = _safe_str(action_ctx.get("risk_level"))
    urgency = _safe_str(action_ctx.get("urgency"))
    candidates: List[Dict[str, Any]] = []

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

        # Companion/ally follow initiative for rush/advance
        if is_companion and movement_intent in ("rush", "advance", "retreat"):
            action_family = "follow_player" if movement_intent != "retreat" else "cover_retreat"
            salience = 0.65
            if urgency == "high":
                salience += 0.1
            candidates.append({
                "kind": "follow_reaction",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": action_family,
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "keep_pace",
                "initiative_reason": f"companion reacts to player {movement_intent}",
                "source_lane": "reaction",
                "action_family": action_family,
                "location_id": npc_loc,
                "tick": tick,
            })

        # Cautious NPC warning on risky moves
        if risk_level in ("medium", "high") and trust > 0.1 and not is_companion:
            salience = 0.5 + (0.1 if risk_level == "high" else 0.0)
            candidates.append({
                "kind": "caution_reaction",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "object_to_risk",
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "call_out_warning",
                "initiative_reason": f"caution about player {movement_intent} (risk={risk_level})",
                "source_lane": "reaction",
                "action_family": "call_out_warning",
                "location_id": npc_loc,
                "tick": tick,
            })

        # Assist on inspect
        if movement_intent == "inspect" and is_companion:
            candidates.append({
                "kind": "assist_reaction",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "assist_investigation",
                "salience": 0.5,
                "interrupt": False,
                "action_intent": "assist_investigation",
                "initiative_reason": "companion assists player inspection",
                "source_lane": "reaction",
                "action_family": "assist_investigation",
                "location_id": npc_loc,
                "tick": tick,
            })

    return candidates[:MAX_INITIATIVE_CANDIDATES]


# ── Build candidates ──────────────────────────────────────────────────────

def build_npc_initiative_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
    *,
    lane: str = "idle",
) -> List[Dict[str, Any]]:
    """Build a list of possible NPC initiative candidates from simulation state.

    Considers NPC beliefs, goals, relationships, faction pressure, opening
    state, and player context to generate rule-based autonomous candidates.

    lane: "reaction" | "idle" | "all"
    """
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_context = _safe_dict(player_context)

    candidates: List[Dict[str, Any]] = []

    player_loc = _safe_str(player_context.get("player_location"))
    nearby_ids = set(_safe_list(player_context.get("nearby_npc_ids")))
    player_idle = bool(player_context.get("player_idle"))
    active_conflict = _safe_str(player_context.get("active_conflict"))
    recent_incidents = _safe_list(player_context.get("recent_incidents"))
    salient_events = _safe_list(player_context.get("salient_events"))

    npc_index = _safe_dict(simulation_state.get("npc_index"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    faction_pressure = _safe_dict(simulation_state.get("faction_pressure"))
    objectives = _safe_list(simulation_state.get("objectives"))
    tick = int(simulation_state.get("tick", 0) or 0)
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_ids = set(_safe_list(player_state.get("party_npc_ids")))

    encounter_active = bool(
        simulation_state.get("encounter_active")
        or simulation_state.get("active_encounter")
    )

    # ── Reaction lane: immediate NPC action-like reactions ──
    if lane == "reaction":
        return _build_reaction_initiative_candidates(
            simulation_state, runtime_state, player_context,
            npc_index=npc_index, npc_minds=npc_minds,
            nearby_ids=nearby_ids, player_loc=player_loc,
            party_ids=party_ids, tick=tick,
            encounter_active=encounter_active,
        )

    opening_runtime = _safe_dict(runtime_state.get("opening_runtime"))
    opening_active = bool(opening_runtime.get("active"))
    opening_npc_ids = set(_safe_list(opening_runtime.get("present_npc_ids")))

    # Collect objective-related NPC ids
    objective_npc_ids: Set[str] = set()
    for obj in objectives:
        obj = _safe_dict(obj)
        for nid in _safe_list(obj.get("related_npc_ids")):
            objective_npc_ids.add(_safe_str(nid))

    # Build a set of witness ids from recent incidents
    witness_ids: Set[str] = set()
    for incident in recent_incidents:
        incident = _safe_dict(incident)
        for wid in _safe_list(incident.get("witness_ids")):
            witness_ids.add(_safe_str(wid))

    # Detect faction pressure spikes near player
    faction_spike_ids: Set[str] = set()
    for faction_id, pressure_info in sorted(faction_pressure.items()):
        pressure_info = _safe_dict(pressure_info)
        level = float(pressure_info.get("level", 0) or 0)
        loc = _safe_str(pressure_info.get("location_id"))
        if level > 0.6 and loc == player_loc:
            for mid in _safe_list(pressure_info.get("messenger_ids")):
                faction_spike_ids.add(_safe_str(mid))

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

        player_belief = _safe_dict(beliefs.get("player"))
        trust = float(player_belief.get("trust", 0) or 0)
        hostility = float(player_belief.get("hostility", 0) or 0)

        role = _safe_str(npc_info.get("role")).lower()
        is_companion = (
            role in ("companion", "ally", "follower", "support", "guard", "scout", "party_member")
            or npc_id in party_ids
            or bool(npc_info.get("is_companion"))
        )
        is_opening_npc = npc_id in opening_npc_ids
        opening_bonus = 0.0
        # Keep the initiative default local; startup mutation is not required.
        # ── Rule: hostile NPC nearby + player idle → taunt/warning ────
        if hostility > 0.5 and player_idle and not encounter_active:
            kind = "taunt" if hostility > 0.7 else "warning"
            salience = 0.6 + hostility * 0.3
            if is_opening_npc and opening_active:
                salience += 0.1
            candidates.append({
                "kind": kind,
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "hostile_idle",
                "salience": min(salience, 1.0),
                "interrupt": hostility > 0.7,
                "action_intent": "threaten" if kind == "taunt" else "warn",
                "location_id": npc_loc,
                "tick": tick,
            })

        # ── Rule: trusted NPC nearby + active conflict → advice/help ──
        if trust > 0.4 and active_conflict and not encounter_active:
            salience = 0.5 + trust * 0.3
            if is_opening_npc and opening_active:
                salience += 0.1
            candidates.append({
                "kind": "plea_for_help" if trust > 0.7 else "npc_to_player",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "trusted_conflict_advice",
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "advise",
                "location_id": npc_loc,
                "tick": tick,
            })

        # ── Rule: faction pressure spike → messenger/warning ──────────
        if npc_id in faction_spike_ids:
            salience = 0.7
            if is_opening_npc and opening_active:
                salience += 0.1
            candidates.append({
                "kind": "warning",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "faction_pressure",
                "salience": min(salience, 1.0),
                "interrupt": True,
                "action_intent": "deliver_message",
                "location_id": npc_loc,
                "tick": tick,
            })

        # ── Rule: objective-related NPC present → quest prompt ────────
        if npc_id in objective_npc_ids:
            salience = 0.6
            if is_opening_npc and opening_active:
                salience += 0.15
            candidates.append({
                "kind": "quest_prompt",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "objective_related",
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "offer_quest",
                "location_id": npc_loc,
                "tick": tick,
            })

        # ── Rule: companion + salient event → companion comment ───────
        if is_companion and salient_events:
            salience = 0.45
            if opening_active:
                salience += 0.1
            candidates.append({
                "kind": "companion_comment",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "salient_event_reaction",
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "comment",
                "location_id": npc_loc,
                "_opening_tied": is_opening_npc,
                "tick": tick,
            })

        # ── Rule: companion nearby + player idle → idle companion comment ───
        if is_companion and player_idle and not encounter_active:
            salience = 0.28 + opening_bonus
            salience += _personality_bias(npc_info, "companion_comment")
            candidates.append({
                "kind": "companion_comment",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "companion_idle_presence",
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "comment",
                "location_id": npc_loc,
                "_opening_tied": is_opening_npc,
                "tick": tick,
            })

        # ── Rule: recent incident + witness nearby → rumor/address ────
        if npc_id in witness_ids:
            is_near_player = npc_id in nearby_ids or npc_loc == player_loc
            kind = "npc_to_player" if is_near_player else "gossip"
            salience = 0.5 if kind == "npc_to_player" else 0.3
            if is_opening_npc and opening_active:
                salience += 0.1
            candidates.append({
                "kind": kind,
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player" if kind == "npc_to_player" else "",
                "target_name": "you" if kind == "npc_to_player" else "",
                "reason": "witness_incident",
                "salience": min(salience, 1.0),
                "interrupt": False,
                "action_intent": "share_information",
                "location_id": npc_loc,
                "tick": tick,
            })

        # ── Rule: NPC-to-NPC interaction (trusted pair at same loc) ───
        for other_id in sorted(npc_index.keys()):
            if other_id == npc_id:
                continue
            other_info = _safe_dict(npc_index.get(other_id))
            other_loc = _safe_str(other_info.get("location_id"))
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
                    "reason": "social_interaction",
                    "salience": 0.25 + other_trust * 0.15,
                    "interrupt": False,
                    "action_intent": "converse",
                    "location_id": npc_loc,
                    "tick": tick,
                })
                break  # One NPC-to-NPC candidate per speaker

        # ── Rule: NPC with goals + idle world → recruitment/gossip ────
        if goals and not encounter_active:
            if hostility < 0.3 and trust > 0.2:
                candidates.append({
                    "kind": "recruitment_offer",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": "player",
                    "target_name": "you",
                    "reason": "goal_driven_recruitment",
                    "salience": 0.35 + trust * 0.1,
                    "interrupt": False,
                    "action_intent": "recruit",
                    "location_id": npc_loc,
                    "tick": tick,
                })
            elif hostility < 0.3:
                candidates.append({
                    "kind": "gossip",
                    "speaker_id": npc_id,
                    "speaker_name": npc_name,
                    "target_id": "",
                    "target_name": "",
                    "reason": "idle_chatter",
                    "salience": 0.2,
                    "interrupt": False,
                    "action_intent": "gossip",
                    "location_id": npc_loc,
                    "tick": tick,
                })

        # ── Rule: hostile NPC with demand intent ──────────────────────
        if hostility > 0.6 and goals and not encounter_active:
            candidates.append({
                "kind": "demand",
                "speaker_id": npc_id,
                "speaker_name": npc_name,
                "target_id": "player",
                "target_name": "you",
                "reason": "hostile_demand",
                "salience": 0.55 + hostility * 0.2,
                "interrupt": hostility > 0.8,
                "action_intent": "demand",
                "location_id": npc_loc,
                "tick": tick,
            })

    # ── Opening hook bias: suppress unrelated generic chatter ─────────
    if opening_active:
        suppressed: List[Dict[str, Any]] = []
        for c in candidates:
            if c.get("kind") in ("gossip", "npc_to_npc") and not _is_opening_tied(c, opening_runtime):
                # Suppress unless very strong
                if float(c.get("salience", 0) or 0) >= 0.6:
                    suppressed.append(c)
            else:
                suppressed.append(c)
        candidates = suppressed

    # Hard cap
    candidates = candidates[:MAX_INITIATIVE_CANDIDATES]
    return candidates


def _is_opening_tied(candidate: Dict[str, Any], opening_runtime: Dict[str, Any]) -> bool:
    """Check if a candidate is tied to the opening state."""
    opening_npc_ids = set(_safe_list(opening_runtime.get("present_npc_ids")))
    speaker_id = _safe_str(candidate.get("speaker_id"))
    return speaker_id in opening_npc_ids


# ── Opening relevance scoring ────────────────────────────────────────────

def compute_opening_relevance(candidate: Dict[str, Any], opening_runtime: Dict[str, Any]) -> float:
    """Score how relevant a candidate is to the current opening state.

    Boosts for NPCs tied to the opening, events tied to the starter
    conflict, and companion/witness dialogue toward the opening problem.
    Returns a float in [0.0, 1.0].
    """
    candidate = _safe_dict(candidate)
    opening_runtime = _safe_dict(opening_runtime)

    if not bool(opening_runtime.get("active")):
        return 0.0

    score = 0.0
    speaker_id = _safe_str(candidate.get("speaker_id"))
    kind = _safe_str(candidate.get("kind"))
    reason = _safe_str(candidate.get("reason"))
    opening_npc_ids = set(_safe_list(opening_runtime.get("present_npc_ids")))
    starter_conflict = _safe_str(opening_runtime.get("starter_conflict")).lower()

    # Boost for NPCs tied to opening
    if speaker_id in opening_npc_ids:
        score += 0.4

    # Boost for events/reasons tied to starter conflict
    if starter_conflict and starter_conflict in reason.lower():
        score += 0.3

    # Boost for companion/witness dialogue toward opening problem
    if kind in ("companion_comment", "npc_to_player") and speaker_id in opening_npc_ids:
        score += 0.2

    # Boost for quest prompts tied to opening
    if kind == "quest_prompt" and speaker_id in opening_npc_ids:
        score += 0.1

    return min(score, 1.0)


# ── World behavior bias ──────────────────────────────────────────────────

def apply_world_behavior_bias(
    candidates: List[Dict[str, Any]],
    world_behavior: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply world behavior config biases to candidate salience.

    Adjusts salience values based on world_behavior settings.  All effects
    are bias-only — they shift salience, never force impossible events.
    Returns a new list; does not mutate the originals.
    """
    world_behavior = _safe_dict(world_behavior)
    if not candidates:
        return []

    npc_init = _safe_str(world_behavior.get("npc_initiative")).lower()
    quest_prompt = _safe_str(world_behavior.get("quest_prompting")).lower()
    companion = _safe_str(world_behavior.get("companion_chatter")).lower()
    opening_guid = _safe_str(world_behavior.get("opening_guidance")).lower()
    play_style = _safe_str(world_behavior.get("play_style_bias")).lower()

    result: List[Dict[str, Any]] = []
    for c in candidates:
        c = dict(_safe_dict(c))
        salience = float(c.get("salience", 0) or 0)
        kind = _safe_str(c.get("kind"))

        # ── npc_initiative level ──────────────────────────────────────
        if npc_init == "low":
            salience -= 0.1
        elif npc_init == "high":
            salience += 0.1

        # ── quest_prompting ───────────────────────────────────────────
        if kind == "quest_prompt":
            if quest_prompt in ("off", "light"):
                salience -= 0.3 if quest_prompt == "off" else 0.15

        # ── companion_chatter ─────────────────────────────────────────
        if kind == "companion_comment" and companion == "quiet":
            salience -= 0.15

        # ── opening_guidance ──────────────────────────────────────────
        if opening_guid == "strong" and bool(c.get("_opening_tied")):
            salience += 0.15

        # ── play_style_bias ───────────────────────────────────────────
        _INCIDENTAL_KINDS = ("gossip", "npc_to_npc", "companion_comment")
        _STEERING_KINDS = ("quest_prompt", "warning", "demand", "plea_for_help")
        if play_style == "sandbox":
            if kind in _INCIDENTAL_KINDS:
                salience += 0.05
            if kind in _STEERING_KINDS:
                salience -= 0.05
        elif play_style == "story_directed":
            if kind in _INCIDENTAL_KINDS:
                salience -= 0.05
            if kind in _STEERING_KINDS:
                salience += 0.05

        c["salience"] = max(0.0, min(salience, 1.0))
        result.append(c)

    return result


# ── Cooldown-aware selection ──────────────────────────────────────────────

def select_npc_initiative_candidate(
    candidates: List[Dict[str, Any]],
    runtime_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Select the best initiative candidate respecting cooldowns.

    Selection is deterministic: sorted by salience descending, interrupt
    True first, opening relevance, then speaker_id and target_id for
    stable tie-breaking.  Cooldowns suppress spam.
    """
    runtime_state = _safe_dict(runtime_state)
    cooldowns = _safe_dict(runtime_state.get("ambient_cooldowns"))
    current_tick = int(runtime_state.get("tick", 0) or 0)
    opening_runtime = _safe_dict(runtime_state.get("opening_runtime"))

    # Pre-compute opening relevance for sorting
    scored: List[Tuple[float, bool, float, str, str, Dict[str, Any]]] = []
    for c in candidates:
        c = _safe_dict(c)
        salience = float(c.get("salience", 0) or 0)
        interrupt = bool(c.get("interrupt"))
        opening_rel = compute_opening_relevance(c, opening_runtime)
        speaker_id = _safe_str(c.get("speaker_id"))
        target_id = _safe_str(c.get("target_id"))
        scored.append((-salience, not interrupt, -opening_rel, speaker_id, target_id, c))

    sorted_candidates = sorted(scored, key=lambda t: t[:5])

    for _, _, _, _, _, candidate in sorted_candidates:
        speaker_id = _safe_str(candidate.get("speaker_id"))
        kind = _safe_str(candidate.get("kind"))
        target_id = _safe_str(candidate.get("target_id"))
        reason = _safe_str(candidate.get("reason"))

        # Check speaker cooldown
        if _is_on_cooldown(cooldowns, f"speaker:{speaker_id}", current_tick, DEFAULT_SPEAKER_COOLDOWN):
            continue
        # Check kind cooldown
        if _is_on_cooldown(cooldowns, f"kind:{kind}", current_tick, DEFAULT_KIND_COOLDOWN):
            continue
        # Check pair cooldown
        if target_id and _is_on_cooldown(cooldowns, f"pair:{speaker_id}:{target_id}", current_tick, DEFAULT_PAIR_COOLDOWN):
            continue
        # Check reason cooldown
        if reason and _is_on_cooldown(cooldowns, f"reason:{reason}", current_tick, DEFAULT_REASON_COOLDOWN):
            continue
        # Check opening cooldown
        if opening_runtime.get("active") and speaker_id in set(_safe_list(opening_runtime.get("present_npc_ids"))):
            hook_id = _safe_str(opening_runtime.get("hook_id") or opening_runtime.get("starter_conflict") or "opening")
            if _is_on_cooldown(cooldowns, f"opening:{hook_id}", current_tick, DEFAULT_OPENING_COOLDOWN):
                continue

        return candidate

    return None


# ── Apply cooldowns ───────────────────────────────────────────────────────

def apply_initiative_cooldowns(
    runtime_state: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """Record cooldowns for the selected initiative candidate.

    Returns the updated runtime_state (shallow copy of cooldowns dict).
    """
    runtime_state = _safe_dict(runtime_state)
    candidate = _safe_dict(candidate)
    cooldowns = dict(_safe_dict(runtime_state.get("ambient_cooldowns")))
    current_tick = int(runtime_state.get("tick", 0) or 0)

    speaker_id = _safe_str(candidate.get("speaker_id"))
    kind = _safe_str(candidate.get("kind"))
    target_id = _safe_str(candidate.get("target_id"))
    reason = _safe_str(candidate.get("reason"))

    _set_cooldown(cooldowns, f"speaker:{speaker_id}", current_tick)
    _set_cooldown(cooldowns, f"kind:{kind}", current_tick)
    if target_id:
        _set_cooldown(cooldowns, f"pair:{speaker_id}:{target_id}", current_tick)
    if reason:
        _set_cooldown(cooldowns, f"reason:{reason}", current_tick)

    # Opening cooldown
    opening_runtime = _safe_dict(runtime_state.get("opening_runtime"))
    if opening_runtime.get("active") and speaker_id in set(_safe_list(opening_runtime.get("present_npc_ids"))):
        hook_id = _safe_str(opening_runtime.get("hook_id") or opening_runtime.get("starter_conflict") or "opening")
        _set_cooldown(cooldowns, f"opening:{hook_id}", current_tick)

    runtime_state["ambient_cooldowns"] = cooldowns
    return runtime_state
