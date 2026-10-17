"""Game Master review routes for single-pass World Forge candidates."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.foundation.debug_logging import new_rpg_trace_id
from app.apps.rpg.genesis.worlds.generation_acceptance import (
    accept_world_generation_candidate,
    accept_world_generation_candidates,
)
from app.apps.rpg.genesis.worlds.generation_repair_evaluation import require_retry_budget
from app.apps.rpg.genesis.worlds.generation_retry import (
    decide_world_generation_retry,
    retry_failed_world_generation,
)
from app.apps.rpg.genesis.worlds.generation_review_analytics import (
    world_generation_review_analytics,
)
from app.apps.rpg.genesis.worlds.generation_review_state import review_state
from app.apps.rpg.genesis.worlds.generation_worker import kick_world_generation_worker

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgWorldGenerationMutationResponse,
    RpgWorldGenerationReviewListResponse,
)
from app.apps.rpg.edge.api.route_contracts import (
    RpgWorldGenerationAcceptanceResponse,
    RpgWorldGenerationRetryDecisionResponse,
    RpgWorldGenerationTopicResultResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgWorldGenerationRetryReviewRequestBody(_TypedRequestModel):
    retry_scopes: _TypedRequestAny = None

class RpgWorldGenerationAcceptCandidateRequestBody(_TypedRequestModel):
    candidate: _TypedRequestAny = None
    expected_candidate_hash: _TypedRequestAny = None
    waiver_reason: _TypedRequestAny = None

class RpgWorldGenerationAcceptAllRequestBody(_TypedRequestModel):
    waiver_reason: _TypedRequestAny = None

class RpgWorldGenerationRetryDecisionRequestBody(_TypedRequestModel):
    decision: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_world_generation_review_routes_registered"
_MAX_RETRY_LINEAGE = 6


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=404,
            detail={"ok": False, "error": str(exc).strip("'")},
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=409,
            detail={"ok": False, "error": str(exc)},
        )
    return HTTPException(
        status_code=500,
        detail={"ok": False, "error": "world_generation_review_internal_error"},
    )


def _decisions(run: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    lineage = dict(run.get("lineage") or {})
    value = lineage.get("review_decisions")
    if not isinstance(value, Mapping):
        value = dict(run.get("plan") or {}).get("review_decisions")
    return {
        str(key): dict(row)
        for key, row in dict(value or {}).items()
        if isinstance(row, Mapping)
    }


def _previous_result_chain(
    topic_id: str,
    parent_results: list[dict[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    chain: dict[str, Any] | None = None
    for by_topic in reversed(parent_results):
        row = by_topic.get(topic_id)
        if row is not None:
            chain = {**row, "previous_result": chain}
    return chain


def _run_with_results(
    run_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    context = current_tenant()
    parent_result_maps: list[dict[str, dict[str, Any]]] = []
    with unit_of_work(None) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        results = work.world_generation.list_topic_results(context, run_id=run_id)
        parent_run_id = str(run.get("parent_run_id") or "")
        visited = {run_id}
        while parent_run_id and len(parent_result_maps) < _MAX_RETRY_LINEAGE:
            if parent_run_id in visited:
                break
            visited.add(parent_run_id)
            parent_run = work.world_generation.get(context, parent_run_id)
            if parent_run is None:
                break
            parent_rows = work.world_generation.list_topic_results(
                context,
                run_id=parent_run_id,
            )
            parent_result_maps.append(
                {
                    str(row.get("topic_id") or ""): dict(row)
                    for row in parent_rows
                    if str(row.get("topic_id") or "")
                }
            )
            parent_run_id = str(parent_run.get("parent_run_id") or "")
        work.rollback()
    if str(run.get("status") or "") in {"planned", "running"}:
        settings = dict(run.get("settings") or {})
        kick_world_generation_worker(
            database=None,
            provider_route=str(settings.get("provider_route") or ""),
        )
    decisions = _decisions(run)
    augmented = []
    for row in results:
        topic_id = str(row.get("topic_id") or "")
        value = {
            **row,
            "previous_result": _previous_result_chain(topic_id, parent_result_maps),
            "decision": decisions.get(topic_id),
            "failure_artifact": dict(
                dict(row.get("provider") or {}).get("failure_artifact") or {}
            )
            or None,
        }
        value["review_state"] = review_state(value)
        augmented.append(value)
    return run, augmented, world_generation_review_analytics(augmented, run)


def _topic_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    values = payload.get("topic_ids") or ()
    if not isinstance(values, (list, tuple)):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "topic_ids_must_be_array"},
        )
    return tuple(str(value) for value in values if str(value))


def _waiver_reasons(payload: Mapping[str, Any]) -> dict[str, str]:
    value = payload.get("waiver_reasons")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "waiver_reasons_must_be_object"},
        )
    return {
        str(key): str(reason)
        for key, reason in value.items()
        if str(key) and str(reason).strip()
    }


def register_rpg_world_generation_review_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/world-generation/{run_id}/results",
        responses=documented(RpgWorldGenerationReviewListResponse),
    )
    def rpg_world_generation_results(run_id: str) -> dict[str, Any]:
        try:
            run, results, analytics = _run_with_results(run_id)
            return {
                "ok": True,
                "run_id": run_id,
                "parent_run_id": run.get("parent_run_id"),
                "topic_results": results,
                "analytics": analytics,
                "review_decisions": _decisions(run),
            }
        except Exception as exc:
            raise _error(exc) from exc

    @router.get(
        "/api/rpg/world-generation/{run_id}/results/{topic_id}",
        responses=documented(RpgWorldGenerationTopicResultResponse),
    )
    def rpg_world_generation_topic_result(
        run_id: str,
        topic_id: str,
    ) -> dict[str, Any]:
        try:
            run, results, _analytics = _run_with_results(run_id)
            result = next(
                (
                    row
                    for row in results
                    if str(row.get("topic_id") or "") == topic_id
                ),
                None,
            )
            if result is None:
                raise KeyError(
                    f"world_generation_topic_result_not_found:{run_id}:{topic_id}"
                )
            return {
                "ok": True,
                "run_id": run_id,
                "parent_run_id": run.get("parent_run_id"),
                "topic_result": result,
            }
        except Exception as exc:
            raise _error(exc) from exc

    @router.post(
        "/api/rpg/world-generation/{run_id}/retry-review",
        responses=documented(RpgWorldGenerationMutationResponse),
    )
    def rpg_world_generation_retry_review(
        run_id: str,
        request: Request, request_body: RpgWorldGenerationRetryReviewRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        topic_ids = _topic_ids(payload)
        retry_scopes = payload.get("retry_scopes")
        if retry_scopes is not None and not isinstance(retry_scopes, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "retry_scopes_must_be_object"},
            )
        diagnostic_id = new_rpg_trace_id("world-generation-manual-retry")
        try:
            require_retry_budget(run_id, topic_ids)
            return retry_failed_world_generation(
                run_id,
                selected_topic_ids=topic_ids,
                retry_scopes=(
                    retry_scopes if isinstance(retry_scopes, Mapping) else {}
                ),
                diagnostic_id=diagnostic_id,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post(
        "/api/rpg/world-generation/{run_id}/results/{topic_id}/accept",
        responses=documented(RpgWorldGenerationAcceptanceResponse),
    )
    def rpg_world_generation_accept_candidate(
        run_id: str,
        topic_id: str,
        request: Request, request_body: RpgWorldGenerationAcceptCandidateRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        candidate = payload.get("candidate")
        if candidate is not None and not isinstance(candidate, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "candidate_must_be_object"},
            )
        try:
            return accept_world_generation_candidate(
                run_id,
                topic_id,
                candidate=candidate if isinstance(candidate, Mapping) else None,
                expected_candidate_hash=str(payload.get("expected_candidate_hash") or ""),
                waiver_reason=str(payload.get("waiver_reason") or ""),
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post(
        "/api/rpg/world-generation/{run_id}/accept-all",
        responses=documented(RpgWorldGenerationAcceptanceResponse),
    )
    def rpg_world_generation_accept_all(
        run_id: str,
        request: Request, request_body: RpgWorldGenerationAcceptAllRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return accept_world_generation_candidates(
                run_id,
                topic_ids=_topic_ids(payload),
                waiver_reasons=_waiver_reasons(payload),
                default_waiver_reason=str(payload.get("waiver_reason") or ""),
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post(
        "/api/rpg/world-generation/{run_id}/results/{topic_id}/decision",
        responses=documented(RpgWorldGenerationRetryDecisionResponse),
    )
    def rpg_world_generation_retry_decision(
        run_id: str,
        topic_id: str,
        request: Request, request_body: RpgWorldGenerationRetryDecisionRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        decision = str(payload.get("decision") or "")
        try:
            return decide_world_generation_retry(
                run_id,
                topic_id,
                decision=decision,
            )
        except Exception as exc:
            raise _error(exc) from exc


__all__ = [
    "register_rpg_world_generation_review_routes",
]
