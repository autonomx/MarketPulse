"""Clean RPG session launch and save-management gateway routes."""
from __future__ import annotations
from fastapi import APIRouter
from app.config.env import env_str as _env_str

import asyncio
from typing import Any

from fastapi import HTTPException, Query, Request
from pydantic import ValidationError

from .rpg_campaign_lore_routes import register_rpg_campaign_lore_routes
from .rpg_narrative_delivery_routes import (
    register_rpg_narrative_delivery_routes,
)
from app.apps.rpg.edge.api.turn_pipeline import execute_foreground_rpg_turn
from app.apps.rpg.narration.presentation.turn_response import RpgTurnResponse
from app.apps.rpg.session.ability_coverage import summarize_ability_coverage
from app.apps.rpg.session.environment_narration import build_environment_narration_contract
from app.apps.rpg.session.environment_regions import derive_active_region_snapshot
from app.apps.rpg.genesis.forge.pipeline_adapter import create_new_game_from_genesis_payload
from app.apps.rpg.session.loadout import RpgLoadoutActionRequest, apply_loadout_action
from app.apps.rpg.session.new_game import (
    RpgNewGameRequest,
    RpgRenameSessionRequest,
    continue_rpg_session,
    create_new_game_session,
    delete_rpg_session,
    list_rpg_presets,
    rename_rpg_session,
    start_rpg_preset,
)
from app.apps.rpg.session.service import list_session_summaries, load_session
from app.apps.rpg.session.world_ability_integration import ensure_world_scale_abilities

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.session.service import SESSION_CAMPAIGN_SESSIONS
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgLaunchResponse,
    RpgLoadoutActionResponse,
    RpgPresetListResponse,
    RpgSessionListResponse,
    RpgSessionMutationResponse,
)
from app.apps.rpg.edge.api.route_contracts import (
    RpgAbilityCoverageResponse,
    RpgLocalDialogueFixtureResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgNewGameRequestBody(RpgNewGameRequest):
    genesis: dict[str, Any] | None = None

class RpgApplyTurnRequestBody(_TypedRequestModel):
    command: str | dict[str, Any] | None = None
    player_input: str | dict[str, Any] | None = None
    text: str | dict[str, Any] | None = None
    message: str | dict[str, Any] | None = None

class RpgLocalDialogueFixtureRequestBody(_TypedRequestModel):
    case_id: _TypedRequestAny = None
    run_id: _TypedRequestAny = None
    session_id: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_session_routes_registered"
_GENESIS_CONTRACT_VERSION = "rpg_genesis_v2"
_LOCAL_DIALOGUE_FIXTURE_HEADER = "x-omnix-rpg-live-smoke"


class _TruthyZero(int):
    """Preserve explicit seed=0 through legacy truthiness checks."""

    def __new__(cls) -> "_TruthyZero":
        return int.__new__(cls, 0)

    def __bool__(self) -> bool:
        return True


def _raise_for_error(
    payload: dict[str, Any],
    *,
    not_found_errors: set[str] | None = None,
) -> dict[str, Any]:
    if payload.get("ok") is not False:
        return payload
    error = str(payload.get("error") or "rpg_session_error")
    status_code = 404 if error in (not_found_errors or set()) else 400
    raise HTTPException(status_code=status_code, detail=payload)


def _payload_root(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("request")
    return request if isinstance(request, dict) else payload


def _has_genesis_contract(payload: dict[str, Any]) -> bool:
    root = _payload_root(payload)
    genesis = root.get("genesis")
    if isinstance(genesis, dict):
        return True
    return root.get("contract_version") == _GENESIS_CONTRACT_VERSION


def _preserve_seed_zero(request: RpgNewGameRequest) -> RpgNewGameRequest:
    if request.seed == 0 and not bool(request.seed):
        object.__setattr__(request, "seed", _TruthyZero())
    return request


def _create_new_game_from_payload(
    payload: dict[str, Any],
    legacy_request: RpgNewGameRequest | None = None,
) -> dict[str, Any]:
    try:
        if _has_genesis_contract(payload):
            return create_new_game_from_genesis_payload(payload, sessions=SESSION_CAMPAIGN_SESSIONS)
        request = legacy_request or RpgNewGameRequest.model_validate(payload)
        return create_new_game_session(_preserve_seed_zero(request))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _ability_coverage_payload(state: dict[str, Any]) -> dict[str, Any]:
    report = summarize_ability_coverage(state)
    return report.model_dump(exclude={"observations"})


def _environment_snapshot_from_state(
    state: dict[str, Any],
) -> dict[str, Any] | None:
    # A published scenario supplies its own canonical opening environment.  Do
    # not derive a replacement from the new-game template that was used only to
    # bootstrap the player sheet (for example, Rusty Flagon Tavern).
    published_world = state.get("published_world")
    published_snapshot = state.get("environment_snapshot")
    if (
        isinstance(published_world, dict)
        and isinstance(published_snapshot, dict)
        and published_snapshot.get("schema_version")
        == "rpg_published_opening_environment_v1"
    ):
        return dict(published_snapshot)
    world_value = state.get("world")
    world = world_value if isinstance(world_value, dict) else {}
    environment_value = world.get("environment")
    regions_value = world.get("regions")
    environment = environment_value if isinstance(environment_value, dict) else None
    regions = regions_value if isinstance(regions_value, dict) else None
    if environment is None and regions is None:
        return None
    scene_value = state.get("scene")
    scene = scene_value if isinstance(scene_value, dict) else {}
    context_value = scene.get("environment_context")
    context = context_value if isinstance(context_value, dict) else {}
    return derive_active_region_snapshot(world, context)


def _attach_environment_snapshot_to_session(
    session: dict[str, Any],
) -> dict[str, Any]:
    state = (
        raw_state if isinstance(raw_state := session.get("state"), dict) else None
    )
    if state is None:
        return session
    snapshot = _environment_snapshot_from_state(state)
    if snapshot is not None:
        state["environment_snapshot"] = snapshot
        state["environment_narration_contract"] = (
            build_environment_narration_contract(snapshot)
        )
    return session


def _with_environment_snapshot(
    payload: dict[str, Any],
) -> dict[str, Any]:
    session = (
        raw_session if isinstance(raw_session := payload.get("session"), dict) else None
    )
    state = (
        session.get("state")
        if session and isinstance(session.get("state"), dict)
        else None
    )
    if state is None and isinstance(payload.get("game"), dict):
        state = payload["game"]
    if state is None:
        return payload
    snapshot = _environment_snapshot_from_state(state)
    if snapshot is None:
        return payload
    contract = build_environment_narration_contract(snapshot)
    state["environment_snapshot"] = snapshot
    state["environment_narration_contract"] = contract
    payload["environment_snapshot"] = snapshot
    payload["environment_narration_contract"] = contract
    payload["game"] = state
    return payload


def _with_world_scale_abilities(
    payload: dict[str, Any],
) -> dict[str, Any]:
    session = (
        raw_session if isinstance(raw_session := payload.get("session"), dict) else None
    )
    if not session:
        return payload
    state = (
        raw_state if isinstance(raw_state := session.get("state"), dict) else None
    )
    if not state:
        return payload
    ensure_world_scale_abilities(state)
    coverage = _ability_coverage_payload(state)
    mechanics = (
        raw_mechanics if isinstance(raw_mechanics := state.get("mechanics"), dict) else {}
    )
    mechanics["ability_coverage_latest"] = coverage
    state["mechanics"] = mechanics
    payload["ability_coverage"] = coverage
    payload["game"] = state
    return payload


def _with_rpg_response_surface(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _with_world_scale_abilities(
        _with_environment_snapshot(payload)
    )


def _foreground_turn_command(payload: Any) -> str:
    if isinstance(payload, dict):
        value = (
            payload.get("command")
            or payload.get("player_input")
            or payload.get("text")
            or payload.get("message")
        )
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise HTTPException(
        status_code=400,
        detail={"ok": False, "error": "missing_command"},
    )


def _require_local_dialogue_fixture_request(request: Request) -> None:
    client_host = str(request.client.host if request.client else "").strip().casefold()
    loopback_hosts = {"127.0.0.1", "::1", "localhost", "testclient"}
    if client_host not in loopback_hosts:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if str(_env_str("CI") or "").strip().casefold() in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if request.headers.get(_LOCAL_DIALOGUE_FIXTURE_HEADER) != "1":
        raise HTTPException(
            status_code=403,
            detail={"ok": False, "error": "local_dialogue_fixture_opt_in_required"},
        )


def register_rpg_session_routes(router: APIRouter, state) -> None:
    """Attach the typed RPG session API once."""
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)
    register_rpg_campaign_lore_routes(router, state)
    register_rpg_narrative_delivery_routes(router, state)

    @router.get("/api/rpg/presets", tags=["rpg-session"], responses=documented(RpgPresetListResponse))
    def rpg_presets() -> dict[str, Any]:
        return list_rpg_presets()

    @router.post("/api/rpg/presets/{preset_id}/start", tags=["rpg-session"], responses=documented(RpgLaunchResponse))
    def rpg_start_preset(preset_id: str) -> dict[str, Any]:
        return _with_rpg_response_surface(
            _raise_for_error(
                start_rpg_preset(preset_id),
                not_found_errors={"unknown_rpg_preset"},
            )
        )

    @router.get("/api/rpg/sessions", tags=["rpg-session"], responses=documented(RpgSessionListResponse))
    def rpg_sessions(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        sessions = [
            _attach_environment_snapshot_to_session(session)
            for session in (list_session_summaries(limit=limit) or [])
        ]
        return {"ok": True, "sessions": sessions}

    @router.get("/api/rpg/sessions/{session_id}", tags=["rpg-session"], responses=documented(RpgLaunchResponse))
    def rpg_read_session(session_id: str) -> dict[str, Any]:
        session = load_session(session_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error": "session_not_found",
                    "session_id": session_id,
                },
            )
        return _with_rpg_response_surface(
            {
                "ok": True,
                "session_id": session_id,
                "session": session,
                "game": session.get("state", {}),
            }
        )

    @router.get(
        "/api/rpg/sessions/{session_id}/ability-coverage",
        tags=["rpg-session"],
        responses=documented(RpgAbilityCoverageResponse),
    )
    def rpg_ability_coverage(session_id: str) -> dict[str, Any]:
        session = load_session(session_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error": "session_not_found",
                    "session_id": session_id,
                },
            )
        state = (
            raw_state if isinstance(raw_state := session.get("state"), dict) else {}
        )
        ensure_world_scale_abilities(state)
        return {
            "ok": True,
            "session_id": session_id,
            "ability_coverage": _ability_coverage_payload(state),
        }

    @router.post("/api/rpg/new-game", tags=["rpg-session"], responses=documented(RpgLaunchResponse))
    async def rpg_new_game(
        http_request: Request, request_body: RpgNewGameRequestBody,
    ) -> dict[str, Any]:
        raw_payload = request_body.model_dump(exclude_unset=True, by_alias=True)
        request = RpgNewGameRequest.model_validate(raw_payload)
        return await asyncio.to_thread(
            lambda: _with_rpg_response_surface(
                _create_new_game_from_payload(raw_payload, request)
            )
        )

    @router.post(
        "/api/rpg/sessions/{session_id}/continue",
        tags=["rpg-session"],
        responses=documented(RpgLaunchResponse),
    )
    def rpg_continue_session(session_id: str) -> dict[str, Any]:
        return _with_rpg_response_surface(
            _raise_for_error(
                continue_rpg_session(session_id),
                not_found_errors={"session_not_found"},
            )
        )

    @router.post(
        "/api/rpg/sessions/{session_id}/turn",
        tags=["rpg-session"],
        responses={200: {"model": RpgTurnResponse, "description": "The turn's rpg_turn_response_v2 contract."}},
    )
    async def rpg_apply_turn(
        session_id: str,
        http_request: Request, request_body: RpgApplyTurnRequestBody,
    ) -> Any:
        raw_payload = request_body.model_dump(exclude_unset=True, by_alias=True)
        command = _foreground_turn_command(raw_payload)
        return await execute_foreground_rpg_turn(
            session_id=session_id,
            command=command,
            request=http_request,
        )

    @router.post(
        "/api/rpg/local-qualification/dialogue-fixture",
        tags=["rpg-session"],
        responses=documented(RpgLocalDialogueFixtureResponse),
    )
    async def rpg_local_dialogue_fixture(http_request: Request, request_body: RpgLocalDialogueFixtureRequestBody) -> dict[str, Any]:
        _require_local_dialogue_fixture_request(http_request)
        payload = request_body.model_dump(exclude_unset=True, by_alias=True)
        from app.apps.rpg.edge.local_dialogue_quality_fixtures import (
            dialogue_benchmark_case,
            provision_local_dialogue_fixture,
        )

        try:
            case = dialogue_benchmark_case(str(payload.get("case_id") or ""))
            return await asyncio.to_thread(
                lambda: provision_local_dialogue_fixture(
                    case=case,
                    run_id=str(payload.get("run_id") or ""),
                    session_id=str(payload.get("session_id") or "").strip() or None,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"ok": False, "error": str(exc)},
            ) from exc

    @router.post(
        "/api/rpg/sessions/{session_id}/rename",
        tags=["rpg-session"],
        responses=documented(RpgSessionMutationResponse),
    )
    def rpg_rename_session(
        session_id: str,
        request: RpgRenameSessionRequest,
    ) -> dict[str, Any]:
        name = request.name.strip()
        if not name:
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "error": "missing_session_name",
                    "session_id": session_id,
                },
            )
        return _raise_for_error(
            rename_rpg_session(session_id, name),
            not_found_errors={"session_not_found"},
        )

    @router.post(
        "/api/rpg/sessions/{session_id}/delete",
        tags=["rpg-session"],
        responses=documented(RpgSessionMutationResponse),
    )
    def rpg_delete_session(session_id: str) -> dict[str, Any]:
        return _raise_for_error(
            delete_rpg_session(session_id),
            not_found_errors={"session_not_found"},
        )

    @router.post(
        "/api/rpg/sessions/{session_id}/loadout-action",
        tags=["rpg-session"],
        responses=documented(RpgLoadoutActionResponse),
    )
    def rpg_loadout_action(
        session_id: str,
        request: RpgLoadoutActionRequest,
    ) -> dict[str, Any]:
        return _with_environment_snapshot(
            _raise_for_error(
                apply_loadout_action(session_id, request),
                not_found_errors={"session_not_found"},
            )
        )
