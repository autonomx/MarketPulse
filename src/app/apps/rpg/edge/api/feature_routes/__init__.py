"""Feature-owned RPG HTTP routes that retain the established gateway paths."""
from __future__ import annotations
from fastapi import APIRouter


from app.runtime.features import FeatureContext

from .rpg_debug_routes import (
    add_rpg_debug_middleware,
    register_rpg_debug_routes,
)
from .rpg_geometry_patch_routes import register_rpg_geometry_patch_routes
from .rpg_grid_performance_routes import register_rpg_grid_performance_routes
from .rpg_map_editor_routes import register_rpg_map_editor_routes
from .rpg_map_routes import register_rpg_map_routes
from .rpg_npc_spatial_routes import register_rpg_npc_spatial_routes
from .rpg_observer_routes import register_rpg_observer_routes
from .rpg_progressive_map_routes import register_rpg_progressive_map_routes
from .rpg_session_routes import register_rpg_session_routes
from .rpg_tactical_spatial_routes import register_rpg_tactical_spatial_routes
from .rpg_world_authoring_routes import register_rpg_world_authoring_routes
from .rpg_world_bundle_routes import register_rpg_world_bundle_routes
from .rpg_world_deletion_routes import register_rpg_world_deletion_routes
from .rpg_world_dossier_routes import register_rpg_world_dossier_routes
from .rpg_world_generation_review_routes import (
    register_rpg_world_generation_review_routes,
)
from .rpg_world_image_routes import register_rpg_world_image_routes
from .rpg_world_profile_routes import register_rpg_world_profile_routes
from .rpg_world_routes import register_rpg_world_routes


def create_rpg_routes_router(context: FeatureContext) -> APIRouter:
    router = APIRouter()
    state = context.runtime_state
    for register_routes in (
        register_rpg_debug_routes,
        register_rpg_geometry_patch_routes,
        register_rpg_grid_performance_routes,
        register_rpg_map_editor_routes,
        register_rpg_map_routes,
        register_rpg_world_bundle_routes,
        register_rpg_world_routes,
        register_rpg_world_generation_review_routes,
        register_rpg_world_deletion_routes,
        register_rpg_world_authoring_routes,
        register_rpg_world_dossier_routes,
        register_rpg_world_image_routes,
        register_rpg_world_profile_routes,
        register_rpg_progressive_map_routes,
        register_rpg_npc_spatial_routes,
        register_rpg_observer_routes,
        register_rpg_tactical_spatial_routes,
        register_rpg_session_routes,
    ):
        register_routes(router, state)
    return router


__all__ = ["create_rpg_routes_router", "add_rpg_debug_middleware"]
