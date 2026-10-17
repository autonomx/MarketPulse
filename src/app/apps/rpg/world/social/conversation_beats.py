"""4C-B — Conversation beat model and advancement.

A beat is one authoritative step in a conversation thread.
It captures who spoke, to whom, the semantic meaning, stance,
mentions, and how much world-signal strength it carries.

The rendered spoken line is presentation; the beat is truth.
"""
from __future__ import annotations

from hashlib import sha1
from typing import Any, Dict, List, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

# ── Constants ─────────────────────────────────────────────────────────────

_MAX_BEATS_PER_THREAD = 12
_MAX_MENTIONS_PER_BEAT = 6

# Beat-count caps by conversation mode
MODE_BEAT_CAPS: Dict[str, tuple] = {
    "ambient": (2, 5),
    "directed_to_player": (3, 6),
    "group": (4, 8),
}


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_safe_str(p) for p in parts)
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


# ── Beat construction ─────────────────────────────────────────────────────

def build_conversation_beat(
    *,
    thread_id: str,
    speaker_id: str,
    addressed_to: Optional[List[str]] = None,
    summary: str,
    stance: str = "neutral",
    mentions: Optional[List[str]] = None,
    player_relevant: bool = False,
    world_signal_strength: float = 0.0,
    tick: int,
    beat_index: int = 0,
) -> Dict[str, Any]:
    """Build an authoritative conversation beat."""
    addressed_to = [_safe_str(x) for x in (addressed_to or []) if _safe_str(x)]
    mentions = [_safe_str(x) for x in (mentions or []) if _safe_str(x)][:_MAX_MENTIONS_PER_BEAT]

    beat_id = _stable_id(
        "beat",
        thread_id,
        beat_index,
        speaker_id,
    )

    return {
        "beat_id": beat_id,
        "thread_id": _safe_str(thread_id),
        "beat_index": int(beat_index),
        "speaker_id": _safe_str(speaker_id),
        "addressed_to": addressed_to,
        "summary": _safe_str(summary)[:500],
        "stance": _safe_str(stance) or "neutral",
        "mentions": mentions,
        "player_relevant": bool(player_relevant),
        "world_signal_strength": max(0.0, min(1.0, _safe_float(world_signal_strength))),
        "tick": int(tick or 0),
    }


# ── Beat storage in conversation state ────────────────────────────────────

def ensure_beats_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure beats storage exists in simulation_state."""
    if not isinstance(simulation_state, dict):
        simulation_state = {}
    social_state = simulation_state.setdefault("social_state", {})
    if not isinstance(social_state, dict):
        social_state = {}
        simulation_state["social_state"] = social_state
    conversations = social_state.setdefault("conversations", {})
    if not isinstance(conversations, dict):
        conversations = {}
        social_state["conversations"] = conversations
    beats = conversations.setdefault("beats_by_thread", {})
    if not isinstance(beats, dict):
        conversations["beats_by_thread"] = {}
    return simulation_state


def append_beat(simulation_state: Dict[str, Any], beat: Dict[str, Any]) -> Dict[str, Any]:
    """Append a beat to the thread's beat list."""
    ensure_beats_state(simulation_state)
    beat = _safe_dict(beat)
    thread_id = _safe_str(beat.get("thread_id"))
    if not thread_id:
        return simulation_state

    beats_by_thread = simulation_state["social_state"]["conversations"]["beats_by_thread"]
    rows = _safe_list(beats_by_thread.get(thread_id))
    rows.append(dict(beat))
    # Trim to bound
    if len(rows) > _MAX_BEATS_PER_THREAD:
        rows = rows[-_MAX_BEATS_PER_THREAD:]
    beats_by_thread[thread_id] = rows
    return simulation_state


def get_beats(simulation_state: Dict[str, Any], thread_id: str) -> List[Dict[str, Any]]:
    """Return all beats for a given thread."""
    ensure_beats_state(simulation_state)
    rows = _safe_list(
        simulation_state["social_state"]["conversations"]["beats_by_thread"].get(_safe_str(thread_id))
    )
    return [dict(x) for x in rows if isinstance(x, dict)]


