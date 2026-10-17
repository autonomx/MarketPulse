"""Player-safe Campaign Genesis progress and Lore projections."""
from __future__ import annotations

from typing import Any, Mapping

from app.apps.rpg.genesis.worlds.authoring_presentations import entity_card

from .runtime_lore_materialization import canonical_lore_topic_id


_PLAYER_VISIBLE_STATUSES = {
    "public_at_campaign_start",
    "learned",
    "partially_known",
    "disputed",
}
_PLAYER_VISIBLE_VISIBILITY = {
    "public",
    "player_known",
    "learned",
    "partially_known",
    "disputed",
}
_ALLOWED_DISCOVERY_TRANSITIONS = {
    "public_at_campaign_start",
    "learned",
    "partially_known",
    "disputed",
}


class LoreDocumentNotFound(KeyError):
    pass


class LoreDocumentForbidden(PermissionError):
    pass


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _documents(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    projection = _mapping(session.get("campaign_bible_projection"))
    return [
        dict(row)
        for row in projection.get("documents") or ()
        if isinstance(row, Mapping)
    ]


def _entities(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    projection = _mapping(session.get("campaign_bible_projection"))
    return [
        {"id": str(entity_id), **dict(row)}
        for entity_id, row in _mapping(projection.get("entities")).items()
        if isinstance(row, Mapping)
    ]


def _discovery(session: Mapping[str, Any]) -> dict[str, Any]:
    projection = _mapping(session.get("campaign_bible_projection"))
    state = _mapping(session.get("state"))
    bible = _mapping(state.get("campaign_bible"))
    discovery = _mapping(
        projection.get("discovery_state")
        or bible.get("discovery_state")
    )
    discovery.setdefault("pages", {})
    discovery.setdefault("entities", {})
    discovery.setdefault("discoveries", [])
    return discovery


def _status(document: Mapping[str, Any], discovery: Mapping[str, Any]) -> str:
    pages = _mapping(discovery.get("pages"))
    document_id = str(document.get("document_id") or "")
    explicit = str(pages.get(document_id) or "")
    if explicit:
        return explicit
    visibility = str(document.get("visibility") or "game_master_canon")
    return {
        "public": "public_at_campaign_start",
        "player_known": "learned",
        "learned": "learned",
        "partially_known": "partially_known",
        "disputed": "disputed",
    }.get(visibility, "hidden_from_player")


def _entity_status(entity: Mapping[str, Any], discovery: Mapping[str, Any]) -> str:
    entities = _mapping(discovery.get("entities"))
    entity_id = str(entity.get("id") or "")
    explicit = str(entities.get(entity_id) or "")
    if explicit:
        return explicit
    return {
        "public": "public_at_campaign_start",
        "player_known": "learned",
        "learned": "learned",
        "partially_known": "partially_known",
        "disputed": "disputed",
    }.get(str(entity.get("visibility") or ""), "hidden_from_player")


def _safe_dossier(entity: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    """Return identity and observable dossier fields, never secrets or GM notes."""

    kind = str(entity.get("kind") or "entity")
    result: dict[str, Any] = {
        "id": str(entity.get("id") or ""),
        "kind": kind,
        "name": str(entity.get("name") or entity.get("title") or entity.get("id") or "Unknown"),
        "status": status,
        "visibility": str(entity.get("visibility") or ""),
    }
    provenance = _mapping(entity.get("provenance"))
    source = str(
        provenance.get("source")
        or provenance.get("last_source")
        or ""
    )
    if source in {
        "runtime_scene_materialization_v1",
        "encountered_npc_profile_sync_v1",
    }:
        result["lore_origin"] = "gameplay"
    safe_fields = {
        "npc": (
            "short_summary",
            "description",
            "appearance",
            "personality",
            "backstory",
            "speech_style",
            "role",
            "location_id",
            "faction_ids",
            "goals",
            "motives",
            "relationships",
            "known_facts",
            "current_situation",
            "mobility_status",
            "dossier_status",
        ),
        "location": ("region_id", "sensory_profile", "description", "services"),
        "faction": ("values", "public_goal", "goals", "description"),
        "item": (
            "short_summary",
            "description",
            "appearance",
            "materials",
            "origin",
            "properties",
            "effects",
            "uses",
            "limitations",
            "rarity",
            "current_situation",
        ),
        "vehicle": (
            "short_summary",
            "description",
            "appearance",
            "function",
            "capabilities",
            "limitations",
            "current_situation",
        ),
        "technology": (
            "short_summary",
            "description",
            "appearance",
            "capability",
            "failure_mode",
            "limitations",
            "current_situation",
        ),
        "quest": (
            "short_summary",
            "description",
            "premise",
            "stakes",
            "objectives",
            "giver_id",
            "location_ids",
            "faction_ids",
            "complications",
            "rewards",
            "current_situation",
        ),
        "monster": (
            "short_summary",
            "description",
            "appearance",
            "habitat",
            "behavior",
            "behaviour",
            "abilities",
            "current_situation",
        ),
        "creature": (
            "short_summary",
            "description",
            "appearance",
            "habitat",
            "behavior",
            "behaviour",
            "abilities",
            "current_situation",
        ),
    }.get(
        kind,
        (
            "short_summary",
            "description",
            "appearance",
            "summary",
            "history",
            "origin",
            "values",
            "goals",
            "relationships",
            "current_situation",
        ),
    )
    for field in safe_fields:
        value = entity.get(field)
        if value not in (None, "", [], {}):
            result[field] = value
    return result


def _category(document: Mapping[str, Any]) -> str:
    topic = str(document.get("topic_id") or "world_lore")
    mapping = {
        "history": "History & Calendar",
        "calendar": "History & Calendar",
        "realm": "World Lore",
        "cosmology": "World Lore",
        "magic_technology": "World Lore",
        "hero_system": "World Lore",
        "pantheon": "World Lore",
        "cultures": "World Lore",
        "regions": "Regions",
        "places": "Locations",
        "points_of_interest": "Locations",
        "factions": "Factions",
        "groups": "Factions",
        "institutions": "Institutions",
        "npcs": "Characters",
        "actors": "Characters",
        "locations": "Locations",
        "current_conflicts": "Conflicts",
        "opening_threads": "Discoveries",
        "economy": "World Lore",
        "monsters": "Monsters",
        "items": "Items",
        "equipment_vehicles": "Items",
        "technology_augmentations": "Items",
        "spells": "Spells",
        "feats": "Feats",
        "quests": "Quests",
    }
    return mapping.get(topic, "World Lore")


def _safe_document(
    document: Mapping[str, Any],
    *,
    status: str,
    include_full_text: bool,
) -> dict[str, Any]:
    result = {
        "document_id": str(document.get("document_id") or ""),
        "title": str(document.get("title") or "Untitled lore"),
        "topic_id": str(document.get("topic_id") or "world_lore"),
        "category": _category(document),
        "summary_120": str(document.get("summary_120") or ""),
        "summary_500": str(document.get("summary_500") or ""),
        "keywords": list(document.get("keywords") or ()),
        "visibility": str(document.get("visibility") or "public"),
        "status": status,
        "canon_revision": int(document.get("canon_revision") or 0),
    }
    if include_full_text:
        result["full_text"] = str(document.get("full_text") or "")
    return result


def campaign_genesis_progress_payload(
    session: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = _mapping(session.get("runtime_state"))
    setup = _mapping(session.get("setup_payload"))
    generation = _mapping(runtime.get("campaign_generation"))
    expansion = _mapping(runtime.get("campaign_expansion"))
    world_forge = _mapping(setup.get("world_forge"))
    jobs = [
        dict(row)
        for row in (
            generation.get("world_forge_jobs")
            or world_forge.get("generation_jobs")
            or ()
        )
        if isinstance(row, Mapping)
    ]
    completed = sum(1 for row in jobs if row.get("status") == "completed")
    total = len(jobs)
    return {
        "enabled": bool(world_forge or generation),
        "status": str(generation.get("status") or "unknown"),
        "stage": str(generation.get("stage") or "unknown"),
        "launch_ready": generation.get("launch_ready") is True,
        "percent": int(
            generation.get("percent")
            or (round(completed / total * 100) if total else 0)
        ),
        "completed_jobs": completed,
        "total_jobs": total,
        "jobs": jobs,
        "topic_graph": dict(world_forge.get("topic_graph") or {}),
        "audit": dict(world_forge.get("audit") or {}),
        "compilation": dict(world_forge.get("compilation") or {}),
        "campaign_bible_revision": int(
            runtime.get("campaign_bible_revision") or 0
        ),
        "campaign_bible_content_hash": str(
            runtime.get("campaign_bible_content_hash") or ""
        ),
        "background_expansion": expansion,
    }


def campaign_lore_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    discovery = _discovery(session)
    visible: list[dict[str, Any]] = []
    hidden_count = 0
    for document in _documents(session):
        status = _status(document, discovery)
        visibility = str(document.get("visibility") or "")
        if (
            status not in _PLAYER_VISIBLE_STATUSES
            or visibility not in _PLAYER_VISIBLE_VISIBILITY
        ):
            hidden_count += 1
            continue
        visible.append(
            _safe_document(
                document,
                status=status,
                include_full_text=False,
            )
        )
    visible.sort(key=lambda row: (row["category"], row["title"]))
    categories: dict[str, list[dict[str, Any]]] = {}
    for document in visible:
        categories.setdefault(str(document["category"]), []).append(document)
    state = _mapping(session.get("state"))
    bible = _mapping(state.get("campaign_bible"))
    dossiers: dict[str, list[dict[str, Any]]] = {
        "characters": [],
        "locations": [],
        "factions": [],
    }
    dossier_cards: dict[str, list[dict[str, Any]]] = {
        "characters": [],
        "locations": [],
        "factions": [],
    }
    topic_cards: dict[str, list[dict[str, Any]]] = {}
    dossier_key = {"npc": "characters", "location": "locations", "faction": "factions"}
    visible_documents = [
        document
        for document in _documents(session)
        if _status(document, discovery) in _PLAYER_VISIBLE_STATUSES
        and str(document.get("visibility") or "") in _PLAYER_VISIBLE_VISIBILITY
    ]
    for index, entity in enumerate(_entities(session)):
        kind = str(entity.get("kind") or "entity")
        key = dossier_key.get(kind)
        status = _entity_status(entity, discovery)
        if status not in _PLAYER_VISIBLE_STATUSES:
            continue
        safe = _safe_dossier(entity, status=status)
        topic_id = canonical_lore_topic_id(kind)
        safe["lore_topic_id"] = topic_id
        card = entity_card(
            safe,
            card_type=topic_id,
            kind=kind,
            index=index,
            content={"documents": visible_documents},
        )
        topic_cards.setdefault(topic_id, []).append(card)
        if key is not None:
            dossiers[key].append(safe)
            dossier_cards[key].append(card)
    for rows in dossiers.values():
        rows.sort(key=lambda row: str(row.get("name") or ""))
    for rows in dossier_cards.values():
        rows.sort(key=lambda row: str(row.get("title") or ""))
    for rows in topic_cards.values():
        rows.sort(key=lambda row: str(row.get("title") or ""))
    return {
        "ok": True,
        "canon_revision": int(bible.get("canon_revision") or 0),
        "content_hash": str(bible.get("content_hash") or ""),
        "categories": [
            {"label": label, "documents": documents}
            for label, documents in categories.items()
        ],
        "documents": visible,
        "visible_count": len(visible),
        "hidden_count": hidden_count,
        "dossiers": dossiers,
        "dossier_cards": dossier_cards,
        "topic_cards": topic_cards,
        "discoveries": list(discovery.get("discoveries") or ()),
        "generation": campaign_genesis_progress_payload(session),
    }


def campaign_lore_document_payload(
    session: Mapping[str, Any],
    document_id: str,
) -> dict[str, Any]:
    document_id = str(document_id or "").strip()
    document = next(
        (
            row
            for row in _documents(session)
            if str(row.get("document_id") or "") == document_id
        ),
        None,
    )
    if document is None:
        raise LoreDocumentNotFound(document_id)
    discovery = _discovery(session)
    status = _status(document, discovery)
    visibility = str(document.get("visibility") or "")
    if (
        status not in _PLAYER_VISIBLE_STATUSES
        or visibility not in _PLAYER_VISIBLE_VISIBILITY
    ):
        raise LoreDocumentForbidden(document_id)
    return {
        "ok": True,
        "document": _safe_document(
            document,
            status=status,
            include_full_text=True,
        ),
    }


def transition_lore_discovery(
    session: dict[str, Any],
    *,
    document_id: str,
    status: str,
    source: str = "gameplay",
) -> dict[str, Any]:
    normalized = str(status or "").strip().casefold()
    if normalized not in _ALLOWED_DISCOVERY_TRANSITIONS:
        raise ValueError(f"unsupported lore discovery status: {status}")
    document = next(
        (
            row
            for row in _documents(session)
            if str(row.get("document_id") or "") == document_id
        ),
        None,
    )
    if document is None:
        raise LoreDocumentNotFound(document_id)
    visibility = str(document.get("visibility") or "")
    if visibility not in _PLAYER_VISIBLE_VISIBILITY:
        raise LoreDocumentForbidden(document_id)
    projection = _mapping(session.get("campaign_bible_projection"))
    discovery = _discovery(session)
    pages = _mapping(discovery.get("pages"))
    pages[document_id] = normalized
    discoveries = list(discovery.get("discoveries") or ())
    discoveries.append(
        {
            "document_id": document_id,
            "status": normalized,
            "source": str(source or "gameplay"),
        }
    )
    discovery["pages"] = pages
    discovery["discoveries"] = discoveries[-500:]
    projection["discovery_state"] = discovery
    session["campaign_bible_projection"] = projection
    state = _mapping(session.get("state"))
    bible = _mapping(state.get("campaign_bible"))
    bible["discovery_state"] = discovery
    state["campaign_bible"] = bible
    session["state"] = state
    return session
