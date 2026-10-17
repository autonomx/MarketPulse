"""Rebuild nested runtime artifacts after world-release identifier remapping."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import (
    WorldArtifactStage,
    WorldReleaseDocument,
    WorldRevisionDocument,
    canonical_content_hash,
)
from .runtime_seed import (
    RuntimeSeedDocument,
    VerticalSliceMaterializationDocument,
    run_player_absent_playtest,
)


def _hashed_model(model_type: type[Any], payload: Mapping[str, Any]) -> Any:
    value = dict(payload)
    value["content_hash"] = ""
    value["content_hash"] = canonical_content_hash(value)
    return model_type.model_validate(value)


def _semantic_canon(canon: Mapping[str, Any]) -> dict[str, Any]:
    """Remove field-origin metadata from the simulation-facing canon hash.

    Authorship ledgers attest how prose was created; they are not world facts and must
    not change runtime seed identity when otherwise identical canon is compiled or
    remapped.
    """

    payload = dict(canon)
    provenance = payload.get("provenance")
    if isinstance(provenance, Mapping):
        semantic_provenance = dict(provenance)
        semantic_provenance.pop("authorship", None)
        if semantic_provenance:
            payload["provenance"] = semantic_provenance
        else:
            payload.pop("provenance", None)
    return payload


def _stage(
    *,
    runtime_seed_passed: bool,
    materialization_passed: bool,
    playtest_passed: bool,
) -> WorldArtifactStage:
    if playtest_passed:
        return "playtested"
    if materialization_passed:
        return "materialized"
    if runtime_seed_passed:
        return "runtime_seeded"
    return "canon_validated"


def refresh_release_runtime_artifacts(
    world_revision: WorldRevisionDocument,
    release: WorldReleaseDocument,
) -> WorldReleaseDocument:
    """Rehash transformed artifacts and rerun deterministic playtest evidence.

    World-bundle cloning remaps IDs inside all release fields. The outer release hash
    is rebuilt by certification, but nested runtime/materialization/playtest hashes
    must also be regenerated from the remapped payload.
    """

    if not release.runtime_seed:
        return release

    runtime_payload = dict(release.runtime_seed)
    runtime_payload.update(
        {
            "world_id": world_revision.world_id,
            "world_revision": world_revision.revision,
            "source_canon_hash": canonical_content_hash(
                _semantic_canon(world_revision.canon)
            ),
        }
    )
    runtime_seed = _hashed_model(RuntimeSeedDocument, runtime_payload)

    materialization_payload = dict(release.materialization)
    if materialization_payload:
        materialization_payload.update(
            {
                "world_id": world_revision.world_id,
                "world_revision": world_revision.revision,
                "runtime_seed_hash": runtime_seed.content_hash,
            }
        )
        materialization = _hashed_model(
            VerticalSliceMaterializationDocument,
            materialization_payload,
        )
    else:
        materialization = None

    previous_playtest = dict(release.playtest_report)
    playtest = (
        run_player_absent_playtest(
            runtime_seed,
            days=int(previous_playtest.get("days_simulated") or 7),
        )
        if previous_playtest
        else None
    )
    stage = _stage(
        runtime_seed_passed=runtime_seed.passed,
        materialization_passed=bool(materialization and materialization.passed),
        playtest_passed=bool(playtest and playtest.passed),
    )

    certification = dict(release.certification)
    readiness = {
        "canon_validated": True,
        "runtime_seeded": runtime_seed.passed,
        "materialized": bool(materialization and materialization.passed),
        "playtested": bool(playtest and playtest.passed),
        "highest_stage": stage,
    }
    certification.update(
        {
            "artifact_readiness": readiness,
            "runtime_seed_hash": runtime_seed.content_hash,
            "materialization_hash": (
                materialization.content_hash if materialization is not None else ""
            ),
            "playtest_report_hash": playtest.content_hash if playtest is not None else "",
        }
    )
    missing = [
        str(item)
        for item in certification.get("missing_requirements") or ()
        if str(item)
        not in {
            "runtime_seed",
            "vertical_slice_materialization",
            "player_absent_playtest",
        }
    ]
    if not runtime_seed.passed:
        missing.append("runtime_seed")
    if materialization is not None and not materialization.passed:
        missing.append("vertical_slice_materialization")
    if playtest is not None and not playtest.passed:
        missing.append("player_absent_playtest")
    certification["missing_requirements"] = list(dict.fromkeys(missing))
    certification["launch_ready"] = bool(certification.get("launch_ready")) and not missing

    payload = release.model_dump(mode="json")
    payload.update(
        {
            "world_id": world_revision.world_id,
            "world_revision": world_revision.revision,
            "world_revision_hash": world_revision.content_hash,
            "artifact_stage": stage,
            "runtime_seed": runtime_seed.model_dump(mode="json"),
            "materialization": (
                materialization.model_dump(mode="json")
                if materialization is not None
                else {}
            ),
            "playtest_report": (
                playtest.model_dump(mode="json") if playtest is not None else {}
            ),
            "certification": certification,
            "release_hash": "",
        }
    )
    payload["release_hash"] = canonical_content_hash(payload)
    return WorldReleaseDocument.model_validate(payload)
