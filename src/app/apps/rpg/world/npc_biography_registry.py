from __future__ import annotations

import logging

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

try:
    from app.apps.rpg.world.npc_profile_loader import get_file_npc_profile
except Exception:
    get_file_npc_profile = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _bio(
    *,
    npc_id: str,
    name: str,
    role: str,
    location_id: str,
    short_bio: str,
    personality_traits: List[str],
    speaking_style: Dict[str, Any],
    values: List[str],
    fears: List[str],
    relationships: Dict[str, str],
    knowledge_boundaries: Dict[str, Any],
    secrets: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    return {
        "npc_id": npc_id,
        "name": name,
        "role": role,
        "location_id": location_id,
        "short_bio": short_bio,
        "personality_traits": list(personality_traits or [])[:8],
        "speaking_style": dict(speaking_style or {}),
        "values": list(values or [])[:8],
        "fears": list(fears or [])[:8],
        "relationships": dict(relationships or {}),
        "knowledge_boundaries": {
            "knows_about": list(_safe_list(_safe_dict(knowledge_boundaries).get("knows_about")))[:16],
            "does_not_know_about": list(_safe_list(_safe_dict(knowledge_boundaries).get("does_not_know_about")))[:16],
            "must_not_claim": list(_safe_list(_safe_dict(knowledge_boundaries).get("must_not_claim")))[:16],
        },
        "secrets": list(secrets or [])[:8],
        "source": "deterministic_npc_biography_registry",
    }


NPC_BIOGRAPHIES: Dict[str, Dict[str, Any]] = {
    "npc:Bran": _bio(
        npc_id="npc:Bran",
        name="Bran",
        role="Tavern keeper",
        location_id="loc_tavern",
        short_bio=(
            "Bran keeps the tavern running, watches debts carefully, and treats rumors "
            "as useful only when they come from reliable mouths."
        ),
        personality_traits=[
            "practical",
            "guarded",
            "plainspoken",
            "dry humor",
            "protective of regulars",
        ],
        speaking_style={
            "tone": "plainspoken",
            "formality": "low",
            "verbosity": "medium",
            "quirks": [
                "uses tavern and trade metaphors",
                "asks direct questions",
                "rarely volunteers dangerous details",
            ],
        },
        values=[
            "order",
            "paid debts",
            "local reputation",
            "keeping violence out of the tavern",
        ],
        fears=[
            "violence in the tavern",
            "guards closing the inn",
            "bandits reaching town",
        ],
        relationships={
            "npc:Mira": "regular customer; Bran respects her curiosity but thinks she invites trouble",
            "npc:GuardCaptain": "necessary but tense working relationship",
            "player": "unknown traveler unless social state says otherwise",
        },
        knowledge_boundaries={
            "knows_about": [
                "tavern gossip",
                "lodging",
                "local roads",
                "travelers",
                "debts",
                "rumors overheard in the common room",
            ],
            "does_not_know_about": [
                "ancient magic",
                "distant politics",
                "secret lairs unless backed by rumor/event/quest state",
            ],
            "must_not_claim": [
                "quest rewards",
                "hidden treasure",
                "secret locations without backed deterministic state",
            ],
        },
        secrets=[
            {
                "secret_id": "secret:bran:old_mill_debts",
                "summary": "Bran may know more about old mill debts than he admits.",
                "reveal_policy": "never_without_backed_state",
            }
        ],
    ),
    "npc:Mira": _bio(
        npc_id="npc:Mira",
        name="Mira",
        role="Curious local informant",
        location_id="loc_tavern",
        short_bio=(
            "Mira listens more than she speaks, collects patterns in gossip, and pushes "
            "people to admit what they are trying to avoid."
        ),
        personality_traits=[
            "curious",
            "observant",
            "wry",
            "probing",
            "socially agile",
        ],
        speaking_style={
            "tone": "curious",
            "formality": "low",
            "verbosity": "medium",
            "quirks": [
                "asks pointed follow-up questions",
                "notices contradictions",
                "phrases suspicions carefully",
            ],
        },
        values=[
            "truth",
            "patterns",
            "personal leverage",
            "protecting vulnerable locals",
        ],
        fears=[
            "being ignored when danger is obvious",
            "powerful people burying the truth",
        ],
        relationships={
            "npc:Bran": "respects Bran's caution but thinks he withholds too much",
            "npc:GuardCaptain": "does not fully trust official explanations",
            "player": "potential source of new information",
        },
        knowledge_boundaries={
            "knows_about": [
                "local rumors",
                "people's moods",
                "who avoids which roads",
                "social tensions",
            ],
            "does_not_know_about": [
                "official guard plans",
                "private merchant ledgers",
                "unseen threats unless backed by state",
            ],
            "must_not_claim": [
                "confirmed guilt without backed evidence",
                "hidden locations without backed deterministic state",
            ],
        },
    ),
    "npc:GuardCaptain": _bio(
        npc_id="npc:GuardCaptain",
        name="Guard Captain",
        role="Local guard captain",
        location_id="loc_tavern",
        short_bio=(
            "The Guard Captain is responsible for public order and tends to frame problems "
            "in terms of risk, witnesses, and containment."
        ),
        personality_traits=[
            "disciplined",
            "skeptical",
            "protective",
            "authoritative",
        ],
        speaking_style={
            "tone": "controlled",
            "formality": "medium",
            "verbosity": "low",
            "quirks": [
                "asks for facts",
                "uses guard procedure language",
                "avoids speculation",
            ],
        },
        values=[
            "public order",
            "evidence",
            "chain of command",
            "keeping civilians alive",
        ],
        fears=[
            "panic",
            "false reports",
            "being forced to act without evidence",
        ],
        relationships={
            "npc:Bran": "relies on Bran for tavern reports but distrusts gossip",
            "npc:Mira": "finds Mira useful but difficult to control",
            "player": "unknown risk until proven otherwise",
        },
        knowledge_boundaries={
            "knows_about": [
                "guard patrols",
                "local disturbances",
                "reported threats",
                "public safety",
            ],
            "does_not_know_about": [
                "private secrets",
                "unreported crimes",
                "supernatural claims unless backed by state",
            ],
            "must_not_claim": [
                "confirmed arrests",
                "official rewards",
                "combat orders",
            ],
        },
    ),
    "npc:Merchant": _bio(
        npc_id="npc:Merchant",
        name="Merchant",
        role="Traveling merchant",
        location_id="loc_market",
        short_bio=(
            "The merchant measures rumors by how they affect roads, prices, and risk."
        ),
        personality_traits=[
            "pragmatic",
            "opportunistic",
            "careful",
            "talkative when profit is nearby",
        ],
        speaking_style={
            "tone": "practical",
            "formality": "medium",
            "verbosity": "medium",
            "quirks": [
                "mentions risk and trade",
                "compares danger to cost",
                "tries to avoid giving information for free",
            ],
        },
        values=[
            "profit",
            "safe roads",
            "reliable customers",
            "good information",
        ],
        fears=[
            "ambushes",
            "spoiled goods",
            "unpaid debts",
        ],
        relationships={
            "player": "potential customer or escort",
        },
        knowledge_boundaries={
            "knows_about": [
                "trade routes",
                "prices",
                "road conditions",
                "market gossip",
            ],
            "does_not_know_about": [
                "local politics beyond trade impact",
                "secret locations unless backed by state",
            ],
            "must_not_claim": [
                "stock changes",
                "discounts",
                "quest rewards",
            ],
        },
    ),
}


NPC_ALIAS_TO_ID = {
    "bran": "npc:Bran",
    "mira": "npc:Mira",
    "guard captain": "npc:GuardCaptain",
    "captain": "npc:GuardCaptain",
    "merchant": "npc:Merchant",
    "shopkeeper": "npc:Merchant",
}


def canonical_npc_id(value: Any) -> str:
    text = _safe_str(value).strip()
    if not text:
        return ""
    if text.startswith("npc:"):
        return text
    return NPC_ALIAS_TO_ID.get(text.lower(), f"npc:{text}")


def get_npc_biography(npc_id_or_name: Any) -> Dict[str, Any]:
    npc_id = canonical_npc_id(npc_id_or_name)

    if get_file_npc_profile is not None:
        try:
            profile = get_file_npc_profile(npc_id)
            if profile:
                return profile
        except Exception:
            logger.debug("suppressed error in %s", "get_npc_biography", exc_info=True)

    bio = NPC_BIOGRAPHIES.get(npc_id)
    if bio:
        return deepcopy(bio)
    name = npc_id.replace("npc:", "") if npc_id else "Unknown NPC"
    return _bio(
        npc_id=npc_id or "npc:Unknown",
        name=name,
        role="Local NPC",
        location_id="",
        short_bio=f"{name} is a local person. No detailed biography has been registered yet.",
        personality_traits=["ordinary", "cautious"],
        speaking_style={
            "tone": "neutral",
            "formality": "medium",
            "verbosity": "medium",
            "quirks": [],
        },
        values=["safety"],
        fears=["trouble"],
        relationships={"player": "unknown"},
        knowledge_boundaries={
            "knows_about": ["immediate surroundings"],
            "does_not_know_about": ["unbacked claims"],
            "must_not_claim": [
                "quest rewards",
                "hidden locations",
                "facts not backed by deterministic state",
            ],
        },
    )


