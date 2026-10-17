"""Materialize missing scene canon before turn-time narrative retrieval."""
from __future__ import annotations

import logging

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway

from .campaign_lore_store import _mapping, _rows, _text, current_location_identity
from .world_forge_dossiers import (
    dossier_prompt_contract,
    project_entity_dossier,
    validate_entity_dossier,
)
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.session_genesis_runtime_lore_materialization.prompt', "1", (
    "Return one JSON object that materializes durable, player-safe RPG canon "
        "for the target. Existing world lore is binding. Never contradict it or "
        "invent quest solutions, hidden secrets, mechanical stats, or facts the "
        "player could not know. For a location, include a rich location dossier, "
        "two to four local NPC dossiers, overview and history documents, public "
        "facts, and explicit relationships. For an NPC, creature, or monster, "
        "include one consistent dossier and lore document. Every entity must include "
        "short_summary and a dossier matching the supplied dossier contract. NPC "
        "entities must additionally include "
        "description, appearance, personality, backstory, speech_style, goals, motives, "
        "relationships, known_facts, and current_situation. Required top-level "
        "arrays: entities, documents, facts, relationships. Return JSON only."
))

logger = logging.getLogger(__name__)

_VISIBLE = {"public", "player_known", "learned", "partially_known", "disputed"}
_KIND_PREFIXES = {
    "npc": "npc",
    "character": "npc",
    "actor": "npc",
    "creature": "creature",
    "monster": "monster",
    "beast": "creature",
    "location": "location",
    "place": "location",
    "point_of_interest": "point_of_interest",
    "poi": "point_of_interest",
    "settlement": "location",
    "town": "location",
    "city": "location",
    "region": "region",
    "faction": "faction",
    "organization": "faction",
    "organisation": "faction",
    "institution": "faction",
    "item": "item",
    "equipment": "item",
    "weapon": "item",
    "vehicle": "vehicle",
    "technology": "technology",
    "augmentation": "technology",
    "network": "network",
    "ai": "network",
    "quest": "quest",
    "job": "quest",
    "contract": "quest",
    "culture": "culture",
    "subculture": "culture",
    "role": "role",
    "archetype": "role",
    "threat": "threat",
}
_TOPIC_BY_KIND = {
    "npc": "actors",
    "location": "places",
    "point_of_interest": "points_of_interest",
    "region": "regions",
    "faction": "groups",
    "item": "equipment_vehicles",
    "vehicle": "equipment_vehicles",
    "technology": "technology_augmentations",
    "network": "networks",
    "quest": "quests",
    "culture": "cultures",
    "role": "roles_archetypes",
    "monster": "threats",
    "creature": "threats",
    "threat": "threats",
}
_SCENE_LIST_KEYS = (
    "present_npcs",
    "nearby_npcs",
    "npcs",
    "present_creatures",
    "nearby_creatures",
    "creatures",
    "present_monsters",
    "nearby_monsters",
    "monsters",
    "entities",
    "actors",
    "items",
    "equipment",
    "weapons",
    "vehicles",
    "locations",
    "places",
    "points_of_interest",
    "regions",
    "factions",
    "organizations",
    "organisations",
    "institutions",
    "quests",
    "jobs",
    "contracts",
    "cultures",
    "technologies",
    "augmentations",
    "networks",
    "introduced_entities",
    "discovered_entities",
    "created_entities",
    "revealed_entities",
    "new_entities",
    "lore_additions",
)
_SCENE_ID_KEYS = (
    "present_npc_ids",
    "nearby_npc_ids",
    "present_creature_ids",
    "nearby_creature_ids",
    "present_monster_ids",
    "nearby_monster_ids",
    "entity_ids",
    "actor_ids",
    "item_ids",
    "equipment_ids",
    "weapon_ids",
    "vehicle_ids",
    "location_ids",
    "place_ids",
    "point_of_interest_ids",
    "region_ids",
    "faction_ids",
    "organization_ids",
    "institution_ids",
    "quest_ids",
    "job_ids",
    "contract_ids",
    "culture_ids",
    "technology_ids",
    "augmentation_ids",
    "network_ids",
    "introduced_entity_ids",
    "discovered_entity_ids",
    "created_entity_ids",
    "revealed_entity_ids",
    "new_entity_ids",
)


