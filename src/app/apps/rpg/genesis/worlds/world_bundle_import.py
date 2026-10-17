"""Validated import of portable RPG world archives into durable authoring state."""
from __future__ import annotations

import logging

from typing import Any, Iterable, Mapping, cast

from app.assets.content import asset_available, asset_checksum
from app.assets import AssetRecord, AssetType, SharedAssetStore, default_asset_store, iter_assets
from app.security.tenant_context import current_tenant
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition
from app.runtime.paths import resources_data_root

from .contracts import ScenarioRevisionDocument
from .map_blueprint_authoring import MapBlueprintDocument, reconcile_blueprint_scenarios
from .world_bundle import (
    ParsedWorldBundle,
    image_extension,
    parse_world_bundle_archive,
    replace_identifiers,
    safe_bundle_segment,
    sha256_hex,
)
from .world_bundle_transform import TransformedWorldBundle, transform_world_bundle

logger = logging.getLogger(__name__)


class WorldBundleImportConflict(ValueError):
    pass


def _target_asset_id(
    source_id: str,
    transformed: TransformedWorldBundle,
    parsed: ParsedWorldBundle,
    occupied: set[str],
) -> str:
    mapped = transformed.asset_id_map.get(source_id)
    if mapped:
        occupied.add(mapped)
        return mapped
    if source_id not in occupied:
        occupied.add(source_id)
        return source_id
    base = (
        f"image:world-import:{parsed.bundle_sha256[:12]}:"
        f"{safe_bundle_segment(source_id)}"
    )
    candidate = base
    index = 2
    while candidate in occupied:
        candidate = f"{base}:{index}"
        index += 1
    occupied.add(candidate)
    return candidate


def _prepare_assets(
    parsed: ParsedWorldBundle,
    transformed: TransformedWorldBundle,
    store: SharedAssetStore,
) -> tuple[list[AssetRecord], list[str], dict[str, str]]:
    existing = {asset.id: asset for asset in iter_assets(store)}
    occupied = set(existing)
    created: list[AssetRecord] = []
    reused: list[str] = []
    asset_map = dict(transformed.asset_id_map)
    import_root = (
        resources_data_root()
        / "assets"
        / "world-imports"
        / parsed.bundle_sha256[:16]
    )
    import_root.mkdir(parents=True, exist_ok=True)

    for descriptor in parsed.manifest.assets:
        source_id = descriptor.asset_id
        content = parsed.asset_bytes[source_id]
        target_id = _target_asset_id(
            source_id,
            transformed,
            parsed,
            occupied,
        )
        asset_map[source_id] = target_id
        current = existing.get(target_id)
        if current is not None:
            if asset_available(current) and asset_checksum(current) == descriptor.checksum_sha256:
                reused.append(target_id)
                continue
            raise WorldBundleImportConflict(f"world_bundle_asset_conflict:{target_id}")
        filename = safe_bundle_segment(target_id) + image_extension(descriptor.mime_type)
        destination = import_root / filename
        if destination.exists() and sha256_hex(destination.read_bytes()) != descriptor.checksum_sha256:
            raise WorldBundleImportConflict(
                f"world_bundle_asset_path_conflict:{destination.name}"
            )
        destination.write_bytes(content)
        metadata = replace_identifiers(
            dict(descriptor.metadata or {}),
            {**transformed.identifier_map, **asset_map},
        )
        metadata["world_bundle_import"] = {
            "source_asset_id": source_id,
            "source_world_id": parsed.manifest.source_world_id,
            "target_world_id": str(transformed.payload.world.get("id") or ""),
            "bundle_sha256": parsed.bundle_sha256,
        }
        compat = replace_identifiers(
            dict(descriptor.compat or {}),
            {**transformed.identifier_map, **asset_map},
        )
        if descriptor.source_job_id:
            compat["world_bundle_source_job_id"] = descriptor.source_job_id
        created.append(
            AssetRecord(
                id=target_id,
                module=descriptor.module,
                type=AssetType.IMAGE,
                mime_type=descriptor.mime_type,
                storage_path=str(destination),
                source_job_id=None,
                created_at=parsed.manifest.exported_at,
                metadata=metadata,
                compat=compat,
            )
        )
    return created, reused, asset_map


def _register_assets(store: SharedAssetStore, assets: Iterable[AssetRecord]) -> list[str]:
    installed: list[str] = []
    try:
        for asset in assets:
            store.upsert_asset(asset)
            installed.append(asset.id)
    except Exception:
        for asset_id in reversed(installed):
            store.delete_asset(asset_id)
        raise
    return installed


