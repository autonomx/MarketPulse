from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.foundation.dynamic_npc_profiles import (
    load_npc_profile,
    save_npc_profile,
)
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.profiles_profile_portraits.prompt', "1", 'Medieval fantasy RPG character portrait of {v0}. {v1}. Bust portrait, expressive face, grounded realistic fantasy style, detailed clothing appropriate to their role, neutral background, no text, no watermark.')


def _join_traits(traits: List[Any]) -> str:
    clean = [_safe_str(item).strip() for item in traits if _safe_str(item).strip()]
    return ", ".join(clean[:6])


def build_npc_profile_portrait_prompt(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = _safe_dict(profile)
    npc_id = _safe_str(profile.get("npc_id"))
    name = _safe_str(profile.get("name")) or npc_id.replace("npc:", "") or "Unknown NPC"

    biography = _safe_dict(profile.get("biography"))
    personality = _safe_dict(profile.get("personality"))
    evolution = _safe_dict(profile.get("evolution"))

    short_summary = _safe_str(biography.get("short_summary"))
    current_role = _safe_str(evolution.get("current_role"))
    identity_arc = _safe_str(evolution.get("identity_arc"))
    traits = _join_traits(_safe_list(personality.get("traits")))
    temperament = _safe_str(personality.get("temperament"))
    speech_style = _safe_str(personality.get("speech_style"))

    descriptors = []
    if current_role:
        descriptors.append(current_role)
    if short_summary:
        descriptors.append(short_summary)
    if traits:
        descriptors.append(f"personality traits: {traits}")
    if temperament:
        descriptors.append(f"temperament: {temperament}")
    if identity_arc:
        descriptors.append(f"story arc: {identity_arc}")
    if speech_style:
        descriptors.append(f"speech style impression: {speech_style}")

    context = "; ".join(descriptors)

    prompt = (
        _PROMPT_1.format(v0=(name), v1=(context))
    )

    return {
        "npc_id": npc_id,
        "name": name,
        "prompt": prompt[:1800],
        "source": "deterministic_profile_portrait_prompt",
    }


def save_profile_portrait_prompt(
    npc_id: str,
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    profile = load_npc_profile(npc_id)
    if not profile:
        return {
            "saved": False,
            "reason": "profile_not_found",
            "npc_id": _safe_str(npc_id),
            "source": "deterministic_profile_portrait_prompt",
        }

    prompt_result = build_npc_profile_portrait_prompt(profile)

    profile = deepcopy(profile)
    portrait = _safe_dict(profile.get("portrait"))
    portrait.update({
        "prompt": _safe_str(prompt_result.get("prompt")),
        "image_url": _safe_str(portrait.get("image_url")),
        "generated_tick": int(tick or 0),
        "source": "deterministic_profile_portrait_prompt",
    })
    profile["portrait"] = portrait

    saved = save_npc_profile(profile)
    return {
        "saved": True,
        "npc_id": _safe_str(npc_id),
        "portrait": deepcopy(portrait),
        "prompt_result": deepcopy(prompt_result),
        "profile": deepcopy(saved.get("profile")),
        "path": saved.get("path"),
        "source": "deterministic_profile_portrait_prompt",
    }
