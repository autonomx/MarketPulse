"""4C-C — Conversation Scheduler Helpers.

Pure helper utilities for the conversation engine.  This module does
**not** own a lifecycle loop.  The sole authoritative conversation
lifecycle entrypoint is ``conversation_engine.run_conversation_tick(...)``.

Provided helpers:
- classify_thread_mode          — mode classification
- compute_thread_importance     — importance scoring
- compute_world_effect_budget   — per-thread world signal budget
- thread_is_expired / stale     — expiry checks
- should_start_new_thread       — capacity gating
- get_active_thread_summary     — compact UI summary
"""
from __future__ import annotations

from typing import Any, Dict, List

from .npc_conversations import list_active_conversations
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

# ── Constants ─────────────────────────────────────────────────────────────

_MAX_CONCURRENT_THREADS = 3
_AMBIENT_PRIORITY = 20
_DIRECTED_PRIORITY = 80
_GROUP_PRIORITY = 60
PIVOT_TURN_EXTENSION = 4   # additional turns granted after mode pivot


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# ── Thread eligibility ────────────────────────────────────────────────────

def thread_is_expired(conversation: Dict[str, Any], tick: int) -> bool:
    """Check if a conversation thread has expired by tick."""
    expires = _safe_int(conversation.get("expires_at_tick"), 0)
    return expires > 0 and tick > expires


def thread_is_stale(conversation: Dict[str, Any], tick: int, stale_ticks: int = 6) -> bool:
    """Check if a thread hasn't advanced in too many ticks."""
    updated = _safe_int(conversation.get("updated_tick"), 0)
    return (tick - updated) > stale_ticks


# ── Mode classification ───────────────────────────────────────────────────

def classify_thread_mode(
    conversation: Dict[str, Any],
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
) -> str:
    """Classify what mode a thread should be in based on current state.

    Returns: "ambient" | "directed_to_player" | "group"
    """
    conversation = _safe_dict(conversation)
    participants = _safe_list(conversation.get("participants"))

    # If player is a participant, it's group mode
    if "player" in participants:
        return "group"

    # If 3+ participants (excluding player), it's group mode
    if len(participants) >= 3:
        return "group"

    # Check for player relevance via topic
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))
    if topic_type in {"plan_reaction", "risk_conflict"}:
        # These topics are often directed at the player
        if conversation.get("player_present"):
            return "directed_to_player"

    # Check existing mode
    current_mode = _safe_str(conversation.get("mode"))
    if current_mode in {"directed_to_player", "group"}:
        return current_mode

    return "ambient"


# ── Scheduling decisions ──────────────────────────────────────────────────

def should_start_new_thread(
    active_conversations: List[Dict[str, Any]],
    settings: Dict[str, Any],
) -> bool:
    """Decide if a new ambient conversation thread should start.

    Only counts ambient-mode conversations against the concurrent limit,
    since ``max_concurrent_ambient_threads`` specifically caps ambient threads.
    """
    max_concurrent = _safe_int(settings.get("max_concurrent_ambient_threads"), _MAX_CONCURRENT_THREADS)
    ambient_count = sum(
        1 for c in active_conversations
        if _safe_str(_safe_dict(c).get("mode")) in {"ambient", ""}
    )
    return ambient_count < max_concurrent


def compute_thread_importance(conversation: Dict[str, Any]) -> int:
    """Compute importance score (0-100) for a conversation thread."""
    conversation = _safe_dict(conversation)
    mode = _safe_str(conversation.get("mode"))
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))
    priority = float(topic.get("priority", 0.5) or 0.5)

    base = {
        "ambient": _AMBIENT_PRIORITY,
        "directed_to_player": _DIRECTED_PRIORITY,
        "group": _GROUP_PRIORITY,
    }.get(mode, _AMBIENT_PRIORITY)

    # Boost by topic priority
    base = int(base + priority * 20)

    # Boost for high-tension topics
    if topic_type in {"faction_tension", "local_incident", "moral_conflict"}:
        base += 10

    return min(100, max(0, base))


def compute_world_effect_budget(conversation: Dict[str, Any]) -> int:
    """Compute how many world signals this thread is allowed to emit."""
    conversation = _safe_dict(conversation)
    mode = _safe_str(conversation.get("mode"))
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))

    if mode == "group":
        return 3
    if mode == "directed_to_player":
        return 2
    if topic_type in {"faction_tension", "local_incident", "moral_conflict"}:
        return 2
    return 1


# ── Conversation mode summary for UI ──────────────────────────────────────

def get_active_thread_summary(
    simulation_state: Dict[str, Any],
    location_id: str = "",
) -> List[Dict[str, Any]]:
    """Return a compact summary of active threads for the UI inspector."""
    result: List[Dict[str, Any]] = []
    for conv in list_active_conversations(simulation_state, location_id=location_id):
        conv = _safe_dict(conv)
        topic = _safe_dict(conv.get("topic"))
        result.append({
            "conversation_id": _safe_str(conv.get("conversation_id")),
            "mode": _safe_str(conv.get("mode")) or "ambient",
            "participants": _safe_list(conv.get("participants")),
            "topic_type": _safe_str(topic.get("type")),
            "topic_summary": _safe_str(topic.get("summary"))[:100],
            "beat_count": _safe_int(conv.get("beat_count"), 0),
            "max_turns": _safe_int(conv.get("max_turns"), 1),
            "importance": _safe_int(conv.get("importance"), 0),
            "mode_pivots": len(_safe_list(conv.get("pivot_history"))),
            "status": _safe_str(conv.get("status")),
        })
    return result
