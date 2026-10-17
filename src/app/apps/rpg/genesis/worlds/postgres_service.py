"""Transactional services for reusable RPG world and scenario resources."""
from __future__ import annotations

import re
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition

from .contracts import (
    CampaignWorldBinding,
    ScenarioProjectCreate,
    ScenarioRevisionDocument,
    WorldProjectCreate,
    WorldReleaseDocument,
    WorldRevisionDocument,
    canonical_content_hash,
)
from .generation_worker import kick_world_generation_worker
from .lifecycle_service import require_scenario_writable, require_world_writable
from .profile_generation_jobs import plan_world_profile_creation
from .revision_authorship import (
    prepare_direct_world_revision,
    require_release_authorship,
    require_revision_authorship,
)
from .semantic_validation import (
    WorldSemanticError,
    certify_world_release,
    validate_release_bindings,
    validate_scenario_against_release,
)

_HashedContract = TypeVar("_HashedContract", bound=BaseModel)


def _ensure_hash(document: _HashedContract, field: str) -> _HashedContract:
    current = str(getattr(document, field, "") or "")
    if current:
        return document
    payload = document.model_dump(mode="json")
    payload[field] = ""
    payload[field] = canonical_content_hash(payload)
    return type(document).model_validate(payload)


def _resource_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-")
    return (slug or "untitled")[:48]


def _allocate_resource_id(
    work: Any,
    context: Any,
    *,
    prefix: str,
    title: str,
    exists_sql: str,
) -> str:
    """Allocate a friendly, collision-safe technical id inside the write transaction."""

    slug = _resource_slug(title)
    for _ in range(16):
        candidate = f"{prefix}:{slug}:{uuid4().hex[:12]}"
        existing = work.connection.execute(
            exists_sql,
            (context.workspace_id, candidate),
        ).fetchone()
        if existing is None:
            return candidate
    raise RuntimeError(f"{prefix}_id_allocation_exhausted")


def _definitions_from_work(
    work: Any,
    context: Any,
    release: WorldReleaseDocument,
) -> dict[str, GridMapDefinition]:
    definitions: dict[str, GridMapDefinition] = {}
    for binding in release.map_bindings:
        row = work.map_instances.get_definition(
            context,
            binding.map_id,
            binding.definition_revision,
        )
        if row is None:
            continue
        definitions[binding.map_id] = GridMapDefinition.model_validate(row["document"])
    return definitions


def _world_revision_from_work(
    work: Any,
    context: Any,
    world_id: str,
    revision: int,
) -> WorldRevisionDocument:
    row = work.world_scenarios.get_world_revision(context, world_id, revision)
    if row is None:
        raise KeyError(f"world_revision_not_found:{world_id}:{revision}")
    return WorldRevisionDocument.model_validate(row["document"])


