"""Bootstrap live-session seed state from compiled genesis."""

from __future__ import annotations

from typing import Any


def bootstrap_session_from_compiled_genesis(compiled: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic bootstrap state derived from compiled genesis."""

    world_forge = dict(compiled.get("compiled_world_forge") or {})
    return {
        "bootstrap_version": "rpg_genesis_bootstrap_v2",
        "active_goals": list(compiled.get("compiled_goals") or []),
        "decision_biases": dict(compiled.get("compiled_decision_biases") or {}),
        "gear_intents": list(compiled.get("compiled_gear_intents") or []),
        "starter_loadout": list(compiled.get("compiled_starter_loadout") or []),
        "world_traits": list(compiled.get("compiled_world_traits") or []),
        "feature_flags": dict(compiled.get("compiled_feature_flags") or {}),
        "stats": dict(compiled.get("compiled_stats") or {}),
        "world_forge": world_forge,
        "campaign_topic_graph": dict(world_forge.get("topic_graph") or {}),
        "campaign_generation_status": "planned" if world_forge.get("enabled") is True else "disabled",
        "provenance": dict(compiled.get("compiled_provenance") or {}),
    }