@dataclass(frozen=True)
class SceneLoreTarget:
    entity_id: str
    kind: str
    name: str
    location_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "name": self.name,
            "location_id": self.location_id,
        }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unknown"


def _name_from_id(entity_id: str) -> str:
    words = entity_id.split(":", 1)[-1].replace("_", " ").replace("-", " ").split()
    if len(words) > 1 and words[0].casefold() in {"loc", "location", "npc"}:
        words = words[1:]  # loc_tavern reads as "Tavern", not "Loc Tavern"
    return " ".join(words).title() or "Unknown Entity"


def _kind(entity_id: str, value: Mapping[str, Any] | None = None) -> str:
    raw = _text((value or {}).get("kind") or (value or {}).get("type")).casefold()
    if raw in _KIND_PREFIXES:
        return _KIND_PREFIXES[raw]
    prefix = entity_id.split(":", 1)[0].casefold() if ":" in entity_id else ""
    return _KIND_PREFIXES.get(prefix, "entity")


def canonical_lore_topic_id(kind: str) -> str:
    normalized = _KIND_PREFIXES.get(_text(kind).casefold(), _text(kind).casefold())
    return _TOPIC_BY_KIND.get(normalized, f"{normalized or 'entity'}s")


def _kind_hint(key: str) -> str:
    singular = key.removesuffix("_ids").removesuffix("s")
    for token in (
        "point_of_interest",
        "organization",
        "organisation",
        "institution",
        "augmentation",
        "technology",
        "equipment",
        "weapon",
        "vehicle",
        "location",
        "place",
        "region",
        "faction",
        "quest",
        "contract",
        "culture",
        "network",
        "item",
        "npc",
        "creature",
        "monster",
        "threat",
    ):
        if token in singular:
            return _KIND_PREFIXES[token]
    return ""


def _target(
    value: Any,
    *,
    location_id: str = "",
    kind_hint: str = "",
) -> SceneLoreTarget | None:
    if isinstance(value, Mapping):
        entity_id = _text(
            value.get("entity_id")
            or value.get("npc_id")
            or value.get("creature_id")
            or value.get("monster_id")
            or value.get("speaker_id")
            or value.get("actor_id")
            or value.get("location_id")
            or value.get("item_id")
            or value.get("equipment_id")
            or value.get("weapon_id")
            or value.get("vehicle_id")
            or value.get("place_id")
            or value.get("point_of_interest_id")
            or value.get("region_id")
            or value.get("faction_id")
            or value.get("organization_id")
            or value.get("institution_id")
            or value.get("quest_id")
            or value.get("job_id")
            or value.get("contract_id")
            or value.get("culture_id")
            or value.get("technology_id")
            or value.get("augmentation_id")
            or value.get("network_id")
            or value.get("id")
        )
        name = _text(
            value.get("name")
            or value.get("title")
            or value.get("label")
            or value.get("location_name")
            or value.get("speaker")
        )
        resolved_kind = _kind(entity_id, value)
    else:
        entity_id = _text(value)
        name = ""
        resolved_kind = _kind(entity_id)
    if not entity_id or entity_id == "player" or entity_id.startswith("player:"):
        return None
    if ":" not in entity_id and kind_hint:
        entity_id = f"{kind_hint}:{_slug(entity_id)}"
    resolved_kind = kind_hint or resolved_kind
    return SceneLoreTarget(
        entity_id=entity_id,
        kind=resolved_kind,
        name=name or _name_from_id(entity_id),
        location_id=location_id,
    )


