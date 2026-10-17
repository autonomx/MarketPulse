"""Bridge Campaign Genesis v2 payloads into the current new-game creator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contract import (
    CAMPAIGN_GENESIS_CONTRACT_VERSION,
    CampaignGenesisContract,
    canonical_genesis_payload,
    genesis_contract_hash,
)
from .source_info import wizard_source_payload
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions

_ARCHETYPE_TO_BUILD = {
    "balanced_adventurer": "balanced_adventurer",
    "warrior": "warrior",
    "ranger": "ranger",
    "scout": "ranger",
    "silver_tongue": "silver_tongue",
    "face": "silver_tongue",
}
_ARCHETYPE_TO_PRIMARY_CAPABILITY = {
    "balanced_adventurer": "recon",
    "warrior": "combat",
    "ranger": "recon",
    "scout": "recon",
    "silver_tongue": "influence",
    "face": "influence",
}
_CAPABILITY_IDS = {"combat", "recon", "influence", "technical", "survival", "knowledge", "support"}
_TALENT_CAPABILITY_ALIASES = {
    "action_readiness": "combat",
    "archery": "recon",
    "bowcraft": "recon",
    "combat_readiness": "combat",
    "combat_survival": "combat",
    "diplomacy": "influence",
    "fieldcraft": "survival",
    "lore": "knowledge",
    "medicine": "support",
    "reconnaissance": "recon",
    "scouting": "recon",
    "social": "influence",
    "survival_sense": "survival",
    "tracking": "recon",
}
_GEAR_TAG_LABELS = {
    "close_weapon": "Iron dagger",
    "melee_weapon": "Iron dagger",
    "ranged_weapon": "Shortbow",
    "survival_tool": "Rope coil",
    "travel_supplies": "Trail rations x3",
    "light_source": "Torch x2",
    "field_notes": "Field journal",
    "starting_coin": "10 gold",
}
_GENESIS_CORE_STAT_MAP = {
    "strength": "strength",
    "agility": "dexterity",
    "endurance": "constitution",
    "intellect": "intelligence",
    "charisma": "charisma",
    "perception": "wisdom",
}
_GENESIS_RPG_ONLY_STATS = ("archery", "survival")


def _normal_key(value: object, fallback: str = "custom") -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text or fallback


def _title_from_key(value: object, fallback: str = "Adventurer") -> str:
    text = str(value or fallback).strip().replace("_", " ")
    return " ".join(part.capitalize() for part in text.split()) or fallback


def _capability_from_key(key: str) -> str | None:
    capability = _TALENT_CAPABILITY_ALIASES.get(key, key)
    return capability if capability in _CAPABILITY_IDS else None


def _capabilities_from_talents(contract: CampaignGenesisContract) -> list[str]:
    capabilities: list[str] = []
    for talent in contract.drivers.talents:
        key = _normal_key(talent.id, "")
        capability = _capability_from_key(key)
        if capability and capability not in capabilities:
            capabilities.append(capability)
    return capabilities


def _gear_labels(tags: list[str]) -> list[str]:
    labels: list[str] = []
    for tag in tags:
        raw_label = str(tag or "").strip()
        key = _normal_key(raw_label, "")
        if key in _GEAR_TAG_LABELS:
            labels.append(_GEAR_TAG_LABELS[key])
        elif raw_label:
            labels.append(raw_label)
    return labels or ["Iron dagger", "Trail rations x2", "10 gold"]


def _render_genesis_summary(contract: CampaignGenesisContract) -> str:
    stats = contract.initial_stats.model_dump(mode="json")
    stat_text = ", ".join(f"{key} {value}" for key, value in stats.items())
    gear_text = ", ".join(_gear_labels(contract.starter_gear_tags))
    story = contract.story_options
    parts = [
        f"Archetype: {_title_from_key(contract.drivers.archetype)}",
        f"Origin: {contract.identity.origin}",
        f"Motivation: {contract.drivers.motivation.primary}",
        f"Starter gear: {gear_text}",
        f"Stats: {stat_text}",
        f"Opening: {story.opening_hook or 'Tavern Rumor'}",
        f"Pace: {story.opening_pace or 'Balanced'}",
        f"Relationship: {story.relationship_preset or 'Unknown Outsider'}",
    ]
    if contract.drivers.flaw:
        parts.insert(3, f"Flaw: {contract.drivers.flaw}")
    return ". ".join(parts) + "."


def adapt_genesis_payload_to_new_game_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _safe_dict(payload.get("request") or payload)
    contract = CampaignGenesisContract.model_validate(raw.get("genesis") or raw)
    archetype = _normal_key(contract.drivers.archetype)
    capability_ids = _capabilities_from_talents(contract)
    build = _ARCHETYPE_TO_BUILD.get(
        archetype,
        "balanced_adventurer",
    )
    primary = capability_ids[0] if capability_ids else _ARCHETYPE_TO_PRIMARY_CAPABILITY.get(archetype, "recon")
    secondary_capabilities = [capability for capability in capability_ids[1:] if capability != primary]
    world = contract.world_options
    system = contract.system_options
    identity = contract.identity
    story = contract.story_options
    return {
        "campaign_template": contract.campaign_template,
        "genre": contract.genre,
        "tone": contract.tone,
        "background": identity.background,
        "starting_location": world.starting_location,
        "player": {
            "name": identity.name,
            "pronouns": identity.pronouns,
            "background": identity.background,
            "build": build,
        },
        "primary_capability": primary,
        "secondary_capabilities": secondary_capabilities,
        "power_source": identity.power_source,
        "generated_class_name": _title_from_key(contract.drivers.archetype),
        "generated_class_summary": _render_genesis_summary(contract),
        "difficulty": world.difficulty,
        "world_activity": world.world_activity,
        "economy_pressure": world.economy_pressure,
        "combat_lethality": world.combat_lethality,
        "companions_enabled": system.companions,
        "permadeath": system.permadeath,
        "seed": world.seed,
        "opening_hook": story.opening_hook,
        "opening_pace": story.opening_pace,
        "relationship_preset": story.relationship_preset,
        "story_options": story.model_dump(mode="json"),
        "features": {
            "autosave": system.autosave,
            "validator": system.validator,
            "background_soft_audit": system.background_soft_audit,
            "llm_narration": system.llm_narration,
            "image_generation": system.image_generation,
            "tts": system.tts,
            "stt": system.stt,
        },
    }


def _provenance(contract: CampaignGenesisContract) -> dict[str, Any]:
    source = wizard_source_payload()
    return {
        "created_by": source["source_kind"],
        "contract_version": CAMPAIGN_GENESIS_CONTRACT_VERSION,
        "compiler_version": None,
        "creation_seed": contract.world_options.seed,
        "contract_hash": genesis_contract_hash(contract),
        "wizard_build": source["source_build"],
        "created_from": source["source_name"],
        "source": source,
    }


def _talent_snapshots(contract: CampaignGenesisContract) -> list[dict[str, Any]]:
    return [talent.model_dump(mode="json") for talent in contract.drivers.talents]


def _genesis_stat_profile(contract: CampaignGenesisContract) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    initial_stats = {key: int(value) for key, value in contract.initial_stats.model_dump(mode="json").items()}
    core_stats = {
        core_key: initial_stats[genesis_key]
        for genesis_key, core_key in _GENESIS_CORE_STAT_MAP.items()
        if genesis_key in initial_stats
    }
    rpg_only_stats = {
        key: initial_stats[key]
        for key in _GENESIS_RPG_ONLY_STATS
        if key in initial_stats
    }
    return initial_stats, core_stats, rpg_only_stats


def _sync_session_stats_from_genesis(state: dict[str, Any], contract: CampaignGenesisContract) -> None:
    initial_stats, core_stats, rpg_only_stats = _genesis_stat_profile(contract)

    metadata = _safe_dict(state.get("metadata"))
    metadata["initial_stats"] = initial_stats
    metadata["stat_source"] = "genesis_contract"
    state["metadata"] = metadata

    player = _safe_dict(state.get("player"))
    player_stats = _safe_dict(player.get("stats"))
    player_stats.update(core_stats)
    player["stats"] = player_stats
    state["player"] = player

    skill_progression = _safe_dict(state.get("skill_progression"))
    if rpg_only_stats:
        skill_progression["starting_stats"] = {
            key: {"value": value, "source": "genesis_contract"}
            for key, value in rpg_only_stats.items()
        }
    state["skill_progression"] = skill_progression

    affordances = _safe_dict(state.get("narrative_affordances"))
    stat_profile = _safe_dict(affordances.get("stat_profile"))
    stat_profile.update(
        {
            "initial_stats": initial_stats,
            "core_stats": dict(player_stats),
            "rpg_only_stats": rpg_only_stats,
            "source": "genesis_contract",
        }
    )
    affordances["stat_profile"] = stat_profile
    state["narrative_affordances"] = affordances


def attach_genesis_to_created_session(
    result: dict[str, Any],
    contract: CampaignGenesisContract,
    *,
    sessions: CampaignSessions,
    persist: bool = True,
    compact_save: bool = False,
) -> dict[str, Any]:
    if result.get("ok") is not True:
        return result
    session_id = str(result.get("session_id") or "")
    if not session_id:
        return result
    session = raw_session if isinstance(raw_session := result.get("session"), dict) else None
    if session is None:
        session = sessions.load(session_id)
    if not session:
        return result
    genesis = canonical_genesis_payload(contract)
    provenance = _provenance(contract)
    state = _safe_dict(session.get("state"))
    _sync_session_stats_from_genesis(state, contract)
    metadata = _safe_dict(state.get("metadata"))
    runtime_state = _safe_dict(session.get("runtime_state"))
    setup_payload = _safe_dict(session.get("setup_payload"))
    manifest = _safe_dict(session.get("manifest"))
    talents = _talent_snapshots(contract)

    state["contract_version"] = CAMPAIGN_GENESIS_CONTRACT_VERSION
    state["genesis_snapshot"] = genesis
    state["creation_provenance"] = provenance
    state["character_genesis"] = {
        "identity": genesis.get("identity", {}),
        "starting_archetype": contract.drivers.archetype,
        "starting_motivation": contract.drivers.motivation.model_dump(mode="json"),
        "starting_flaw": contract.drivers.flaw,
        "starting_talents": talents,
        "starting_values": list(contract.drivers.values),
    }
    state["character_state"] = {
        "current_motivation": contract.drivers.motivation.model_dump(mode="json"),
        "current_flaw": contract.drivers.flaw,
        "current_talents": talents,
        "current_values": list(contract.drivers.values),
    }
    metadata.update(
        {
            "contract_version": CAMPAIGN_GENESIS_CONTRACT_VERSION,
            "created_by": provenance["created_by"],
            "contract_hash": provenance["contract_hash"],
            "origin": contract.identity.origin,
            "motivation": contract.drivers.motivation.model_dump(mode="json"),
            "flaw": contract.drivers.flaw,
            "talents": talents,
            "values": list(contract.drivers.values),
        }
    )
    state["metadata"] = metadata
    setup_payload["genesis"] = genesis
    setup_payload["creation_provenance"] = provenance
    runtime_state["creation_provenance"] = provenance
    manifest["contract_version"] = CAMPAIGN_GENESIS_CONTRACT_VERSION
    manifest["contract_hash"] = provenance["contract_hash"]
    manifest["created_by"] = provenance["created_by"]
    session.update(
        {
            "state": state,
            "setup_payload": setup_payload,
            "runtime_state": runtime_state,
            "manifest": manifest,
        }
    )
    saved = sessions.save(session, compact=compact_save) if persist else session
    return {
        **result,
        "session": saved,
        "game": saved.get("state", result.get("game", {})),
    }


def create_new_game_from_genesis_payload(payload: dict[str, Any], *, sessions: CampaignSessions) -> dict[str, Any]:
    raw = _safe_dict(payload.get("request") or payload)
    contract = CampaignGenesisContract.model_validate(raw.get("genesis") or raw)
    legacy = adapt_genesis_payload_to_new_game_payload(
        {"request": {"genesis": contract.model_dump(mode="json")}}
    )
    result = sessions.create_new_game(sessions.new_game_request(legacy))
    return attach_genesis_to_created_session(result, contract, sessions=sessions)
