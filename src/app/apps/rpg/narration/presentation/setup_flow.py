"""Product Layer A1 — Structured adventure setup flow.

Read-only builder for player-facing setup scaffolding.
This module does not mutate simulation truth.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict

VALID_GENRES = {
    "fantasy",
    "cyberpunk",
    "horror",
    "science_fiction",
    "post_apocalypse",
    "mystery",
    "western",
}

VALID_TONES = {
    "grim",
    "heroic",
    "comedic",
    "mysterious",
    "tragic",
    "hopeful",
}

VALID_RULE_KEYS = {
    "magic_level",
    "tech_level",
    "lawfulness",
    "violence_level",
    "social_density",
}


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp(v: int, lo: int = 0, hi: int = 10) -> int:
    return max(lo, min(hi, v))


def _normalize_genre(value: Any) -> str:
    value = _safe_str(value, "fantasy").strip().lower()
    return value if value in VALID_GENRES else "fantasy"


def _normalize_tone(value: Any) -> str:
    value = _safe_str(value, "heroic").strip().lower()
    return value if value in VALID_TONES else "heroic"


def _normalize_rules(rules: Dict[str, Any]) -> Dict[str, int]:
    rules = _safe_dict(rules)
    out: Dict[str, int] = {}
    for key in sorted(VALID_RULE_KEYS):
        out[key] = _clamp(_safe_int(rules.get(key), 5))
    return out


def _build_genre_defaults(genre: str) -> Dict[str, Any]:
    defaults = {
        "fantasy": {
            "world_seed": {
                "genre": "fantasy",
                "default_region": "frontier_kingdom",
                "default_conflict": "border unrest",
            },
            "rules": {
                "magic_level": 7,
                "tech_level": 2,
                "lawfulness": 4,
                "violence_level": 6,
                "social_density": 5,
            },
        },
        "cyberpunk": {
            "world_seed": {
                "genre": "cyberpunk",
                "default_region": "neon_district",
                "default_conflict": "corporate pressure",
            },
            "rules": {
                "magic_level": 0,
                "tech_level": 9,
                "lawfulness": 3,
                "violence_level": 7,
                "social_density": 8,
            },
        },
        "horror": {
            "world_seed": {
                "genre": "horror",
                "default_region": "isolated_settlement",
                "default_conflict": "unknown menace",
            },
            "rules": {
                "magic_level": 3,
                "tech_level": 3,
                "lawfulness": 4,
                "violence_level": 5,
                "social_density": 2,
            },
        },
        "science_fiction": {
            "world_seed": {
                "genre": "science_fiction",
                "default_region": "orbital_hub",
                "default_conflict": "resource tension",
            },
            "rules": {
                "magic_level": 0,
                "tech_level": 10,
                "lawfulness": 6,
                "violence_level": 5,
                "social_density": 6,
            },
        },
        "post_apocalypse": {
            "world_seed": {
                "genre": "post_apocalypse",
                "default_region": "ruined_corridor",
                "default_conflict": "scarcity",
            },
            "rules": {
                "magic_level": 1,
                "tech_level": 4,
                "lawfulness": 2,
                "violence_level": 8,
                "social_density": 3,
            },
        },
        "mystery": {
            "world_seed": {
                "genre": "mystery",
                "default_region": "old_quarter",
                "default_conflict": "hidden conspiracy",
            },
            "rules": {
                "magic_level": 1,
                "tech_level": 4,
                "lawfulness": 7,
                "violence_level": 3,
                "social_density": 6,
            },
        },
        "western": {
            "world_seed": {
                "genre": "western",
                "default_region": "dust_basin",
                "default_conflict": "rail expansion dispute",
            },
            "rules": {
                "magic_level": 0,
                "tech_level": 3,
                "lawfulness": 4,
                "violence_level": 6,
                "social_density": 3,
            },
        },
    }
    return _safe_dict(defaults.get(genre, defaults["fantasy"]))


def _build_tone_tags(tone: str) -> List[str]:
    mapping = {
        "grim": ["tone:grim", "stakes:high", "humor:low"],
        "heroic": ["tone:heroic", "stakes:rising", "hope:present"],
        "comedic": ["tone:comedic", "absurdity:allowed", "humor:high"],
        "mysterious": ["tone:mysterious", "secrets:present", "clarity:low"],
        "tragic": ["tone:tragic", "loss:possible", "hope:fragile"],
        "hopeful": ["tone:hopeful", "recovery:possible", "humor:light"],
    }
    return list(mapping.get(tone, mapping["heroic"]))


def build_setup_flow_payload(user_input: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build deterministic setup-flow payload for player-facing world creation."""
    user_input = _safe_dict(user_input)
    genre = _normalize_genre(user_input.get("genre"))
    tone = _normalize_tone(user_input.get("tone"))
    defaults = _build_genre_defaults(genre)

    rules = _normalize_rules({
        **_safe_dict(defaults.get("rules")),
        **_safe_dict(user_input.get("rules")),
    })

    player_role = _safe_str(user_input.get("player_role"), "wanderer").strip() or "wanderer"
    seed_prompt = _safe_str(user_input.get("seed_prompt")).strip()
    world_seed = _safe_dict(defaults.get("world_seed"))
    world_seed["tone"] = tone

    return {
        "setup_flow": {
            "selected": {
                "genre": genre,
                "tone": tone,
                "player_role": player_role,
                "seed_prompt": seed_prompt,
            },
            "world_seed": world_seed,
            "rules": rules,
            "tone_tags": _build_tone_tags(tone),
            "wizard_steps": [
                {"step_id": "genre", "label": "Genre", "required": True},
                {"step_id": "tone", "label": "Tone", "required": True},
                {"step_id": "rules", "label": "Rules", "required": False},
                {"step_id": "player_role", "label": "Player Role", "required": True},
                {"step_id": "seed_prompt", "label": "Seed Prompt", "required": False},
            ],
            "options": {
                "genres": sorted(list(VALID_GENRES)),
                "tones": sorted(list(VALID_TONES)),
                "rule_keys": sorted(list(VALID_RULE_KEYS)),
            },
        }
    }