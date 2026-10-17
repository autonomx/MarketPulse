"""Living-world ambient interruption policy.

Determines when ambient updates should interrupt the player vs queue silently.
All decisions are deterministic and bounded.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


# ── Interruption constants ────────────────────────────────────────────────

_MIN_TICKS_BETWEEN_INTERRUPTS = 3
_MAX_PENDING_INTERRUPTS = 2

# Reaction kinds that bypass quiet-window suppression
_REACTION_KINDS = frozenset({
    "follow_reaction",
    "caution_reaction",
    "assist_reaction",
    "warning",
    "combat_start",
})


def _is_reaction_update(update: Dict[str, Any]) -> bool:
    """Check if update is a reaction kind that should bypass quiet window."""
    return (
        _safe_str(update.get("kind")) in _REACTION_KINDS
        or bool(update.get("interrupt"))
    )


# ── Main interruption policy ─────────────────────────────────────────────

def should_interrupt_player(session: Dict[str, Any], update: Dict[str, Any]) -> bool:
    """Determine if an ambient update should interrupt the player.
    
    Interrupt when:
    - Direct address to player (npc_to_player with high priority)
    - Urgent threat / combat start
    - Encounter start
    - Arrival of important NPC
    - Escalation tied to active thread
    - Companion urgent warning
    - Quest prompt with high priority (Phase 7)
    - Plea for help with urgency (Phase 2)
    
    Do NOT interrupt when:
    - Low salience gossip
    - Repetitive chatter
    - Player just received an interruption recently
    - Post-player quiet window is active (Phase 3D)
    """
    session = _safe_dict(session)
    update = _safe_dict(update)
    runtime = _safe_dict(session.get("runtime_state"))
    
    kind = _safe_str(update.get("kind"))
    priority = float(update.get("priority", 0) or 0)
    target_id = _safe_str(update.get("target_id"))
    
    # Phase 3D: quiet-window suppression (unless truly urgent or reaction)
    quiet_ticks = int(runtime.get("post_player_quiet_ticks", 0) or 0)
    if quiet_ticks > 0:
        # Reaction kinds and combat bypass the quiet window
        if _is_reaction_update(update):
            pass  # Allow through
        elif kind == "combat_start" and priority >= 0.9:
            pass  # Allow through
        else:
            return False
    
    # Never interrupt for system summaries or low-priority world events
    if kind == "system_summary":
        return False
    if kind == "world_event" and priority < 0.5:
        return False
    if kind == "gossip":
        return False
    
    # Rate-limit interruptions
    last_interrupt_tick = int(runtime.get("last_interrupt_tick", -999) or -999)
    current_tick = int(runtime.get("tick", 0) or 0)
    if (current_tick - last_interrupt_tick) < _MIN_TICKS_BETWEEN_INTERRUPTS:
        # Still allow truly urgent events
        if kind not in ("combat_start",) and priority < 0.9:
            return False
    
    # Combat and urgent threats always interrupt
    if kind == "combat_start":
        return True
    if kind == "warning" and priority >= 0.7:
        return True
    
    # Direct NPC address to player
    if kind == "npc_to_player" and target_id == "player":
        structured = _safe_dict(update.get("structured"))
        scene_kind = _safe_str(structured.get("scene_kind"))
        reason = _safe_str(structured.get("reason"))
        if scene_kind and "player_pull" in reason:
            return priority >= 0.6
        return priority >= 0.5
    
    # Companion warnings
    if kind == "companion_comment" and priority >= 0.6:
        return True
    
    # Reaction kinds: follow/caution/assist
    if kind in ("follow_reaction", "caution_reaction", "assist_reaction"):
        return priority >= 0.5
    
    # Arrivals with high priority (important NPC)
    if kind == "arrival" and priority >= 0.6:
        return True
    
    # Phase 2: Plea for help (high urgency)
    if kind == "plea_for_help" and priority >= 0.6:
        return True
    
    # Phase 7: Quest prompt (only if high priority and targets player)
    if kind == "quest_prompt" and target_id == "player" and priority >= 0.7:
        return True
    
    # Phase 2: Demand (always urgent)
    if kind == "demand" and priority >= 0.6:
        return True
    
    # Phase 2: Recruitment offer (moderate priority)
    if kind == "recruitment_offer" and priority >= 0.7:
        return True
    
    # Explicit interrupt flag from the update itself
    if update.get("interrupt") and priority >= 0.5:
        return True
    
    return False


def classify_ambient_delivery(
    session: Dict[str, Any],
    update: Dict[str, Any],
    is_typing: bool = False,
) -> str:
    """Classify how an ambient update should be delivered to the frontend.
    
    Returns one of:
    - "interrupt" — display immediately, even if typing
    - "badge" — show unread badge, queue for later
    - "silent" — add to feed without notification
    
    Also tags the update dict in-place with delivery metadata.
    """
    session = _safe_dict(session)
    update = _safe_dict(update)
    
    should_int = should_interrupt_player(session, update)
    kind = _safe_str(update.get("kind"))
    lane = _safe_str(update.get("lane") or _safe_dict(update.get("structured")).get("lane"))
    priority = float(update.get("priority", 0) or 0)
    is_reaction = _is_reaction_update(update)
    is_idle = (
        lane == "idle"
        or kind in ("idle_check_in", "gossip", "npc_to_npc")
    )
    
    # Tag delivery metadata on the update itself
    update["is_reaction"] = is_reaction
    update["is_idle_conversation"] = is_idle
    
    if should_int:
        # If typing and it's not truly urgent, downgrade to badge
        if is_typing and kind not in ("combat_start",) and priority < 0.9:
            update["delivery_reason"] = "downgraded_typing"
            return "badge"
        update["delivery_reason"] = "interrupt_policy"
        return "interrupt"
    
    # Medium priority → badge
    if priority >= 0.4:
        update["delivery_reason"] = "medium_priority"
        return "badge"
    
    update["delivery_reason"] = "low_priority"
    return "silent"


def record_interrupt(session: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """Record that an interruption was delivered, updating pending state."""
    session = _safe_dict(session)
    runtime = _safe_dict(session.get("runtime_state"))
    
    runtime["last_interrupt_tick"] = int(runtime.get("tick", 0) or 0)
    runtime["pending_interrupt"] = {
        "ambient_id": _safe_str(update.get("ambient_id")),
        "kind": _safe_str(update.get("kind")),
        "speaker_id": _safe_str(update.get("speaker_id")),
        "text": _safe_str(update.get("text"))[:200],
    }
    
    session["runtime_state"] = runtime
    return session
