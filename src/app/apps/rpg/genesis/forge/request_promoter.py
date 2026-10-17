"""Promote current wizard new-game payloads into Campaign Genesis v2."""

from __future__ import annotations

from typing import Any, cast

from .contract import CampaignGenesisContract
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _safe_str(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _normal_key(value: object, fallback: str = "custom") -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text or fallback


def _origin_from_background(background: str) -> str:
    mapping = {
        "ex_guard": "watch_barracks",
        "guild": "guild_hall",
        "local": "rusty_flagon_district",
        "wanderer": "open_road",
    }
    return mapping.get(
        _normal_key(background),
        _normal_key(background, "unknown_origin"),
    )


def _motivation(opening_hook: str) -> dict[str, Any]:
    mapping = {
        "bandit_trail": {"primary": "justice", "target": "road_threat"},
        "guard_trouble": {"primary": "freedom", "target": "local_watch"},
        "merchant_job": {"primary": "wealth", "target": "stable_work"},
        "missing_person": {"primary": "protection", "target": "missing_local"},
        "random_from_seed": {"primary": "discovery", "target": None},
        "tavern_rumor": {"primary": "survival", "target": None},
    }
    selected = dict(cast(Any, mapping.get(_normal_key(opening_hook), mapping["tavern_rumor"])))
    selected.update({"intensity": 100, "fulfilled": False})
    return selected


def _talents(request: dict[str, Any]) -> list[dict[str, Any]]:
    talents: list[dict[str, Any]] = []
    primary = _normal_key(request.get("primary_capability"), "")
    if primary:
        talents.append({"id": primary, "rank": 2})
    for raw in _safe_list(request.get("secondary_capabilities")):
        key = _normal_key(raw, "")
        if key and key != primary:
            talents.append({"id": key, "rank": 1})
    return talents


def promote_new_game_request_to_genesis(request: dict[str, Any]) -> CampaignGenesisContract:
    """Return the authoritative v2 contract for either v2 or legacy payloads."""

    request = _safe_dict(request)
    if isinstance(request.get("genesis"), dict):
        return CampaignGenesisContract.model_validate(request["genesis"])
    player = _safe_dict(request.get("player"))
    features = _safe_dict(request.get("features"))
    story_options = _safe_dict(request.get("story_options"))
    system_options = _safe_dict(request.get("system_options"))
    background = _safe_str(
        player.get("background") or request.get("background"),
        "wanderer",
    )
    opening_hook = _safe_str(
        story_options.get("opening_hook") or request.get("opening_hook"),
        "tavern_rumor",
    )
    return CampaignGenesisContract.model_validate(
        {
            "campaign_template": _safe_str(
                request.get("campaign_template"),
                "deterministic_rpg_campaign",
            ),
            "genre": request.get("genre"),
            "tone": _safe_str(request.get("tone"), "heroic adventure"),
            "identity": {
                "name": _safe_str(player.get("name"), "Alyndra"),
                "pronouns": _safe_str(player.get("pronouns"), "she/her"),
                "background": background,
                "origin": _origin_from_background(background),
                "power_source": request.get("power_source"),
            },
            "drivers": {
                "archetype": _normal_key(
                    request.get("generated_class_name") or player.get("build"),
                    "balanced_adventurer",
                ),
                "motivation": _motivation(opening_hook),
                "flaw": request.get("flaw"),
                "talents": _talents(request),
                "values": _safe_list(request.get("values")),
            },
            "initial_stats": _safe_dict(request.get("initial_stats")),
            "starter_gear_tags": _safe_list(request.get("starter_gear_tags")),
            "story_options": {
                "opening_hook": opening_hook,
                "opening_pace": story_options.get("opening_pace") or request.get("opening_pace"),
                "relationship_preset": story_options.get("relationship_preset")
                or request.get("relationship_preset"),
            },
            "world_options": {
                "world_profile": request.get("world_profile"),
                "starting_location": _safe_str(
                    request.get("starting_location"),
                    "rusty_flagon_tavern",
                ),
                "difficulty": request.get("difficulty") or "normal",
                "world_activity": request.get("world_activity") or "standard",
                "economy_pressure": request.get("economy_pressure") or "normal",
                "combat_lethality": request.get("combat_lethality") or "normal",
                "seed": request.get("seed"),
            },
            "system_options": {
                "autosave": bool(
                    system_options.get("autosave", features.get("autosave", True))
                ),
                "companions": bool(
                    request.get("companions_enabled", system_options.get("companions", True))
                ),
                "permadeath": bool(
                    request.get("permadeath", system_options.get("permadeath", False))
                ),
                "validator": bool(features.get("validator", True)),
                "background_soft_audit": bool(features.get("background_soft_audit", True)),
                "llm_narration": bool(features.get("llm_narration", True)),
                "image_generation": bool(features.get("image_generation", False)),
                "tts": bool(features.get("tts", False)),
                "stt": bool(features.get("stt", False)),
            },
        }
    )
