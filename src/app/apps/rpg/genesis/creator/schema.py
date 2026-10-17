from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LoreConstraint:
    name: str
    description: str
    authority: str = "creator_canon"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LoreConstraint":
        return cls(**data)


@dataclass
class FactionSeed:
    faction_id: str
    name: str
    description: str
    goals: list[str] = field(default_factory=list)
    relationships: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FactionSeed":
        return cls(**data)


@dataclass
class LocationSeed:
    location_id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LocationSeed":
        return cls(**data)


@dataclass
class NPCSeed:
    npc_id: str
    name: str
    role: str
    description: str
    goals: list[str] = field(default_factory=list)
    faction_id: str | None = None
    location_id: str | None = None
    must_survive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NPCSeed":
        return cls(**data)


@dataclass
class ThemeConstraint:
    name: str
    description: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ThemeConstraint":
        return cls(**data)


@dataclass
class PacingProfile:
    style: str = "balanced"
    danger_level: str = "medium"
    mystery_weight: float = 0.25
    combat_weight: float = 0.25
    politics_weight: float = 0.15
    social_weight: float = 0.35

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PacingProfile":
        return cls(**data)


@dataclass
class SafetyConstraint:
    forbidden_themes: list[str] = field(default_factory=list)
    soft_avoid_themes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SafetyConstraint":
        return cls(**data)


@dataclass
class ContentBalance:
    mystery: float = 0.2
    combat: float = 0.2
    politics: float = 0.2
    exploration: float = 0.2
    social: float = 0.2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ContentBalance":
        return cls(**data)


# ── Phase F — World Behavior Customization ────────────────────────────────

# Allowed enum values for each world-behavior axis.
_WORLD_BEHAVIOR_ENUMS: dict[str, tuple[str, ...]] = {
    "ambient_activity": ("low", "medium", "high"),
    "npc_initiative": ("low", "medium", "high"),
    "interruptions": ("minimal", "normal", "frequent"),
    "quest_prompting": ("off", "light", "guided", "strong"),
    "companion_chatter": ("quiet", "normal", "talkative"),
    "world_pressure": ("gentle", "standard", "harsh"),
    "opening_guidance": ("light", "normal", "strong"),
    "play_style_bias": ("sandbox", "balanced", "story_directed"),
}

_WORLD_BEHAVIOR_DEFAULTS: dict[str, str] = {
    "ambient_activity": "medium",
    "npc_initiative": "medium",
    "interruptions": "normal",
    "quest_prompting": "guided",
    "companion_chatter": "normal",
    "world_pressure": "standard",
    "opening_guidance": "normal",
    "play_style_bias": "balanced",
}


_RESPONSE_LENGTH_ENUMS: dict[str, tuple[str, ...]] = {
    "narrator_length": ("short", "medium", "long"),
    "character_length": ("short", "medium", "long"),
}

_RESPONSE_LENGTH_DEFAULTS: dict[str, str] = {
    "narrator_length": "medium",
    "character_length": "medium",
}

# ── Idle / reaction settings constants ────────────────────────────────────
# Shared enum values for backend validation and frontend UI.

_IDLE_CONVERSATION_SECONDS_ALLOWED: tuple[int, ...] = (30, 60, 300, 600)
_REACTION_STYLE_ENUMS: tuple[str, ...] = ("minimal", "normal", "lively")


def normalize_response_length_config(value: dict | None) -> dict:
    if not isinstance(value, dict):
        return dict(_RESPONSE_LENGTH_DEFAULTS)
    result: dict[str, str] = {}
    for key, allowed in _RESPONSE_LENGTH_ENUMS.items():
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip().lower() in allowed:
            result[key] = raw.strip().lower()
        else:
            result[key] = _RESPONSE_LENGTH_DEFAULTS[key]
    return result


