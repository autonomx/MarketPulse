from __future__ import annotations

import uuid

from .schema import (
    ContentBalance,
    PacingProfile,
    SafetyConstraint,
    normalize_world_behavior_config,
)
from .default_content import (
    _generate_default_factions,
    _generate_default_locations,
    _generate_default_npcs,
)


def default_pacing_profile() -> PacingProfile:
    """Return the default pacing profile for new adventures."""
    return PacingProfile(
        style="balanced",
        danger_level="medium",
        mystery_weight=0.25,
        combat_weight=0.25,
        politics_weight=0.15,
        social_weight=0.35,
    )


def default_safety_constraint() -> SafetyConstraint:
    """Return the default safety constraint for new adventures."""
    return SafetyConstraint(
        forbidden_themes=[],
        soft_avoid_themes=[],
    )


def default_content_balance() -> ContentBalance:
    """Return the default content balance for new adventures."""
    return ContentBalance(
        mystery=0.2,
        combat=0.2,
        politics=0.2,
        exploration=0.2,
        social=0.2,
    )


def apply_adventure_defaults(setup_data: dict) -> dict:
    """Apply default values to a raw adventure setup dict.

    Missing or ``None`` fields receive sensible defaults so that
    downstream consumers always see a complete payload.

    If factions, locations, or NPCs are not provided, they are
    auto-generated based on the genre and setting.
    """
    result = dict(setup_data)

    # Required fields
    if not result.get("setup_id"):
        result["setup_id"] = f"preview_{uuid.uuid4().hex[:12]}"
    if not result.get("title"):
        result["title"] = "Untitled Adventure"
    if not result.get("genre"):
        result["genre"] = "fantasy"
    if not result.get("setting"):
        result["setting"] = "A mysterious world"
    if not result.get("premise"):
        result["premise"] = "An adventure awaits"

    if not result.get("hard_rules"):
        result["hard_rules"] = []
    if not result.get("soft_tone_rules"):
        result["soft_tone_rules"] = []
    if not result.get("lore_constraints"):
        result["lore_constraints"] = []
    if not result.get("themes"):
        result["themes"] = []
    if not result.get("forbidden_content"):
        result["forbidden_content"] = []
    if not result.get("canon_notes"):
        result["canon_notes"] = []
    if not result.get("metadata"):
        result["metadata"] = {}
    if not result.get("starting_npc_ids"):
        result["starting_npc_ids"] = []

    if result.get("pacing") is None:
        result["pacing"] = default_pacing_profile().to_dict()
    if result.get("safety") is None:
        result["safety"] = default_safety_constraint().to_dict()
    if result.get("content_balance") is None:
        result["content_balance"] = default_content_balance().to_dict()

    # Auto-generate locationss, factions, and NPCs if not provided
    genre = result.get("genre", "")
    setting = result.get("setting", "")

    if not result.get("locations"):
        result["locations"] = _generate_default_locations(genre, setting)

    if not result.get("factions"):
        result["factions"] = _generate_default_factions(genre, setting)

    if not result.get("npc_seeds"):
        result["npc_seeds"] = _generate_default_npcs(genre, setting, result["locations"])

    # Set starting location to first location if not specified
    if not result.get("starting_location_id") and result.get("locations"):
        result["starting_location_id"] = result["locations"][0]["location_id"]

    # Set starting NPCs to first few NPCs if not specified
    if not result.get("starting_npc_ids") and result.get("npc_seeds"):
        result["starting_npc_ids"] = [npc["npc_id"] for npc in result["npc_seeds"][:3]]

    # Phase A — merge creator input defaults
    result = merge_creator_input_with_defaults(result)

    return result


# ---------------------------------------------------------------------------
# Phase A — Creator input defaults & opening inference
# ---------------------------------------------------------------------------

