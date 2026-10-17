from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

PLAYER_PERSONALITY_PROFILE_VERSION = "rpg_player_personality_profile_v1"

_PRESETS: dict[str, dict[str, Any]] = {
    "heroic": {
        "id": "heroic",
        "label": "Heroic",
        "alignment": "good",
        "tone_hint": "heroic",
        "traits": ["brave", "honorable", "protective"],
        "description": "Courageous, protective, and inclined to do the right thing even at a cost.",
    },
    "pragmatic": {
        "id": "pragmatic",
        "label": "Pragmatic",
        "alignment": "neutral",
        "tone_hint": "neutral",
        "traits": ["practical", "careful", "resourceful"],
        "description": "Focused on workable choices, risks, resources, and useful outcomes.",
    },
    "ruthless": {
        "id": "ruthless",
        "label": "Ruthless",
        "alignment": "evil",
        "tone_hint": "dark",
        "traits": ["ruthless", "cold", "dominating"],
        "description": "Willing to intimidate, exploit weakness, and pursue advantage without mercy.",
    },
    "deceptive": {
        "id": "deceptive",
        "label": "Deceptive",
        "alignment": "neutral",
        "tone_hint": "cunning",
        "traits": ["sly", "patient", "manipulative"],
        "description": "Prefers misdirection, leverage, and social angles over open confrontation.",
    },
    "merciful": {
        "id": "merciful",
        "label": "Merciful",
        "alignment": "good",
        "tone_hint": "heroic",
        "traits": ["kind", "forgiving", "restrained"],
        "description": "Looks for humane solutions, restraint, and ways to spare unnecessary harm.",
    },
    "chaotic": {
        "id": "chaotic",
        "label": "Chaotic",
        "alignment": "chaotic",
        "tone_hint": "wild",
        "traits": ["bold", "unpredictable", "impulsive"],
        "description": "Acts boldly, disrupts stale patterns, and accepts volatility for opportunity.",
    },
}

_TONE_TERMS = {
    "dark": ("evil", "cruel", "ruthless", "villain", "dark", "menacing", "tyrant", "cold", "dominating"),
    "heroic": ("hero", "kind", "good", "merciful", "honorable", "noble", "brave", "protective"),
    "cunning": ("trick", "cunning", "sly", "rogue", "deceptive", "manipulative", "patient"),
    "wild": ("chaotic", "wild", "impulsive", "unpredictable", "reckless"),
}


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _l(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _clip(value: Any, limit: int = 160) -> str:
    return _s(value).strip()[:limit]


def _slug(value: Any) -> str:
    return "-".join(_clip(value, 80).lower().replace("_", "-").split())


def _descriptor(parts: list[str], traits: list[str]) -> str:
    chunks = [part for part in parts if part]
    if traits:
        chunks.append("traits: " + ", ".join(traits[:8]))
    return "; ".join(chunks).strip() or "neutral pragmatic player"


def _infer_tone(descriptor: str) -> str:
    lowered = descriptor.lower()
    for tone, terms in _TONE_TERMS.items():
        if any(term in lowered for term in terms):
            return tone
    return "neutral"


def normalize_player_personality_profile(raw_profile: Mapping[str, Any] | None = None, *, preset: str = "") -> dict[str, Any]:
    raw = _d(raw_profile)
    preset_id = _slug(preset or raw.get("preset") or raw.get("id") or raw.get("archetype"))
    base = deepcopy(_PRESETS.get(preset_id, {}))
    merged = {**base, **{key: deepcopy(value) for key, value in raw.items() if value not in (None, "", [], {})}}
    resolved_id = _slug(merged.get("id") or preset_id or merged.get("label") or "custom") or "custom"
    label = _clip(merged.get("label") or resolved_id.replace("-", " ").title(), 80)
    alignment = _clip(merged.get("alignment") or merged.get("morality") or "neutral", 40).lower() or "neutral"
    playstyle = _clip(merged.get("playstyle") or merged.get("style"), 80)
    temperament = _clip(merged.get("temperament"), 80)
    traits = []
    for value in _l(merged.get("traits") or merged.get("personality_traits")):
        trait = _clip(value, 32).lower().replace(" ", "_")
        if trait and trait not in traits:
            traits.append(trait)
    if not traits and base:
        traits = list(base.get("traits") or [])[:]
    tone_hint = _clip(merged.get("tone_hint") or merged.get("tone"), 40).lower()
    description = _clip(merged.get("description") or merged.get("summary"), 260)
    parts = [
        f"profile: {label}",
        f"alignment: {alignment}" if alignment else "",
        f"playstyle: {playstyle}" if playstyle else "",
        f"temperament: {temperament}" if temperament else "",
        f"tone: {tone_hint}" if tone_hint else "",
    ]
    descriptor = _descriptor(parts, traits)
    if not tone_hint:
        tone_hint = _infer_tone(descriptor)
    return {
        "format_version": PLAYER_PERSONALITY_PROFILE_VERSION,
        "id": resolved_id,
        "label": label,
        "alignment": alignment,
        "playstyle": playstyle,
        "temperament": temperament,
        "traits": traits[:8],
        "tone_hint": tone_hint or "neutral",
        "description": description,
        "descriptor": descriptor,
        "raw": merged,
        "source": "preset" if base else "custom_or_inferred",
        "presentation_only": True,
        "simulation_authority": False,
    }


def _sources(*, session: Mapping[str, Any] | None, simulation_state: Mapping[str, Any] | None, runtime_state: Mapping[str, Any] | None, result: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    result_dict = _d(result)
    nested = _d(result_dict.get("result"))
    result_session = _d(result_dict.get("session") or nested.get("session"))
    result_simulation = _d(result_dict.get("simulation_state") or nested.get("simulation_state") or result_session.get("simulation_state"))
    result_runtime = _d(result_dict.get("runtime_state") or nested.get("runtime_state") or result_session.get("runtime_state"))
    return [
        _d(session),
        _d(simulation_state),
        _d(runtime_state),
        result_dict,
        nested,
        result_session,
        result_simulation,
        result_runtime,
    ]


def extract_player_personality_profile(
    *,
    session: Mapping[str, Any] | None = None,
    simulation_state: Mapping[str, Any] | None = None,
    runtime_state: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    sources = _sources(session=session, simulation_state=simulation_state, runtime_state=runtime_state, result=result)
    for source in sources:
        stable = _d(source.get("player_personality_profile"))
        if stable:
            return normalize_player_personality_profile(stable)
        player_state_stable = _d(_d(source.get("player_state")).get("personality_profile"))
        if player_state_stable:
            return normalize_player_personality_profile(player_state_stable)

    candidates: list[dict[str, Any]] = []
    for source in sources:
        player_state = _d(source.get("player_state"))
        candidates.extend(
            _d(value)
            for value in (
                source.get("player_personality"),
                source.get("player_profile"),
                source.get("persona"),
                player_state.get("personality"),
                player_state.get("profile"),
            )
            if _d(value)
        )
    merged: dict[str, Any] = {}
    for candidate in candidates:
        for key, value in candidate.items():
            if key not in merged and value not in (None, "", [], {}):
                merged[key] = deepcopy(value)
    return normalize_player_personality_profile(merged)