def normalize_world_behavior_config(value: dict | None) -> dict:
    """Normalize a world_behavior config dict.

    Allowed enums only.  Missing keys are filled with defaults.
    Unknown keys are silently dropped.
    """
    if not isinstance(value, dict):
        return dict(_WORLD_BEHAVIOR_DEFAULTS)

    result: dict[str, str] = {}
    for key, allowed in _WORLD_BEHAVIOR_ENUMS.items():
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip().lower() in allowed:
            result[key] = raw.strip().lower()
        else:
            result[key] = _WORLD_BEHAVIOR_DEFAULTS[key]
    return result


@dataclass
class WorldBehaviorConfig:
    """Structured world-behavior settings for builder and runtime."""
    ambient_activity: str = "medium"
    npc_initiative: str = "medium"
    interruptions: str = "normal"
    quest_prompting: str = "guided"
    companion_chatter: str = "normal"
    world_pressure: str = "standard"
    opening_guidance: str = "normal"
    play_style_bias: str = "balanced"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorldBehaviorConfig":
        normalized = normalize_world_behavior_config(data)
        return cls(**normalized)


# ---------------------------------------------------------------------------
# Phase A — schema helper functions (standalone, operate on raw dicts)
# ---------------------------------------------------------------------------


def normalize_desired_content_mix(mix: dict) -> dict:
    """Clamp values to [0,1] and normalize to sum 1.0 if nonzero."""
    if not isinstance(mix, dict):
        return {}
    clamped = {k: max(0.0, min(1.0, float(v))) for k, v in mix.items() if isinstance(v, (int, float))}
    total = sum(clamped.values())
    if total > 0:
        clamped = {k: round(v / total, 4) for k, v in clamped.items()}
    return clamped


def normalize_starting_gear(items: list) -> list:
    """Cap to 16, trim descriptions, drop empty items."""
    if not isinstance(items, list):
        return []
    result: list[dict] = []
    for item in items[:16]:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name", "")).strip().split())
        if not name:
            continue
        cleaned = dict(item)
        cleaned["name"] = name
        if "description" in cleaned:
            cleaned["description"] = " ".join(str(cleaned["description"]).strip().split())
        result.append(cleaned)
    return result


def normalize_starting_resources(resources: dict) -> dict:
    """Clamp values to [0, 999999], drop zero/negative."""
    if not isinstance(resources, dict):
        return {}
    result: dict[str, int] = {}
    for k, v in resources.items():
        try:
            val = int(v)
        except (TypeError, ValueError):
            continue
        val = max(0, min(999999, val))
        if val > 0:
            result[str(k)] = val
    return result


def normalize_opening_payload(opening: dict) -> dict:
    """Normalize nested opening payload."""
    if not isinstance(opening, dict):
        return {}

    result = dict(opening)
    for str_field in (
        "location_id",
        "location_name",
        "scene_frame",
        "immediate_problem",
        "player_involvement_reason",
        "tension_level",
        "time_of_day",
        "weather",
    ):
        val = result.get(str_field)
        if isinstance(val, str):
            result[str_field] = " ".join(val.strip().split())[:300]

    present = result.get("present_npc_ids")
    if isinstance(present, list):
        result["present_npc_ids"] = [
            " ".join(str(x).strip().split())[:80]
            for x in present[:8]
            if str(x).strip()
        ]
    else:
        result["present_npc_ids"] = []

    first_choices = result.get("first_choices")
    if isinstance(first_choices, list):
        result["first_choices"] = [
            " ".join(str(x).strip().split())[:160]
            for x in first_choices[:8]
            if str(x).strip()
        ]
    else:
        result["first_choices"] = []

    tension = (result.get("tension_level") or "").lower()
    if tension not in {"low", "medium", "high"}:
        result["tension_level"] = "medium"

    return result


