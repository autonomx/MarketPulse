"""Gateway routes for reusable RPG worlds, releases, and scenarios."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Literal, Mapping

from fastapi import HTTPException, Query, Request
from pydantic import ValidationError

from app.apps.rpg.genesis.worlds.authorship_audit import (
    audit_world_authorship,
    remediate_world_authorship,
)
from app.apps.rpg.genesis.worlds.contracts import (
    CampaignWorldBinding,
    MapDefinitionBinding,
    MapInitializationOperation,
    ScenarioProjectCreate,
    ScenarioRevisionDocument,
    WorldProjectCreate,
    WorldReleaseDocument,
    WorldRevisionDocument,
    WorldArtifactStage,
)
from app.apps.rpg.genesis.worlds.legacy_bible_import import import_campaign_bible_as_world
from app.apps.rpg.genesis.worlds.lifecycle_service import (
    archive_scenario_project,
    archive_world_project,
    restore_scenario_project,
    restore_world_project,
)
from app.apps.rpg.genesis.worlds.postgres_service import (
    bind_campaign_world,
    create_scenario_project,
    create_world_project,
    list_world_projects,
    publish_scenario_revision,
    publish_world_release,
    publish_world_revision,
    read_campaign_world_binding,
)
from app.apps.rpg.genesis.worlds.topic_history import (
    list_world_topic_history,
    restore_world_topic_draft,
)

from .rpg_world_library_routes import register_rpg_world_library_routes

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict, Field as _typed_field
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.route_contracts import (
    RpgCampaignWorldBindingResponse,
    RpgLegacyWorldImportResponse,
    RpgScenarioLifecycleResponse,
    RpgScenarioResponse,
    RpgScenarioRevisionResponse,
    RpgWorldAuthorshipAuditResponse,
    RpgWorldAuthorshipRemediationResponse,
    RpgWorldDraftRestoreResponse,
    RpgWorldLifecycleResponse,
    RpgWorldListResponse,
    RpgWorldReleaseResponse,
    RpgWorldResponse,
    RpgWorldRevisionResponse,
    RpgWorldTopicHistoryResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgCreateWorldRequestBody(_TypedRequestModel):
    contract_version: Literal["rpg_world_contract_v1"] = "rpg_world_contract_v1"
    world_id: str | None = None
    title: str = _typed_field(min_length=1)
    description: str = ""
    source_mode: Literal["manual", "ai", "hybrid", "imported"] = "manual"
    genre: str = "classic_fantasy"
    tone: str = "heroic adventure"
    seed: int = 0
    metadata: dict[str, Any] = _typed_field(default_factory=dict)

class RpgRemediateWorldAuthorshipRequestBody(_TypedRequestModel):
    queue_regeneration: bool | None = True

class RpgRestoreWorldTopicDraftRequestBody(_TypedRequestModel):
    expected_current_draft_revision: _TypedRequestAny = None

class RpgPublishWorldRevisionRequestBody(_TypedRequestModel):
    contract_version: Literal["rpg_world_contract_v1"] = "rpg_world_contract_v1"
    revision: int = _typed_field(ge=1)
    title: str = _typed_field(min_length=1)
    canon: dict[str, Any] = _typed_field(default_factory=dict)
    entity_manifest: dict[str, Any] = _typed_field(default_factory=dict)
    topology: dict[str, Any] = _typed_field(default_factory=dict)
    adventure_seeds: tuple[dict[str, Any], ...] = ()
    blueprint_requirements: tuple[dict[str, Any], ...] = ()
    provenance: dict[str, Any] = _typed_field(default_factory=dict)
    content_hash: str = ""

class RpgPublishWorldReleaseRequestBody(_TypedRequestModel):
    release: int = _typed_field(ge=1)
    world_revision_hash: str = _typed_field(pattern=r"^sha256:")
    map_bindings: tuple[MapDefinitionBinding, ...] = ()
    indexes: dict[str, Any] = _typed_field(default_factory=dict)
    asset_bindings: dict[str, Any] = _typed_field(default_factory=dict)
    compiler_provenance: dict[str, Any] = _typed_field(default_factory=dict)
    certification: dict[str, Any] = _typed_field(default_factory=dict)
    artifact_stage: WorldArtifactStage = "canon_validated"
    runtime_seed: dict[str, Any] = _typed_field(default_factory=dict)
    materialization: dict[str, Any] = _typed_field(default_factory=dict)
    playtest_report: dict[str, Any] = _typed_field(default_factory=dict)
    release_hash: str = ""

class RpgCreateScenarioRequestBody(_TypedRequestModel):
    contract_version: Literal["rpg_scenario_contract_v1"] = "rpg_scenario_contract_v1"
    scenario_id: str | None = None
    world_id: str = _typed_field(min_length=1)
    title: str = _typed_field(min_length=1)
    description: str = ""
    metadata: dict[str, Any] = _typed_field(default_factory=dict)

class RpgPublishScenarioRevisionRequestBody(_TypedRequestModel):
    contract_version: Literal["rpg_scenario_contract_v1"] = "rpg_scenario_contract_v1"
    revision: int = _typed_field(ge=1)
    world_id: str = _typed_field(min_length=1)
    world_revision: int = _typed_field(ge=1)
    world_revision_hash: str = _typed_field(pattern=r"^sha256:")
    compatible_release: int | None = _typed_field(default=None, ge=1)
    starting_epoch: str = ""
    starting_location_id: str = _typed_field(min_length=1)
    activated_conflict_ids: tuple[str, ...] = ()
    initial_npc_ids: tuple[str, ...] = ()
    protagonist_options: tuple[dict[str, Any], ...] = ()
    starting_resources: dict[str, Any] = _typed_field(default_factory=dict)
    opening_seed_ids: tuple[str, ...] = ()
    map_initialization: tuple[MapInitializationOperation, ...] = ()
    runtime_seed_hash: str = ""
    content_hash: str = ""

class RpgImportLegacyCampaignBibleRequestBody(_TypedRequestModel):
    scenario_id: _TypedRequestAny = None
    world_id: _TypedRequestAny = None

class RpgBindCampaignWorldRequestBody(_TypedRequestModel):
    world_id: str = _typed_field(min_length=1)
    world_revision: int = _typed_field(ge=1)
    world_revision_hash: str = _typed_field(pattern=r"^sha256:")
    world_release: int = _typed_field(ge=1)


_ROUTE_SENTINEL = "_omnix_rpg_world_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _validation_error(exc: ValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.errors())


def _domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=404,
            detail={"ok": False, "error": str(exc).strip("'")},
        )
    return HTTPException(
        status_code=409,
        detail={"ok": False, "error": str(exc)},
    )


def register_rpg_world_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/worlds",
        tags=["rpg-world"],
        responses=documented(RpgWorldListResponse),
    )
    def rpg_list_worlds(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return {"ok": True, "worlds": list_world_projects(limit=limit)}

    @router.post(
        "/api/rpg/worlds",
        tags=["rpg-world"],
        responses=documented(RpgWorldResponse),
    )
    def rpg_create_world(request: Request, request_body: RpgCreateWorldRequestBody) -> dict[str, Any]:
        try:
            contract = WorldProjectCreate.model_validate(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
            return {"ok": True, "world": create_world_project(contract)}
        except ValidationError as exc:
            raise _validation_error(exc) from exc

    @router.post(
        "/api/rpg/worlds/{world_id}/archive",
        tags=["rpg-world"],
        responses=documented(RpgWorldLifecycleResponse),
    )
    def rpg_archive_world(world_id: str) -> dict[str, Any]:
        try:
            return archive_world_project(world_id)
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/worlds/{world_id}/restore",
        tags=["rpg-world"],
        responses=documented(RpgWorldLifecycleResponse),
    )
    def rpg_restore_world(world_id: str) -> dict[str, Any]:
        try:
            return restore_world_project(world_id)
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.get(
        "/api/rpg/worlds/{world_id}/authorship-audit",
        tags=["rpg-world"],
        responses=documented(RpgWorldAuthorshipAuditResponse),
    )
    def rpg_audit_world_authorship(world_id: str) -> dict[str, Any]:
        try:
            return audit_world_authorship(world_id)
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/worlds/{world_id}/authorship-audit/remediate",
        tags=["rpg-world"],
        responses=documented(RpgWorldAuthorshipRemediationResponse),
    )
    def rpg_remediate_world_authorship(
        world_id: str,
        request: Request, request_body: RpgRemediateWorldAuthorshipRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return remediate_world_authorship(
                world_id,
                queue_regeneration=bool(payload.get("queue_regeneration", True)),
            )
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.get(
        "/api/rpg/worlds/{world_id}/topic-history",
        tags=["rpg-world"],
        responses=documented(RpgWorldTopicHistoryResponse),
    )
    def rpg_world_topic_history(
        world_id: str,
        draft_revision: int | None = Query(default=None, ge=1),
        latest_per_topic: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                "history": list_world_topic_history(
                    world_id,
                    draft_revision=draft_revision,
                    latest_per_topic=latest_per_topic,
                ),
            }
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/worlds/{world_id}/drafts/{source_draft_revision}/restore",
        tags=["rpg-world"],
        responses=documented(RpgWorldDraftRestoreResponse),
    )
    def rpg_restore_world_topic_draft(
        world_id: str,
        source_draft_revision: int,
        request: Request, request_body: RpgRestoreWorldTopicDraftRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return restore_world_topic_draft(
                world_id,
                source_draft_revision=source_draft_revision,
                expected_current_draft_revision=int(
                    payload.get("expected_current_draft_revision") or 0
                ),
            )
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/worlds/{world_id}/revisions",
        tags=["rpg-world"],
        responses=documented(RpgWorldRevisionResponse),
    )
    def rpg_publish_world_revision(
        world_id: str,
        request: Request, request_body: RpgPublishWorldRevisionRequestBody,
    ) -> dict[str, Any]:
        raw = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        raw["world_id"] = world_id
        try:
            document = WorldRevisionDocument.model_validate(raw)
            expected_revision = max(0, document.revision - 1)
            stored = publish_world_revision(
                document,
                expected_revision=expected_revision,
            )
            return {"ok": True, "world_revision": stored}
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/worlds/{world_id}/revisions/{world_revision}/releases",
        tags=["rpg-world"],
        responses=documented(RpgWorldReleaseResponse),
    )
    def rpg_publish_world_release(
        world_id: str,
        world_revision: int,
        request: Request, request_body: RpgPublishWorldReleaseRequestBody,
    ) -> dict[str, Any]:
        raw = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        raw["world_id"] = world_id
        raw["world_revision"] = world_revision
        try:
            document = WorldReleaseDocument.model_validate(raw)
            stored = publish_world_release(document)
            return {"ok": True, "world_release": stored}
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/scenarios",
        tags=["rpg-world"],
        responses=documented(RpgScenarioResponse),
    )
    def rpg_create_scenario(request: Request, request_body: RpgCreateScenarioRequestBody) -> dict[str, Any]:
        try:
            contract = ScenarioProjectCreate.model_validate(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
            return {"ok": True, "scenario": create_scenario_project(contract)}
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/scenarios/{scenario_id}/archive",
        tags=["rpg-world"],
        responses=documented(RpgScenarioLifecycleResponse),
    )
    def rpg_archive_scenario(scenario_id: str) -> dict[str, Any]:
        try:
            return archive_scenario_project(scenario_id)
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/scenarios/{scenario_id}/restore",
        tags=["rpg-world"],
        responses=documented(RpgScenarioLifecycleResponse),
    )
    def rpg_restore_scenario(scenario_id: str) -> dict[str, Any]:
        try:
            return restore_scenario_project(scenario_id)
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/scenarios/{scenario_id}/revisions",
        tags=["rpg-world"],
        responses=documented(RpgScenarioRevisionResponse),
    )
    def rpg_publish_scenario_revision(
        scenario_id: str,
        request: Request, request_body: RpgPublishScenarioRevisionRequestBody,
    ) -> dict[str, Any]:
        raw = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        raw["scenario_id"] = scenario_id
        try:
            document = ScenarioRevisionDocument.model_validate(raw)
            stored = publish_scenario_revision(document)
            return {"ok": True, "scenario_revision": stored}
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/legacy-world-import",
        tags=["rpg-world"],
        responses=documented(RpgLegacyWorldImportResponse),
    )
    def rpg_import_legacy_campaign_bible(
        campaign_id: str,
        request: Request, request_body: RpgImportLegacyCampaignBibleRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return import_campaign_bible_as_world(
                campaign_id,
                world_id=str(payload.get("world_id") or "") or None,
                scenario_id=str(payload.get("scenario_id") or "") or None,
            )
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    @router.get(
        "/api/rpg/campaigns/{campaign_id}/world-binding",
        tags=["rpg-world"],
        responses=documented(RpgCampaignWorldBindingResponse),
    )
    def rpg_read_campaign_world_binding(campaign_id: str) -> dict[str, Any]:
        binding = read_campaign_world_binding(campaign_id)
        if binding is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error": "campaign_world_binding_not_found",
                    "campaign_id": campaign_id,
                },
            )
        return {"ok": True, "binding": binding}

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/world-binding",
        tags=["rpg-world"],
        responses=documented(RpgCampaignWorldBindingResponse),
    )
    def rpg_bind_campaign_world(
        campaign_id: str,
        request: Request, request_body: RpgBindCampaignWorldRequestBody,
    ) -> dict[str, Any]:
        raw = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        raw["campaign_id"] = campaign_id
        try:
            binding = CampaignWorldBinding.model_validate(raw)
            return {"ok": True, "binding": bind_campaign_world(binding)}
        except ValidationError as exc:
            raise _validation_error(exc) from exc
        except (KeyError, ValueError) as exc:
            raise _domain_error(exc) from exc

    register_rpg_world_library_routes(router, state)