def _cleanup_assets(store: SharedAssetStore, asset_ids: Iterable[str]) -> None:
    for asset_id in reversed(list(asset_ids)):
        try:
            store.delete_asset(asset_id)
        except Exception:
            logger.debug("suppressed error in %s", "_cleanup_assets", exc_info=True)
            continue


def _existing_ids(work: Any, context: Any) -> dict[str, set[str]]:
    scenario_rows = work.connection.execute(
        "SELECT id FROM omnix_rpg_scenarios WHERE workspace_id = %s",
        (context.workspace_id,),
    ).fetchall()
    map_rows = work.connection.execute(
        "SELECT DISTINCT map_id FROM omnix_rpg_map_definitions WHERE workspace_id = %s",
        (context.workspace_id,),
    ).fetchall()
    run_rows = work.connection.execute(
        "SELECT run_id FROM omnix_rpg_world_generation_runs WHERE workspace_id = %s",
        (context.workspace_id,),
    ).fetchall()
    return {
        "scenario": {str(row[0]) for row in scenario_rows},
        "map": {str(row[0]) for row in map_rows},
        "run": {str(row[0]) for row in run_rows},
    }


def _timestamp(value: Any) -> Any:
    return value or None


def _insert_world(work: Any, context: Any, world: Mapping[str, Any]) -> None:
    work.connection.execute(
        "INSERT INTO omnix_rpg_worlds (workspace_id, id, title, description, status, "
        "source_mode, genre, tone, seed, draft_revision, metadata_jsonb, created_at, "
        "updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
        "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), "
        "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
        (
            context.workspace_id,
            world["id"],
            world["title"],
            str(world.get("description") or ""),
            str(world.get("status") or "draft"),
            str(world.get("source_mode") or "imported"),
            str(world.get("genre") or "classic_fantasy"),
            str(world.get("tone") or "heroic adventure"),
            int(world.get("seed") or 0),
            int(world.get("draft_revision") or 1),
            canonical_json(dict(world.get("metadata") or {})),
            _timestamp(world.get("created_at")),
            _timestamp(world.get("updated_at")),
        ),
    )


def _insert_topics(work: Any, context: Any, world_id: str, transformed: TransformedWorldBundle) -> None:
    for row in transformed.payload.topics:
        work.connection.execute(
            "INSERT INTO omnix_rpg_world_topics (workspace_id, world_id, topic_id, "
            "draft_revision, source, status, content_jsonb, directives_jsonb, "
            "dependency_hashes_jsonb, input_hash, content_hash, provenance_jsonb, "
            "updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, "
            "%s::jsonb, %s, %s, %s::jsonb, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
            (
                context.workspace_id,
                world_id,
                row["topic_id"],
                int(row["draft_revision"]),
                row["source"],
                row["status"],
                canonical_json(dict(row.get("content") or {})),
                canonical_json(dict(row.get("directives") or {})),
                canonical_json(dict(row.get("dependency_hashes") or {})),
                row.get("input_hash") or "",
                row.get("content_hash") or "",
                canonical_json(dict(row.get("provenance") or {})),
                _timestamp(row.get("updated_at")),
            ),
        )
    if transformed.payload.topic_history:
        work.connection.execute(
            "DELETE FROM omnix_rpg_world_topic_history WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        )
        for row in transformed.payload.topic_history:
            work.connection.execute(
                "INSERT INTO omnix_rpg_world_topic_history (workspace_id, world_id, "
                "topic_id, draft_revision, source, status, content_jsonb, directives_jsonb, "
                "dependency_hashes_jsonb, input_hash, content_hash, provenance_jsonb, "
                "topic_updated_at, captured_at) VALUES (%s, %s, %s, %s, %s, %s, "
                "%s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, "
                "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), "
                "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
                (
                    context.workspace_id,
                    world_id,
                    row["topic_id"],
                    int(row["draft_revision"]),
                    row["source"],
                    row["status"],
                    canonical_json(dict(row.get("content") or {})),
                    canonical_json(dict(row.get("directives") or {})),
                    canonical_json(dict(row.get("dependency_hashes") or {})),
                    row.get("input_hash") or "",
                    row.get("content_hash") or "",
                    canonical_json(dict(row.get("provenance") or {})),
                    _timestamp(row.get("topic_updated_at")),
                    _timestamp(row.get("captured_at")),
                ),
            )


def _insert_revisions_and_maps(
    work: Any,
    context: Any,
    world_id: str,
    transformed: TransformedWorldBundle,
) -> None:
    for row in transformed.payload.world_revisions:
        work.connection.execute(
            "INSERT INTO omnix_rpg_world_revisions (workspace_id, world_id, revision, "
            "document_jsonb, content_hash, created_at) VALUES (%s, %s, %s, %s::jsonb, "
            "%s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
            (
                context.workspace_id,
                world_id,
                int(row["revision"]),
                canonical_json(dict(row["document"])),
                row["content_hash"],
                _timestamp(row.get("created_at")),
            ),
        )
    for row in transformed.payload.map_definitions:
        definition = GridMapDefinition.model_validate(row["document"])
        work.map_instances.put_definition(
            context,
            map_id=definition.map_id,
            definition_revision=definition.definition_revision,
            world_id=world_id,
            world_revision=definition.world_revision,
            document=definition.model_dump(mode="json"),
            definition_hash=definition.definition_hash,
            semantic_interface_hash=definition.semantic_interface_hash,
        )
    for row in transformed.payload.world_releases:
        work.connection.execute(
            "INSERT INTO omnix_rpg_world_releases (workspace_id, world_id, world_revision, "
            "release, document_jsonb, release_hash, created_at) VALUES (%s, %s, %s, %s, "
            "%s::jsonb, %s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
            (
                context.workspace_id,
                world_id,
                int(row["world_revision"]),
                int(row["release"]),
                canonical_json(dict(row["document"])),
                row["release_hash"],
                _timestamp(row.get("created_at")),
            ),
        )


def _insert_scenarios_and_blueprints(
    work: Any,
    context: Any,
    world_id: str,
    transformed: TransformedWorldBundle,
) -> None:
    for row in transformed.payload.scenarios:
        work.connection.execute(
            "INSERT INTO omnix_rpg_scenarios (workspace_id, id, world_id, title, "
            "description, status, metadata_jsonb, created_at, updated_at) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s::jsonb, "
            "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), "
            "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
            (
                context.workspace_id,
                row["id"],
                world_id,
                row["title"],
                str(row.get("description") or ""),
                str(row.get("status") or "draft"),
                canonical_json(dict(row.get("metadata") or {})),
                _timestamp(row.get("created_at")),
                _timestamp(row.get("updated_at")),
            ),
        )
    scenario_documents: dict[str, list[ScenarioRevisionDocument]] = {}
    for row in transformed.payload.scenario_revisions:
        document = ScenarioRevisionDocument.model_validate(row["document"])
        scenario_documents.setdefault(document.scenario_id, []).append(document)
        work.connection.execute(
            "INSERT INTO omnix_rpg_scenario_revisions (workspace_id, scenario_id, "
            "revision, world_id, world_revision, document_jsonb, content_hash, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, "
            "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
            (
                context.workspace_id,
                document.scenario_id,
                document.revision,
                world_id,
                document.world_revision,
                canonical_json(document.model_dump(mode="json")),
                document.content_hash,
                _timestamp(row.get("created_at")),
            ),
        )
    latest_scenarios = [
        max(documents, key=lambda item: item.revision)
        for documents in scenario_documents.values()
    ]
    for row in transformed.payload.map_blueprints:
        blueprint_document = MapBlueprintDocument.model_validate(row["document"])
        findings = reconcile_blueprint_scenarios(blueprint_document, latest_scenarios)
        work.connection.execute(
            "INSERT INTO omnix_rpg_map_blueprint_revisions (workspace_id, world_id, "
            "map_id, blueprint_revision, document_jsonb, content_hash, "
            "semantic_interface_hash, status, findings_jsonb, created_at) VALUES "
            "(%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, "
            "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))",
            (
                context.workspace_id,
                world_id,
                blueprint_document.map_id,
                int(row["blueprint_revision"]),
                canonical_json(blueprint_document.model_dump(mode="json")),
                row["content_hash"],
                row["semantic_interface_hash"],
                "invalid" if findings else "ready",
                canonical_json(findings),
                _timestamp(row.get("created_at")),
            ),
        )


def _insert_generation_runs(
    work: Any,
    context: Any,
    world_id: str,
    transformed: TransformedWorldBundle,
) -> None:
    for row in transformed.payload.generation_runs:
        work.connection.execute(
            "INSERT INTO omnix_rpg_world_generation_runs (workspace_id, run_id, world_id, "
            "draft_revision, status, graph_jsonb, context_jsonb, settings_jsonb, "
            "plan_jsonb, progress_jsonb, error_jsonb, parent_run_id, lineage_jsonb, "
            "created_at, updated_at, completed_at) VALUES (%s, %s, %s, %s, %s, "
            "%s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, "
            "NULL, %s::jsonb, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), "
            "COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s::timestamptz)",
            (
                context.workspace_id,
                row["run_id"],
                world_id,
                int(row["draft_revision"]),
                row["status"],
                canonical_json(dict(row.get("graph") or {})),
                canonical_json(dict(row.get("context") or {})),
                canonical_json(dict(row.get("settings") or {})),
                canonical_json(dict(row.get("plan") or {})),
                canonical_json(dict(row.get("progress") or {})),
                canonical_json(dict(row.get("error") or {})),
                canonical_json(dict(row.get("lineage") or {})),
                _timestamp(row.get("created_at")),
                _timestamp(row.get("updated_at")),
                _timestamp(row.get("completed_at")),
            ),
        )
    for row in transformed.payload.generation_runs:
        parent = row.get("parent_run_id")
        if parent:
            work.connection.execute(
                "UPDATE omnix_rpg_world_generation_runs SET parent_run_id = %s "
                "WHERE workspace_id = %s AND run_id = %s",
                (parent, context.workspace_id, row["run_id"]),
            )


def import_world_bundle(
    content: bytes,
    *,
    target_world_id: str | None = None,
    database: Any | None = None,
    asset_store: SharedAssetStore | None = None,
) -> dict[str, Any]:
    parsed = parse_world_bundle_archive(content)
    target = (target_world_id or parsed.manifest.source_world_id).strip()
    if not target:
        raise ValueError("world_bundle_target_world_id_required")
    store = asset_store or default_asset_store()
    context = current_tenant()
    with unit_of_work(database) as work:
        if work.world_scenarios.get_world(context, target) is not None:
            work.rollback()
            raise WorldBundleImportConflict(f"world_bundle_target_exists:{target}")
        existing = _existing_ids(work, context)
        work.rollback()
    transformed = transform_world_bundle(
        parsed.payload,
        target_world_id=target,
        bundle_sha256=parsed.bundle_sha256,
        existing_scenario_ids=existing["scenario"],
        existing_map_ids=existing["map"],
        existing_asset_ids={asset.id for asset in iter_assets(store)},
        existing_run_ids=existing["run"],
    )
    created_assets, reused_assets, asset_map = _prepare_assets(parsed, transformed, cast(Any, store))
    installed_assets = _register_assets(cast(Any, store), created_assets)
    try:
        with unit_of_work(database) as work:
            if work.world_scenarios.get_world(context, target) is not None:
                raise WorldBundleImportConflict(f"world_bundle_target_exists:{target}")
            _insert_world(work, context, transformed.payload.world)
            _insert_topics(work, context, target, transformed)
            _insert_revisions_and_maps(work, context, target, transformed)
            _insert_scenarios_and_blueprints(work, context, target, transformed)
            _insert_generation_runs(work, context, target, transformed)
            work.commit()
    except Exception:
        _cleanup_assets(cast(Any, store), installed_assets)
        raise

    launch_preparation: dict[str, Any] = {"status": "not_required"}
    # A portable release is immediately playable as-is. Authoring-only bundles
    # need the same launch preparation as a newly forged world, after commit.
    if not transformed.payload.scenario_revisions:
        try:
            from .launch_repair_service import prepare_opening_scenarios_for_launch

            launch_preparation = prepare_opening_scenarios_for_launch(
                target,
                database=database,
            )
        except ValueError as exc:
            if str(exc) != "world_opening_scenarios_not_found":
                launch_preparation = {
                    "status": "recovery_required",
                    "error": str(exc),
                }
        except Exception as exc:  # Imported world is durable even if recovery is not.
            launch_preparation = {
                "status": "recovery_required",
                "error": str(exc),
            }

    return {
        "ok": True,
        "status": "imported",
        "world_id": target,
        "source_world_id": parsed.manifest.source_world_id,
        "bundle_sha256": parsed.bundle_sha256,
        "counts": {
            "topics": len(transformed.payload.topics),
            "topic_history": len(transformed.payload.topic_history),
            "generation_runs": len(transformed.payload.generation_runs),
            "map_blueprints": len(transformed.payload.map_blueprints),
            "world_revisions": len(transformed.payload.world_revisions),
            "map_definitions": len(transformed.payload.map_definitions),
            "world_releases": len(transformed.payload.world_releases),
            "scenarios": len(transformed.payload.scenarios),
            "scenario_revisions": len(transformed.payload.scenario_revisions),
            "images_created": len(installed_assets),
            "images_reused": len(reused_assets),
        },
        "identifier_map": {
            **transformed.identifier_map,
            **asset_map,
        },
        "warnings": [
            "Generation run history was restored without generic execution jobs; planned or running runs were imported as canceled."
        ] if transformed.payload.generation_runs else [],
        "launch_preparation": launch_preparation,
    }