def get_latest_beat(simulation_state: Dict[str, Any], thread_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent beat for a thread, or None."""
    beats = get_beats(simulation_state, thread_id)
    return beats[-1] if beats else None


# ── Beat advancement logic ────────────────────────────────────────────────

def compute_beat_caps(mode: str) -> tuple:
    """Return (min_beats, max_beats) for a conversation mode."""
    return MODE_BEAT_CAPS.get(_safe_str(mode), MODE_BEAT_CAPS["ambient"])


_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "has", "but", "not", "you",
    "they", "this", "that", "with", "from",
})


def extract_mentions_from_topic(topic: Dict[str, Any]) -> List[str]:
    """Extract entity mentions from a conversation topic."""
    topic = _safe_dict(topic)
    mentions: List[str] = []
    anchor = _safe_str(topic.get("anchor"))
    if anchor:
        mentions.append(anchor)
    summary = _safe_str(topic.get("summary"))
    # Simple extraction: split on common delimiters, keep non-empty tokens
    for token in summary.replace(",", " ").replace(".", " ").split():
        token = token.strip().lower()
        if len(token) > 2 and token not in _STOPWORDS:
            if token not in mentions:
                mentions.append(token)
            if len(mentions) >= _MAX_MENTIONS_PER_BEAT:
                break
    return mentions[:_MAX_MENTIONS_PER_BEAT]


def build_beat_from_conversation_line(
    conversation: Dict[str, Any],
    line: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    """Build a beat from an existing conversation line dict.

    This bridges the old line-based system to the new beat model,
    so beats are produced whenever a line is generated.
    """
    conversation = _safe_dict(conversation)
    line = _safe_dict(line)
    topic = _safe_dict(conversation.get("topic"))
    participants = _safe_list(conversation.get("participants"))
    speaker_id = _safe_str(line.get("speaker"))

    # addressed_to = other participants
    addressed_to = [p for p in participants if _safe_str(p) != speaker_id]

    # Player-relevant if player is present or topic is plan_reaction
    player_relevant = bool(conversation.get("player_present")) or _safe_str(topic.get("type")) in {
        "plan_reaction", "risk_conflict",
    }

    # World signal strength based on topic type
    topic_type = _safe_str(topic.get("type"))
    signal_map = {
        "moral_conflict": 0.4,
        "plan_reaction": 0.5,
        "local_incident": 0.6,
        "risk_conflict": 0.5,
        "faction_tension": 0.7,
        "event_commentary": 0.3,
        "ambient_chat": 0.1,
    }
    world_signal_strength = signal_map.get(topic_type, 0.2)

    return build_conversation_beat(
        thread_id=_safe_str(conversation.get("conversation_id")),
        speaker_id=speaker_id,
        addressed_to=addressed_to,
        summary=_safe_str(line.get("text")),
        stance=_safe_str(line.get("kind")) or "neutral",
        mentions=extract_mentions_from_topic(topic),
        player_relevant=player_relevant,
        world_signal_strength=world_signal_strength,
        tick=tick,
        beat_index=int(conversation.get("beat_count", 0) or 0),
    )


def trim_beats_state(simulation_state: Dict[str, Any], max_threads: int = 64) -> Dict[str, Any]:
    """Trim beats storage to bounded size."""
    ensure_beats_state(simulation_state)
    beats_by_thread = simulation_state["social_state"]["conversations"]["beats_by_thread"]
    if not isinstance(beats_by_thread, dict):
        return simulation_state

    # Keep only the most recent max_threads threads (by latest beat tick)
    if len(beats_by_thread) > max_threads:
        def _latest_tick(thread_id: str) -> int:
            beats = _safe_list(beats_by_thread.get(thread_id))
            if not beats:
                return 0
            return max(int(_safe_dict(b).get("tick", 0) or 0) for b in beats)

        sorted_ids = sorted(beats_by_thread.keys(), key=_latest_tick, reverse=True)
        trimmed = {}
        for tid in sorted_ids[:max_threads]:
            trimmed[tid] = beats_by_thread[tid]
        simulation_state["social_state"]["conversations"]["beats_by_thread"] = trimmed

    return simulation_state
