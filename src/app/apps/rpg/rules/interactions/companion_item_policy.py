from __future__ import annotations

import logging

from typing import Any, Dict, cast

from app.apps.rpg.rules.interactions.item_model import normalize_item_instance
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

logger = logging.getLogger(__name__)


def _norm(value: Any) -> str:
    return _safe_str(value).strip().lower()


def _tags(item: Dict[str, Any]) -> set[str]:
    item = normalize_item_instance(_safe_dict(item))
    return {_norm(tag) for tag in _safe_list(item.get("tags")) if _norm(tag)}


def _profile_terms(companion: Dict[str, Any]) -> set[str]:
    companion = _safe_dict(companion)

    fields = [
        companion.get("role"),
        companion.get("current_role"),
        companion.get("identity_arc"),
        companion.get("personality"),
        companion.get("morality"),
        companion.get("profession"),
        companion.get("background"),
    ]

    profile = _safe_dict(companion.get("profile"))
    biography = _safe_dict(companion.get("biography"))
    card = _safe_dict(companion.get("character_card"))

    fields.extend([
        profile.get("role"),
        profile.get("current_role"),
        profile.get("identity_arc"),
        profile.get("personality"),
        profile.get("morality"),
        profile.get("profession"),
        profile.get("background"),
        biography.get("summary"),
        biography.get("personality"),
        biography.get("morality"),
        card.get("personality"),
        card.get("morality"),
        card.get("background"),
    ])

    terms: set[str] = set()
    for value in fields:
        text = _norm(value)
        if not text:
            continue
        for token in text.replace(",", " ").replace("/", " ").replace("-", " ").split():
            if token:
                terms.add(token)
        terms.add(text)

    motivations = _safe_list(companion.get("active_motivations"))
    motivations.extend(_safe_list(profile.get("active_motivations")))
    for motivation in motivations:
        text = _norm(motivation)
        if text:
            terms.add(text)
            for token in text.split():
                terms.add(token)

    traits = _safe_list(companion.get("traits"))
    traits.extend(_safe_list(profile.get("traits")))
    traits.extend(_safe_list(card.get("traits")))
    for trait in traits:
        text = _norm(trait)
        if text:
            terms.add(text)

    return terms


def _loyalty_score(companion: Dict[str, Any]) -> int:
    companion = _safe_dict(companion)
    for key in ("loyalty", "loyalty_score", "trust", "relationship_score"):
        raw = companion.get(key)
        try:
            return int(cast(Any, raw))
        except Exception:
            logger.debug("suppressed error in %s", "_loyalty_score", exc_info=True)

    relationship = _safe_dict(companion.get("relationship"))
    for key in ("loyalty", "trust", "score"):
        try:
            return int(cast(Any, relationship.get(key)))
        except Exception:
            logger.debug("suppressed error in %s", "_loyalty_score", exc_info=True)

    return 0


def evaluate_companion_item_acceptance(
    *,
    companion: Dict[str, Any],
    item: Dict[str, Any],
) -> Dict[str, Any]:
    companion = _safe_dict(companion)
    item = normalize_item_instance(_safe_dict(item))

    item_tags = _tags(item)
    profile_terms = _profile_terms(companion)
    loyalty = _loyalty_score(companion)

    npc_id = _safe_str(companion.get("npc_id"))
    item_id = _safe_str(item.get("item_id"))

    lawful_terms = {"guard", "captain", "watch", "lawful", "honorable", "honourable", "justice"}
    thief_terms = {"thief", "rogue", "criminal", "opportunist", "smuggler", "bandit"}
    revenge_terms = {"revenge", "vengeance", "bandit", "revenge_after_losing_tavern"}

    illicit_tags = {"stolen", "contraband", "illicit"}
    weapon_tags = {"weapon", "blade", "bow", "ranged", "dagger"}
    armor_tags = {"armor", "armor_light", "shield"}
    valuable_tags = {"valuable", "jewelry", "ring", "trinket"}

    if item_tags & illicit_tags and profile_terms & lawful_terms and loyalty < 80:
        return {
            "accepted": False,
            "reason": "morality_refuses_stolen_goods",
            "npc_id": npc_id,
            "item_id": item_id,
            "item_tags": sorted(item_tags),
            "profile_terms": sorted(profile_terms),
            "loyalty": loyalty,
            "source": "deterministic_companion_item_policy",
        }

    if item_tags & illicit_tags and (profile_terms & thief_terms or item_tags & valuable_tags):
        return {
            "accepted": True,
            "reason": "personality_accepts_illicit_valuable",
            "npc_id": npc_id,
            "item_id": item_id,
            "item_tags": sorted(item_tags),
            "profile_terms": sorted(profile_terms),
            "loyalty": loyalty,
            "source": "deterministic_companion_item_policy",
        }

    if item_tags & weapon_tags and (profile_terms & revenge_terms or "companion" in profile_terms):
        return {
            "accepted": True,
            "reason": "item_useful_to_companion_arc",
            "npc_id": npc_id,
            "item_id": item_id,
            "item_tags": sorted(item_tags),
            "profile_terms": sorted(profile_terms),
            "loyalty": loyalty,
            "source": "deterministic_companion_item_policy",
        }

    if item_tags & armor_tags:
        return {
            "accepted": True,
            "reason": "defensive_item_accepted",
            "npc_id": npc_id,
            "item_id": item_id,
            "item_tags": sorted(item_tags),
            "profile_terms": sorted(profile_terms),
            "loyalty": loyalty,
            "source": "deterministic_companion_item_policy",
        }

    if loyalty < -25:
        return {
            "accepted": False,
            "reason": "low_loyalty_refuses_nonessential_item",
            "npc_id": npc_id,
            "item_id": item_id,
            "item_tags": sorted(item_tags),
            "profile_terms": sorted(profile_terms),
            "loyalty": loyalty,
            "source": "deterministic_companion_item_policy",
        }

    return {
        "accepted": True,
        "reason": "neutral_item_accepted",
        "npc_id": npc_id,
        "item_id": item_id,
        "item_tags": sorted(item_tags),
        "profile_terms": sorted(profile_terms),
        "loyalty": loyalty,
        "source": "deterministic_companion_item_policy",
    }
