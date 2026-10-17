from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.foundation.dynamic_npc_profiles import (
    load_npc_profile,
)
from app.apps.rpg.genesis.profiles.profile_drafts import (
    profile_draft_summary,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _known_npc_ids_from_simulation_state(simulation_state: Dict[str, Any]) -> List[str]:
    ids = []

    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    for companion in _safe_list(party_state.get("companions")):
        npc_id = _safe_str(_safe_dict(companion).get("npc_id"))
        if npc_id:
            ids.append(npc_id)

    present_state = _safe_dict(simulation_state.get("present_npc_state"))
    by_location = _safe_dict(present_state.get("by_location"))
    for location_entry in by_location.values():
        for npc in _safe_list(_safe_dict(location_entry).get("present_npcs")):
            npc_id = _safe_str(_safe_dict(npc).get("npc_id"))
            if npc_id:
                ids.append(npc_id)

    evolution_by_npc = _safe_dict(_safe_dict(simulation_state.get("npc_evolution_state")).get("by_npc"))
    for npc_id in evolution_by_npc.keys():
        if _safe_str(npc_id):
            ids.append(_safe_str(npc_id))

    # Stable order, no duplicates.
    seen = set()
    result = []
    for npc_id in ids:
        if npc_id not in seen:
            seen.add(npc_id)
            result.append(npc_id)

    return result


def build_character_card(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = _safe_dict(profile)
    npc_id = _safe_str(profile.get("npc_id"))

    return {
        "npc_id": npc_id,
        "name": _safe_str(profile.get("name")),
        "origin": _safe_str(profile.get("origin")),
        "biography": deepcopy(_safe_dict(profile.get("biography"))),
        "history": deepcopy(_safe_dict(profile.get("history"))),
        "personality": deepcopy(_safe_dict(profile.get("personality"))),
        "morality": deepcopy(_safe_dict(profile.get("morality"))),
        "motivations": deepcopy(_safe_list(profile.get("motivations"))),
        "relationships": deepcopy(_safe_dict(profile.get("relationships"))),
        "evolution": deepcopy(_safe_dict(profile.get("evolution"))),
        "portrait": deepcopy(_safe_dict(profile.get("portrait"))),
        "card_edit_state": deepcopy(_safe_dict(profile.get("card_edit_state"))),
        "draft_summary": profile_draft_summary(npc_id),
        "source": "deterministic_character_card_service",
    }


def list_character_cards_for_simulation_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    cards = []
    missing = []

    for npc_id in _known_npc_ids_from_simulation_state(simulation_state):
        profile = load_npc_profile(npc_id)
        if profile:
            cards.append(build_character_card(profile))
        else:
            missing.append(npc_id)

    return {
        "ok": True,
        "cards": cards,
        "missing_profile_npc_ids": missing,
        "count": len(cards),
        "source": "deterministic_character_card_service",
    }


