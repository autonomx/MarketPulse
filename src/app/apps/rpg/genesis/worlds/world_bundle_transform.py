"""Deterministic ID remapping and hash rebuilding for imported world bundles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, with_grid_definition_hashes

from .contracts import (
    ScenarioRevisionDocument,
    WorldReleaseDocument,
    WorldRevisionDocument,
    canonical_content_hash,
)
from .generation_jobs import canonical_hash
from .map_blueprint_authoring import MapBlueprintDocument
from .semantic_validation import certify_world_release
from .world_bundle import (
    WorldBundlePayload,
    discover_image_asset_ids,
    replace_identifiers,
    safe_bundle_segment,
)


@dataclass(frozen=True)
class TransformedWorldBundle:
    payload: WorldBundlePayload
    identifier_map: dict[str, str]
    asset_id_map: dict[str, str]
    run_id_map: dict[str, str]


def _portable_id(
    kind: str,
    source_id: str,
    target_world_id: str,
    bundle_sha256: str,
    occupied: set[str],
) -> str:
    prefix = {
        "map": "map",
        "scenario": "scenario",
        "asset": "image:world-import",
        "run": "world-run:import",
    }[kind]
    base = f"{prefix}:{safe_bundle_segment(target_world_id)}:{safe_bundle_segment(source_id)}"
    candidate = base
    if candidate in occupied:
        candidate = f"{base}:{bundle_sha256[:12]}"
    index = 2
    while candidate in occupied:
        candidate = f"{base}:{bundle_sha256[:12]}:{index}"
        index += 1
    occupied.add(candidate)
    return candidate


def _mapping(
    source_ids: Iterable[str],
    *,
    kind: str,
    target_world_id: str,
    source_world_id: str,
    bundle_sha256: str,
    occupied: set[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for source_id in sorted({str(value) for value in source_ids if str(value)}):
        if target_world_id == source_world_id and source_id not in occupied:
            occupied.add(source_id)
            result[source_id] = source_id
        else:
            result[source_id] = _portable_id(
                kind,
                source_id,
                target_world_id,
                bundle_sha256,
                occupied,
            )
    return result


def _with_hash(document: Any, field: str) -> Any:
    payload = document.model_dump(mode="json")
    payload[field] = ""
    payload[field] = canonical_content_hash(payload)
    return type(document).model_validate(payload)


def _import_metadata(
    value: Mapping[str, Any] | None,
    *,
    source_world_id: str,
    target_world_id: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    result = dict(value or {})
    result["world_bundle_import"] = {
        "source_world_id": source_world_id,
        "target_world_id": target_world_id,
        "bundle_sha256": bundle_sha256,
        "format_version": 1,
    }
    return result


def _source_ids(payload: WorldBundlePayload) -> dict[str, set[str]]:
    return {
        "map": {
            str(row.get("map_id") or "")
            for row in (*payload.map_blueprints, *payload.map_definitions)
            if str(row.get("map_id") or "")
        },
        "scenario": {
            str(row.get("id") or "")
            for row in payload.scenarios
            if str(row.get("id") or "")
        }
        | {
            str(row.get("scenario_id") or "")
            for row in payload.scenario_revisions
            if str(row.get("scenario_id") or "")
        },
        "asset": discover_image_asset_ids(payload.model_dump(mode="json")),
        "run": {
            str(row.get("run_id") or "")
            for row in payload.generation_runs
            if str(row.get("run_id") or "")
        },
    }


def _transform_blueprints(
    payload: WorldBundlePayload,
    replacements: Mapping[str, str],
    map_id_map: Mapping[str, str],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], tuple[str, str]]]:
    rows: list[dict[str, Any]] = []
    hashes: dict[tuple[str, int], tuple[str, str]] = {}
    for row in payload.map_blueprints:
        source_map_id = str(row.get("map_id") or "")
        revision = int(row.get("blueprint_revision") or 0)
        raw = replace_identifiers(dict(row.get("document") or {}), replacements)
        raw["map_id"] = map_id_map.get(source_map_id, source_map_id)
        document = MapBlueprintDocument.model_validate(raw)
        content_hash = canonical_content_hash(document)
        semantic_hash = canonical_content_hash(document.semantic_interface())
        hashes[(document.map_id, revision)] = (content_hash, semantic_hash)
        rows.append(
            {
                **replace_identifiers(dict(row), replacements),
                "map_id": document.map_id,
                "blueprint_revision": revision,
                "document": document.model_dump(mode="json"),
                "content_hash": content_hash,
                "semantic_interface_hash": semantic_hash,
                "findings": [],
                "status": "ready",
            }
        )
    return rows, hashes


def _transform_world_revisions(
    payload: WorldBundlePayload,
    replacements: Mapping[str, str],
    target_world_id: str,
    blueprint_hashes: Mapping[tuple[str, int], tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[int, WorldRevisionDocument]]:
    rows: list[dict[str, Any]] = []
    models: dict[int, WorldRevisionDocument] = {}
    for row in payload.world_revisions:
        revision = int(row.get("revision") or 0)
        raw = replace_identifiers(dict(row.get("document") or {}), replacements)
        raw["world_id"] = target_world_id
        raw["revision"] = revision
        requirements = []
        for requirement in list(raw.get("blueprint_requirements") or []):
            item = dict(requirement)
            key = (
                str(item.get("map_id") or ""),
                int(item.get("blueprint_revision") or 0),
            )
            hashes = blueprint_hashes.get(key)
            if hashes:
                item["blueprint_hash"], item["semantic_interface_hash"] = hashes
            requirements.append(item)
        raw["blueprint_requirements"] = requirements
        raw["content_hash"] = ""
        document = _with_hash(WorldRevisionDocument.model_validate(raw), "content_hash")
        models[revision] = document
        rows.append(
            {
                **replace_identifiers(dict(row), replacements),
                "revision": revision,
                "document": document.model_dump(mode="json"),
                "content_hash": document.content_hash,
            }
        )
    return rows, models


def _transform_definitions(
    payload: WorldBundlePayload,
    replacements: Mapping[str, str],
    target_world_id: str,
    map_id_map: Mapping[str, str],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], GridMapDefinition]]:
    rows: list[dict[str, Any]] = []
    models: dict[tuple[str, int], GridMapDefinition] = {}
    for row in payload.map_definitions:
        source_map_id = str(row.get("map_id") or "")
        definition_revision = int(row.get("definition_revision") or 0)
        raw = replace_identifiers(dict(row.get("document") or {}), replacements)
        raw.update(
            {
                "map_id": map_id_map.get(source_map_id, source_map_id),
                "world_id": target_world_id,
                "definition_revision": definition_revision,
                "world_revision": int(row.get("world_revision") or 0),
                "definition_hash": "",
                "semantic_interface_hash": "",
            }
        )
        definition = with_grid_definition_hashes(GridMapDefinition.model_validate(raw))
        models[(definition.map_id, definition.definition_revision)] = definition
        rows.append(
            {
                **replace_identifiers(dict(row), replacements),
                "map_id": definition.map_id,
                "definition_revision": definition.definition_revision,
                "world_revision": definition.world_revision,
                "document": definition.model_dump(mode="json"),
                "definition_hash": definition.definition_hash,
                "semantic_interface_hash": definition.semantic_interface_hash,
            }
        )
    return rows, models


def _transform_releases(
    payload: WorldBundlePayload,
    replacements: Mapping[str, str],
    target_world_id: str,
    world_revisions: Mapping[int, WorldRevisionDocument],
    definitions: Mapping[tuple[str, int], GridMapDefinition],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in payload.world_releases:
        world_revision = int(row.get("world_revision") or 0)
        release_number = int(row.get("release") or 0)
        revision_document = world_revisions[world_revision]
        raw = replace_identifiers(dict(row.get("document") or {}), replacements)
        raw.update(
            {
                "world_id": target_world_id,
                "world_revision": world_revision,
                "release": release_number,
                "world_revision_hash": revision_document.content_hash,
                "release_hash": "",
            }
        )
        bindings: list[dict[str, Any]] = []
        pinned: dict[str, GridMapDefinition] = {}
        for binding in list(raw.get("map_bindings") or []):
            item = dict(binding)
            key = (
                str(item.get("map_id") or ""),
                int(item.get("definition_revision") or 0),
            )
            definition = definitions.get(key)
            if definition is None:
                raise ValueError(
                    "world_bundle_release_definition_missing:"
                    f"{key[0]}:{key[1]}"
                )
            item["definition_hash"] = definition.definition_hash
            item["semantic_interface_hash"] = definition.semantic_interface_hash
            bindings.append(item)
            pinned[definition.map_id] = definition
        raw["map_bindings"] = bindings
        release = certify_world_release(
            revision_document,
            WorldReleaseDocument.model_validate(raw),
            pinned,
        )
        rows.append(
            {
                **replace_identifiers(dict(row), replacements),
                "world_revision": world_revision,
                "release": release_number,
                "document": release.model_dump(mode="json"),
                "release_hash": release.release_hash,
            }
        )
    return rows


def _transform_scenarios(
    payload: WorldBundlePayload,
    replacements: Mapping[str, str],
    scenario_id_map: Mapping[str, str],
    target_world_id: str,
    source_world_id: str,
    bundle_sha256: str,
    world_revisions: Mapping[int, WorldRevisionDocument],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    projects = tuple(
        {
            **replace_identifiers(dict(row), replacements),
            "id": scenario_id_map.get(
                str(row.get("id") or ""),
                str(row.get("id") or ""),
            ),
            "metadata": _import_metadata(
                replace_identifiers(dict(row.get("metadata") or {}), replacements),
                source_world_id=source_world_id,
                target_world_id=target_world_id,
                bundle_sha256=bundle_sha256,
            ),
        }
        for row in payload.scenarios
    )
    revisions: list[dict[str, Any]] = []
    for row in payload.scenario_revisions:
        revision = int(row.get("revision") or 0)
        source_scenario_id = str(row.get("scenario_id") or "")
        world_revision = int(row.get("world_revision") or 0)
        raw = replace_identifiers(dict(row.get("document") or {}), replacements)
        raw.update(
            {
                "scenario_id": scenario_id_map.get(
                    source_scenario_id,
                    source_scenario_id,
                ),
                "revision": revision,
                "world_id": target_world_id,
                "world_revision": world_revision,
                "world_revision_hash": world_revisions[world_revision].content_hash,
                "content_hash": "",
            }
        )
        document = _with_hash(
            ScenarioRevisionDocument.model_validate(raw),
            "content_hash",
        )
        revisions.append(
            {
                **replace_identifiers(dict(row), replacements),
                "scenario_id": document.scenario_id,
                "revision": revision,
                "world_revision": world_revision,
                "document": document.model_dump(mode="json"),
                "content_hash": document.content_hash,
            }
        )
    return projects, tuple(revisions)


def _transform_topic(
    row: Mapping[str, Any],
    replacements: Mapping[str, str],
    *,
    source_world_id: str,
    target_world_id: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    result = replace_identifiers(dict(row), replacements)
    content = dict(result.get("content") or {})
    directives = dict(result.get("directives") or {})
    result["content_hash"] = canonical_hash(content)
    result["input_hash"] = canonical_hash(
        {
            "topic_id": str(result.get("topic_id") or ""),
            "content": content,
            "directives": directives,
        }
    )
    result["provenance"] = _import_metadata(
        dict(result.get("provenance") or {}),
        source_world_id=source_world_id,
        target_world_id=target_world_id,
        bundle_sha256=bundle_sha256,
    )
    return result


def _transform_run(
    row: Mapping[str, Any],
    replacements: Mapping[str, str],
    run_id_map: Mapping[str, str],
    *,
    source_world_id: str,
    target_world_id: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    result = replace_identifiers(dict(row), replacements)
    source_run_id = str(row.get("run_id") or "")
    result["run_id"] = run_id_map.get(source_run_id, source_run_id)
    parent = row.get("parent_run_id")
    result["parent_run_id"] = (
        run_id_map.get(str(parent), str(parent)) if parent else None
    )
    if str(result.get("status") or "") in {"planned", "running"}:
        result["status"] = "canceled"
        result["completed_at"] = result.get("updated_at") or result.get("created_at")
    lineage = dict(result.get("lineage") or {})
    lineage["world_bundle_import"] = {
        "source_world_id": source_world_id,
        "target_world_id": target_world_id,
        "bundle_sha256": bundle_sha256,
        "execution_jobs_restored": False,
    }
    result["lineage"] = lineage
    return result


def transform_world_bundle(
    payload: WorldBundlePayload,
    *,
    target_world_id: str,
    bundle_sha256: str,
    existing_scenario_ids: Iterable[str] = (),
    existing_map_ids: Iterable[str] = (),
    existing_asset_ids: Iterable[str] = (),
    existing_run_ids: Iterable[str] = (),
) -> TransformedWorldBundle:
    source_world_id = str(payload.world.get("id") or "")
    if not source_world_id or not target_world_id.strip():
        raise ValueError("world_bundle_world_id_required")
    target_world_id = target_world_id.strip()
    source_ids = _source_ids(payload)
    map_id_map = _mapping(
        source_ids["map"],
        kind="map",
        target_world_id=target_world_id,
        source_world_id=source_world_id,
        bundle_sha256=bundle_sha256,
        occupied=set(existing_map_ids),
    )
    scenario_id_map = _mapping(
        source_ids["scenario"],
        kind="scenario",
        target_world_id=target_world_id,
        source_world_id=source_world_id,
        bundle_sha256=bundle_sha256,
        occupied=set(existing_scenario_ids),
    )
    asset_id_map = _mapping(
        source_ids["asset"],
        kind="asset",
        target_world_id=target_world_id,
        source_world_id=source_world_id,
        bundle_sha256=bundle_sha256,
        occupied=set(existing_asset_ids),
    )
    run_id_map = _mapping(
        source_ids["run"],
        kind="run",
        target_world_id=target_world_id,
        source_world_id="",
        bundle_sha256=bundle_sha256,
        occupied=set(existing_run_ids),
    )
    replacements = {
        source_world_id: target_world_id,
        **map_id_map,
        **scenario_id_map,
        **asset_id_map,
        **run_id_map,
    }
    blueprints, blueprint_hashes = _transform_blueprints(
        payload,
        replacements,
        map_id_map,
    )
    revision_rows, revision_models = _transform_world_revisions(
        payload,
        replacements,
        target_world_id,
        blueprint_hashes,
    )
    definition_rows, definition_models = _transform_definitions(
        payload,
        replacements,
        target_world_id,
        map_id_map,
    )
    release_rows = _transform_releases(
        payload,
        replacements,
        target_world_id,
        revision_models,
        definition_models,
    )
    scenario_rows, scenario_revision_rows = _transform_scenarios(
        payload,
        replacements,
        scenario_id_map,
        target_world_id,
        source_world_id,
        bundle_sha256,
        revision_models,
    )
    topics = tuple(
        _transform_topic(
            row,
            replacements,
            source_world_id=source_world_id,
            target_world_id=target_world_id,
            bundle_sha256=bundle_sha256,
        )
        for row in payload.topics
    )
    history = tuple(
        _transform_topic(
            row,
            replacements,
            source_world_id=source_world_id,
            target_world_id=target_world_id,
            bundle_sha256=bundle_sha256,
        )
        for row in payload.topic_history
    )
    runs = tuple(
        _transform_run(
            row,
            replacements,
            run_id_map,
            source_world_id=source_world_id,
            target_world_id=target_world_id,
            bundle_sha256=bundle_sha256,
        )
        for row in payload.generation_runs
    )
    world = replace_identifiers(dict(payload.world), replacements)
    world["id"] = target_world_id
    world["metadata"] = _import_metadata(
        replace_identifiers(dict(payload.world.get("metadata") or {}), replacements),
        source_world_id=source_world_id,
        target_world_id=target_world_id,
        bundle_sha256=bundle_sha256,
    )
    return TransformedWorldBundle(
        payload=WorldBundlePayload(
            world=world,
            topics=topics,
            topic_history=history,
            generation_runs=runs,
            map_blueprints=tuple(blueprints),
            world_revisions=tuple(revision_rows),
            map_definitions=tuple(definition_rows),
            world_releases=tuple(release_rows),
            scenarios=scenario_rows,
            scenario_revisions=scenario_revision_rows,
        ),
        identifier_map=replacements,
        asset_id_map=asset_id_map,
        run_id_map=run_id_map,
    )