def _scene_containers(
    result: Mapping[str, Any],
    session: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    state = _mapping(session.get("state"))
    simulation = _mapping(session.get("simulation_state"))
    runtime = _mapping(session.get("runtime_state"))
    return (
        _mapping(result.get("scene")),
        _mapping(result.get("resolved_result") or result.get("result")),
        _mapping(state.get("scene")),
        _mapping(runtime.get("scene") or runtime.get("current_scene")),
        _mapping(simulation.get("scene")),
        _mapping(simulation.get("scene_population_state")),
    )


def _scene_location(
    result: Mapping[str, Any],
    session: Mapping[str, Any],
) -> SceneLoreTarget | None:
    for container in _scene_containers(result, session):
        nested = _mapping(container.get("location") or container.get("current_location"))
        candidate = {
            "location_id": container.get("location_id") or nested.get("id"),
            "location_name": (
                container.get("location_name")
                or nested.get("name")
                or nested.get("title")
            ),
        }
        target = _target(candidate, kind_hint="location")
        if target is not None:
            return target
    location = current_location_identity(session)
    return _target(location, kind_hint="location") if location else None


def scene_lore_targets(
    result: Mapping[str, Any],
    session: Mapping[str, Any],
    *,
    explicit_entity_ids: Sequence[str] = (),
) -> tuple[SceneLoreTarget, ...]:
    """Return current location and encountered scene entities in stable order."""

    values: dict[str, SceneLoreTarget] = {}
    location = _scene_location(result, session)
    location_id = location.entity_id if location else ""
    if location:
        values[location_id.casefold()] = location

    def add(candidate: SceneLoreTarget | None) -> None:
        if candidate is None:
            return
        key = candidate.entity_id.casefold()
        previous = values.get(key)
        if previous is None or previous.kind == "entity":
            values[key] = candidate

    for entity_id in explicit_entity_ids:
        add(_target(entity_id, location_id=location_id))
    for container in _scene_containers(result, session):
        for key in _SCENE_ID_KEYS:
            hint = _kind_hint(key)
            for entity_id in container.get(key) or ():
                add(_target(entity_id, location_id=location_id, kind_hint=hint))
        for key in _SCENE_LIST_KEYS:
            hint = _kind_hint(key)
            for row in container.get(key) or ():
                add(_target(row, location_id=location_id, kind_hint=hint))
    npc = _mapping(result.get("npc"))
    add(_target(npc, location_id=location_id, kind_hint="npc") if npc else None)
    return tuple(values.values())


def _document_for_entity(
    documents: Sequence[Mapping[str, Any]],
    entity_id: str,
) -> bool:
    target = entity_id.casefold()
    return any(
        target
        in {
            _text(value).casefold()
            for value in (
                *list(row.get("entity_refs") or ()),
                *list(row.get("entities") or ()),
            )
        }
        for row in documents
    )


def scene_lore_entity_is_rich(entity: Mapping[str, Any]) -> bool:
    """Require a full character dossier, not merely an encountered-NPC stub."""

    if not _text(entity.get("name") or entity.get("title")):
        return False
    kind = _text(entity.get("kind")).casefold()
    if kind == "npc":
        dossier = _mapping(entity.get("dossier"))
        sections = dossier.get("sections")
        valid_dossier = (
            not validate_entity_dossier(dossier)
            and isinstance(sections, list)
            and len(sections) >= 4
        )
        rich_fields = all(
            bool(_text(entity.get(field)))
            for field in (
                "description",
                "appearance",
                "personality",
                "backstory",
                "speech_style",
            )
        ) and bool(entity.get("goals")) and bool(entity.get("motives"))
        return valid_dossier or rich_fields
    return bool(
        _text(
            entity.get("description")
            or entity.get("public_bio")
            or entity.get("sensory_profile")
            or entity.get("appearance")
            or entity.get("behavior")
        )
    )


def _rich_entity(entity: Mapping[str, Any]) -> bool:
    return scene_lore_entity_is_rich(entity)


def _context(bible: Mapping[str, Any], target: SceneLoreTarget) -> dict[str, Any]:
    visible_documents = []
    for row in _rows(bible.get("documents")):
        if _text(row.get("visibility")).casefold() not in _VISIBLE:
            continue
        summary = _text(
            row.get("summary_500")
            or row.get("summary_120")
            or row.get("full_text")
        )
        if summary:
            visible_documents.append(
                {"title": _text(row.get("title")), "summary": summary[:600]}
            )
        if len(visible_documents) >= 10:
            break
    entities = _mapping(bible.get("entities"))
    return {
        "target": target.as_dict(),
        "location": _mapping(entities.get(target.location_id)),
        "known_world_lore": visible_documents,
    }


def _parse_json(value: str) -> dict[str, Any]:
    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        str(value or "").strip(),
        flags=re.IGNORECASE,
    ).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _stable_choices(
    campaign_id: str,
    entity_id: str,
) -> tuple[str, str, str, str]:
    digest = hashlib.sha256(f"{campaign_id}|{entity_id}".encode()).digest()
    names = (
        "Mara Venn",
        "Tomas Reed",
        "Elian Ward",
        "Nessa Vale",
        "Corin Ash",
        "Sera Holt",
    )
    roles = (
        "innkeeper",
        "market steward",
        "local healer",
        "watch sergeant",
        "scribe",
        "stable master",
    )
    traits = (
        "practical and observant",
        "warm but guarded",
        "direct and civic-minded",
        "patient and meticulous",
    )
    histories = (
        "an old trade crossing",
        "a refuge founded after a border war",
        "a river settlement grown around a shrine",
        "a fortified market built over older ruins",
    )
    return (
        names[digest[0] % len(names)],
        roles[digest[1] % len(roles)],
        traits[digest[2] % len(traits)],
        histories[digest[3] % len(histories)],
    )