_GENRE_ROLES: dict[str, str] = {
    "fantasy": "Wandering adventurer seeking fortune and glory",
    "cyberpunk": "Street-level operator navigating corporate shadows",
    "mystery": "Investigator drawn into a web of secrets",
    "noir": "Hard-boiled detective with a complicated past",
    "political": "Ambitious newcomer navigating deadly court politics",
    "intrigue": "Ambitious newcomer navigating deadly court politics",
    "grimdark": "Desperate survivor clinging to life in a broken world",
    "survival": "Desperate survivor clinging to life in a broken world",
    "horror": "Ordinary person confronting unspeakable terror",
}

_GENRE_OBJECTIVES: dict[str, str] = {
    "fantasy": "Uncover the source of the growing darkness and restore balance",
    "cyberpunk": "Pull off the job and survive the fallout",
    "mystery": "Solve the central case and expose the truth",
    "noir": "Solve the central case and expose the truth",
    "political": "Secure lasting influence without being destroyed by rivals",
    "intrigue": "Secure lasting influence without being destroyed by rivals",
    "grimdark": "Find safety for your people in a world that offers none",
    "survival": "Find safety for your people in a world that offers none",
    "horror": "Survive the night and uncover what is really happening",
}

_GENRE_HOOKS: dict[str, str] = {
    "fantasy": "A mysterious stranger arrives bearing an urgent warning",
    "cyberpunk": "A dead drop contains a job offer too good to be true",
    "mystery": "A body is found under impossible circumstances",
    "noir": "A desperate client walks into your office with a simple request",
    "political": "An unexpected invitation to the inner court arrives",
    "intrigue": "An unexpected invitation to the inner court arrives",
    "grimdark": "Scouts report movement on the horizon — something is coming",
    "survival": "Scouts report movement on the horizon — something is coming",
    "horror": "Strange noises are heard from a place that should be empty",
}

_GENRE_CONFLICTS: dict[str, str] = {
    "fantasy": "A local village is threatened and no one else will help",
    "cyberpunk": "Your crew owes a dangerous debt that comes due tonight",
    "mystery": "Key evidence is about to be destroyed unless you act now",
    "noir": "Key evidence is about to be destroyed unless you act now",
    "political": "Two powerful factions demand your allegiance simultaneously",
    "intrigue": "Two powerful factions demand your allegiance simultaneously",
    "grimdark": "Supplies are running out and a rival group blocks the only route",
    "survival": "Supplies are running out and a rival group blocks the only route",
    "horror": "The only exit is sealed and something is inside with you",
}

_GENRE_RULES: dict[str, list[str]] = {
    "fantasy": [
        "Magic always has a cost",
        "Ancient prophecies may be misleading",
        "The wilds are dangerous after dark",
    ],
    "cyberpunk": [
        "Cyberware degrades over time without maintenance",
        "Corporate zones are surveilled at all times",
        "Net-running carries risk of brain damage",
    ],
    "mystery": [
        "Clues must be discoverable through investigation",
        "NPCs have alibis that can be verified or broken",
        "The truth is never simple",
    ],
    "noir": [
        "Clues must be discoverable through investigation",
        "Everyone has something to hide",
        "Violence has consequences",
    ],
    "political": [
        "Reputation is currency",
        "Alliances are temporary",
        "Public actions have political repercussions",
    ],
    "intrigue": [
        "Reputation is currency",
        "Alliances are temporary",
        "Public actions have political repercussions",
    ],
    "grimdark": [
        "Resources are finite and must be tracked",
        "Death is permanent",
        "Trust must be earned through action",
    ],
    "survival": [
        "Resources are finite and must be tracked",
        "Death is permanent",
        "Trust must be earned through action",
    ],
    "horror": [
        "The unknown is more frightening than the revealed",
        "Isolation increases danger",
        "Light sources are limited",
    ],
}

