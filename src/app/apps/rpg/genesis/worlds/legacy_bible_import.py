"""Import a persisted Campaign Bible into immutable reusable-world resources."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .service import (
    compile_scenario_revision,
    compile_world_release,
    compile_world_revision,
)

LEGACY_BIBLE_IMPORT_VERSION = "rpg_legacy_bible_import_v1"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")
_ROUTE_KINDS = {"route", "travel", "portal", "road", "path"}


def _safe(value: str) -> str:
    return _SAFE_ID.sub("-", value).strip("-") or "campaign"


def legacy_import_ids(campaign_id: str) -> tuple[str, str]:
    safe_campaign = _safe(campaign_id)
    return (
        f"world:legacy-bible:{safe_campaign}",
        f"scenario:legacy-bible:{safe_campaign}",
    )


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [
            {"id": str(key), **dict(row)}
            for key, row in value.items()
            if isinstance(row, Mapping)
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def _entities(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _rows(document.get("entities"))


def _entity_map(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(entity["id"]): entity
        for entity in _entities(document)
        if entity.get("id")
    }


def _topology(document: Mapping[str, Any]) -> dict[str, Any]:
    entities = _entities(document)
    relationships = _rows(document.get("relationships"))
    return {
        "schema_version": "rpg_world_topology_v1",
        "locations": sorted(
            str(entity.get("id") or "")
            for entity in entities
            if str(entity.get("kind") or "") == "location" and entity.get("id")
        ),
        "routes": [
            relationship
            for relationship in relationships
            if str(
                relationship.get("kind")
                or relationship.get("type")
                or ""
            ).casefold()
            in _ROUTE_KINDS
        ],
    }


def _blueprint_requirements(document: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "location_id": str(entity["id"]),
            "map_id": str(entity.get("map_id") or f"map:{entity['id']}"),
            "simulation_readiness": "semantic",
            "presentation_readiness": "placeholder",
            "legacy_import": True,
        }
        for entity in sorted(
            _entities(document),
            key=lambda row: str(row.get("id") or ""),
        )
        if entity.get("id") and str(entity.get("kind") or "") == "location"
    )


def _completeness(bible: Mapping[str, Any]) -> dict[str, Any]:
    value = bible.get("completeness")
    if isinstance(value, Mapping) and value:
        return dict(value)
    document = bible.get("document")
    if isinstance(document, Mapping) and isinstance(
        document.get("completeness"),
        Mapping,
    ):
        return dict(document["completeness"])
    return {}


def _starting_location(
    document: Mapping[str, Any],
    completeness: Mapping[str, Any],
) -> str:
    opening = [
        str(item)
        for item in completeness.get("opening_location_ids") or ()
        if item
    ]
    if opening:
        return sorted(opening)[0]
    locations = [
        str(entity.get("id") or "")
        for entity in _entities(document)
        if entity.get("id") and str(entity.get("kind") or "") == "location"
    ]
    return sorted(locations)[0] if locations else "location:legacy-unresolved"


def _opening_npcs(
    document: Mapping[str, Any],
    completeness: Mapping[str, Any],
) -> tuple[str, ...]:
    opening = tuple(
        sorted(
            str(item)
            for item in completeness.get("opening_actor_ids") or ()
            if item
        )
    )
    if opening:
        return opening
    return tuple(
        sorted(
            str(entity.get("id") or "")
            for entity in _entities(document)
            if entity.get("id") and str(entity.get("kind") or "") == "npc"
        )
    )


def _story_threads(document: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(_rows(document.get("story_threads")))


def _source_hash(value: str) -> str:
    return value if value.startswith("sha256:") else f"sha256:{value}"


def _campaign_seed(campaign: Mapping[str, Any]) -> int:
    try:
        return int(str(campaign.get("seed") or "0"))
    except ValueError:
        return 0


def _existing_import(
    work: Any,
    context: Any,
    *,
    campaign_id: str,
    world_id: str,
    scenario_id: str,
    bible_hash: str,
) -> dict[str, Any] | None:
    world = work.world_scenarios.get_world(context, world_id)
    if world is None:
        return None
    revision = work.world_scenarios.get_world_revision(context, world_id, 1)
    release = work.world_scenarios.get_world_release(context, world_id, 1, 1)
    scenario_revision = work.world_scenarios.get_scenario_revision(
        context,
        scenario_id,
        1,
    )
    if revision is None or release is None or scenario_revision is None:
        raise ValueError(f"legacy_bible_import_partial_conflict:{campaign_id}")
    provenance = dict(revision["document"].get("provenance") or {})
    imported = dict(provenance.get("legacy_campaign_bible_import") or {})
    if (
        str(imported.get("source_campaign_id") or "") != campaign_id
        or str(imported.get("source_bible_hash") or "") != bible_hash
    ):
        raise ValueError(f"legacy_bible_import_identity_conflict:{campaign_id}")
    return {
        "ok": True,
        "status": "imported",
        "reused": True,
        "source_campaign_id": campaign_id,
        "world": world,
        "world_revision": revision,
        "world_release": release,
        "scenario_revision": scenario_revision,
        "source_campaign_rebound": False,
    }


def import_campaign_bible_as_world(
    campaign_id: str,
    *,
    world_id: str | None = None,
    scenario_id: str | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    """Import the latest persisted Bible without mutating or rebinding its campaign."""

    default_world_id, default_scenario_id = legacy_import_ids(campaign_id)
    target_world_id = world_id or default_world_id
    target_scenario_id = scenario_id or default_scenario_id
    context = current_tenant()
    with unit_of_work(database) as work:
        campaign = work.rpg.get_campaign(context, campaign_id, for_update=True)
        if campaign is None:
            raise KeyError(f"campaign_not_found:{campaign_id}")
        bible = work.campaign_bibles.get(context, campaign_id, for_update=True)
        if bible is None:
            raise KeyError(f"campaign_bible_not_found:{campaign_id}")
        bible_hash = _source_hash(str(bible["content_hash"]))
        existing = _existing_import(
            work,
            context,
            campaign_id=campaign_id,
            world_id=target_world_id,
            scenario_id=target_scenario_id,
            bible_hash=bible_hash,
        )
        if existing is not None:
            work.rollback()
            return existing

        document = dict(bible["document"])
        completeness = _completeness(bible)
        consistency_report = dict(bible.get("consistency_report") or {})
        story_threads = _story_threads(document)
        starting_location_id = _starting_location(document, completeness)
        import_provenance = {
            "import_version": LEGACY_BIBLE_IMPORT_VERSION,
            "source_campaign_id": campaign_id,
            "source_campaign_title": str(campaign.get("title") or campaign_id),
            "source_campaign_revision": int(campaign.get("revision") or 0),
            "source_campaign_state_hash": str(campaign.get("state_hash") or ""),
            "source_bible_revision": int(bible["revision"]),
            "source_bible_hash": bible_hash,
            "source_bible_provenance": dict(bible.get("provenance") or {}),
            "source_bible_created_at": str(bible.get("created_at") or ""),
            "source_bible_updated_at": str(bible.get("updated_at") or ""),
        }
        world_revision = compile_world_revision(
            world_id=target_world_id,
            revision=1,
            title=str(campaign.get("title") or campaign_id),
            canon=document,
            entity_manifest={
                "schema_version": "rpg_world_entity_manifest_v1",
                "entities": _entity_map(document),
                "manifest": dict(document.get("manifest") or {}),
            },
            topology=_topology(document),
            adventure_seeds=story_threads,
            blueprint_requirements=_blueprint_requirements(document),
            provenance={"legacy_campaign_bible_import": import_provenance},
        )
        release = compile_world_release(
            world_revision,
            release=1,
            indexes=dict(document.get("indexes") or {}),
            compiler_provenance={
                "compiler": LEGACY_BIBLE_IMPORT_VERSION,
                "source_bible_hash": bible_hash,
            },
            certification={
                "schema_version": "rpg_legacy_world_release_certification_v1",
                "launch_ready": False,
                "missing_requirements": ["legacy_map_compilation_required"],
                "source_campaign_id": campaign_id,
                "source_bible_revision": int(bible["revision"]),
                "source_bible_hash": bible_hash,
                "completeness": completeness,
                "consistency_report": consistency_report,
            },
        )
        scenario = compile_scenario_revision(
            scenario_id=target_scenario_id,
            revision=1,
            world_revision=world_revision,
            compatible_release=None,
            starting_epoch=str(document.get("starting_epoch") or "legacy_import"),
            starting_location_id=starting_location_id,
            initial_npc_ids=_opening_npcs(document, completeness),
            opening_seed_ids=tuple(
                str(row.get("id") or "")
                for row in story_threads
                if row.get("id")
            ),
            starting_resources={
                "legacy_campaign_id": campaign_id,
                "legacy_campaign_state_hash": str(campaign.get("state_hash") or ""),
            },
        )

        stored_world = work.world_scenarios.create_world(
            context,
            world_id=target_world_id,
            title=world_revision.title,
            description="Imported from a persisted legacy Campaign Bible.",
            source_mode="imported",
            genre=str(
                campaign.get("metadata", {}).get("genre") or "classic_fantasy"
            ),
            tone=str(
                campaign.get("metadata", {}).get("tone") or "legacy campaign"
            ),
            seed=_campaign_seed(campaign),
            metadata={"legacy_campaign_bible_import": import_provenance},
        )
        stored_revision = work.world_scenarios.publish_world_revision(
            context,
            world_id=target_world_id,
            document=world_revision.model_dump(mode="json"),
            content_hash=world_revision.content_hash,
            expected_revision=0,
        )
        stored_release = work.world_scenarios.publish_world_release(
            context,
            world_id=target_world_id,
            world_revision=1,
            document=release.model_dump(mode="json"),
            release_hash=release.release_hash,
        )
        work.world_scenarios.create_scenario(
            context,
            scenario_id=target_scenario_id,
            world_id=target_world_id,
            title=f"{world_revision.title} Legacy Opening",
            description="Imported opening state from a persisted Campaign Bible.",
            metadata={"legacy_campaign_bible_import": import_provenance},
        )
        stored_scenario = work.world_scenarios.publish_scenario_revision(
            context,
            scenario_id=target_scenario_id,
            world_id=target_world_id,
            world_revision=1,
            document=scenario.model_dump(mode="json"),
            content_hash=scenario.content_hash,
        )
        work.commit()
    return {
        "ok": True,
        "status": "imported",
        "reused": False,
        "source_campaign_id": campaign_id,
        "world": stored_world,
        "world_revision": stored_revision,
        "world_release": stored_release,
        "scenario_revision": stored_scenario,
        "source_campaign_rebound": False,
    }