def normalize_creator_setup(payload: dict) -> dict:
    """Normalize all Phase A creator setup fields in a raw dict payload."""
    if not isinstance(payload, dict):
        return payload

    result = dict(payload)

    for str_field in (
        "player_role", "player_archetype", "player_background",
        "campaign_objective", "opening_hook", "starter_conflict",
    ):
        val = result.get(str_field)
        if isinstance(val, str):
            result[str_field] = " ".join(val.strip().split())

    # core_world_laws: cap 12 items, each max 160 chars
    laws = result.get("core_world_laws")
    if isinstance(laws, list):
        result["core_world_laws"] = [
            " ".join(str(x).strip().split())[:160]
            for x in laws[:12]
            if str(x).strip()
        ]

    # genre_rules: cap 12 items, each max 160 chars
    rules = result.get("genre_rules")
    if isinstance(rules, list):
        result["genre_rules"] = [
            " ".join(str(x).strip().split())[:160]
            for x in rules[:12]
            if str(x).strip()
        ]

    result["desired_content_mix"] = normalize_desired_content_mix(
        result.get("desired_content_mix", {})
    )
    result["starting_gear"] = normalize_starting_gear(
        result.get("starting_gear", [])
    )
    result["starting_resources"] = normalize_starting_resources(
        result.get("starting_resources", {})
    )
    result["opening"] = normalize_opening_payload(
        result.get("opening", {})
    )
    result["world_behavior"] = normalize_world_behavior_config(
        result.get("world_behavior")
    )
    result["response_length"] = normalize_response_length_config(
        result.get("response_length")
    )

    return result


