"""Deterministic RPG memory helpers."""

from app.apps.rpg.world.memory.causal_memory import (
    add_causal_memory,
    ensure_npc_memory_state,
    make_causal_memory,
    normalize_causal_memory,
    normalize_npc_memory_state,
)
from app.apps.rpg.world.memory.causal_retrieval import retrieve_causal_memories
from app.apps.rpg.world.memory.observation import (
    record_event_observations,
    record_told_memory,
)


def update_memory(session, event=None, **kwargs):
    """Legacy pipeline hook retained for older import paths.

    The deterministic memory subsystem now records observations through the
    explicit observation helpers. Older pipeline modules still import this
    symbol during app startup, so keep it as a safe pass-through hook.
    """
    return session


__all__ = [
    "add_causal_memory",
    "ensure_npc_memory_state",
    "make_causal_memory",
    "normalize_causal_memory",
    "normalize_npc_memory_state",
    "record_event_observations",
    "record_told_memory",
    "retrieve_causal_memories",
    "update_memory",
]