_GENRE_CONTENT_MIX: dict[str, dict[str, float]] = {
    "fantasy": {"combat": 0.25, "exploration": 0.25, "social": 0.2, "mystery": 0.15, "politics": 0.15},
    "cyberpunk": {"combat": 0.2, "exploration": 0.15, "social": 0.2, "mystery": 0.2, "politics": 0.25},
    "mystery": {"combat": 0.05, "exploration": 0.15, "social": 0.25, "mystery": 0.45, "politics": 0.1},
    "noir": {"combat": 0.1, "exploration": 0.15, "social": 0.25, "mystery": 0.4, "politics": 0.1},
    "political": {"combat": 0.05, "exploration": 0.1, "social": 0.25, "mystery": 0.2, "politics": 0.4},
    "intrigue": {"combat": 0.05, "exploration": 0.1, "social": 0.25, "mystery": 0.2, "politics": 0.4},
    "grimdark": {"combat": 0.35, "exploration": 0.25, "social": 0.15, "mystery": 0.1, "politics": 0.15},
    "survival": {"combat": 0.3, "exploration": 0.3, "social": 0.15, "mystery": 0.1, "politics": 0.15},
    "horror": {"combat": 0.1, "exploration": 0.25, "social": 0.15, "mystery": 0.4, "politics": 0.1},
}

_GENRE_GEAR: dict[str, list[dict]] = {
    "fantasy": [
        {"name": "Torch", "description": "A simple torch for dark places"},
        {"name": "Rope", "description": "50 feet of sturdy hemp rope"},
        {"name": "Rations", "description": "Three days of dried travel rations"},
        {"name": "Waterskin", "description": "A leather waterskin, full"},
    ],
    "cyberpunk": [
        {"name": "Credstick", "description": "Loaded with 500 credits"},
        {"name": "Sidearm", "description": "A compact 9mm pistol"},
        {"name": "Cyberdeck", "description": "A basic hacking terminal"},
        {"name": "Fake ID", "description": "Corporate-grade forged identity"},
    ],
    "mystery": [
        {"name": "Notebook", "description": "A well-worn investigator's notebook"},
        {"name": "Magnifying glass", "description": "For examining fine details"},
        {"name": "Camera", "description": "A compact camera for evidence"},
    ],
    "noir": [
        {"name": "Revolver", "description": "A snub-nosed .38 special"},
        {"name": "Flask", "description": "Filled with cheap bourbon"},
        {"name": "Notebook", "description": "A battered detective's notebook"},
    ],
    "political": [
        {"name": "Signet ring", "description": "Proof of minor noble standing"},
        {"name": "Sealed letter", "description": "A letter of introduction"},
        {"name": "Coin purse", "description": "Enough gold for bribes"},
    ],
    "intrigue": [
        {"name": "Signet ring", "description": "Proof of minor noble standing"},
        {"name": "Sealed letter", "description": "A letter of introduction"},
        {"name": "Coin purse", "description": "Enough gold for bribes"},
    ],
    "grimdark": [
        {"name": "Rusted blade", "description": "A notched short sword"},
        {"name": "Bandages", "description": "Dirty but functional cloth wraps"},
        {"name": "Tinderbox", "description": "Flint and steel, still works"},
    ],
    "survival": [
        {"name": "Medkit", "description": "A half-empty first aid kit"},
        {"name": "Flashlight", "description": "Batteries at 40%"},
        {"name": "Knife", "description": "A sturdy survival knife"},
    ],
    "horror": [
        {"name": "Flashlight", "description": "Batteries are fresh — for now"},
        {"name": "Medkit", "description": "A basic first aid kit"},
        {"name": "Lighter", "description": "A disposable butane lighter"},
    ],
}

_GENRE_RESOURCES: dict[str, dict[str, int]] = {
    "fantasy": {"gold": 50, "health_potions": 2},
    "cyberpunk": {"credits": 500, "stim_packs": 2},
    "mystery": {"cash": 200, "favors": 1},
    "noir": {"cash": 100, "cigarettes": 10},
    "political": {"gold": 200, "influence_tokens": 3},
    "intrigue": {"gold": 200, "influence_tokens": 3},
    "grimdark": {"rations": 3, "bandages": 2, "water": 3},
    "survival": {"rations": 3, "ammo": 12, "water": 3},
    "horror": {"batteries": 4, "bandages": 3},
}

# ── Phase F — Genre-sensitive world behavior defaults ──────────────────────

_GENRE_WORLD_BEHAVIOR: dict[str, dict[str, str]] = {
    "mystery": {
        "ambient_activity": "low",
        "npc_initiative": "medium",
        "interruptions": "minimal",
        "quest_prompting": "strong",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "strong",
        "play_style_bias": "story_directed",
    },
    "noir": {
        "ambient_activity": "low",
        "npc_initiative": "medium",
        "interruptions": "minimal",
        "quest_prompting": "strong",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "strong",
        "play_style_bias": "story_directed",
    },
    "fantasy": {
        "ambient_activity": "medium",
        "npc_initiative": "medium",
        "interruptions": "normal",
        "quest_prompting": "guided",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "normal",
        "play_style_bias": "balanced",
    },
    "grimdark": {
        "ambient_activity": "high",
        "npc_initiative": "low",
        "interruptions": "frequent",
        "quest_prompting": "light",
        "companion_chatter": "quiet",
        "world_pressure": "harsh",
        "opening_guidance": "normal",
        "play_style_bias": "balanced",
    },
    "survival": {
        "ambient_activity": "high",
        "npc_initiative": "low",
        "interruptions": "frequent",
        "quest_prompting": "light",
        "companion_chatter": "quiet",
        "world_pressure": "harsh",
        "opening_guidance": "normal",
        "play_style_bias": "balanced",
    },
    "political": {
        "ambient_activity": "medium",
        "npc_initiative": "high",
        "interruptions": "normal",
        "quest_prompting": "guided",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "normal",
        "play_style_bias": "story_directed",
    },
    "intrigue": {
        "ambient_activity": "medium",
        "npc_initiative": "high",
        "interruptions": "normal",
        "quest_prompting": "guided",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "normal",
        "play_style_bias": "story_directed",
    },
    "cyberpunk": {
        "ambient_activity": "high",
        "npc_initiative": "medium",
        "interruptions": "normal",
        "quest_prompting": "guided",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "normal",
        "play_style_bias": "balanced",
    },
    "horror": {
        "ambient_activity": "low",
        "npc_initiative": "low",
        "interruptions": "minimal",
        "quest_prompting": "light",
        "companion_chatter": "quiet",
        "world_pressure": "harsh",
        "opening_guidance": "strong",
        "play_style_bias": "story_directed",
    },
}


def infer_default_world_behavior(setup: dict) -> dict[str, str]:
    """Infer genre-sensitive world behavior defaults."""
    key = _match_genre(setup.get("genre", ""))
    return dict(_GENRE_WORLD_BEHAVIOR.get(key, {
        "ambient_activity": "medium",
        "npc_initiative": "medium",
        "interruptions": "normal",
        "quest_prompting": "guided",
        "companion_chatter": "normal",
        "world_pressure": "standard",
        "opening_guidance": "normal",
        "play_style_bias": "balanced",
    }))


def _match_genre(genre: str) -> str:
    """Find the best matching genre key from the lookup tables."""
    g = (genre or "").lower()
    for key in _GENRE_ROLES:
        if key in g:
            return key
    return ""


def infer_default_player_role(setup: dict) -> str:
    """Infer a default player role from genre/setting/premise."""
    key = _match_genre(setup.get("genre", ""))
    return _GENRE_ROLES.get(key, "A capable individual drawn into extraordinary events")


def infer_default_campaign_objective(setup: dict) -> str:
    """Infer a default campaign objective from genre/premise."""
    key = _match_genre(setup.get("genre", ""))
    return _GENRE_OBJECTIVES.get(key, "Uncover the truth and shape the outcome")


def infer_default_opening_hook(setup: dict) -> str:
    """Infer a default opening hook from genre/mood/premise."""
    key = _match_genre(setup.get("genre", ""))
    return _GENRE_HOOKS.get(key, "Something unexpected disrupts an ordinary day")


def infer_default_starter_conflict(setup: dict) -> str:
    """Infer a default starter conflict from genre/difficulty/mood."""
    key = _match_genre(setup.get("genre", ""))
    return _GENRE_CONFLICTS.get(key, "A pressing problem demands immediate attention")


def infer_default_genre_rules(setup: dict) -> list[str]:
    """Infer default genre rules from genre/setting."""
    key = _match_genre(setup.get("genre", ""))
    return list(_GENRE_RULES.get(key, ["Actions have consequences", "The world reacts to player choices"]))


def infer_default_content_mix(setup: dict) -> dict[str, float]:
    """Infer a default content mix from genre/mood/difficulty."""
    key = _match_genre(setup.get("genre", ""))
    return dict(_GENRE_CONTENT_MIX.get(key, {
        "combat": 0.2, "exploration": 0.2, "social": 0.2, "mystery": 0.2, "politics": 0.2,
    }))


def infer_default_starting_gear(setup: dict) -> list[dict]:
    """Infer default starting gear from genre/mood/difficulty."""
    key = _match_genre(setup.get("genre", ""))
    return list(_GENRE_GEAR.get(key, [
        {"name": "Basic supplies", "description": "A small pack of essentials"},
    ]))


def infer_default_starting_resources(setup: dict) -> dict[str, int]:
    """Infer default starting resources from genre/difficulty."""
    key = _match_genre(setup.get("genre", ""))
    return dict(_GENRE_RESOURCES.get(key, {"supplies": 10}))


# ---------------------------------------------------------------------------
# Phase B1 — Opening inference helpers
# ---------------------------------------------------------------------------


def infer_opening_location(setup: dict) -> str:
    """Infer the opening location id from setup context."""
    loc_id = setup.get("starting_location_id")
    if loc_id:
        return loc_id
    locations = setup.get("locations") or []
    if locations:
        return locations[0].get("location_id", "") if isinstance(locations[0], dict) else ""
    return ""


def infer_opening_scene_frame(setup: dict) -> str:
    """Infer a brief scene-setting sentence for the opening."""
    genre = (setup.get("genre") or "").lower()
    hook = setup.get("opening_hook", "")

    if hook:
        return f"The story opens as: {hook}"

    frames = {
        "fantasy": "The air smells of woodsmoke and old magic as you arrive at your destination.",
        "cyberpunk": "Neon reflections shimmer on rain-slicked streets as you step out of the shadows.",
        "mystery": "A heavy silence hangs over the scene — something is not right.",
        "noir": "Rain drums on the window. The phone rings.",
        "political": "The great hall buzzes with whispered alliances and veiled threats.",
        "intrigue": "The great hall buzzes with whispered alliances and veiled threats.",
        "grimdark": "Ash drifts on a cold wind. The camp is restless.",
        "survival": "Ash drifts on a cold wind. The camp is restless.",
        "horror": "The lights flicker once, then go out entirely.",
    }
    for key, frame in frames.items():
        if key in genre:
            return frame
    return "The adventure begins in an unremarkable place on an unremarkable day — or so it seems."


def infer_opening_problem(setup: dict) -> str:
    """Infer the immediate problem the player faces at the start."""
    conflict = setup.get("starter_conflict", "")
    if conflict:
        return conflict
    return infer_default_starter_conflict(setup)


def infer_player_involvement_reason(setup: dict) -> str:
    """Infer why the player is involved in the opening situation."""
    role = setup.get("player_role", "")
    background = setup.get("player_background", "")
    genre = (setup.get("genre") or "").lower()

    if background:
        return f"Your background as {background} has brought you here."
    if role:
        return f"As a {role}, this situation demands your attention."

    reasons = {
        "fantasy": "Fate — or something like it — has placed you at the center of events.",
        "cyberpunk": "A contact called in a favor you can't refuse.",
        "mystery": "Someone asked for your help, and you couldn't say no.",
        "noir": "A client showed up with a case that smelled like trouble.",
        "political": "An invitation arrived that could not be safely ignored.",
        "grimdark": "Survival leaves no room for bystanders.",
        "survival": "Survival leaves no room for bystanders.",
        "horror": "You were in the wrong place at the wrong time.",
    }
    for key, reason in reasons.items():
        if key in genre:
            return reason
    return "Circumstances have drawn you into the heart of the matter."


def infer_opening_present_npcs(setup: dict) -> list[str]:
    """Infer which NPCs are present in the opening scene."""
    npc_ids = setup.get("starting_npc_ids") or []
    if npc_ids:
        return list(npc_ids[:5])
    npcs = setup.get("npc_seeds") or []
    return [n.get("npc_id", "") for n in npcs[:3] if isinstance(n, dict) and n.get("npc_id")]


def infer_first_choices(setup: dict) -> list[str]:
    """Infer the initial set of choices available to the player."""
    genre = (setup.get("genre") or "").lower()

    choices_map = {
        "fantasy": [
            "Investigate the rumor at the tavern",
            "Seek out the local authority for information",
            "Head into the wilds to see for yourself",
        ],
        "cyberpunk": [
            "Accept the job offer",
            "Dig into the client's background first",
            "Consult your fixer for leverage",
        ],
        "mystery": [
            "Examine the evidence at the scene",
            "Interview the first witness",
            "Research the victim's background",
        ],
        "noir": [
            "Take the case",
            "Ask around the neighborhood",
            "Follow the money trail",
        ],
        "political": [
            "Accept the invitation and attend",
            "Send a proxy to gauge the situation",
            "Seek counsel from a trusted advisor",
        ],
        "intrigue": [
            "Accept the invitation and attend",
            "Send a proxy to gauge the situation",
            "Seek counsel from a trusted advisor",
        ],
        "grimdark": [
            "Fortify the camp and prepare to defend",
            "Send scouts to assess the threat",
            "Negotiate with the approaching group",
        ],
        "survival": [
            "Fortify the camp and prepare to defend",
            "Send scouts to assess the threat",
            "Search for an alternate supply route",
        ],
        "horror": [
            "Search for another way out",
            "Barricade the entrance",
            "Investigate the source of the noise",
        ],
    }
    for key, ch in choices_map.items():
        if key in genre:
            return list(ch)
    return [
        "Investigate further",
        "Seek allies",
        "Prepare for what comes next",
    ]


def infer_default_opening(setup: dict) -> dict:
    """Infer a complete opening context dict from the setup."""
    location_id = infer_opening_location(setup)

    # Determine location name
    location_name = location_id
    for loc in (setup.get("locations") or []):
        if isinstance(loc, dict) and loc.get("location_id") == location_id:
            location_name = loc.get("name", location_id)
            break

    mood = (setup.get("mood") or "").lower()
    tension = "medium"
    if mood in ("dark", "grim", "tense", "edgy"):
        tension = "high"
    elif mood in ("heroic", "light", "whimsical"):
        tension = "low"

    return {
        "location_id": location_id,
        "location_name": location_name,
        "scene_frame": infer_opening_scene_frame(setup),
        "immediate_problem": infer_opening_problem(setup),
        "player_involvement_reason": infer_player_involvement_reason(setup),
        "present_npc_ids": infer_opening_present_npcs(setup),
        "first_choices": infer_first_choices(setup),
        "tension_level": tension,
        "time_of_day": "evening",
        "weather": "clear",
    }


# ---------------------------------------------------------------------------
# Phase A — Merge creator input with defaults
# ---------------------------------------------------------------------------


def merge_creator_input_with_defaults(payload: dict) -> dict:
    """Normalize schema, backfill defaults only when missing, preserve explicit choices."""
    from .schema import normalize_creator_setup

    result = normalize_creator_setup(dict(payload))

    if not result.get("player_role"):
        result["player_role"] = infer_default_player_role(result)
    if not result.get("campaign_objective"):
        result["campaign_objective"] = infer_default_campaign_objective(result)
    if not result.get("opening_hook"):
        result["opening_hook"] = infer_default_opening_hook(result)
    if not result.get("starter_conflict"):
        result["starter_conflict"] = infer_default_starter_conflict(result)
    if not result.get("genre_rules"):
        result["genre_rules"] = infer_default_genre_rules(result)
    if not result.get("desired_content_mix"):
        result["desired_content_mix"] = infer_default_content_mix(result)
    if not result.get("starting_gear"):
        result["starting_gear"] = infer_default_starting_gear(result)
    if not result.get("starting_resources"):
        result["starting_resources"] = infer_default_starting_resources(result)

    inferred_opening = infer_default_opening(result)
    existing_opening = result.get("opening")
    if not isinstance(existing_opening, dict):
        existing_opening = {}
    merged_opening = dict(existing_opening)
    for key, value in inferred_opening.items():
        if value and not merged_opening.get(key):
            merged_opening[key] = value
    result["opening"] = merged_opening

    # Phase F — world behavior defaults
    existing_wb = result.get("world_behavior")
    if not isinstance(existing_wb, dict) or not existing_wb:
        result["world_behavior"] = infer_default_world_behavior(result)
    else:
        result["world_behavior"] = normalize_world_behavior_config(existing_wb)

    return result

_TEMPLATES: dict[str, dict] = {
    "fantasy_adventure": {
        "genre": "fantasy",
        "setting": "A sprawling medieval kingdom on the edge of wild frontier lands",
        "premise": "An ancient evil stirs, and unlikely heroes must answer the call.",
        "hard_rules": [
            "Magic is real but has consequences",
            "The gods are distant and enigmatic",
        ],
        "soft_tone_rules": ["Heroic but grounded"],
        "pacing": PacingProfile(
            style="balanced",
            danger_level="medium",
            mystery_weight=0.2,
            combat_weight=0.3,
            politics_weight=0.1,
            social_weight=0.4,
        ).to_dict(),
        "content_balance": ContentBalance(
            mystery=0.15,
            combat=0.3,
            politics=0.1,
            exploration=0.25,
            social=0.2,
        ).to_dict(),
        "difficulty_style": "moderate",
        "mood": "heroic",
    },
    "political_intrigue": {
        "genre": "political intrigue",
        "setting": "A decadent renaissance court rife with hidden alliances",
        "premise": "Power is the only currency that matters, and everyone is buying.",
        "hard_rules": [
            "Violence has political consequences",
            "Reputation matters more than force",
        ],
        "soft_tone_rules": ["Tense and cerebral"],
        "pacing": PacingProfile(
            style="slow_burn",
            danger_level="low",
            mystery_weight=0.3,
            combat_weight=0.05,
            politics_weight=0.45,
            social_weight=0.2,
        ).to_dict(),
        "content_balance": ContentBalance(
            mystery=0.25,
            combat=0.05,
            politics=0.4,
            exploration=0.1,
            social=0.2,
        ).to_dict(),
        "difficulty_style": "hard",
        "mood": "tense",
    },
    "mystery_noir": {
        "genre": "mystery noir",
        "setting": "A rain-soaked 1940s city where everyone has something to hide",
        "premise": "A seemingly simple case spirals into a web of betrayal and danger.",
        "hard_rules": [
            "Clues must be discoverable through investigation",
            "NPCs lie unless motivated to tell the truth",
        ],
        "soft_tone_rules": ["Dark, atmospheric, morally ambiguous"],
        "pacing": PacingProfile(
            style="slow_burn",
            danger_level="medium",
            mystery_weight=0.5,
            combat_weight=0.1,
            politics_weight=0.1,
            social_weight=0.3,
        ).to_dict(),
        "content_balance": ContentBalance(
            mystery=0.45,
            combat=0.1,
            politics=0.1,
            exploration=0.15,
            social=0.2,
        ).to_dict(),
        "difficulty_style": "hard",
        "mood": "dark",
    },
    "grimdark_survival": {
        "genre": "grimdark",
        "setting": "A war-torn wasteland where resources are scarce and trust is rarer",
        "premise": "Survival is not guaranteed. Every choice has a cost.",
        "hard_rules": [
            "Death is permanent",
            "Resources must be tracked",
        ],
        "soft_tone_rules": ["Bleak and unforgiving"],
        "pacing": PacingProfile(
            style="relentless",
            danger_level="high",
            mystery_weight=0.1,
            combat_weight=0.4,
            politics_weight=0.1,
            social_weight=0.4,
        ).to_dict(),
        "content_balance": ContentBalance(
            mystery=0.05,
            combat=0.4,
            politics=0.1,
            exploration=0.3,
            social=0.15,
        ).to_dict(),
        "difficulty_style": "brutal",
        "mood": "grim",
    },
    "cyberpunk_heist": {
        "genre": "cyberpunk",
        "setting": "A neon-lit megacity controlled by megacorps and haunted by hackers",
        "premise": "One last job could set you free — or get you flatlined.",
        "hard_rules": [
            "Cyberware has side effects",
            "Corporate security is relentless",
        ],
        "soft_tone_rules": ["Stylish, fast-paced, morally grey"],
        "pacing": PacingProfile(
            style="fast",
            danger_level="high",
            mystery_weight=0.2,
            combat_weight=0.3,
            politics_weight=0.15,
            social_weight=0.35,
        ).to_dict(),
        "content_balance": ContentBalance(
            mystery=0.2,
            combat=0.25,
            politics=0.15,
            exploration=0.2,
            social=0.2,
        ).to_dict(),
        "difficulty_style": "hard",
        "mood": "edgy",
    },
}


