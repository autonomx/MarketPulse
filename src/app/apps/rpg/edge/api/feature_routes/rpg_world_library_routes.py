"""Hidden compatibility routes for the RPG Worlds & Campaigns library UI."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Query, Request
from pydantic import ValidationError

from app.persistence.config import DatabaseConfigurationError
from app.persistence.database import DatabaseUnavailableError
from app.apps.rpg.foundation.debug_logging import new_rpg_trace_id
from app.apps.rpg.genesis.worlds.generation_diagnostics import (
    log_world_generation_event,
    world_generation_log_hint,
)
from app.apps.rpg.genesis.worlds.generation_retry import (
    continue_world_generation,
    retry_failed_world_generation,
)
from app.apps.rpg.genesis.worlds.launch_repair_service import (
    prepare_opening_scenarios_for_launch,
    repair_world_for_launch,
)
from app.apps.rpg.genesis.worlds.library_service import (
    publish_world_library_generation,
    read_world_detail,
    read_world_generation,
    read_world_library,
    save_world_topic,
    start_world_library_generation,
)
from app.apps.rpg.genesis.worlds.map_blueprint_authoring import (
    MapBlueprintDocument,
    list_map_blueprints,
    materialize_missing_location_blueprints,
    save_map_blueprint,
)
from app.apps.rpg.genesis.worlds.published_launch import launch_published_scenario
from app.apps.rpg.genesis.worlds.starter_bubble import (
    build_starter_bubble,
    predictive_materialization_queue,
)
from app.apps.rpg.genesis.worlds.starter_bubble_service import promote_starter_bubble

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.session.service import SESSION_CAMPAIGN_SESSIONS
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgPublishedLaunchResponse,
    RpgStarterBubblePromotionResponse,
    RpgStarterBubbleResponse,
    RpgWorldDetailResponse,
    RpgWorldGenerationMutationResponse,
    RpgWorldLaunchRepairResponse,
    RpgWorldLibraryResponse,
)
from app.apps.rpg.edge.api.route_contracts import (
    RpgMapBlueprintListResponse,
    RpgMapBlueprintMaterializeResponse,
    RpgMapBlueprintResponse,
    RpgPrepareOpeningsResponse,
    RpgWorldGenerationDiagnosticsResponse,
    RpgWorldGenerationPublishResponse,
    RpgWorldGenerationStatusResponse,
    RpgWorldTopicResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgWorldSaveMapBlueprintRequestBody(_TypedRequestModel):
    document: _TypedRequestAny = None
    expected_revision: _TypedRequestAny = None

class RpgWorldSaveTopicRequestBody(_TypedRequestModel):
    content: _TypedRequestAny = None
    directives: _TypedRequestAny = None
    status: _TypedRequestAny = None
    topic_id: _TypedRequestAny = None

class RpgWorldStartGenerationRequestBody(_TypedRequestModel):
    background_expansion: bool | None = True
    depth: _TypedRequestAny = None
    directives: _TypedRequestAny = None
    entity_manifest: _TypedRequestAny = None
    generator_version: _TypedRequestAny = None
    model: _TypedRequestAny = None
    prompt_version: _TypedRequestAny = None
    provider_route: _TypedRequestAny = None
    replace_locked: bool | None = False
    scope: _TypedRequestAny = None
    starting_location: _TypedRequestAny = None
    strategy: _TypedRequestAny = None
    topic_directives: _TypedRequestAny = None

class RpgWorldRepairForLaunchRequestBody(_TypedRequestModel):
    scenario_id: _TypedRequestAny = None
    starting_location_id: _TypedRequestAny = None

class RpgWorldStarterBubblePromoteRequestBody(_TypedRequestModel):
    neighboring_location_id: _TypedRequestAny = None
    source_world_revision: _TypedRequestAny = None
    starting_location_id: _TypedRequestAny = None

class RpgWorldLaunchScenarioRequestBody(_TypedRequestModel):
    features: _TypedRequestAny = None
    gameplay: _TypedRequestAny = None
    player: _TypedRequestAny = None
    world_id: _TypedRequestAny = None
    world_release: _TypedRequestAny = None
    world_revision: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_world_library_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _raise_domain_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": str(exc).strip("'")},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "error": str(exc)},
        ) from exc
    raise exc


def _raise_generation_error(
    exc: Exception,
    *,
    operation: str,
    diagnostic_id: str,
    world_id: str | None = None,
    run_id: str | None = None,
) -> None:
    log_path = world_generation_log_hint()
    error_fields: dict[str, Any] = {"operation": operation, "log_path": log_path}
    if isinstance(exc, DatabaseUnavailableError):
        root: BaseException = exc
        seen: set[int] = set()
        while id(root) not in seen and (root.__cause__ or root.__context__):
            seen.add(id(root))
            root = root.__cause__ or root.__context__  # type: ignore[assignment]
        error_fields.update(
            {
                "database_sqlstate": exc.sqlstate or getattr(root, "sqlstate", None),
                "database_error_type": type(root).__name__,
                "database_error_message": str(root),
            }
        )
    log_world_generation_event(
        f"world_generation.{operation}_failed",
        level="error",
        diagnostic_id=diagnostic_id,
        world_id=world_id,
        run_id=run_id,
        fields=error_fields,
        error=exc,
    )
    detail = {
        "ok": False,
        "error": str(exc).strip("'") if isinstance(exc, KeyError) else str(exc),
        "diagnostic_id": diagnostic_id,
        "diagnostic_log": log_path,
    }
    if isinstance(exc, (DatabaseConfigurationError, DatabaseUnavailableError)):
        authentication_failed = (
            isinstance(exc, DatabaseUnavailableError) and exc.sqlstate == "28P01"
        )
        detail.update(
            {
                "error": (
                    "world_generation_database_authentication_failed"
                    if authentication_failed
                    else "world_generation_database_unavailable"
                ),
                "message": (
                    "PostgreSQL is reachable, but Omnix's database credential was rejected. "
                    "Refresh the protected credential and restart Omnix; completed topics "
                    "remain safe."
                    if authentication_failed
                    else "World generation could not reach PostgreSQL. No completed "
                    "topics were discarded; restore database connectivity and retry."
                ),
                "retryable": True,
            }
        )
        raise HTTPException(
            status_code=503,
            detail=detail,
            headers={"Retry-After": "5"},
        ) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=detail) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=detail) from exc
    detail["error"] = "world_generation_internal_error"
    raise HTTPException(status_code=500, detail=detail) from exc


def register_rpg_world_library_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get("/api/rpg/world-library", responses=documented(RpgWorldLibraryResponse))
    def rpg_world_library(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return read_world_library(limit=limit)

    @router.get("/api/rpg/worlds/{world_id}/library", responses=documented(RpgWorldDetailResponse))
    def rpg_world_detail(world_id: str) -> dict[str, Any]:
        try:
            return read_world_detail(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.get(
        "/api/rpg/worlds/{world_id}/map-blueprints",
        responses=documented(RpgMapBlueprintListResponse),
    )
    def rpg_world_map_blueprints(
        world_id: str,
        latest_only: bool = Query(default=True),
    ) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                "map_blueprints": list_map_blueprints(
                    world_id,
                    latest_only=latest_only,
                ),
            }
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/map-blueprints/materialize",
        responses=documented(RpgMapBlueprintMaterializeResponse),
    )
    def rpg_world_materialize_map_blueprints(world_id: str) -> dict[str, Any]:
        try:
            return materialize_missing_location_blueprints(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/map-blueprints/{map_id}",
        responses=documented(RpgMapBlueprintResponse),
    )
    def rpg_world_save_map_blueprint(
        world_id: str,
        map_id: str,
        request: Request, request_body: RpgWorldSaveMapBlueprintRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        document_payload = payload.get("document")
        if not isinstance(document_payload, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "map_blueprint_document_required"},
            )
        raw = dict(document_payload)
        raw["map_id"] = map_id
        try:
            document = MapBlueprintDocument.model_validate(raw)
            return save_map_blueprint(
                world_id,
                document,
                expected_revision=int(payload.get("expected_revision") or 0),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post("/api/rpg/worlds/{world_id}/topics", responses=documented(RpgWorldTopicResponse))
    def rpg_world_save_topic(
        world_id: str,
        request: Request, request_body: RpgWorldSaveTopicRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        topic_id = str(payload.get("topic_id") or "").strip()
        content = payload.get("content")
        if not topic_id or not isinstance(content, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "topic_id_and_content_required"},
            )
        status = str(payload.get("status") or "ready")
        if status not in {"draft", "ready", "stale", "failed"}:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "invalid_topic_status"},
            )
        try:
            return save_world_topic(
                world_id,
                topic_id=topic_id,
                content=content,
                directives=(
                    raw_directives if isinstance(raw_directives := payload.get("directives"), Mapping) else {}
                ),
                status=status,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post("/api/rpg/worlds/{world_id}/generation", responses=documented(RpgWorldGenerationMutationResponse))
    def rpg_world_start_generation(
        world_id: str,
        request: Request, request_body: RpgWorldStartGenerationRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        diagnostic_id = new_rpg_trace_id("world-generation")
        scope = raw_scope if isinstance(raw_scope := payload.get("scope"), Mapping) else {}
        log_world_generation_event(
            "world_generation.start_requested",
            diagnostic_id=diagnostic_id,
            world_id=world_id,
            fields={
                "depth": payload.get("depth") or "standard",
                "scope_mode": dict(scope).get("mode") or "full",
                "selected_topic_ids": dict(scope).get("topic_ids") or [],
                "strategy": payload.get("strategy") or "reuse_unchanged",
                "provider_route": payload.get("provider_route") or "configured",
                "model": payload.get("model") or "configured",
                "background_expansion": bool(payload.get("background_expansion", True)),
            },
        )
        try:
            result = start_world_library_generation(
                world_id,
                depth=str(payload.get("depth") or "standard"),
                starting_location=str(payload.get("starting_location") or ""),
                background_expansion=bool(payload.get("background_expansion", True)),
                topic_directives=(
                    raw_topic_directives if isinstance(raw_topic_directives := payload.get("topic_directives"), Mapping) else raw_directives if isinstance(raw_directives := payload.get("directives"), Mapping) else {}
                ),
                entity_manifest=(
                    raw_entity_manifest if isinstance(raw_entity_manifest := payload.get("entity_manifest"), Mapping) else {}
                ),
                scope=scope,
                strategy=str(payload.get("strategy") or "reuse_unchanged"),
                replace_locked=bool(payload.get("replace_locked", False)),
                generator_version=str(
                    payload.get("generator_version") or "world-generator-v1"
                ),
                prompt_version=str(
                    payload.get("prompt_version") or "world-prompt-v1"
                ),
                provider_route=str(payload.get("provider_route") or "configured"),
                model=str(payload.get("model") or "configured"),
            )
            run = dict(result.get("run") or {})
            log_world_generation_event(
                "world_generation.start_succeeded",
                diagnostic_id=diagnostic_id,
                world_id=world_id,
                run_id=str(run.get("run_id") or ""),
                fields={
                    "status": run.get("status"),
                    "worker_started": result.get("worker_started"),
                    "target_topic_ids": dict(result.get("scope") or {}).get("resolved_topic_ids") or [],
                },
            )
            return {**result, "diagnostic_id": diagnostic_id, "diagnostic_log": world_generation_log_hint()}
        except Exception as exc:
            _raise_generation_error(
                exc,
                operation="start",
                diagnostic_id=diagnostic_id,
                world_id=world_id,
            )
            raise

    @router.get("/api/rpg/world-generation/diagnostics", responses=documented(RpgWorldGenerationDiagnosticsResponse))
    def rpg_world_generation_diagnostics() -> dict[str, Any]:
        return {
            "ok": True,
            "path": world_generation_log_hint(),
            "format": "jsonl",
            "contains_generated_content": False,
        }

    @router.post(
        "/api/rpg/world-generation/{run_id}/retry-failed",
        responses=documented(RpgWorldGenerationMutationResponse),
    )
    def rpg_world_retry_failed_generation(run_id: str) -> dict[str, Any]:
        diagnostic_id = new_rpg_trace_id("world-generation-retry")
        try:
            result = retry_failed_world_generation(
                run_id,
                diagnostic_id=diagnostic_id,
            )
            return {**result, "diagnostic_log": world_generation_log_hint()}
        except Exception as exc:
            _raise_generation_error(
                exc,
                operation="retry_failed",
                diagnostic_id=diagnostic_id,
                run_id=run_id,
            )
            raise

    @router.post(
        "/api/rpg/world-generation/{run_id}/continue",
        responses=documented(RpgWorldGenerationMutationResponse),
    )
    def rpg_world_continue_generation(run_id: str) -> dict[str, Any]:
        diagnostic_id = new_rpg_trace_id("world-generation-continue")
        try:
            result = continue_world_generation(
                run_id,
                diagnostic_id=diagnostic_id,
            )
            return {**result, "diagnostic_log": world_generation_log_hint()}
        except Exception as exc:
            _raise_generation_error(
                exc,
                operation="continue",
                diagnostic_id=diagnostic_id,
                run_id=run_id,
            )
            raise

    @router.get("/api/rpg/world-generation/{run_id}", responses=documented(RpgWorldGenerationStatusResponse))
    def rpg_world_generation_status(
        run_id: str,
        reconcile: bool = Query(default=True),
    ) -> dict[str, Any]:
        try:
            return read_world_generation(run_id, reconcile=reconcile)
        except Exception as exc:
            diagnostic_id = new_rpg_trace_id("world-generation-status")
            _raise_generation_error(
                exc,
                operation="status",
                diagnostic_id=diagnostic_id,
                run_id=run_id,
            )
            raise

    @router.post(
        "/api/rpg/world-generation/{run_id}/publish",
        responses=documented(RpgWorldGenerationPublishResponse),
    )
    def rpg_world_publish_generation(run_id: str) -> dict[str, Any]:
        try:
            return publish_world_library_generation(run_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.get(
        "/api/rpg/worlds/{world_id}/starter-bubble/preview",
        responses=documented(RpgStarterBubbleResponse),
    )
    def rpg_world_starter_bubble_preview(
        world_id: str,
        source_world_revision: int = Query(ge=1),
        starting_location_id: str = Query(min_length=1),
        neighboring_location_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        plan = build_starter_bubble(
            world_id=world_id,
            source_world_revision=source_world_revision,
            starting_location_id=starting_location_id,
            neighboring_location_id=neighboring_location_id,
        )
        return {
            "ok": True,
            "starter_bubble": plan.model_dump(mode="json"),
            "predictive_materialization": list(
                predictive_materialization_queue(
                    plan,
                    current_location_id=starting_location_id,
                )
            ),
        }

    @router.post(
        "/api/rpg/worlds/{world_id}/repair-for-launch",
        responses=documented(RpgWorldLaunchRepairResponse),
    )
    def rpg_world_repair_for_launch(
        world_id: str,
        request: Request, request_body: RpgWorldRepairForLaunchRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return repair_world_for_launch(
                world_id,
                scenario_id=str(payload.get("scenario_id") or ""),
                starting_location_id=str(payload.get("starting_location_id") or ""),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/prepare-openings-for-launch",
        responses=documented(RpgPrepareOpeningsResponse),
    )
    def rpg_world_prepare_openings_for_launch(world_id: str) -> dict[str, Any]:
        try:
            return prepare_opening_scenarios_for_launch(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/starter-bubble/promote",
        responses=documented(RpgStarterBubblePromotionResponse),
    )
    def rpg_world_starter_bubble_promote(
        world_id: str,
        request: Request, request_body: RpgWorldStarterBubblePromoteRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return promote_starter_bubble(
                world_id=world_id,
                source_world_revision=int(payload.get("source_world_revision") or 0),
                starting_location_id=str(payload.get("starting_location_id") or ""),
                neighboring_location_id=(
                    str(payload.get("neighboring_location_id"))
                    if payload.get("neighboring_location_id")
                    else None
                ),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/scenarios/{scenario_id}/revisions/{scenario_revision}/launch",
        responses=documented(RpgPublishedLaunchResponse),
    )
    def rpg_world_launch_scenario(
        scenario_id: str,
        scenario_revision: int,
        request: Request, request_body: RpgWorldLaunchScenarioRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return launch_published_scenario(
                world_id=str(payload.get("world_id") or ""),
                world_revision=int(payload.get("world_revision") or 0),
                world_release=int(payload.get("world_release") or 0),
                scenario_id=scenario_id,
                scenario_revision=scenario_revision,
                player=(
                    raw_player if isinstance(raw_player := payload.get("player"), Mapping) else {}
                ),
                gameplay=(
                    raw_gameplay if isinstance(raw_gameplay := payload.get("gameplay"), Mapping) else {}
                ),
                features=(
                    raw_features if isinstance(raw_features := payload.get("features"), Mapping) else {}
                ),
                    sessions=SESSION_CAMPAIGN_SESSIONS,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise
