"""Shared replay and persistence platform interfaces."""
from .models import (
    CheckpointEnvelope,
    CheckpointBundleRequest,
    PersistenceInventory,
    ReplayPrimitive,
    ReplayPrimitiveList,
    StateHashRequest,
    StateHashResponse,
)
from .rpg_adapter import RpgReplayPersistenceAdapter, default_rpg_replay_adapter

__all__ = [
    "CheckpointEnvelope",
    "CheckpointBundleRequest",
    "PersistenceInventory",
    "ReplayPrimitive",
    "ReplayPrimitiveList",
    "RpgReplayPersistenceAdapter",
    "StateHashRequest",
    "StateHashResponse",
    "default_rpg_replay_adapter",
]
