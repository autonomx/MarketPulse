from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_PARTY_COMPOSITION_EFFECTS = 12


def active_companions_from_state(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    companions = []
    for companion in _safe_list(party_state.get("companions")):
        companion = _safe_dict(companion)
        if not companion:
            continue
        if _safe_str(companion.get("status") or "active") != "active":
            continue
        companions.append(companion)
    return companions


def project_party_composition_effects(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    companions = active_companions_from_state(simulation_state)
    effects: List[Dict[str, Any]] = []

    by_id = {
        _safe_str(companion.get("npc_id")): companion
        for companion in companions
        if _safe_str(companion.get("npc_id"))
    }

    bran = by_id.get("npc:Bran")
    mira = by_id.get("npc:Mira")

    if bran and mira:
        bran_arc = _safe_str(bran.get("identity_arc"))

        if bran_arc == "revenge_after_losing_tavern":
            effects.append({
                "kind": "companion_pair_tension",
                "npc_ids": ["npc:Bran", "npc:Mira"],
                "reason": "revenge_arc_vs_cautious_mediator",
                "severity": 1,
                "bounded": True,
                "source": "deterministic_party_composition_runtime",
            })

    # Generic v1: if more than one active companion exists, expose stable party size context.
    if len(companions) > 1:
        effects.append({
            "kind": "multi_companion_party_context",
            "npc_ids": [_safe_str(companion.get("npc_id")) for companion in companions],
            "reason": "multiple_active_companions",
            "severity": 0,
            "bounded": True,
            "source": "deterministic_party_composition_runtime",
        })

    effects = effects[:MAX_PARTY_COMPOSITION_EFFECTS]

    result = {
        "projected": True,
        "active_companion_count": len(companions),
        "effects": deepcopy(effects),
        "source": "deterministic_party_composition_runtime",
    }

    simulation_state["party_composition_effects"] = result
    return result
