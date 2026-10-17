from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_CONSEQUENCE_SIGNALS = 32
ALLOWED_CONSEQUENCE_SIGNAL_KINDS = {
    "trust_signal",
    "social_tension",
    "quest_interest",
    "rumor_pressure",
    "referral_hint",
}


def _is_backed_quest_access(quest_access: Dict[str, Any]) -> bool:
    access = _safe_dict(quest_access)
    if not access.get("requested"):
        return False

    topic_type = _safe_str(access.get("topic_type"))
    topic_id = _safe_str(access.get("topic_id"))
    access_level = _safe_str(access.get("access"))

    if access_level not in {"partial", "normal", "trusted"}:
        return False

    return topic_type == "quest" or topic_id.startswith("topic:quest:")


def ensure_consequence_signal_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("consequence_signal_state"))
    if not isinstance(state.get("signals"), list):
        state["signals"] = []
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["consequence_signal_state"] = state
    return state


def emit_consequence_signals(
    simulation_state: Dict[str, Any],
    *,
    conversation_result: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    conversation = _safe_dict(conversation_result)
    reputation = _safe_dict(conversation.get("player_reputation_consequence"))
    referral = _safe_dict(conversation.get("npc_referral"))
    rumor = _safe_dict(conversation.get("quest_rumor_result"))
    quest_access = _safe_dict(conversation.get("quest_conversation_access"))
    requested_access = _safe_dict(conversation.get("requested_topic_access"))

    emitted: List[Dict[str, Any]] = []
    current_tick = int(tick or 0)

    event = _safe_dict(reputation.get("event"))
    kind = _safe_str(event.get("kind"))

    if kind == "polite_cooperation":
        emitted.append({
            "signal_id": f"signal:trust:{current_tick}",
            "kind": "trust_signal",
            "strength": 1,
            "tick": current_tick,
            "source": "deterministic_consequence_signal_runtime",
        })

    if kind in {"unbacked_topic_pressure", "hostile_social_reply"}:
        emitted.append({
            "signal_id": f"signal:social_tension:{current_tick}",
            "kind": "social_tension",
            "strength": 1,
            "tick": current_tick,
            "source": "deterministic_consequence_signal_runtime",
        })

    if _is_backed_quest_access(quest_access):
        emitted.append({
            "signal_id": f"signal:quest_interest:{current_tick}",
            "kind": "quest_interest",
            "strength": 1,
            "topic_id": _safe_str(quest_access.get("topic_id")),
            "tick": current_tick,
            "source": "deterministic_consequence_signal_runtime",
        })

    if requested_access.get("requested") and requested_access.get("access") == "none":
        emitted.append({
            "signal_id": f"signal:rumor_pressure:{current_tick}",
            "kind": "rumor_pressure",
            "strength": 1,
            "requested_topic_hint": _safe_str(requested_access.get("requested_topic_hint")),
            "tick": current_tick,
            "source": "deterministic_consequence_signal_runtime",
        })

    if referral.get("suggested"):
        emitted.append({
            "signal_id": f"signal:referral:{current_tick}",
            "kind": "referral_hint",
            "strength": 1,
            "referral_npc_id": _safe_str(referral.get("referral_npc_id")),
            "tick": current_tick,
            "source": "deterministic_consequence_signal_runtime",
        })

    emitted = [
        signal for signal in emitted
        if _safe_str(signal.get("kind")) in ALLOWED_CONSEQUENCE_SIGNAL_KINDS
    ]

    if not emitted:
        return {
            "emitted": False,
            "signals": [],
            "reason": "no_consequence_signals",
            "source": "deterministic_consequence_signal_runtime",
        }

    state = ensure_consequence_signal_state(simulation_state)
    signals = _safe_list(state.get("signals"))
    signals = emitted + signals
    state["signals"] = signals[:MAX_CONSEQUENCE_SIGNALS]
    state["debug"] = {
        "last_updated_tick": current_tick,
        "last_emitted_count": len(emitted),
        "source": "deterministic_consequence_signal_runtime",
    }

    return {
        "emitted": True,
        "signals": deepcopy(emitted),
        "source": "deterministic_consequence_signal_runtime",
    }