def build_setup_template(template_name: str) -> dict:
    """Return a setup-data dict pre-populated from the named template.

    The returned dict is ready for ``apply_adventure_defaults`` and then
    ``AdventureSetup.from_dict`` after adding required ids/titles.

    Raises ``ValueError`` if the template name is unknown.
    """
    if template_name not in _TEMPLATES:
        raise ValueError(
            f"Unknown template '{template_name}'. "
            f"Available: {sorted(_TEMPLATES)}"
        )
    return dict(_TEMPLATES[template_name])


_TEMPLATE_META: dict[str, dict] = {
    "fantasy_adventure": {
        "label": "Fantasy Adventure",
        "description": "Classic high-fantasy with heroic quests, magical creatures, and ancient prophecies.",
        "recommended_for": "First-time players, heroic storytelling",
    },
    "political_intrigue": {
        "label": "Political Intrigue",
        "description": "Courtly machinations, hidden alliances, and power plays in a renaissance setting.",
        "recommended_for": "Players who enjoy dialogue, scheming, and consequences",
    },
    "mystery_noir": {
        "label": "Mystery Noir",
        "description": "A rain-soaked detective story where nothing is what it seems.",
        "recommended_for": "Players who enjoy investigation and moral ambiguity",
    },
    "grimdark_survival": {
        "label": "Grimdark Survival",
        "description": "A harsh, unforgiving world where every resource counts and death is permanent.",
        "recommended_for": "Experienced players seeking challenge and tension",
    },
    "cyberpunk_heist": {
        "label": "Cyberpunk Heist",
        "description": "Neon-lit megacities, corporate espionage, and one last job to pull off.",
        "recommended_for": "Players who enjoy fast-paced action and stylish storytelling",
    },
}


def list_setup_templates() -> list[dict]:
    """Return a list of available template descriptors for UI display."""
    result = []
    for name, tpl in _TEMPLATES.items():
        meta = _TEMPLATE_META.get(name, {})
        result.append({
            "name": name,
            "genre": tpl.get("genre", ""),
            "mood": tpl.get("mood", ""),
            "difficulty_style": tpl.get("difficulty_style", ""),
            "label": meta.get("label", name.replace("_", " ").title()),
            "description": meta.get("description", ""),
            "recommended_for": meta.get("recommended_for", ""),
            "setting": tpl.get("setting", ""),
            "premise": tpl.get("premise", ""),
        })
    return result
