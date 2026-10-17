"""First-class deterministic compiler for Campaign Genesis contracts."""

from __future__ import annotations

from typing import Any

from .contract import CampaignGenesisContract, genesis_contract_hash
from .world_forge_contract import world_forge_depth_profile
from .world_forge_profile_generation import resolve_or_generate_genre_profile
from .world_forge_profile_graph import build_profile_topic_graph

GENESIS_COMPILER_VERSION = "rpg_genesis_compiler_v3"
_WORLD_PROFILE_TRAITS = {
    "harsh_frontier": ["scarce_resources", "remote_start", "active_factions"],
    "quiet_start": ["low_pressure", "local_rumors"],
}
_DRIVER_PREFERENCES = {
    "greedy": {"reward_priority": 1.2, "shared_cost_priority": 0.8},
    "reckless": {"speed_priority": 1.2, "planning_priority": 0.85},
    "cowardly": {"safety_priority": 1.2, "boldness_priority": 0.85},
    "arrogant": {"independence_priority": 1.15, "deference_priority": 0.85},
    "naive": {"trust_priority": 1.2, "verification_priority": 0.85},
    "impulsive": {"immediacy_priority": 1.2, "delay_priority": 0.8},
}
_STARTER_TAGS = {
    "ranged_weapon": {
        "category": "equipment",
        "role": "ranged",
        "item_id": "starter_ranged_kit",
        "label": "Starter ranged kit",
    },
    "close_weapon": {
        "category": "equipment",
        "role": "close",
        "item_id": "starter_close_kit",
        "label": "Starter close kit",
    },
    "survival_tool": {
        "category": "tool",
        "role": "survival",
        "item_id": "field_survival_tool",
        "label": "Field survival tool",
    },
    "travel_supplies": {
        "category": "supply",
        "role": "travel",
        "item_id": "travel_supplies",
        "label": "Travel supplies",
    },
    "field_notes": {
        "category": "record",
        "role": "reference",
        "item_id": "field_notes",
        "label": "Field notes",
    },
}


def _normal_key(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _world_traits(contract: CampaignGenesisContract) -> list[str]:
    world = contract.world_options
    traits = list(_WORLD_PROFILE_TRAITS.get(_normal_key(world.world_profile), []))
    if world.difficulty == "harsh":
        traits.append("high_risk")
    if world.world_activity == "living_world":
        traits.append("active_world")
    if world.economy_pressure == "strict":
        traits.append("scarce_resources")
    if world.combat_lethality == "deadly":
        traits.append("high_consequence")
    return list(dict.fromkeys(traits))


def _starter_intent(tag: object) -> dict[str, str]:
    key = _normal_key(tag)
    template = dict(_STARTER_TAGS.get(key, {}))
    return {
        "tag": key,
        "category": template.get("category", "misc"),
        "role": template.get("role", "starter"),
        "quality": "starter",
        "item_id": template.get("item_id", key or "starter_item"),
        "label": template.get("label", (key or "starter item").replace("_", " ").title()),
    }


def _gear_intents(contract: CampaignGenesisContract) -> list[dict[str, str]]:
    return [_starter_intent(tag) for tag in contract.starter_gear_tags]


def _starter_loadout(intents: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "id": intent["item_id"],
            "name": intent["label"],
            "source_tag": intent["tag"],
            "quality": intent["quality"],
        }
        for intent in intents
    ]


def _motivation_goals(contract: CampaignGenesisContract) -> list[dict[str, Any]]:
    motivation = contract.drivers.motivation
    primary = _normal_key(motivation.primary) or "survival"
    target = _normal_key(motivation.target) or "campaign"
    priority = max(1, min(100, int(motivation.intensity or 0)))
    return [
        {
            "id": f"pursue_{primary}_for_{target}",
            "source": f"motivation:{primary}",
            "priority": priority,
            "status": "complete" if motivation.fulfilled else "active",
        }
    ]


def _decision_biases(contract: CampaignGenesisContract) -> dict[str, float]:
    key = _normal_key(contract.drivers.flaw)
    return dict(_DRIVER_PREFERENCES.get(key, {}))


def compile_campaign_genesis(contract: CampaignGenesisContract) -> dict[str, Any]:
    """Compile declarative genesis and pin the exact resolved lore ontology."""

    intents = _gear_intents(contract)
    depth_profile = world_forge_depth_profile(contract.world_forge.depth)
    profile_resolution = resolve_or_generate_genre_profile(
        genre=contract.genre or contract.campaign_template,
        description=" ".join(contract.world_forge.custom_directives),
        campaign_mode=(
            "persistent_living_world"
            if contract.world_options.world_activity == "living_world"
            else "bounded_campaign"
        ),
    )
    graph = build_profile_topic_graph(
        profile_resolution.profile,
        campaign_template=contract.campaign_template,
        depth=contract.world_forge.depth,
        tone=contract.tone,
        starting_location=contract.world_options.starting_location,
        background_expansion=contract.world_forge.background_expansion,
        runtime_capabilities={
            "living_world": contract.world_options.world_activity == "living_world",
            "economy_pressure": contract.world_options.economy_pressure == "strict",
            "high_consequence_combat": contract.world_options.combat_lethality == "deadly",
        },
    )
    max_parallel_jobs = (
        contract.world_forge.max_parallel_jobs or depth_profile.max_parallel_jobs
    )
    max_parallel_jobs = max(
        1,
        min(int(max_parallel_jobs), depth_profile.max_parallel_jobs, 4),
    )
    return {
        "compiler_version": GENESIS_COMPILER_VERSION,
        "compiled_stats": contract.initial_stats.model_dump(mode="json"),
        "compiled_resources": {},
        "compiled_world_traits": _world_traits(contract),
        "compiled_goals": [
            {
                "id": "establish_foothold",
                "source": "genesis:bootstrap",
                "priority": 50,
                "status": "active",
            },
            *_motivation_goals(contract),
        ],
        "compiled_decision_biases": _decision_biases(contract),
        "compiled_gear_intents": intents,
        "compiled_starter_loadout": _starter_loadout(intents),
        "compiled_story_state": contract.story_options.model_dump(
            mode="json", exclude_none=True
        ),
        "compiled_feature_flags": contract.system_options.model_dump(mode="json"),
        "compiled_world_forge": {
            "enabled": contract.world_forge.enabled,
            "use_hermes": contract.world_forge.use_hermes,
            "require_consistency_audit": contract.world_forge.require_consistency_audit,
            "require_opening_dossiers": contract.world_forge.require_opening_dossiers,
            "background_expansion": contract.world_forge.background_expansion,
            "custom_directives": list(contract.world_forge.custom_directives),
            "depth_profile": depth_profile.as_dict(),
            "max_parallel_jobs": max_parallel_jobs,
            "genre_profile_resolution": profile_resolution.as_dict(),
            "resolved_profile_hash": profile_resolution.profile.content_hash,
            "topic_graph": graph.as_dict(),
        },
        "compiled_provenance": {
            "contract_version": contract.contract_version,
            "compiler_version": GENESIS_COMPILER_VERSION,
            "contract_hash": genesis_contract_hash(contract),
            "resolved_profile_hash": profile_resolution.profile.content_hash,
        },
    }
