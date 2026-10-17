"""4C-D — Directed Conversation Pivots.

Handles NPC conversations pivoting toward the player or other NPCs.
Pivots happen when:
- Player proximity + topic relevance
- Player reputation threshold
- NPC addresses player mid-conversation
- Third NPC joins group conversation

All pivot decisions are deterministic and bounded.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# ── Pivot trigger thresholds ──────────────────────────────────────────────

_PLAYER_RELEVANCE_THRESHOLD = 0.4     # world_signal_strength above which to consider pivot
_MIN_BEATS_BEFORE_PIVOT = 1           # must have at least 1 beat before pivoting
_MAX_PIVOTS_PER_THREAD = 2            # don't flip-flop modes
_REPUTATION_PIVOT_THRESHOLD = 0.3     # NPC trust/respect that triggers pivot
_MAX_GROUP_PARTICIPANTS = 6           # max participants in a group conversation
_PIVOT_TURN_EXTENSION = 4             # additional turns granted after a mode pivot
_MAX_PIVOT_HISTORY = 8                # max pivot records kept per thread


# ── Pivot eligibility ─────────────────────────────────────────────────────

def should_pivot_to_player(
    conversation: Dict[str, Any],
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    latest_beat: Optional[Dict[str, Any]] = None,
) -> bool:
    """Determine if a conversation should pivot to address the player.

    Returns True if pivot conditions are met:
    1. Conversation is currently ambient
    2. Player is present at the location
    3. Topic is player-relevant OR latest beat has high signal strength
    4. Haven't exceeded max pivots
    5. At least _MIN_BEATS_BEFORE_PIVOT beats have occurred
    """
    conversation = _safe_dict(conversation)
    mode = _safe_str(conversation.get("mode"))

    # Only ambient conversations can pivot to player
    if mode != "ambient":
        return False

    # Player must be present
    if not conversation.get("player_present"):
        return False

    # Check pivot count limit
    pivot_history = _safe_list(conversation.get("pivot_history"))
    if len(pivot_history) >= _MAX_PIVOTS_PER_THREAD:
        return False

    # Must have at least some beats before pivoting
    beat_count = _safe_int(conversation.get("beat_count"), 0)
    if beat_count < _MIN_BEATS_BEFORE_PIVOT:
        return False

    # Check topic relevance
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))
    if topic_type in {"plan_reaction", "risk_conflict"}:
        return True

    # Check latest beat signal strength
    if latest_beat:
        latest_beat = _safe_dict(latest_beat)
        if latest_beat.get("player_relevant"):
            return True
        if _safe_float(latest_beat.get("world_signal_strength")) >= _PLAYER_RELEVANCE_THRESHOLD:
            return True

    # Check NPC reputation toward player
    social_state = _safe_dict(_safe_dict(simulation_state).get("social_state"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    for participant_id in _safe_list(conversation.get("participants")):
        pid = _safe_str(participant_id)
        mind = _safe_dict(npc_minds.get(pid))
        beliefs = _safe_dict(mind.get("beliefs"))
        player_belief = _safe_dict(beliefs.get("player"))
        trust = _safe_float(player_belief.get("trust"))
        respect = _safe_float(player_belief.get("respect"))
        if abs(trust) >= _REPUTATION_PIVOT_THRESHOLD or abs(respect) >= _REPUTATION_PIVOT_THRESHOLD:
            return True

    return False


def should_pivot_to_group(
    conversation: Dict[str, Any],
    simulation_state: Dict[str, Any],
    nearby_npc_ids: Optional[List[str]] = None,
) -> bool:
    """Determine if a conversation should expand to group mode.

    Returns True if:
    1. There are additional NPCs nearby not already in the conversation
    2. Topic is high-importance (faction_tension, local_incident)
    3. Haven't exceeded max pivots
    """
    conversation = _safe_dict(conversation)
    mode = _safe_str(conversation.get("mode"))

    # Only ambient or directed conversations can become group
    if mode == "group":
        return False

    # Check pivot limit
    pivot_history = _safe_list(conversation.get("pivot_history"))
    if len(pivot_history) >= _MAX_PIVOTS_PER_THREAD:
        return False

    # Need at least some beats first
    beat_count = _safe_int(conversation.get("beat_count"), 0)
    if beat_count < _MIN_BEATS_BEFORE_PIVOT:
        return False

    # Check for eligible nearby NPCs
    participants = set(_safe_str(x) for x in _safe_list(conversation.get("participants")))
    nearby = [_safe_str(x) for x in (nearby_npc_ids or []) if _safe_str(x)]
    joinable = [nid for nid in nearby if nid not in participants and nid != "player"]

    if not joinable:
        return False

    # Only for high-importance topics
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))
    return topic_type in {"faction_tension", "local_incident", "moral_conflict"}


# ── Pivot application ─────────────────────────────────────────────────────

def apply_pivot_to_player(conversation: Dict[str, Any], tick: int) -> Dict[str, Any]:
    """Apply a pivot from ambient to directed_to_player mode.

    Updates the conversation dict in place and returns it.
    """
    conversation = _safe_dict(conversation)
    old_mode = _safe_str(conversation.get("mode"))

    conversation["mode"] = "directed_to_player"
    conversation["player_can_intervene"] = True

    # Add player to audience if not already present
    audience = _safe_list(conversation.get("audience"))
    if "player" not in audience:
        audience.append("player")
    conversation["audience"] = audience[:10]

    # Record pivot
    pivot_history = _safe_list(conversation.get("pivot_history"))
    pivot_history.append({
        "from_mode": old_mode,
        "to_mode": "directed_to_player",
        "at_beat": _safe_int(conversation.get("beat_count"), 0),
        "trigger": "player_relevance",
    })
    conversation["pivot_history"] = pivot_history[-_MAX_PIVOT_HISTORY:]

    # Extend max_turns since directed conversations can go longer
    from .conversation_beats import compute_beat_caps
    _, max_beats = compute_beat_caps("directed_to_player")
    current_max = _safe_int(conversation.get("max_turns"), 1)
    beat_count = _safe_int(conversation.get("beat_count"), 0)
    conversation["max_turns"] = max(current_max, min(max_beats, beat_count + _PIVOT_TURN_EXTENSION))
    conversation["expires_at_tick"] = tick + conversation["max_turns"] + _PIVOT_TURN_EXTENSION

    # Recalculate importance
    conversation["importance"] = min(100, _safe_int(conversation.get("importance"), 0) + 30)

    # Increase world effect budget
    conversation["world_effect_budget"] = max(
        _safe_int(conversation.get("world_effect_budget"), 0), 2,
    )

    return conversation


def apply_pivot_to_group(
    conversation: Dict[str, Any],
    new_participant_ids: List[str],
    tick: int,
) -> Dict[str, Any]:
    """Apply a pivot to group mode, adding new participants.

    Updates the conversation dict in place and returns it.
    """
    conversation = _safe_dict(conversation)
    old_mode = _safe_str(conversation.get("mode"))

    # Add new participants
    participants = _safe_list(conversation.get("participants"))
    for nid in new_participant_ids:
        nid = _safe_str(nid)
        if nid and nid not in participants:
            participants.append(nid)
    conversation["participants"] = sorted(participants)[:_MAX_GROUP_PARTICIPANTS]

    conversation["mode"] = "group"

    # Record pivot
    pivot_history = _safe_list(conversation.get("pivot_history"))
    pivot_history.append({
        "from_mode": old_mode,
        "to_mode": "group",
        "at_beat": _safe_int(conversation.get("beat_count"), 0),
        "trigger": "group_expansion",
        "added": [_safe_str(x) for x in new_participant_ids],
    })
    conversation["pivot_history"] = pivot_history[-_MAX_PIVOT_HISTORY:]

    # Extend max_turns for group
    from .conversation_beats import compute_beat_caps
    _, max_beats = compute_beat_caps("group")
    current_max = _safe_int(conversation.get("max_turns"), 1)
    beat_count = _safe_int(conversation.get("beat_count"), 0)
    conversation["max_turns"] = max(current_max, min(max_beats, beat_count + _PIVOT_TURN_EXTENSION))
    conversation["expires_at_tick"] = tick + conversation["max_turns"] + _PIVOT_TURN_EXTENSION

    # Boost importance
    conversation["importance"] = min(100, _safe_int(conversation.get("importance"), 0) + 20)
    conversation["world_effect_budget"] = max(
        _safe_int(conversation.get("world_effect_budget"), 0), 3,
    )

    return conversation


# ── Pivot evaluation pass ─────────────────────────────────────────────────

def evaluate_pivots(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    """Evaluate all active conversations for potential pivots.

    This should be called during the conversation scheduler tick.
    Returns the updated simulation_state.
    """
    from .conversation_beats import get_latest_beat
    from .npc_conversations import list_active_conversations, upsert_conversation

    player_context = _safe_dict(runtime_state.get("player_context"))
    nearby_npc_ids = _safe_list(
        player_context.get("nearby_npc_ids")
        or runtime_state.get("nearby_npc_ids")
    )

    for conv in list_active_conversations(simulation_state):
        conv = dict(conv)
        cid = _safe_str(conv.get("conversation_id"))

        latest_beat = get_latest_beat(simulation_state, cid)

        # Check player pivot first (higher priority)
        if should_pivot_to_player(conv, simulation_state, runtime_state, latest_beat):
            conv = apply_pivot_to_player(conv, tick)
            upsert_conversation(simulation_state, conv)
            continue

        # Check group pivot
        if should_pivot_to_group(conv, simulation_state, nearby_npc_ids):
            participants = set(_safe_str(x) for x in _safe_list(conv.get("participants")))
            joinable = [
                _safe_str(nid)
                for nid in nearby_npc_ids
                if _safe_str(nid) and _safe_str(nid) not in participants and _safe_str(nid) != "player"
            ][:2]  # add at most 2 new participants
            if joinable:
                conv = apply_pivot_to_group(conv, joinable, tick)
                upsert_conversation(simulation_state, conv)

    return simulation_state
