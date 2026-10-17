"""Agent runtime HTTP routes owned by the agent-runtime feature."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.runtime.features import FeatureContext


def create_agent_runtime_router(_context: FeatureContext) -> APIRouter:
    """Compose the agent runtime's public routers under one feature boundary."""
    from .api import router
    from .broker_api import router as broker_router
    from .model_gateway import router as model_router
    from .planning_api import router as planning_router
    from .run_token_guard import require_live_run_token
    from .preview_api import router as preview_router
    from .routing_api import router as routing_router
    from .task_graph_api import router as task_graph_router
    from .workflow_api import router as workflow_router

    feature_router = APIRouter()
    for router_part in (
        router,
        preview_router,
        routing_router,
        task_graph_router,
        workflow_router,
    ):
        feature_router.include_router(router_part)
    # Called by the agent's own process with its run token (WP-4.6).
    for agent_router in (broker_router, planning_router, model_router):
        feature_router.include_router(agent_router, dependencies=[Depends(require_live_run_token)])
    return feature_router
