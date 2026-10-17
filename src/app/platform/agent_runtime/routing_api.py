"""Routing diagnostics/API for Omnix execution lanes."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .router import OmnixRouteDecision, route_omnix_request
from .workflow_runtime import default_workflow_runtime

router = APIRouter(prefix="/api/assistant-routing", tags=["assistant-routing"])


class RouteRequest(BaseModel):
    content: str


@router.post("/decide", response_model=OmnixRouteDecision)
def decide_route(request: RouteRequest) -> OmnixRouteDecision:
    return route_omnix_request(
        request.content,
        workflow_lookup=default_workflow_runtime().lookup,
    )
