"""RPG replay routes (/api/replay/*), mounted only when the rpg feature is enabled.

Importing this module loads RPG's replay models, so a gateway without RPG (MarketPulse's trading-only build,
roadmap Phase 12) never imports it and the routes answer 404, as other disabled features' routes do (WP-7.7).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from app.apps.rpg.edge.replay import (
    CheckpointBundleRequest,
    CheckpointEnvelope,
    PersistenceInventory,
    ReplayPrimitiveList,
    StateHashRequest,
    StateHashResponse,
    default_rpg_replay_adapter,
)


def register_replay_routes(router: APIRouter, get_replay_adapter: Callable[[], Any] | None = None) -> None:
    replay_adapter = get_replay_adapter or default_rpg_replay_adapter

    @router.get(
        "/api/replay/primitives", response_model=ReplayPrimitiveList, tags=["replay"]
    )
    def replay_primitives() -> ReplayPrimitiveList:
        return replay_adapter().list_primitives()

    @router.post(
        "/api/replay/state-hash", response_model=StateHashResponse, tags=["replay"]
    )
    def replay_state_hash(request: StateHashRequest) -> StateHashResponse:
        return replay_adapter().state_hash(request.state)

    @router.post(
        "/api/replay/checkpoints", response_model=CheckpointEnvelope, tags=["replay"]
    )
    def replay_checkpoint(bundle: CheckpointBundleRequest) -> CheckpointEnvelope:
        return replay_adapter().create_checkpoint(
            bundle.model_dump(exclude_unset=True)
        )

    @router.get(
        "/api/replay/persistence/inventory",
        response_model=PersistenceInventory,
        tags=["replay"],
    )
    def replay_persistence_inventory() -> PersistenceInventory:
        return replay_adapter().list_sessions()


__all__ = ["register_replay_routes"]
