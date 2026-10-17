"""Read-only export of one reusable RPG world into a portable archive."""
from __future__ import annotations

from app.assets.protocol import AssetStore
from typing import Any

from app.assets.content import AssetContentUnavailable, read_asset_bytes
from app.assets import AssetRecord, AssetType, default_asset_store, iter_assets
from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .world_bundle import (
    WorldBundleArchive,
    WorldBundleAsset,
    WorldBundlePayload,
    asset_archive_path,
    build_world_bundle_archive,
    discover_image_asset_ids,
    sha256_hex,
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _world_payload(work: Any, context: Any, world_id: str) -> WorldBundlePayload:
    world = work.world_scenarios.get_world(context, world_id)
    if world is None:
        raise KeyError(f"world_not_found:{world_id}")

    topic_rows = work.connection.execute(
        "SELECT topic_id, draft_revision, source, status, content_jsonb, "
        "directives_jsonb, dependency_hashes_jsonb, input_hash, content_hash, "
        "provenance_jsonb, updated_at FROM omnix_rpg_world_topics "
        "WHERE workspace_id = %s AND world_id = %s ORDER BY topic_id",
        (context.workspace_id, world_id),
    ).fetchall()
    history_rows = work.connection.execute(
        "SELECT history_sequence, topic_id, draft_revision, source, status, "
        "content_jsonb, directives_jsonb, dependency_hashes_jsonb, input_hash, "
        "content_hash, provenance_jsonb, topic_updated_at, captured_at "
        "FROM omnix_rpg_world_topic_history WHERE workspace_id = %s "
        "AND world_id = %s ORDER BY history_sequence",
        (context.workspace_id, world_id),
    ).fetchall()
    run_rows = work.connection.execute(
        "SELECT run_id, draft_revision, status, graph_jsonb, context_jsonb, "
        "settings_jsonb, plan_jsonb, progress_jsonb, error_jsonb, parent_run_id, "
        "lineage_jsonb, created_at, updated_at, completed_at "
        "FROM omnix_rpg_world_generation_runs WHERE workspace_id = %s "
        "AND world_id = %s ORDER BY draft_revision, run_id",
        (context.workspace_id, world_id),
    ).fetchall()
    blueprint_rows = work.connection.execute(
        "SELECT map_id, blueprint_revision, document_jsonb, content_hash, "
        "semantic_interface_hash, status, findings_jsonb, created_at "
        "FROM omnix_rpg_map_blueprint_revisions WHERE workspace_id = %s "
        "AND world_id = %s ORDER BY map_id, blueprint_revision",
        (context.workspace_id, world_id),
    ).fetchall()
    revision_rows = work.connection.execute(
        "SELECT revision, document_jsonb, content_hash, created_at "
        "FROM omnix_rpg_world_revisions WHERE workspace_id = %s AND world_id = %s "
        "ORDER BY revision",
        (context.workspace_id, world_id),
    ).fetchall()
    definition_rows = work.connection.execute(
        "SELECT map_id, definition_revision, world_revision, document_jsonb, "
        "definition_hash, semantic_interface_hash, created_at "
        "FROM omnix_rpg_map_definitions WHERE workspace_id = %s AND world_id = %s "
        "ORDER BY world_revision, map_id, definition_revision",
        (context.workspace_id, world_id),
    ).fetchall()
    release_rows = work.connection.execute(
        "SELECT world_revision, release, document_jsonb, release_hash, created_at "
        "FROM omnix_rpg_world_releases WHERE workspace_id = %s AND world_id = %s "
        "ORDER BY world_revision, release",
        (context.workspace_id, world_id),
    ).fetchall()
    scenario_rows = work.connection.execute(
        "SELECT id, title, description, status, metadata_jsonb, created_at, updated_at "
        "FROM omnix_rpg_scenarios WHERE workspace_id = %s AND world_id = %s "
        "ORDER BY id",
        (context.workspace_id, world_id),
    ).fetchall()
    scenario_ids = [str(row[0]) for row in scenario_rows]
    scenario_revision_rows = []
    if scenario_ids:
        scenario_revision_rows = work.connection.execute(
            "SELECT scenario_id, revision, world_revision, document_jsonb, "
            "content_hash, created_at FROM omnix_rpg_scenario_revisions "
            "WHERE workspace_id = %s AND world_id = %s "
            "ORDER BY scenario_id, revision",
            (context.workspace_id, world_id),
        ).fetchall()

    return WorldBundlePayload(
        world={key: value for key, value in world.items() if key != "workspace_id"},
        topics=tuple(
            {
                "topic_id": str(row[0]),
                "draft_revision": int(row[1]),
                "source": str(row[2]),
                "status": str(row[3]),
                "content": dict(row[4]),
                "directives": dict(row[5]),
                "dependency_hashes": dict(row[6]),
                "input_hash": str(row[7]),
                "content_hash": str(row[8]),
                "provenance": dict(row[9]),
                "updated_at": _iso(row[10]),
            }
            for row in topic_rows
        ),
        topic_history=tuple(
            {
                "history_sequence": int(row[0]),
                "topic_id": str(row[1]),
                "draft_revision": int(row[2]),
                "source": str(row[3]),
                "status": str(row[4]),
                "content": dict(row[5]),
                "directives": dict(row[6]),
                "dependency_hashes": dict(row[7]),
                "input_hash": str(row[8]),
                "content_hash": str(row[9]),
                "provenance": dict(row[10]),
                "topic_updated_at": _iso(row[11]),
                "captured_at": _iso(row[12]),
            }
            for row in history_rows
        ),
        generation_runs=tuple(
            {
                "run_id": str(row[0]),
                "draft_revision": int(row[1]),
                "status": str(row[2]),
                "graph": dict(row[3]),
                "context": dict(row[4]),
                "settings": dict(row[5]),
                "plan": dict(row[6]),
                "progress": dict(row[7]),
                "error": dict(row[8]),
                "parent_run_id": str(row[9]) if row[9] is not None else None,
                "lineage": dict(row[10]),
                "created_at": _iso(row[11]),
                "updated_at": _iso(row[12]),
                "completed_at": _iso(row[13]),
            }
            for row in run_rows
        ),
        map_blueprints=tuple(
            {
                "map_id": str(row[0]),
                "blueprint_revision": int(row[1]),
                "document": dict(row[2]),
                "content_hash": str(row[3]),
                "semantic_interface_hash": str(row[4]),
                "status": str(row[5]),
                "findings": list(row[6]),
                "created_at": _iso(row[7]),
            }
            for row in blueprint_rows
        ),
        world_revisions=tuple(
            {
                "revision": int(row[0]),
                "document": dict(row[1]),
                "content_hash": str(row[2]),
                "created_at": _iso(row[3]),
            }
            for row in revision_rows
        ),
        map_definitions=tuple(
            {
                "map_id": str(row[0]),
                "definition_revision": int(row[1]),
                "world_revision": int(row[2]),
                "document": dict(row[3]),
                "definition_hash": str(row[4]),
                "semantic_interface_hash": str(row[5]),
                "created_at": _iso(row[6]),
            }
            for row in definition_rows
        ),
        world_releases=tuple(
            {
                "world_revision": int(row[0]),
                "release": int(row[1]),
                "document": dict(row[2]),
                "release_hash": str(row[3]),
                "created_at": _iso(row[4]),
            }
            for row in release_rows
        ),
        scenarios=tuple(
            {
                "id": str(row[0]),
                "title": str(row[1]),
                "description": str(row[2]),
                "status": str(row[3]),
                "metadata": dict(row[4]),
                "created_at": _iso(row[5]),
                "updated_at": _iso(row[6]),
            }
            for row in scenario_rows
        ),
        scenario_revisions=tuple(
            {
                "scenario_id": str(row[0]),
                "revision": int(row[1]),
                "world_revision": int(row[2]),
                "document": dict(row[3]),
                "content_hash": str(row[4]),
                "created_at": _iso(row[5]),
            }
            for row in scenario_revision_rows
        ),
    )


def _selected_image_assets(
    payload: WorldBundlePayload,
    store: AssetStore,
) -> list[AssetRecord]:
    values = payload.model_dump(mode="json")
    referenced_ids = discover_image_asset_ids(values)
    map_ids = {
        str(row.get("map_id") or "")
        for row in (*payload.map_blueprints, *payload.map_definitions)
        if str(row.get("map_id") or "")
    }
    world_id = str(payload.world.get("id") or "")
    assets_by_id = {
        asset.id: asset
        for asset in iter_assets(store, asset_type=AssetType.IMAGE.value)
    }
    selected_ids = set(referenced_ids)
    for asset in assets_by_id.values():
        metadata = dict(asset.metadata or {})
        if str(metadata.get("world_id") or metadata.get("source_world_id") or "") == world_id:
            selected_ids.add(asset.id)
        if str(metadata.get("map_id") or metadata.get("source_map_id") or "") in map_ids:
            selected_ids.add(asset.id)
    missing = sorted(asset_id for asset_id in referenced_ids if asset_id not in assets_by_id)
    if missing:
        raise ValueError("world_bundle_referenced_assets_missing:" + ",".join(missing))
    return [assets_by_id[asset_id] for asset_id in sorted(selected_ids) if asset_id in assets_by_id]


def _asset_rows(assets: list[AssetRecord]) -> list[tuple[WorldBundleAsset, bytes]]:
    rows: list[tuple[WorldBundleAsset, bytes]] = []
    for asset in assets:
        try:
            content = read_asset_bytes(asset)
        except AssetContentUnavailable:
            raise ValueError(f"world_bundle_asset_file_missing:{asset.id}") from None
        if not content:
            raise ValueError(f"world_bundle_asset_file_empty:{asset.id}")
        mime_type = str(asset.mime_type or "").lower()
        rows.append(
            (
                WorldBundleAsset(
                    asset_id=asset.id,
                    archive_path=asset_archive_path(asset.id, mime_type),
                    module=asset.module,
                    asset_type="image",
                    mime_type=mime_type,
                    byte_size=len(content),
                    checksum_sha256=sha256_hex(content),
                    source_job_id=asset.source_job_id,
                    metadata=dict(asset.metadata or {}),
                    compat=dict(asset.compat or {}),
                ),
                content,
            )
        )
    return rows


def export_world_bundle(
    world_id: str,
    *,
    database: Any | None = None,
    asset_store: AssetStore | None = None,
) -> WorldBundleArchive:
    context = current_tenant()
    with unit_of_work(database) as work:
        payload = _world_payload(work, context, world_id)
        work.rollback()
    store = asset_store or default_asset_store()
    assets = _selected_image_assets(payload, store)
    return build_world_bundle_archive(payload, _asset_rows(assets))