def create_world_project(
    request: WorldProjectCreate,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    profile_plan = None
    with unit_of_work(database) as work:
        world_id = str(request.world_id or "").strip()
        if world_id:
            if work.world_scenarios.get_world(context, world_id) is not None:
                raise ValueError(f"world_already_exists:{world_id}")
        else:
            world_id = _allocate_resource_id(
                work,
                context,
                prefix="world",
                title=request.title,
                exists_sql=(
                    "SELECT 1 FROM omnix_rpg_worlds "
                    "WHERE workspace_id = %s AND id = %s"
                ),
            )
        request_metadata = dict(request.metadata or {})
        profile_plan = plan_world_profile_creation(
            world_id=world_id,
            title=request.title,
            genre=request.genre,
            description=request.description,
            tone=request.tone,
            campaign_mode=str(
                request_metadata.get("campaign_mode") or "persistent_living_world"
            ),
            seed=request.seed,
        )
        metadata = {
            **request_metadata,
            "genre_profile_binding": dict(profile_plan.binding),
        }
        world = work.world_scenarios.create_world(
            context,
            world_id=world_id,
            title=request.title,
            description=request.description,
            source_mode=request.source_mode,
            genre=request.genre,
            tone=request.tone,
            seed=request.seed,
            metadata=metadata,
        )
        if profile_plan.job_payload is not None:
            work.jobs.create_job(context, dict(profile_plan.job_payload))
        work.commit()
    if profile_plan is not None and profile_plan.job_payload is not None:
        kick_world_generation_worker(
            database=database,
            provider_route=profile_plan.provider_route,
        )
    return world


def list_world_projects(
    *,
    database: Any | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    context = current_tenant()
    with unit_of_work(database) as work:
        worlds = work.world_scenarios.list_worlds(context, limit=limit)
        work.rollback()
    return worlds


def publish_world_revision(
    document: WorldRevisionDocument,
    *,
    expected_revision: int,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, document.world_id)
        document = prepare_direct_world_revision(world, document)
        document = _ensure_hash(document, "content_hash")
        stored = work.world_scenarios.publish_world_revision(
            context,
            world_id=document.world_id,
            document=document.model_dump(mode="json"),
            content_hash=document.content_hash,
            expected_revision=expected_revision,
        )
        work.commit()
    return stored


def publish_world_release(
    document: WorldReleaseDocument,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        require_world_writable(work, context, document.world_id)
        world_revision = _world_revision_from_work(
            work,
            context,
            document.world_id,
            document.world_revision,
        )
        authorship_receipt = require_revision_authorship(world_revision)
        next_row = work.connection.execute(
            "SELECT COALESCE(MAX(release), 0) + 1 FROM omnix_rpg_world_releases "
            "WHERE workspace_id = %s AND world_id = %s AND world_revision = %s",
            (context.workspace_id, document.world_id, document.world_revision),
        ).fetchone()
        next_release = int(next_row[0])
        if document.release != next_release:
            raise WorldSemanticError(
                "world_release_sequence_mismatch",
                f"expected={next_release}:received={document.release}",
            )
        release_payload = document.model_dump(mode="json")
        release_payload["certification"] = {
            **dict(document.certification),
            "authorship_validated": True,
            "authorship_source": str(authorship_receipt.get("source") or "field_origin_ledger"),
            "authorship_schema_version": str(authorship_receipt.get("schema_version") or ""),
        }
        document = WorldReleaseDocument.model_validate(release_payload)
        definitions = _definitions_from_work(work, context, document)
        certified = certify_world_release(world_revision, document, definitions)
        stored = work.world_scenarios.publish_world_release(
            context,
            world_id=certified.world_id,
            world_revision=certified.world_revision,
            document=certified.model_dump(mode="json"),
            release_hash=certified.release_hash,
        )
        work.commit()
    return stored


def create_scenario_project(
    request: ScenarioProjectCreate,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        require_world_writable(work, context, request.world_id)
        scenario_id = str(request.scenario_id or "").strip()
        if scenario_id:
            existing = work.connection.execute(
                "SELECT 1 FROM omnix_rpg_scenarios "
                "WHERE workspace_id = %s AND id = %s",
                (context.workspace_id, scenario_id),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"scenario_already_exists:{scenario_id}")
        else:
            scenario_id = _allocate_resource_id(
                work,
                context,
                prefix="scenario",
                title=request.title,
                exists_sql=(
                    "SELECT 1 FROM omnix_rpg_scenarios "
                    "WHERE workspace_id = %s AND id = %s"
                ),
            )
        scenario = work.world_scenarios.create_scenario(
            context,
            scenario_id=scenario_id,
            world_id=request.world_id,
            title=request.title,
            description=request.description,
            metadata=request.metadata,
        )
        work.commit()
    return scenario


def publish_scenario_revision(
    document: ScenarioRevisionDocument,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    document = _ensure_hash(document, "content_hash")
    context = current_tenant()
    with unit_of_work(database) as work:
        require_scenario_writable(work, context, document.scenario_id)
        next_row = work.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM omnix_rpg_scenario_revisions "
            "WHERE workspace_id = %s AND scenario_id = %s",
            (context.workspace_id, document.scenario_id),
        ).fetchone()
        next_revision = int(next_row[0])
        if document.revision != next_revision:
            raise WorldSemanticError(
                "scenario_revision_sequence_mismatch",
                f"expected={next_revision}:received={document.revision}",
            )
        world_revision = _world_revision_from_work(
            work,
            context,
            document.world_id,
            document.world_revision,
        )
        require_revision_authorship(world_revision)
        if document.world_revision_hash != world_revision.content_hash:
            raise WorldSemanticError("scenario_world_hash_mismatch")
        if document.compatible_release is not None:
            release_row = work.world_scenarios.get_world_release(
                context,
                document.world_id,
                document.world_revision,
                document.compatible_release,
            )
            if release_row is None:
                raise KeyError(
                    "world_release_not_found:"
                    f"{document.world_id}:{document.world_revision}:"
                    f"{document.compatible_release}"
                )
            release = WorldReleaseDocument.model_validate(release_row["document"])
            require_release_authorship(world_revision, release)
            definitions = _definitions_from_work(work, context, release)
            validate_release_bindings(world_revision, release, definitions)
            validate_scenario_against_release(document, release, definitions)
        stored = work.world_scenarios.publish_scenario_revision(
            context,
            scenario_id=document.scenario_id,
            world_id=document.world_id,
            world_revision=document.world_revision,
            document=document.model_dump(mode="json"),
            content_hash=document.content_hash,
        )
        work.commit()
    return stored


def bind_campaign_world(
    binding: CampaignWorldBinding,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        require_scenario_writable(work, context, binding.scenario_id)
        world_revision = _world_revision_from_work(
            work,
            context,
            binding.world_id,
            binding.world_revision,
        )
        require_revision_authorship(world_revision)
        release_row = work.world_scenarios.get_world_release(
            context,
            binding.world_id,
            binding.world_revision,
            binding.world_release,
        )
        if release_row is None:
            raise KeyError(
                f"world_release_not_found:{binding.world_id}:"
                f"{binding.world_revision}:{binding.world_release}"
            )
        release = WorldReleaseDocument.model_validate(release_row["document"])
        require_release_authorship(world_revision, release)
        stored = work.world_scenarios.bind_campaign(
            context,
            campaign_id=binding.campaign_id,
            world_id=binding.world_id,
            world_revision=binding.world_revision,
            world_release=binding.world_release,
            scenario_id=binding.scenario_id,
            scenario_revision=binding.scenario_revision,
            binding=binding.model_dump(mode="json"),
        )
        work.commit()
    return stored


def read_campaign_world_binding(
    campaign_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any] | None:
    context = current_tenant()
    with unit_of_work(database) as work:
        binding = work.world_scenarios.get_campaign_binding(context, campaign_id)
        work.rollback()
    return binding


def load_release_definitions(
    world_revision: WorldRevisionDocument,
    release: WorldReleaseDocument,
    *,
    database: Any | None = None,
) -> dict[str, GridMapDefinition]:
    require_release_authorship(world_revision, release)
    context = current_tenant()
    with unit_of_work(database) as work:
        definitions = _definitions_from_work(work, context, release)
        work.rollback()
    validate_release_bindings(world_revision, release, definitions)
    return definitions


def load_published_resources(
    *,
    world_id: str,
    world_revision: int,
    world_release: int,
    scenario_id: str,
    scenario_revision: int,
    database: Any | None = None,
) -> tuple[WorldRevisionDocument, WorldReleaseDocument, ScenarioRevisionDocument]:
    context = current_tenant()
    with unit_of_work(database) as work:
        revision = work.world_scenarios.get_world_revision(
            context, world_id, world_revision
        )
        release = work.world_scenarios.get_world_release(
            context, world_id, world_revision, world_release
        )
        scenario = work.world_scenarios.get_scenario_revision(
            context, scenario_id, scenario_revision
        )
        work.rollback()
    if revision is None:
        raise KeyError(f"world_revision_not_found:{world_id}:{world_revision}")
    if release is None:
        raise KeyError(
            f"world_release_not_found:{world_id}:{world_revision}:{world_release}"
        )
    if scenario is None:
        raise KeyError(
            f"scenario_revision_not_found:{scenario_id}:{scenario_revision}"
        )
    revision_document = WorldRevisionDocument.model_validate(revision["document"])
    release_document = WorldReleaseDocument.model_validate(release["document"])
    scenario_document = ScenarioRevisionDocument.model_validate(scenario["document"])
    require_release_authorship(revision_document, release_document)
    return revision_document, release_document, scenario_document
