"""Merge ready authored map blueprints into immutable world publications."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .contracts import WorldReleaseDocument, WorldRevisionDocument
from .service import compile_world_release, compile_world_revision


def merge_authored_blueprints(
    world_revision: WorldRevisionDocument,
    world_release: WorldReleaseDocument,
    requirements: Sequence[dict[str, Any]],
) -> tuple[WorldRevisionDocument, WorldReleaseDocument]:
    if not requirements:
        return world_revision, world_release
    by_map = {
        str(row.get("map_id") or ""): dict(row)
        for row in world_revision.blueprint_requirements
        if row.get("map_id")
    }
    for requirement in requirements:
        by_map[str(requirement["map_id"])] = dict(requirement)
    authored = [
        {
            "map_id": str(row["map_id"]),
            "blueprint_revision": int(row["blueprint_revision"]),
            "blueprint_hash": str(row["blueprint_hash"]),
            "semantic_interface_hash": str(row["semantic_interface_hash"]),
        }
        for row in requirements
    ]
    revision = compile_world_revision(
        world_id=world_revision.world_id,
        revision=world_revision.revision,
        title=world_revision.title,
        canon=world_revision.canon,
        entity_manifest=world_revision.entity_manifest,
        topology=world_revision.topology,
        adventure_seeds=world_revision.adventure_seeds,
        blueprint_requirements=(by_map[key] for key in sorted(by_map)),
        provenance={
            **dict(world_revision.provenance),
            "authored_map_blueprints": authored,
        },
    )
    release = compile_world_release(
        revision,
        release=world_release.release,
        map_bindings=world_release.map_bindings,
        indexes=world_release.indexes,
        asset_bindings=world_release.asset_bindings,
        compiler_provenance={
            **dict(world_release.compiler_provenance),
            "authored_map_blueprints": authored,
        },
        certification={
            **dict(world_release.certification),
            "authored_map_blueprint_count": len(authored),
        },
        artifact_stage=world_release.artifact_stage,
        runtime_seed=world_release.runtime_seed,
        materialization=world_release.materialization,
        playtest_report=world_release.playtest_report,
    )
    return revision, release