def _fallback_location_bundle(
    campaign_id: str,
    target: SceneLoreTarget,
) -> dict[str, Any]:
    first = _stable_choices(campaign_id, target.entity_id)
    second = _stable_choices(campaign_id, target.entity_id + ":2")
    overview = (
        f"{target.name} is a lived-in settlement shaped by its roads, trades, "
        "and surrounding region. Its streets join a practical civic center, "
        "clustered homes, workshops, and public gathering places. The settlement "
        "has an established daily rhythm and recognizable local customs."
    )
    history = (
        f"Local tradition describes {target.name} as {first[3]}. Its institutions "
        "grew from the need to protect travelers, settle disputes, preserve supplies, "
        "and maintain links with neighboring communities."
    )
    npcs = []
    for index, choice in enumerate((first, second), start=1):
        name, role, traits, _ = choice
        npc_id = f"npc:{_slug(target.name)}:{index}:{_slug(name)}"
        npcs.append(
            {
                "id": npc_id,
                "kind": "npc",
                "name": name,
                "role": role,
                "description": (
                    f"{name} is a {role} in {target.name}, known publicly as {traits}."
                ),
                "personality": traits,
                "speech_style": "plainspoken and locally informed",
                "location_id": target.entity_id,
                "visibility": "public",
                "dossier_status": "materialized",
            }
        )
    documents = [
        {
            "title": target.name,
            "topic_id": "locations",
            "full_text": overview,
            "entity_refs": [target.entity_id],
        },
        {
            "title": f"History of {target.name}",
            "topic_id": "history",
            "full_text": history,
            "entity_refs": [target.entity_id],
        },
    ]
    documents.extend(
        {
            "title": npc["name"],
            "topic_id": "npcs",
            "full_text": npc["description"],
            "entity_refs": [npc["id"], target.entity_id],
        }
        for npc in npcs
    )
    return {
        "entities": [
            {
                "id": target.entity_id,
                "kind": "location",
                "name": target.name,
                "description": overview,
                "sensory_profile": (
                    "Work and conversation mix with weathered stone, timber, "
                    "cooking smoke, and road dust."
                ),
                "services": ["lodging", "food", "trade", "local guidance"],
                "landmarks": [
                    "central gathering place",
                    "market street",
                    "local watch post",
                ],
                "inhabitants": [row["id"] for row in npcs],
                "visibility": "public",
                "dossier_status": "materialized",
            },
            *npcs,
        ],
        "documents": documents,
        "facts": [
            {
                "id": f"fact:{_slug(target.name)}:settlement",
                "content": overview,
                "entity_refs": [target.entity_id],
            },
            {
                "id": f"fact:{_slug(target.name)}:history",
                "content": history,
                "entity_refs": [target.entity_id],
            },
        ],
        "relationships": [
            {
                "id": f"relationship:{_slug(target.name)}:{index}",
                "kind": "located_in",
                "source_id": npc["id"],
                "target_id": target.entity_id,
                "content": f"{npc['name']} lives and works in {target.name}.",
                "entity_refs": [npc["id"], target.entity_id],
            }
            for index, npc in enumerate(npcs, start=1)
        ],
    }


def _fallback_entity_bundle(target: SceneLoreTarget, location_name: str = "") -> dict[str, Any]:
    kind = target.kind if target.kind != "entity" else "creature"
    if kind == "npc":
        description = (
            f"{target.name} is a person currently encountered in the campaign, "
            "with a public identity grounded in the surrounding community."
        )
    elif kind in {"monster", "creature"}:
        description = (
            f"{target.name} is a known {kind} of this region. Its visible anatomy, "
            "movement, habitat, and behavior define how witnesses recognize it "
            "without revealing unobserved abilities or hidden weaknesses."
        )
    else:
        description = (
            f"{target.name} is an established {kind} in the campaign world, "
            "described through public and presently observable facts."
        )
    entity: dict[str, Any] = {
        "id": target.entity_id,
        "kind": kind,
        "name": target.name,
        "description": description,
        "location_id": target.location_id,
        "visibility": "player_known",
        "dossier_status": "materialized",
    }
    if kind in {"monster", "creature"}:
        entity.update(
            {
                "appearance": "Distinctive enough to identify at close range.",
                "behavior": "Acts according to its habitat, needs, and circumstances.",
                "habitat": target.location_id,
            }
        )
    elif kind == "npc":
        entity.update(
            {
                "appearance": (
                    "Their clothing, posture, and working gear reflect their place "
                    "in the surrounding community."
                ),
                "personality": (
                    "Observant and guarded with strangers, but responsive to direct "
                    "questions and changes in the local situation."
                ),
                "backstory": (
                    f"{target.name} has established ties to the people and routines "
                    f"around {_name_from_id(target.location_id) if target.location_id else 'this region'}."
                ),
                "speech_style": (
                    "Speaks naturally and specifically, using local knowledge and "
                    "avoiding claims beyond what they know."
                ),
                "goals": ["Protect their livelihood and standing in the community."],
                "motives": ["Respond to immediate pressures without exposing private knowledge."],
                "relationships": [],
                "known_facts": [],
                "current_situation": (
                    f"Currently encountered at {_name_from_id(target.location_id)}."
                    if target.location_id
                    else "Currently present in the active scene."
                ),
                "dossier_status": "complete",
            }
        )
        short_summary, dossier = project_entity_dossier(
            entity,
            card_type="npcs",
            entity_id=target.entity_id,
        )
        entity["short_summary"] = short_summary
        entity["dossier"] = dossier
    elif kind not in {"monster", "creature"}:
        entity.update(
            {
                "summary": description,
                "current_situation": "Known through the active campaign.",
            }
        )
    short_summary, dossier = project_entity_dossier(
        entity,
        card_type=canonical_lore_topic_id(kind),
        entity_id=target.entity_id,
    )
    entity["short_summary"] = _text(entity.get("short_summary")) or short_summary
    if validate_entity_dossier(entity.get("dossier")):
        entity["dossier"] = dossier
    return {
        "entities": [entity],
        "documents": [
            {
                "title": target.name,
                "topic_id": canonical_lore_topic_id(kind),
                "full_text": description,
                "entity_refs": [target.entity_id],
            }
        ],
        "facts": [
            {
                "id": f"fact:{_slug(target.entity_id)}:identity",
                "content": description,
                "entity_refs": [target.entity_id],
            }
        ],
        "relationships": (
            [
                {
                    "id": f"relationship:{_slug(target.entity_id)}:location",
                    "kind": "encountered_at",
                    "source_id": target.entity_id,
                    "target_id": target.location_id,
                    "content": f"{target.name} was encountered at {location_name or _name_from_id(target.location_id)}.",
                    "entity_refs": [target.entity_id, target.location_id],
                }
            ]
            if target.location_id
            else []
        ),
    }


