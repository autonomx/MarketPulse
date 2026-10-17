from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def build_intervention_options(conversation: Dict[str, Any], simulation_state: Dict[str, Any], runtime_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    conversation = _safe_dict(conversation)
    participants = [x for x in (_safe_str(p) for p in conversation.get("participants") or []) if x]
    if not conversation.get("player_can_intervene") or len(participants) < 2:
        return []

    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    a_name = _safe_str(_safe_dict(npc_index.get(participants[0])).get("name")) or participants[0]
    b_name = _safe_str(_safe_dict(npc_index.get(participants[1])).get("name")) or participants[1]

    return [
        {"id": f"support:{participants[0]}", "text": f"Support {a_name}", "effect": "trust_boost"},
        {"id": f"support:{participants[1]}", "text": f"Support {b_name}", "effect": "trust_boost"},
        {"id": "clarify", "text": "Ask for clarification", "effect": "respect_boost"},
        {"id": "end_discussion", "text": "End the discussion", "effect": "close_conversation"},
        {"id": "continue", "text": "Keep listening", "effect": "none"},
    ]


