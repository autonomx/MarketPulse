from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.profiles_llm_profile_drafter.build_profile_draft_prompt', "1", 'You are drafting editable NPC character-card text for a deterministic RPG engine.\nDo not invent world-changing secrets, quest outcomes, hidden knowledge, or facts that imply new simulation truth.\nYou may add flavor, personality texture, speech style, and plausible background consistent with the structured scaffold.\nReturn STRICT JSON only with keys: biography, history, personality.\n\nScaffold:\n{v0}\n\nRequired JSON shape:\n{{\n  "biography": {{\n    "short_summary": "...",\n    "full_biography": "...",\n    "public_reputation": "...",\n    "private_notes": "..."\n  }},\n  "history": {{\n    "background": "...",\n    "major_life_events": [],\n    "recent_events": []\n  }},\n  "personality": {{\n    "traits": [],\n    "temperament": "...",\n    "speech_style": "...",\n    "risk_tolerance": "...",\n    "conflict_style": "..."\n  }}\n}}\n')


def build_profile_draft_prompt(profile: Dict[str, Any]) -> str:
    profile = _safe_dict(profile)
    return (
        _PROMPT_1.format(v0=(json.dumps(profile, ensure_ascii=False, indent=2)))
    )


def validate_profile_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft = _safe_dict(draft)

    biography = _safe_dict(draft.get("biography"))
    history = _safe_dict(draft.get("history"))
    personality = _safe_dict(draft.get("personality"))

    clean: dict[str, Any] = {
        "biography": {
            "short_summary": _safe_str(biography.get("short_summary"))[:500],
            "full_biography": _safe_str(biography.get("full_biography"))[:4000],
            "public_reputation": _safe_str(biography.get("public_reputation"))[:1000],
            "private_notes": _safe_str(biography.get("private_notes"))[:1500],
        },
        "history": {
            "background": _safe_str(history.get("background"))[:3000],
            "major_life_events": raw_major_life_events if isinstance(raw_major_life_events := history.get("major_life_events"), list) else [],
            "recent_events": raw_recent_events if isinstance(raw_recent_events := history.get("recent_events"), list) else [],
        },
        "personality": {
            "traits": raw_traits if isinstance(raw_traits := personality.get("traits"), list) else [],
            "temperament": _safe_str(personality.get("temperament"))[:500],
            "speech_style": _safe_str(personality.get("speech_style"))[:500],
            "risk_tolerance": _safe_str(personality.get("risk_tolerance"))[:100],
            "conflict_style": _safe_str(personality.get("conflict_style"))[:500],
        },
    }

    clean["history"]["major_life_events"] = clean["history"]["major_life_events"][:12]
    clean["history"]["recent_events"] = clean["history"]["recent_events"][:12]
    clean["personality"]["traits"] = [
        _safe_str(item)[:64]
        for item in clean["personality"]["traits"][:12]
        if _safe_str(item)
    ]

    return clean


def merge_profile_draft(profile: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    profile = deepcopy(_safe_dict(profile))
    draft = validate_profile_draft(draft)

    for section in ("biography", "history", "personality"):
        merged = _safe_dict(profile.get(section))
        for key, value in _safe_dict(draft.get(section)).items():
            if value not in ("", [], {}):
                merged[key] = value
        profile[section] = merged

    profile["origin"] = "llm_drafted_from_scaffold"
    return profile