@dataclass
class AdventureSetup:
    setup_id: str
    title: str
    genre: str
    setting: str
    premise: str
    hard_rules: list[str] = field(default_factory=list)
    soft_tone_rules: list[str] = field(default_factory=list)
    lore_constraints: list[LoreConstraint] = field(default_factory=list)
    factions: list[FactionSeed] = field(default_factory=list)
    locations: list[LocationSeed] = field(default_factory=list)
    npc_seeds: list[NPCSeed] = field(default_factory=list)
    themes: list[ThemeConstraint] = field(default_factory=list)
    pacing: PacingProfile | None = None
    safety: SafetyConstraint | None = None
    content_balance: ContentBalance | None = None
    forbidden_content: list[str] = field(default_factory=list)
    canon_notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    difficulty_style: str | None = None
    mood: str | None = None
    starting_location_id: str | None = None
    starting_npc_ids: list[str] = field(default_factory=list)

    # Phase A — creator input fields
    player_role: str = ""
    player_archetype: str = ""
    player_background: str = ""
    campaign_objective: str = ""
    opening_hook: str = ""
    starter_conflict: str = ""
    core_world_laws: list[str] = field(default_factory=list)
    genre_rules: list[str] = field(default_factory=list)
    desired_content_mix: dict[str, float] = field(default_factory=dict)
    starting_gear: list[dict[str, Any]] = field(default_factory=list)
    starting_resources: dict[str, int] = field(default_factory=dict)
    opening: dict[str, Any] = field(default_factory=dict)

    # Phase F — World Behavior Customization
    world_behavior: dict[str, str] = field(default_factory=lambda: dict(_WORLD_BEHAVIOR_DEFAULTS))
    response_length: dict[str, str] = field(default_factory=lambda: dict(_RESPONSE_LENGTH_DEFAULTS))

    def validate(self) -> None:
        if not self.setup_id:
            raise ValueError("AdventureSetup.setup_id is required")
        if not self.title:
            raise ValueError("AdventureSetup.title is required")
        if not self.genre:
            raise ValueError("AdventureSetup.genre is required")
        if not self.setting:
            raise ValueError("AdventureSetup.setting is required")
        if not self.premise:
            raise ValueError("AdventureSetup.premise is required")

        seen_locations = set()
        for location in self.locations:
            if location.location_id in seen_locations:
                raise ValueError(f"Duplicate location_id: {location.location_id}")
            seen_locations.add(location.location_id)

        seen_factions = set()
        for faction in self.factions:
            if faction.faction_id in seen_factions:
                raise ValueError(f"Duplicate faction_id: {faction.faction_id}")
            seen_factions.add(faction.faction_id)

        seen_npcs = set()
        for npc in self.npc_seeds:
            if npc.npc_id in seen_npcs:
                raise ValueError(f"Duplicate npc_id: {npc.npc_id}")
            seen_npcs.add(npc.npc_id)

    # ------------------------------------------------------------------
    # Phase 7.1 — defaults, normalisation, UI-friendly validation
    # ------------------------------------------------------------------

    def with_defaults(self) -> "AdventureSetup":
        """Return a copy with defaults applied through the canonical dict path.

        This keeps defaults application aligned with serialize/deserialize
        behavior and avoids partial deep-copy mutation drift.
        """
        from .defaults import apply_adventure_defaults

        data = self.to_dict()
        data = apply_adventure_defaults(data)
        return AdventureSetup.from_dict(data)

    def normalize(self) -> "AdventureSetup":
        """Return a normalized copy of this setup.

        Normalization rules:
        - trim strings
        - collapse empty strings to None where appropriate
        - lowercase enum-like fields
        - deduplicate ordered lists of ids
        - keep normalization deterministic
        """
        import copy

        def _norm_str(value: str | None, *, lower: bool = False, none_if_empty: bool = False) -> str | None:
            if value is None:
                return None
            value = " ".join(value.strip().split())
            if lower:
                value = value.lower()
            if none_if_empty and not value:
                return None
            return value

        def _dedupe_keep_order(values: list[str]) -> list[str]:
            out: list[str] = []
            seen = set()
            for value in values:
                if value not in seen:
                    out.append(value)
                    seen.add(value)
            return out

        result = copy.deepcopy(self)

        result.title = _norm_str(result.title) or ""
        result.genre = _norm_str(result.genre, lower=True) or ""
        result.setting = _norm_str(result.setting) or ""
        result.premise = _norm_str(result.premise) or ""

        result.mood = _norm_str(result.mood, lower=True, none_if_empty=True)
        result.difficulty_style = _norm_str(result.difficulty_style, lower=True, none_if_empty=True)
        result.starting_location_id = _norm_str(result.starting_location_id, none_if_empty=True)

        result.hard_rules = [norm for x in result.hard_rules if (norm := _norm_str(x))]
        result.soft_tone_rules = [norm for x in result.soft_tone_rules if (norm := _norm_str(x))]
        result.forbidden_content = [norm for x in result.forbidden_content if (norm := _norm_str(x))]
        result.canon_notes = [norm for x in result.canon_notes if (norm := _norm_str(x))]

        normalized_starting_npcs = []
        for npc_id in result.starting_npc_ids:
            norm = _norm_str(npc_id, none_if_empty=True)
            if norm:
                normalized_starting_npcs.append(norm)
        result.starting_npc_ids = _dedupe_keep_order(normalized_starting_npcs)

        # Phase A — normalize creator input fields
        result.player_role = _norm_str(result.player_role) or ""
        result.player_archetype = _norm_str(result.player_archetype) or ""
        result.player_background = _norm_str(result.player_background) or ""
        result.campaign_objective = _norm_str(result.campaign_objective) or ""
        result.opening_hook = _norm_str(result.opening_hook) or ""
        result.starter_conflict = _norm_str(result.starter_conflict) or ""

        result.core_world_laws = [
            (_norm_str(x) or "")[:160]
            for x in result.core_world_laws[:12]
            if _norm_str(x)
        ]
        result.genre_rules = [
            (_norm_str(x) or "")[:160]
            for x in result.genre_rules[:12]
            if _norm_str(x)
        ]
        result.starting_gear = normalize_starting_gear(result.starting_gear)
        result.desired_content_mix = normalize_desired_content_mix(result.desired_content_mix)
        result.starting_resources = normalize_starting_resources(result.starting_resources)
        result.world_behavior = normalize_world_behavior_config(result.world_behavior)
        result.response_length = normalize_response_length_config(result.response_length)

        return result

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "setup_id": self.setup_id,
            "title": self.title,
            "genre": self.genre,
            "setting": self.setting,
            "premise": self.premise,
            "hard_rules": list(self.hard_rules),
            "soft_tone_rules": list(self.soft_tone_rules),
            "lore_constraints": [x.to_dict() for x in self.lore_constraints],
            "factions": [x.to_dict() for x in self.factions],
            "locations": [x.to_dict() for x in self.locations],
            "npc_seeds": [x.to_dict() for x in self.npc_seeds],
            "themes": [x.to_dict() for x in self.themes],
            "pacing": self.pacing.to_dict() if self.pacing else None,
            "safety": self.safety.to_dict() if self.safety else None,
            "content_balance": self.content_balance.to_dict() if self.content_balance else None,
            "forbidden_content": list(self.forbidden_content),
            "canon_notes": list(self.canon_notes),
            "metadata": dict(self.metadata),
            "difficulty_style": self.difficulty_style,
            "mood": self.mood,
            "starting_location_id": self.starting_location_id,
            "starting_npc_ids": list(self.starting_npc_ids),
            "player_role": self.player_role,
            "player_archetype": self.player_archetype,
            "player_background": self.player_background,
            "campaign_objective": self.campaign_objective,
            "opening_hook": self.opening_hook,
            "starter_conflict": self.starter_conflict,
            "core_world_laws": list(self.core_world_laws),
            "genre_rules": list(self.genre_rules),
            "desired_content_mix": dict(self.desired_content_mix),
            "starting_gear": list(self.starting_gear),
            "starting_resources": dict(self.starting_resources),
            "opening": dict(self.opening),
            "world_behavior": dict(self.world_behavior),
            "response_length": dict(self.response_length),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AdventureSetup":
        return cls(
            setup_id=data["setup_id"],
            title=data["title"],
            genre=data["genre"],
            setting=data["setting"],
            premise=data["premise"],
            hard_rules=list(data.get("hard_rules", [])),
            soft_tone_rules=list(data.get("soft_tone_rules", [])),
            lore_constraints=[
                LoreConstraint.from_dict(x) for x in data.get("lore_constraints", [])
            ],
            factions=[FactionSeed.from_dict(x) for x in data.get("factions", [])],
            locations=[LocationSeed.from_dict(x) for x in data.get("locations", [])],
            npc_seeds=[NPCSeed.from_dict(x) for x in data.get("npc_seeds", [])],
            themes=[ThemeConstraint.from_dict(x) for x in data.get("themes", [])],
            pacing=PacingProfile.from_dict(data["pacing"]) if data.get("pacing") else None,
            safety=SafetyConstraint.from_dict(data["safety"]) if data.get("safety") else None,
            content_balance=ContentBalance.from_dict(data["content_balance"])
            if data.get("content_balance")
            else None,
            forbidden_content=list(data.get("forbidden_content", [])),
            canon_notes=list(data.get("canon_notes", [])),
            metadata=dict(data.get("metadata", {})),
            difficulty_style=data.get("difficulty_style"),
            mood=data.get("mood"),
            starting_location_id=data.get("starting_location_id"),
            starting_npc_ids=list(data.get("starting_npc_ids", [])),
            player_role=data.get("player_role", ""),
            player_archetype=data.get("player_archetype", ""),
            player_background=data.get("player_background", ""),
            campaign_objective=data.get("campaign_objective", ""),
            opening_hook=data.get("opening_hook", ""),
            starter_conflict=data.get("starter_conflict", ""),
            core_world_laws=list(data.get("core_world_laws", [])),
            genre_rules=list(data.get("genre_rules", [])),
            desired_content_mix=dict(data.get("desired_content_mix", {})),
            starting_gear=list(data.get("starting_gear", [])),
            starting_resources=dict(data.get("starting_resources", {})),
            opening=dict(data.get("opening", {})),
            world_behavior=normalize_world_behavior_config(data.get("world_behavior")),
            response_length=normalize_response_length_config(data.get("response_length")),
        )
