"""Phase 12.6 — Character Card Compatibility Layer.

Provides import/export functions for external character cards
into/from canonical RPG-compatible seed data.

Imported cards are treated as seed/presentation hints, never as
authoritative simulation state.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _normalize_tags(*tag_lists: Any, limit: int = 12) -> List[str]:
    tags: List[str] = []
    seen = set()
    for tag_list in tag_lists:
        for raw in _safe_list(tag_list):
            tag = _safe_str(raw).strip()
            if not tag:
                continue
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            tags.append(tag)
    return sorted(tags, key=lambda v: v.lower())[:limit]


def import_external_character_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """Map an external character card into canonical RPG-compatible seed data."""
    card = _safe_dict(card)

    data = _safe_dict(card.get("data"))
    content = card if data else card

    name = _first_non_empty(
        content.get("name"),
        data.get("name"),
        "Unknown Character",
    )
    description = _first_non_empty(
        content.get("description"),
        data.get("description"),
        content.get("personality"),
        data.get("personality"),
    )
    personality = _first_non_empty(
        content.get("personality"),
        data.get("personality"),
        content.get("scenario"),
        data.get("scenario"),
    )
    scenario = _first_non_empty(
        content.get("scenario"),
        data.get("scenario"),
    )
    first_mes = _first_non_empty(
        content.get("first_mes"),
        data.get("first_mes"),
    )
    mes_example = _first_non_empty(
        content.get("mes_example"),
        data.get("mes_example"),
    )

    tags = _normalize_tags(
        content.get("tags"),
        data.get("tags"),
    )

    creator_notes = _first_non_empty(
        data.get("creator_notes"),
        content.get("creator_notes"),
    )

    system_prompt = _first_non_empty(
        data.get("system_prompt"),
        content.get("system_prompt"),
    )

    post_history_instructions = _first_non_empty(
        data.get("post_history_instructions"),
        content.get("post_history_instructions"),
    )

    alternate_greetings = [
        _safe_str(v).strip()
        for v in _safe_list(data.get("alternate_greetings"))
        if _safe_str(v).strip()
    ][:6]

    return {
        "canonical_seed": {
            "name": name,
            "description": description,
            "role": _first_non_empty(data.get("role"), "character"),
            "traits": tags[:8],
            "greeting": first_mes,
        },
        "personality_seed": {
            "summary": personality,
            "style_tags": tags[:8],
            "archetype": _first_non_empty(data.get("archetype"), ""),
            "tone": _first_non_empty(data.get("tone"), ""),
        },
        "appearance_seed": {
            "base_description": description,
            "current_summary": description,
            "features": {},
            "last_reason": "initial",
            "version": 1,
        },
        "visual_seed": {
            "style": _first_non_empty(data.get("portrait_style"), "rpg-portrait"),
            "model": _first_non_empty(data.get("portrait_model"), "default"),
            "base_prompt": _first_non_empty(description, personality, name),
        },
        "scenario_seed": {
            "scenario": scenario,
            "example_dialogue": mes_example,
            "system_prompt": system_prompt,
            "post_history_instructions": post_history_instructions,
            "alternate_greetings": alternate_greetings,
            "creator_notes": creator_notes,
        },
        "source_meta": {
            "format": _first_non_empty(card.get("spec"), "generic"),
            "spec_version": _safe_str(card.get("spec_version")).strip(),
        },
    }


def export_canonical_character_card(character: Dict[str, Any]) -> Dict[str, Any]:
    """Export canonical character UI object into a portable external-style card."""
    character = _safe_dict(character)
    personality = _safe_dict(character.get("personality"))
    visual_identity = _safe_dict(character.get("visual_identity"))
    appearance = _safe_dict(character.get("appearance"))
    appearance_profile = _safe_dict(appearance.get("profile"))
    card_meta = _safe_dict(character.get("card"))

    tags = _normalize_tags(
        character.get("traits"),
        personality.get("style_tags"),
    )

    description = _first_non_empty(
        character.get("description"),
        appearance_profile.get("current_summary"),
        card_meta.get("summary"),
    )

    return {
        "spec": "rpg-canonical-card",
        "spec_version": "1.0",
        "name": _first_non_empty(character.get("name"), "Unknown Character"),
        "description": description,
        "personality": _first_non_empty(
            personality.get("summary"),
            card_meta.get("summary"),
        ),
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
        "data": {
            "role": _first_non_empty(character.get("role"), "character"),
            "tags": tags,
            "archetype": _safe_str(personality.get("archetype")).strip(),
            "tone": _safe_str(personality.get("tone")).strip(),
            "portrait_style": _safe_str(visual_identity.get("style")).strip(),
            "portrait_model": _safe_str(visual_identity.get("model")).strip(),
            "base_prompt": _safe_str(visual_identity.get("base_prompt")).strip(),
            "appearance_summary": _safe_str(appearance_profile.get("current_summary")).strip(),
            "badge": _safe_str(card_meta.get("badge")).strip(),
        },
    }