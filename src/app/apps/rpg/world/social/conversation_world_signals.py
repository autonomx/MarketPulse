"""4C-E — World Signal Extraction from Conversations.

Extracts bounded, explicit world signals from conversation beats.
Signals are small, typed effects that influence the world without
allowing freeform LLM text to directly mutate state.

Allowed first-pass effects:
- add rumor
- increase/decrease local tension by 1
- mark topic as "active in scene"
- spawn a soft world event candidate
- update NPC relation mood tags
- attach a lead to an existing quest thread

NOT allowed yet:
- create huge state rewrites
- invent major plot facts without backend validation
- directly award/resolve quests
- mutate factions heavily from casual chatter
"""
from __future__ import annotations

from hashlib import sha1
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


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_safe_str(p) for p in parts)
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


# ── Signal types ──────────────────────────────────────────────────────────

SIGNAL_TYPES = frozenset({
    "rumor",
    "tension_increase",
    "tension_decrease",
    "active_scene_topic",
    "soft_world_event",
    "npc_mood_update",
    "quest_lead",
})

# World signal strength thresholds
_RUMOR_THRESHOLD = 0.3
_TENSION_THRESHOLD = 0.5
_QUEST_LEAD_THRESHOLD = 0.6
_MOOD_UPDATE_THRESHOLD = 0.2

# Bounds
_MAX_SIGNALS_PER_BEAT = 2
_MAX_SIGNALS_PER_THREAD = 6
_MAX_PENDING_SIGNALS = 32


# ── Signal construction ───────────────────────────────────────────────────

def build_world_signal(
    *,
    signal_type: str,
    source_thread_id: str,
    source_beat_id: str = "",
    topic: str = "",
    location_id: str = "",
    strength: int = 1,
    metadata: Optional[Dict[str, Any]] = None,
    tick: int = 0,
) -> Dict[str, Any]:
    """Build a typed world signal from a conversation beat."""
    signal_type = _safe_str(signal_type)
    if signal_type not in SIGNAL_TYPES:
        signal_type = "active_scene_topic"

    signal_id = _stable_id(
        "sig",
        source_thread_id,
        source_beat_id,
        signal_type,
        tick,
    )

    return {
        "signal_id": signal_id,
        "type": signal_type,
        "source_thread_id": _safe_str(source_thread_id),
        "source_beat_id": _safe_str(source_beat_id),
        "topic": _safe_str(topic)[:200],
        "location_id": _safe_str(location_id),
        "strength": max(0, min(3, int(strength or 1))),
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        "tick": int(tick or 0),
        "applied": False,
    }


# ── Signal extraction from beats ─────────────────────────────────────────