def _generate_bundle(
    bible: Mapping[str, Any],
    campaign_id: str,
    target: SceneLoreTarget,
    *,
    llm_gateway: Any | None,
) -> dict[str, Any]:
    gateway = llm_gateway if llm_gateway is not None else build_app_llm_gateway()
    fallback = (
        _fallback_location_bundle(campaign_id, target)
        if target.kind == "location"
        else _fallback_entity_bundle(
            target,
            location_name=_text(_mapping(_mapping(bible.get("entities")).get(target.location_id)).get("name")),
        )
    )
    if gateway is None or gateway is False:
        return fallback
    prompt = (
        _PROMPT_1.text
    )
    try:
        raw = gateway.generate(
            prompt,
            context={
                **_context(bible, target),
                "entity_dossier_contract": dossier_prompt_contract(
                    canonical_lore_topic_id(target.kind)
                ),
            },
            timeout_s=30.0,
            provider_options={
                "temperature": 0.35,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_object"},
            },
        )
        parsed = _parse_json(raw)
        if all(
            isinstance(parsed.get(key), list)
            for key in ("entities", "documents", "facts", "relationships")
        ):
            return parsed
    except Exception:
        logger.debug("suppressed error in %s", "_generate_bundle", exc_info=True)
    return fallback


def _document_duplicate(
    documents: Sequence[Mapping[str, Any]],
    *,
    title: str,
    topic_id: str,
    refs: Sequence[str],
) -> bool:
    expected_refs = {value.casefold() for value in refs}
    return any(
        _text(row.get("title")).casefold() == title.casefold()
        and _text(row.get("topic_id")).casefold() == topic_id.casefold()
        and expected_refs.issubset(
            {
                _text(value).casefold()
                for value in row.get("entity_refs") or ()
            }
        )
        for row in documents
    )


