from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.foundation.dynamic_npc_profiles import load_npc_profile
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _npc_evolution(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    return _safe_dict(
        _safe_dict(_safe_dict(simulation_state.get("npc_evolution_state")).get("by_npc")).get(npc_id)
    )


def _party_companion(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    for companion in _safe_list(party_state.get("companions")):
        companion = _safe_dict(companion)
        if _safe_str(companion.get("npc_id")) == npc_id:
            return companion
    return {}


def _default_value_profile_for_npc(npc_id: str) -> Dict[str, Any]:
    """Seed known archetypes without requiring full content coverage yet."""
    npc_id = _safe_str(npc_id)

    if npc_id == "npc:Bran":
        return {
            "npc_id": npc_id,
            "archetype": "displaced_tavern_keeper",
            "traits": ["protective", "grieving", "practical"],
            "morality": {
                "lawfulness": 0,
                "compassion": 2,
                "honor": 1,
                "vengefulness": 3,
                "greed": 0,
                "opportunism": 0,
            },
            "likes": [
                "protect_innocents",
                "keep_promises",
                "pursue_bandit_justice",
                "respect_personal_loss",
            ],
            "dislikes": [
                "dismiss_loss",
                "harm_innocents",
                "betray_companions",
                "mock_grief",
            ],
            "source": "deterministic_companion_value_runtime",
        }

    if npc_id in {"npc:Shade", "npc:thief", "npc:Thief"}:
        return {
            "npc_id": npc_id,
            "archetype": "thief",
            "traits": ["opportunistic", "irreverent", "risk_tolerant"],
            "morality": {
                "lawfulness": -3,
                "compassion": 0,
                "honor": -1,
                "vengefulness": 0,
                "greed": 3,
                "opportunism": 3,
            },
            "likes": [
                "clever_theft",
                "steal_from_rich",
                "deception",
                "profit",
                "avoid_guards",
            ],
            "dislikes": [
                "needless_cruelty",
                "snitching",
                "wasting_profit",
            ],
            "source": "deterministic_companion_value_runtime",
        }

    if npc_id in {"npc:Aldric", "npc:guard", "npc:Guard"}:
        return {
            "npc_id": npc_id,
            "archetype": "guard",
            "traits": ["lawful", "protective", "disciplined"],
            "morality": {
                "lawfulness": 3,
                "compassion": 1,
                "honor": 2,
                "vengefulness": 0,
                "greed": -1,
                "opportunism": -2,
            },
            "likes": [
                "protect_innocents",
                "respect_law",
                "honorable_conduct",
            ],
            "dislikes": [
                "theft",
                "murder",
                "bribery",
                "betrayal",
                "deception_against_innocents",
            ],
            "source": "deterministic_companion_value_runtime",
        }

    return {
        "npc_id": npc_id,
        "archetype": "unknown_companion",
        "traits": [],
        "morality": {
            "lawfulness": 0,
            "compassion": 0,
            "honor": 0,
            "vengefulness": 0,
            "greed": 0,
            "opportunism": 0,
        },
        "likes": ["keep_promises", "protect_companions"],
        "dislikes": ["betray_companions", "needless_cruelty"],
        "source": "deterministic_companion_value_runtime",
    }


def build_companion_value_profile(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
) -> Dict[str, Any]:
    """Build a deterministic value profile from known content + runtime state.

    Later this can read richer file-backed NPC biographies. For now it uses:
    - known archetype defaults,
    - companion party metadata,
    - npc_evolution_state current role / identity arc / motivations.
    """
    npc_id = _safe_str(npc_id)
    profile = deepcopy(_default_value_profile_for_npc(npc_id))

    file_profile = load_npc_profile(npc_id)
    if file_profile:
        personality = _safe_dict(file_profile.get("personality"))
        morality = _safe_dict(file_profile.get("morality"))
        biography = _safe_dict(file_profile.get("biography"))
        evolution = _safe_dict(file_profile.get("evolution"))

        traits = _safe_list(personality.get("traits"))
        if traits:
            profile["traits"] = traits

        if morality:
            profile["morality"] = {
                "lawfulness": _safe_int(morality.get("lawfulness"), 0),
                "compassion": _safe_int(morality.get("compassion"), 0),
                "honor": _safe_int(morality.get("honor"), 0),
                "vengefulness": _safe_int(morality.get("vengefulness"), 0),
                "greed": _safe_int(morality.get("greed"), 0),
                "opportunism": _safe_int(morality.get("opportunism"), 0),
            }

        profile["biography_summary"] = _safe_str(biography.get("short_summary"))
        profile["file_profile_origin"] = _safe_str(file_profile.get("origin"))
        profile["file_profile_loaded"] = True

        if _safe_str(evolution.get("identity_arc")):
            profile["identity_arc"] = _safe_str(evolution.get("identity_arc"))
        if _safe_str(evolution.get("current_role")):
            profile["current_role"] = _safe_str(evolution.get("current_role"))

    companion = _party_companion(simulation_state, npc_id)
    evolution_state = _npc_evolution(simulation_state, npc_id)

    identity_arc = (
        _safe_str(companion.get("identity_arc"))
        or _safe_str(evolution_state.get("identity_arc"))
        or _safe_str(profile.get("identity_arc"))
    )
    current_role = (
        _safe_str(companion.get("current_role"))
        or _safe_str(evolution_state.get("current_role"))
        or _safe_str(profile.get("current_role"))
    )
    motivations = _safe_list(companion.get("active_motivations")) or _safe_list(
        evolution_state.get("active_motivations")
    )

    profile["identity_arc"] = identity_arc
    profile["current_role"] = current_role
    profile["active_motivations"] = deepcopy(motivations)

    # Runtime arc-specific refinement.
    if identity_arc == "revenge_after_losing_tavern":
        likes = set(_safe_list(profile.get("likes")))
        dislikes = set(_safe_list(profile.get("dislikes")))
        likes.update({"pursue_bandit_justice", "respect_personal_loss"})
        dislikes.update({"dismiss_loss", "mock_grief"})
        profile["likes"] = sorted(likes)
        profile["dislikes"] = sorted(dislikes)
        morality = _safe_dict(profile.get("morality"))
        morality["vengefulness"] = max(_safe_int(morality.get("vengefulness"), 0), 3)
        morality["compassion"] = max(_safe_int(morality.get("compassion"), 0), 1)
        profile["morality"] = morality

    profile["source"] = "deterministic_companion_value_runtime"
    return profile


def classify_player_action_values(player_input: str) -> Dict[str, Any]:
    text = _safe_str(player_input).lower()
    tags: List[str] = []

    # Core motivation / empathy tags.
    if any(marker in text for marker in [
        "forget the bandits",
        "tavern does not matter",
        "tavern doesn't matter",
        "your tavern does not matter",
        "your tavern doesn't matter",
        "stop caring",
        "give up revenge",
    ]):
        tags.extend(["dismiss_loss", "dismiss_core_motivation", "callous"])

    if any(marker in text for marker in [
        "find the bandits",
        "hunt the bandits",
        "avenge",
        "destroyed your tavern",
        "burned your tavern",
        "we will find them",
        "they will answer",
    ]):
        tags.extend(["pursue_bandit_justice", "support_core_motivation", "keep_promises"])

    # General moral/action tags for future companions.
    if any(marker in text for marker in ["steal", "rob", "pickpocket", "loot the merchant"]):
        tags.append("theft")
    if any(marker in text for marker in ["lie", "deceive", "trick them", "con"]):
        tags.append("deception")
    if any(marker in text for marker in ["bribe", "pay off the guard"]):
        tags.append("bribery")
    if any(marker in text for marker in ["murder", "kill everyone", "slaughter"]):
        tags.extend(["murder", "needless_cruelty"])
    if any(marker in text for marker in ["protect", "save them", "defend the innocent"]):
        tags.append("protect_innocents")
    if any(marker in text for marker in ["keep my promise", "i promised", "we promised"]):
        tags.append("keep_promises")

    # Normalize while preserving order.
    seen = set()
    clean_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            clean_tags.append(tag)

    return {
        "matched": bool(clean_tags),
        "player_action_tags": clean_tags,
        "source": "deterministic_companion_value_runtime",
    }


def _alignment_from_tags(profile: Dict[str, Any], tags: List[str]) -> Dict[str, Any]:
    likes = set(_safe_list(profile.get("likes")))
    dislikes = set(_safe_list(profile.get("dislikes")))
    archetype = _safe_str(profile.get("archetype"))

    positive = 0
    negative = 0
    reasons: List[str] = []

    for tag in tags:
        if tag in likes:
            positive += 1
            reasons.append(f"tag_aligned:{tag}")
        if tag in dislikes:
            negative += 1
            reasons.append(f"tag_conflicted:{tag}")

    morality = _safe_dict(profile.get("morality"))

    # Archetype/value specific rules.
    if "theft" in tags:
        if archetype == "thief" or _safe_int(morality.get("opportunism"), 0) >= 2:
            positive += 1
            reasons.append("theft_aligned_with_opportunistic_values")
        if _safe_int(morality.get("lawfulness"), 0) >= 2:
            negative += 1
            reasons.append("theft_conflicts_with_lawful_values")

    if "deception" in tags:
        if archetype == "thief" or _safe_int(morality.get("opportunism"), 0) >= 2:
            positive += 1
            reasons.append("deception_aligned_with_opportunistic_values")
        if _safe_int(morality.get("honor"), 0) >= 2:
            negative += 1
            reasons.append("deception_conflicts_with_honorable_values")

    if "needless_cruelty" in tags or "murder" in tags:
        if _safe_int(morality.get("compassion"), 0) >= 0:
            negative += 2
            reasons.append("cruelty_conflicts_with_companion_values")

    if "dismiss_core_motivation" in tags:
        negative += 2
        reasons.append("player_dismissed_companion_core_motivation")

    if "support_core_motivation" in tags:
        positive += 2
        reasons.append("player_supported_companion_core_motivation")

    if positive > negative:
        return {
            "alignment": "aligned_with_npc",
            "score": positive - negative,
            "reasons": reasons,
        }

    if negative > positive:
        return {
            "alignment": "conflicts_with_npc",
            "score": negative - positive,
            "reasons": reasons,
        }

    return {
        "alignment": "neutral_to_npc",
        "score": 0,
        "reasons": reasons or ["no_strong_value_alignment"],
    }


def _deltas_for_alignment(alignment: str, reasons: List[str]) -> Dict[str, int]:
    reasons_set = set(reasons)

    if "player_dismissed_companion_core_motivation" in reasons_set:
        return {
            "trust_delta": -1,
            "respect_delta": -1,
            "morale_delta": -1,
            "loyalty_delta": -1,
        }

    if "player_supported_companion_core_motivation" in reasons_set:
        return {
            "trust_delta": 1,
            "respect_delta": 1,
            "morale_delta": 1,
            "loyalty_delta": 1,
        }

    if alignment == "aligned_with_npc":
        return {
            "trust_delta": 0,
            "respect_delta": 1,
            "morale_delta": 0,
            "loyalty_delta": 0,
        }

    if alignment == "conflicts_with_npc":
        return {
            "trust_delta": -1,
            "respect_delta": -1,
            "morale_delta": 0,
            "loyalty_delta": -1,
        }

    return {
        "trust_delta": 0,
        "respect_delta": 0,
        "morale_delta": 0,
        "loyalty_delta": 0,
    }


def evaluate_companion_value_alignment(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    player_input: str,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    if not npc_id:
        return {
            "matched": False,
            "reason": "missing_npc_id",
            "source": "deterministic_companion_value_runtime",
        }

    profile = build_companion_value_profile(simulation_state, npc_id=npc_id)
    action = classify_player_action_values(player_input)
    tags = _safe_list(action.get("player_action_tags"))

    if not tags:
        return {
            "matched": False,
            "npc_id": npc_id,
            "reason": "no_value_relevant_player_action_tags",
            "profile_basis": deepcopy(profile),
            "evaluated_player_action": deepcopy(action),
            "source": "deterministic_companion_value_runtime",
        }

    alignment = _alignment_from_tags(profile, tags)
    reasons = _safe_list(alignment.get("reasons"))
    deltas = _deltas_for_alignment(_safe_str(alignment.get("alignment")), reasons)

    # Prioritize the most semantically significant reason.
    _priority_reasons = {
        "player_dismissed_companion_core_motivation",
        "player_supported_companion_core_motivation",
    }
    priority = next((r for r in reasons if r in _priority_reasons), None)
    reason = priority or (reasons[0] if reasons else _safe_str(alignment.get("alignment")))

    return {
        "matched": True,
        "npc_id": npc_id,
        "profile_basis": deepcopy(profile),
        "evaluated_player_action": deepcopy(action),
        "alignment": _safe_str(alignment.get("alignment")),
        "alignment_score": _safe_int(alignment.get("score"), 0),
        "reasons": reasons,
        "reason": reason,
        "deltas": deltas,
        "source": "deterministic_companion_value_runtime",
    }