def extract_signals_from_beat(
    beat: Dict[str, Any],
    conversation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Extract world signals from a single conversation beat.

    Returns a list of at most _MAX_SIGNALS_PER_BEAT signals.
    Only produces signals if the beat's world_signal_strength
    exceeds the relevant threshold.
    """
    beat = _safe_dict(beat)
    conversation = _safe_dict(conversation)

    thread_id = _safe_str(beat.get("thread_id"))
    beat_id = _safe_str(beat.get("beat_id"))
    tick = _safe_int(beat.get("tick"), 0)
    signal_strength = _safe_float(beat.get("world_signal_strength"))
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))
    topic_summary = _safe_str(topic.get("summary"))
    location_id = _safe_str(conversation.get("location_id"))
    mentions = _safe_list(beat.get("mentions"))

    # Check budget
    budget = _safe_int(conversation.get("world_effect_budget"), 1)
    emitted = _safe_int(conversation.get("world_effects_emitted"), 0)
    remaining_budget = max(0, budget - emitted)
    if remaining_budget <= 0:
        return []

    signals: List[Dict[str, Any]] = []

    # 1. Rumor signal
    if signal_strength >= _RUMOR_THRESHOLD and topic_type in {
        "moral_conflict", "plan_reaction", "local_incident",
        "faction_tension", "risk_conflict", "event_commentary",
    }:
        signals.append(build_world_signal(
            signal_type="rumor",
            source_thread_id=thread_id,
            source_beat_id=beat_id,
            topic=topic_summary or topic_type,
            location_id=location_id,
            strength=1,
            metadata={"mentions": mentions[:4], "topic_type": topic_type},
            tick=tick,
        ))

    # 2. Tension signal
    if signal_strength >= _TENSION_THRESHOLD and topic_type in {
        "local_incident", "faction_tension", "moral_conflict",
    }:
        signals.append(build_world_signal(
            signal_type="tension_increase",
            source_thread_id=thread_id,
            source_beat_id=beat_id,
            topic=topic_type,
            location_id=location_id,
            strength=1,
            tick=tick,
        ))

    # 3. Active scene topic
    if signal_strength >= _MOOD_UPDATE_THRESHOLD:
        signals.append(build_world_signal(
            signal_type="active_scene_topic",
            source_thread_id=thread_id,
            source_beat_id=beat_id,
            topic=topic_summary[:100] if topic_summary else topic_type,
            location_id=location_id,
            strength=1,
            tick=tick,
        ))

    # 4. Quest lead
    if signal_strength >= _QUEST_LEAD_THRESHOLD and topic_type in {
        "plan_reaction", "risk_conflict", "local_incident",
    } and beat.get("player_relevant"):
        signals.append(build_world_signal(
            signal_type="quest_lead",
            source_thread_id=thread_id,
            source_beat_id=beat_id,
            topic=topic_summary,
            location_id=location_id,
            strength=1,
            metadata={"mentions": mentions[:4]},
            tick=tick,
        ))

    # 5. NPC mood update
    if signal_strength >= _MOOD_UPDATE_THRESHOLD:
        stance = _safe_str(beat.get("stance"))
        if stance in {"challenge", "warning", "threat"}:
            signals.append(build_world_signal(
                signal_type="npc_mood_update",
                source_thread_id=thread_id,
                source_beat_id=beat_id,
                topic=stance,
                location_id=location_id,
                strength=1,
                metadata={
                    "speaker_id": _safe_str(beat.get("speaker_id")),
                    "addressed_to": _safe_list(beat.get("addressed_to"))[:4],
                    "stance": stance,
                },
                tick=tick,
            ))

    # Trim to per-beat max and budget
    return signals[:min(_MAX_SIGNALS_PER_BEAT, remaining_budget)]


# ── Signal storage ────────────────────────────────────────────────────────

def ensure_signal_state(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure world signal storage exists in runtime_state."""
    if not isinstance(runtime_state, dict):
        runtime_state = {}
    signals = runtime_state.setdefault("conversation_world_signals", {})
    if not isinstance(signals, dict):
        signals = {}
        runtime_state["conversation_world_signals"] = signals
    signals.setdefault("pending", [])
    signals.setdefault("applied", [])
    signals.setdefault("total_emitted", 0)
    return runtime_state


def enqueue_signals(runtime_state: Dict[str, Any], signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Add signals to the pending queue."""
    ensure_signal_state(runtime_state)
    pending = runtime_state["conversation_world_signals"]["pending"]
    for sig in signals:
        if isinstance(sig, dict):
            pending.append(dict(sig))
    # Trim to bound
    runtime_state["conversation_world_signals"]["pending"] = pending[-_MAX_PENDING_SIGNALS:]
    runtime_state["conversation_world_signals"]["total_emitted"] = (
        _safe_int(runtime_state["conversation_world_signals"].get("total_emitted"), 0)
        + len(signals)
    )
    return runtime_state


def get_pending_signals(runtime_state: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    """Return pending (unapplied) signals."""
    ensure_signal_state(runtime_state)
    pending = _safe_list(runtime_state["conversation_world_signals"]["pending"])
    return [dict(s) for s in pending if isinstance(s, dict) and not s.get("applied")][:limit]


def mark_signals_applied(
    runtime_state: Dict[str, Any],
    signal_ids: List[str],
) -> Dict[str, Any]:
    """Mark signals as applied after the world has processed them."""
    ensure_signal_state(runtime_state)
    id_set = set(_safe_str(x) for x in signal_ids)
    pending = runtime_state["conversation_world_signals"]["pending"]
    applied = runtime_state["conversation_world_signals"]["applied"]

    still_pending: List[Dict[str, Any]] = []
    for sig in pending:
        sig = _safe_dict(sig)
        if _safe_str(sig.get("signal_id")) in id_set:
            sig["applied"] = True
            applied.append(sig)
        else:
            still_pending.append(sig)

    runtime_state["conversation_world_signals"]["pending"] = still_pending
    runtime_state["conversation_world_signals"]["applied"] = applied[-_MAX_PENDING_SIGNALS:]
    return runtime_state


# ── Signal application helpers ────────────────────────────────────────────

def apply_rumor_signal(
    signal: Dict[str, Any],
    simulation_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a rumor signal to the simulation state.

    Creates a rumor entry in social_state.
    """
    signal = _safe_dict(signal)
    simulation_state = _safe_dict(simulation_state)
    social_state = simulation_state.setdefault("social_state", {})
    if not isinstance(social_state, dict):
        social_state = {}
        simulation_state["social_state"] = social_state

    conversations = social_state.setdefault("conversations", {})
    if not isinstance(conversations, dict):
        conversations = {}
        social_state["conversations"] = conversations
    rumors = conversations.setdefault("conversation_rumors", [])
    if not isinstance(rumors, list):
        rumors = []
        conversations["conversation_rumors"] = rumors

    metadata = _safe_dict(signal.get("metadata"))
    rumor = {
        "rumor_id": _safe_str(signal.get("signal_id")),
        "source": "conversation",
        "topic": _safe_str(signal.get("topic")),
        "location_id": _safe_str(signal.get("location_id")),
        "mentions": _safe_list(metadata.get("mentions"))[:4],
        "topic_type": _safe_str(metadata.get("topic_type")),
        "strength": _safe_int(signal.get("strength"), 1),
        "tick": _safe_int(signal.get("tick"), 0),
        "active": True,
    }
    rumors.append(rumor)
    conversations["conversation_rumors"] = rumors[-32:]  # bound
    return simulation_state


def apply_tension_signal(
    signal: Dict[str, Any],
    simulation_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a tension signal to update local tension counters."""
    signal = _safe_dict(signal)
    simulation_state = _safe_dict(simulation_state)
    location_id = _safe_str(signal.get("location_id"))
    if not location_id:
        return simulation_state

    social_state = simulation_state.setdefault("social_state", {})
    if not isinstance(social_state, dict):
        social_state = {}
        simulation_state["social_state"] = social_state
    tension = social_state.setdefault("location_tension", {})
    if not isinstance(tension, dict):
        tension = {}
        social_state["location_tension"] = tension

    current = _safe_int(tension.get(location_id), 0)
    signal_type = _safe_str(signal.get("type"))
    delta = _safe_int(signal.get("strength"), 1)

    if signal_type == "tension_increase":
        tension[location_id] = min(10, current + delta)
    elif signal_type == "tension_decrease":
        tension[location_id] = max(0, current - delta)

    return simulation_state


def apply_pending_signals(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
) -> tuple:
    """Apply all pending world signals to the simulation state.

    Returns (simulation_state, runtime_state) tuple.
    """
    ensure_signal_state(runtime_state)
    pending = get_pending_signals(runtime_state, limit=8)
    applied_ids: List[str] = []

    for signal in pending:
        signal = _safe_dict(signal)
        sig_type = _safe_str(signal.get("type"))
        sig_id = _safe_str(signal.get("signal_id"))

        if sig_type == "rumor":
            simulation_state = apply_rumor_signal(signal, simulation_state)
        elif sig_type in {"tension_increase", "tension_decrease"}:
            simulation_state = apply_tension_signal(signal, simulation_state)
        # active_scene_topic, quest_lead, npc_mood_update, soft_world_event
        # are stored as pending signals for the world event director to consume

        applied_ids.append(sig_id)

    if applied_ids:
        runtime_state = mark_signals_applied(runtime_state, applied_ids)

    return simulation_state, runtime_state
