from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.party.companion_memory import companion_loyalty_projection
from app.apps.rpg.rules.party.companion_presence import (
    build_party_aware_turn_context,
)
from app.apps.rpg.rules.party.companion_quests import companion_quest_summary
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def maybe_build_direct_companion_turn_response(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
) -> Dict[str, Any]:
    """Build deterministic companion response metadata for direct companion talk.

    This does not replace LLM narration. It gives the turn contract a grounded
    companion target and a deterministic fallback line.
    """
    context = build_party_aware_turn_context(
        simulation_state,
        player_input=player_input,
        tick=tick,
    )
    addressed = _safe_dict(context.get("addressed_companion"))
    if not addressed.get("matched"):
        return {
            "matched": False,
            "reason": "no_active_companion_addressed",
            "party_aware_turn_context": deepcopy(context),
            "source": "deterministic_companion_turn_runtime",
        }

    companion = _safe_dict(addressed.get("companion"))
    npc_id = _safe_str(addressed.get("npc_id"))
    name = _safe_str(addressed.get("name") or companion.get("name") or npc_id.replace("npc:", ""))
    identity_arc = _safe_str(companion.get("identity_arc"))
    current_role = _safe_str(companion.get("current_role"))

    loyalty = companion_loyalty_projection(simulation_state, npc_id=npc_id)
    loyalty_state = _safe_str(loyalty.get("loyalty_state"))

    quest_summary = companion_quest_summary(simulation_state, npc_id=npc_id)
    quests = _safe_list(quest_summary.get("quests"))
    active_quest: dict = {}
    for quest in quests:
        quest = _safe_dict(quest)
        if _safe_str(quest.get("status")) == "active":
            active_quest = quest
            break
    quest_stage = _safe_str(active_quest.get("stage"))

    lowered = _safe_str(player_input).lower()

    if "think" in lowered or "advice" in lowered or "what should" in lowered:
        intent = "advice"
    elif "ready" in lowered:
        intent = "readiness"
    else:
        intent = "direct_companion_address"

    if identity_arc == "revenge_after_losing_tavern" and intent in {"advice", "direct_companion_address"}:
        if loyalty_state == "strained":
            line = (
                f"{name} looks away for a moment. "
                "\"If we're still chasing the bandits, then say it plainly. I need to know this matters.\""
            )
        elif loyalty_state == "at_risk":
            line = (
                f"{name} keeps his voice low. "
                "\"I will not keep walking beside someone who treats the Rusty Flagon like ash underfoot.\""
            )
        elif quest_stage == "follow_bandit_lead":
            line = (
                f"{name} leans forward, voice tight. "
                "\"If there are rumors, then someone heard where those bandits ran. We follow the lead before it goes cold.\""
            )
        elif quest_stage == "track_bandits":
            line = (
                f"{name} studies the ground ahead. "
                "\"Tracks, camp smoke, broken brush — anything they left behind can point us to them.\""
            )
        elif quest_stage == "confront_bandit_scout":
            line = (
                f"{name} lowers his voice. "
                "\"A scout means the rest are close. We take him alive if we can — he may know where the leader hides.\""
            )
        elif loyalty_state == "loyal":
            line = (
                f"{name} nods with grim certainty. "
                "\"We follow every rumor and every track until the bandits answer for it.\""
            )
        else:
            line = (
                f"{name} glances back toward the road. "
                "\"If those bandits left tracks or rumors behind, we follow them before they vanish.\""
            )
    elif intent == "readiness":
        line = f"{name} nods. \"I'm ready.\""
    else:
        line = f"{name} stays close. \"I'm with you.\""

    return {
        "matched": True,
        "npc_id": npc_id,
        "name": name,
        "intent": intent,
        "line": line,
        "identity_arc": identity_arc,
        "current_role": current_role,
        "companion_loyalty_projection": deepcopy(loyalty),
        "companion_quest_summary": deepcopy(quest_summary),
        "active_companion_quest": deepcopy(active_quest),
        "party_aware_turn_context": deepcopy(context),
        "npc_response_beat": {
            "kind": "companion_direct_response",
            "speaker_id": npc_id,
            "speaker_name": name,
            "line": line,
            "identity_arc": identity_arc,
            "current_role": current_role,
            "loyalty_state": loyalty_state,
            "companion_quest_stage": quest_stage,
            "companion_quest_id": _safe_str(active_quest.get("quest_id")),
            "source": "deterministic_companion_turn_runtime",
        },
        "source": "deterministic_companion_turn_runtime",
    }
