"""Resumable ordered delivery routes for persisted canonical RPG responses."""
from __future__ import annotations
from fastapi import APIRouter


import json
from typing import Any, Iterator, Sequence

from fastapi import HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.apps.rpg.narration.narrative_delivery import (
    build_production_narrative_delivery_repository,
)
from app.apps.rpg.narration.narrative_engine.contracts import CanonicalNarrativeResponse
from app.apps.rpg.narration.narrative_engine.delivery import (
    NarrativeDeliveryConflict,
    NarrativeDeliveryCoordinator,
    NarrativeDeliveryEvent,
    NarrativeDeliveryRepository,
)
from app.apps.rpg.narration.narrative_repository import build_production_narrative_repository
from app.apps.rpg.edge.api.route_contracts import (
    NARRATIVE_DELIVERY_STREAM,
    RpgNarrativeDeliveryCancelResponse,
    RpgNarrativeDeliveryResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented


class NarrativeDeliveryCancelRequest(BaseModel):
    semantic_hash: str
    reason: str = "cancelled_before_publication"


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "ok": False,
            "error": "canonical_narrative_delivery_conflict",
            "message": str(exc),
        },
    )


def _response(
    response_id: str,
    semantic_hash: str,
) -> CanonicalNarrativeResponse:
    response = build_production_narrative_repository().get(response_id)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "canonical_narrative_response_not_found",
                "response_id": response_id,
            },
        )
    frozen = response.with_content_hash()
    if not semantic_hash or frozen.semantic_hash != semantic_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "error": "canonical_narrative_semantic_hash_mismatch",
                "response_id": response_id,
            },
        )
    return frozen


def _event_payload(event: NarrativeDeliveryEvent) -> str:
    return (
        f"id: {event.event_id}\n"
        "event: narrative_block\n"
        f"data: {json.dumps(event.as_dict(), ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _status_payload(response_id: str, semantic_hash: str, status: str) -> str:
    data = {
        "response_id": response_id,
        "semantic_hash": semantic_hash,
        "status": status,
    }
    return (
        "event: delivery_status\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _error_payload(response_id: str, semantic_hash: str, message: str) -> str:
    data = {
        "response_id": response_id,
        "semantic_hash": semantic_hash,
        "status": "conflict",
        "error": "canonical_narrative_delivery_conflict",
        "message": message,
    }
    return (
        "event: delivery_error\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _stream(
    response: CanonicalNarrativeResponse,
    repository: NarrativeDeliveryRepository,
    coordinator: NarrativeDeliveryCoordinator,
    replayed: Sequence[NarrativeDeliveryEvent],
    after_index: int,
) -> Iterator[str]:
    last_index = after_index
    for event in replayed:
        last_index = event.index
        yield _event_payload(event)

    while True:
        try:
            caught_up = coordinator.resume(
                response,
                repository,
                expected_semantic_hash=response.semantic_hash,
                after_index=last_index,
            )
            for event in caught_up:
                last_index = event.index
                yield _event_payload(event)
            record = repository.get(response.response_id)
            if record is None:
                raise NarrativeDeliveryConflict(
                    f"unknown narrative delivery: {response.response_id}"
                )
            if record.complete or record.cancelled:
                yield _status_payload(
                    response.response_id,
                    response.semantic_hash,
                    record.status,
                )
                return
            projected, published = coordinator.publish_next(
                response,
                repository,
                expected_semantic_hash=response.semantic_hash,
            )
        except NarrativeDeliveryConflict as exc:
            yield _error_payload(
                response.response_id,
                response.semantic_hash,
                str(exc),
            )
            return
        if published is None:
            yield _status_payload(
                response.response_id,
                response.semantic_hash,
                projected.delivery.status,
            )
            return
        if published.index > last_index:
            last_index = published.index
            yield _event_payload(published)


def register_rpg_narrative_delivery_routes(router: APIRouter, state) -> None:
    @router.get(
        "/api/rpg/narrative-responses/{response_id}/delivery",
        tags=["rpg-session"],
        responses=documented(RpgNarrativeDeliveryResponse),
    )
    def rpg_narrative_delivery_status(
        response_id: str,
        semantic_hash: str = Query(min_length=8, max_length=200),
    ) -> dict[str, Any]:
        response = _response(response_id, semantic_hash)
        repository = build_production_narrative_delivery_repository()
        record = repository.get(response.response_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error": "canonical_narrative_delivery_not_found",
                    "response_id": response_id,
                },
            )
        return {
            "ok": True,
            "response_id": response.response_id,
            "semantic_hash": response.semantic_hash,
            "delivery": record.as_dict(),
        }

    @router.get(
        "/api/rpg/narrative-responses/{response_id}/stream",
        tags=["rpg-session"],
        include_in_schema=False,
        responses=NARRATIVE_DELIVERY_STREAM,
    )
    def rpg_narrative_delivery_stream(
        response_id: str,
        request: Request,
        semantic_hash: str = Query(min_length=8, max_length=200),
        after_index: int | None = Query(default=None, ge=-1),
    ) -> StreamingResponse:
        header_cursor = request.headers.get("last-event-id")
        try:
            cursor = (
                int(header_cursor)
                if header_cursor is not None and header_cursor.strip()
                else int(after_index if after_index is not None else -1)
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "error": "invalid_narrative_delivery_cursor",
                    "value": header_cursor,
                },
            ) from exc
        response = _response(response_id, semantic_hash)
        repository = build_production_narrative_delivery_repository()
        coordinator = NarrativeDeliveryCoordinator()
        try:
            replayed = coordinator.resume(
                response,
                repository,
                expected_semantic_hash=semantic_hash,
                after_index=cursor,
            )
        except NarrativeDeliveryConflict as exc:
            raise _http_error(exc) from exc
        return StreamingResponse(
            _stream(
                response,
                repository,
                coordinator,
                replayed,
                cursor,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Omnix-Narrative-Response-Id": response_id,
                "X-Omnix-Narrative-Semantic-Hash": semantic_hash,
            },
        )

    @router.post(
        "/api/rpg/narrative-responses/{response_id}/cancel",
        tags=["rpg-session"],
        responses=documented(RpgNarrativeDeliveryCancelResponse),
    )
    def rpg_narrative_delivery_cancel(
        response_id: str,
        body: NarrativeDeliveryCancelRequest,
    ) -> dict[str, Any]:
        response = _response(response_id, body.semantic_hash)
        repository = build_production_narrative_delivery_repository()
        try:
            projected = NarrativeDeliveryCoordinator().cancel_before_publication(
                response,
                repository,
                expected_semantic_hash=body.semantic_hash,
                reason=body.reason,
            )
        except NarrativeDeliveryConflict as exc:
            raise _http_error(exc) from exc
        record = repository.get(response_id)
        return {
            "ok": True,
            "response_id": response_id,
            "semantic_hash": response.semantic_hash,
            "delivery": (
                record.as_dict()
                if record is not None
                else projected.delivery.metadata
            ),
        }