def _merge_bundle(
    bible: dict[str, Any],
    bundle: Mapping[str, Any],
    *,
    revision: int,
    source_target: SceneLoreTarget,
) -> tuple[list[str], list[str]]:
    entities = deepcopy(_mapping(bible.get("entities")))
    documents = _rows(bible.get("documents"))
    facts = _rows(bible.get("facts"))
    relationships = _rows(bible.get("relationships"))
    discovery = deepcopy(_mapping(bible.get("discovery_state")))
    page_status = deepcopy(_mapping(discovery.get("pages")))
    entity_status = deepcopy(_mapping(discovery.get("entities")))
    created_entities: list[str] = []
    created_documents: list[str] = []

    for index, raw in enumerate(bundle.get("entities") or (), start=1):
        if not isinstance(raw, Mapping):
            continue
        row = deepcopy(dict(raw))
        entity_id = _text(row.get("id") or row.get("entity_id")) or (
            f"{_text(row.get('kind')) or 'entity'}:"
            f"{_slug(_text(row.get('name')) or str(index))}"
        )
        row.update(
            {
                "id": entity_id,
                "kind": _text(row.get("kind")) or _kind(entity_id, row),
                "name": _text(row.get("name")) or _name_from_id(entity_id),
                "visibility": _text(row.get("visibility")) or "public",
                "canon_revision": revision,
                "provenance": {
                    **_mapping(row.get("provenance")),
                    "source": "runtime_scene_materialization_v1",
                    "source_target_id": source_target.entity_id,
                },
            }
        )
        short_summary, dossier = project_entity_dossier(
            row,
            card_type=canonical_lore_topic_id(str(row["kind"])),
            content=bible,
            entity_id=entity_id,
        )
        row["short_summary"] = _text(row.get("short_summary")) or short_summary
        if validate_entity_dossier(row.get("dossier")):
            row["dossier"] = dossier
        existing = deepcopy(_mapping(entities.get(entity_id)))
        if not _rich_entity(existing):
            entities[entity_id] = {
                **row,
                **{
                    key: value
                    for key, value in existing.items()
                    if value not in (None, "", [], {})
                },
            }
            created_entities.append(entity_id)
        entity_status.setdefault(entity_id, "partially_known")

    occupied = {_text(row.get("document_id")) for row in documents}
    for index, raw in enumerate(bundle.get("documents") or (), start=1):
        if not isinstance(raw, Mapping):
            continue
        row = deepcopy(dict(raw))
        # Models name these fields differently (Claude: ``entity_id`` and ``body``).
        refs = [
            _text(value)
            for value in (*(row.get("entity_refs") or ()), *(row.get("entity_ids") or ()), row.get("entity_id"))
            if _text(value)
        ]
        title = _text(row.get("title")) or source_target.name
        topic_id = _text(row.get("topic_id")) or f"{source_target.kind}s"
        if _document_duplicate(documents, title=title, topic_id=topic_id, refs=refs):
            continue
        document_id = _text(row.get("document_id")) or (
            f"lore:{topic_id}:{_slug(title)}"
        )
        if document_id in occupied:
            document_id = f"{document_id}:{index}"
        full_text = _text(
            row.get("full_text") or row.get("content") or row.get("body") or row.get("text")
            or row.get("summary")
        )
        if not full_text:
            continue
        row.update(
            {
                "document_id": document_id,
                "topic_id": topic_id,
                "title": title,
                "full_text": full_text,
                "summary_500": _text(row.get("summary_500")) or full_text[:500],
                "summary_120": _text(row.get("summary_120")) or full_text[:120],
                "keywords": list(
                    dict.fromkeys([*list(row.get("keywords") or ()), *refs, title])
                ),
                "entity_refs": refs or [source_target.entity_id],
                "visibility": _text(row.get("visibility")) or "public",
                "canon_revision": revision,
                "provenance": {
                    **_mapping(row.get("provenance")),
                    "source": "runtime_scene_materialization_v1",
                    "source_target_id": source_target.entity_id,
                },
            }
        )
        documents.append(row)
        occupied.add(document_id)
        page_status[document_id] = "partially_known"
        created_documents.append(document_id)

    fact_ids = {_text(row.get("id") or row.get("evidence_id")) for row in facts}
    for index, raw in enumerate(bundle.get("facts") or (), start=1):
        if not isinstance(raw, Mapping):
            continue
        row = deepcopy(dict(raw))
        fact_id = _text(row.get("id") or row.get("evidence_id")) or (
            f"fact:{_slug(source_target.entity_id)}:{index}"
        )
        content = _text(row.get("content") or row.get("statement") or row.get("text") or row.get("fact"))
        if fact_id in fact_ids or not content:
            continue
        row.update(
            {
                "id": fact_id,
                "content": content,
                "authority": "objective_canon",
                "approved_authority": "objective_canon",
                "visibility": _text(row.get("visibility")) or "public",
                "canon_revision": revision,
            }
        )
        facts.append(row)
        fact_ids.add(fact_id)

    relationship_ids = {_text(row.get("id")) for row in relationships}
    for index, raw in enumerate(bundle.get("relationships") or (), start=1):
        if not isinstance(raw, Mapping):
            continue
        row = deepcopy(dict(raw))
        relationship_id = _text(row.get("id")) or (
            f"relationship:{_slug(source_target.entity_id)}:{index}"
        )
        if relationship_id in relationship_ids:
            continue
        row.update(
            {
                "id": relationship_id,
                "authority": "objective_canon",
                "approved_authority": "objective_canon",
                "visibility": _text(row.get("visibility")) or "public",
                "canon_revision": revision,
            }
        )
        relationships.append(row)
        relationship_ids.add(relationship_id)

    bible.update(
        {
            "entities": entities,
            "documents": documents,
            "facts": facts,
            "relationships": relationships,
        }
    )
    discovery.update(
        {
            "pages": page_status,
            "entities": entity_status,
            "discoveries": list(discovery.get("discoveries") or ()),
        }
    )
    bible["discovery_state"] = discovery
    return created_entities, created_documents


def materialize_scene_lore(
    bible: Mapping[str, Any],
    session: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    campaign_id: str,
    explicit_entity_ids: Sequence[str] = (),
    canon_revision: int | None = None,
    llm_gateway: Any | None = None,
    pregenerated: Mapping[str, Mapping[str, Any]] | None = None,
    generated: dict[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate and merge missing current-scene canon once.

    ``generated`` collects each target's bundle; ``pregenerated`` supplies bundles
    produced earlier, so a caller can generate outside its row locks and merge
    under them (WP-8.6).
    """

    candidate = deepcopy(dict(bible))
    candidate.setdefault("documents", [])
    candidate.setdefault("entities", {})
    candidate.setdefault("facts", [])
    candidate.setdefault("relationships", [])
    revision = int(canon_revision or int(candidate.get("canon_revision") or 0) + 1)
    created_entities: list[str] = []
    created_documents: list[str] = []
    targets = scene_lore_targets(
        result,
        session,
        explicit_entity_ids=explicit_entity_ids,
    )
    for target in targets:
        existing = _mapping(_mapping(candidate.get("entities")).get(target.entity_id))
        documented = _document_for_entity(
            _rows(candidate.get("documents")),
            target.entity_id,
        )
        if _rich_entity(existing) and documented:
            continue
        bundle = (pregenerated or {}).get(target.entity_id)
        if bundle is None:
            bundle = _generate_bundle(
                candidate,
                campaign_id,
                target,
                llm_gateway=llm_gateway,
            )
        if generated is not None:
            generated[target.entity_id] = bundle
        entity_ids, document_ids = _merge_bundle(
            candidate,
            bundle,
            revision=revision,
            source_target=target,
        )
        created_entities.extend(entity_ids)
        created_documents.extend(document_ids)
    changed = bool(created_entities or created_documents)
    if changed:
        candidate["canon_revision"] = revision
        manifest = deepcopy(_mapping(candidate.get("manifest")))
        manifest.update(
            {
                "document_count": len(_rows(candidate.get("documents"))),
                "entity_count": len(_mapping(candidate.get("entities"))),
                "fact_count": len(_rows(candidate.get("facts"))),
                "relationship_count": len(_rows(candidate.get("relationships"))),
            }
        )
        candidate["manifest"] = manifest
    return candidate, {
        "changed": changed,
        "targets": [target.as_dict() for target in targets],
        "created_entity_ids": list(dict.fromkeys(created_entities)),
        "created_document_ids": list(dict.fromkeys(created_documents)),
        "canon_revision": (
            revision if changed else int(candidate.get("canon_revision") or 0)
        ),
        "generator": "llm_with_deterministic_fallback",
    }
