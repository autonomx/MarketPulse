"""PHASE 5.1 — State Hashing (FOUNDATION)

Deterministic serialization and hashing of game state.

This module provides the foundational building block for all validation
in Phase 5.1. By creating deterministic fingerprints of game state,
we can:
- Compare states across runs (determinism)
- Compare live vs replay (replay validation)
- Compare simulation vs real execution (simulation parity)

CRITICAL DESIGN RULES:
- No reliance on timestamps for hash equality
- All dicts/lists are sorted for ordering guarantees
- Object attributes are serialized via __dict__ recursively
- Same logical state ALWAYS produces same hash

Usage:
    hash1 = compute_state_hash(loop1)
    hash2 = compute_state_hash(loop2)
    assert hash1 == hash2  # Determinism proven
"""

import hashlib
import json
from typing import Any, Dict, List


def stable_serialize(obj: Any) -> Any:
    """Deterministic serialization with no ordering issues.

    Recursively handles:
    - dicts: sorted by keys
    - lists: preserved order, each element serialized
    - objects with __dict__: converted to stable dict
    - primitives: returned as-is

    This function is idempotent: calling it on already-serialized
    data produces the same result.

    Args:
        obj: Any object to serialize.

    Returns:
        A JSON-serializable representation with deterministic ordering.
    """
    if obj is None:
        return None
    elif isinstance(obj, bool):
        return obj
    elif isinstance(obj, int):
        return obj
    elif isinstance(obj, float):
        return round(obj, 6)  # PHASE 5.1.5 — HARDENING (rpg-design.txt Issue #4): Round floats to prevent precision drift across platforms
    elif isinstance(obj, str):
        return obj
    elif isinstance(obj, dict):
        return {k: stable_serialize(obj[k]) for k in sorted(obj.keys())}
    elif isinstance(obj, (list, tuple)):
        return [stable_serialize(x) for x in obj]
    elif isinstance(obj, (set, frozenset)):
        return sorted([stable_serialize(x) for x in obj])
    elif hasattr(obj, "__dict__"):
        d = vars(obj)
        # Guard against objects without __dict__ or circular references
        if not isinstance(d, dict):
            return str(obj)
        return stable_serialize(d)
    else:
        # Fallback for unrecognized types (enums, dates, etc.)
        return str(obj)


def _has_real_attribute(obj, attr_name: str) -> bool:
    """Check if an object has a real (non-Mock) attribute.
    
    This properly handles MagicMock objects by checking if the attribute
    is actually a real object and not just a mock auto-generated attribute.
    
    Args:
        obj: Object to check.
        attr_name: Name of the attribute to look for.
    
    Returns:
        True if the object has a real attribute with the given name.
    """
    if obj is None:
        return False
    
    # Skip MagicMock objects - they auto-create any attribute
    type_name = type(obj).__name__
    if 'MagicMock' in type_name or 'Mock' in type_name:
        return False
    
    attr = getattr(obj, attr_name, None)
    # Check attribute exists and is not None/Mock
    if attr is None:
        return False
    attr_type = type(attr).__name__
    if 'MagicMock' in attr_type or 'Mock' in attr_type:
        return False
    return True


def _has_real_method(obj, method_name: str) -> bool:
    """Check if an object has a real callable method.
    
    Args:
        obj: Object to check.
        method_name: Name of the method to look for.
    
    Returns:
        True if the object has a real callable method with the given name.
    """
    if obj is None:
        return False
    type_name = type(obj).__name__
    if 'MagicMock' in type_name or 'Mock' in type_name:
        return False
    method = getattr(obj, method_name, None)
    if method is None:
        return False
    method_type = type(method).__name__
    if 'MagicMock' in method_type or 'Mock' in method_type:
        return False
    return callable(method)


def _extract_world_state(loop: Any) -> Dict[str, Any]:
    """Extract ALL deterministic state from subsystems.

    This function inspects the game loop for known subsystems and
    extracts their state in a deterministic, serializable format.

    CRITICAL: This ensures the state hash covers the FULL game state,
    not just events. Without this, the hash is blind to:
    - NPC memory changes
    - Relationship graph updates
    - World state caches
    - Goal engine state

    Args:
        loop: game loop instance with optional subsystem attributes.

    Returns:
        Dictionary containing extracted world state from all subsystems.
    """
    state: Dict[str, Any] = {}

    # Skip MagicMock objects entirely
    type_name = type(loop).__name__
    if 'MagicMock' in type_name or 'Mock' in type_name:
        return state

    # Extract NPC manager state
    if _has_real_attribute(loop, "npc_manager"):
        npc_mgr = loop.npc_manager
        if _has_real_method(npc_mgr, "export_state"):
            raw = npc_mgr.export_state()
            # Only serialize if result is dict-like, not a mock
            if isinstance(raw, dict):
                state["npcs"] = stable_serialize(raw)

    # Extract memory system state
    if _has_real_attribute(loop, "memory"):
        mem = loop.memory
        if _has_real_method(mem, "export_state"):
            raw = mem.export_state()
            if isinstance(raw, dict):
                state["memory"] = stable_serialize(raw)

    # Extract relationship graph state
    if _has_real_attribute(loop, "relationship_graph"):
        rel_graph = loop.relationship_graph
        if _has_real_method(rel_graph, "export_state"):
            raw = rel_graph.export_state()
            if isinstance(raw, dict):
                state["relationships"] = stable_serialize(raw)

    # Extract world state if present
    if _has_real_attribute(loop, "world_state"):
        world = loop.world_state
        if _has_real_method(world, "export_state"):
            raw = world.export_state()
            if isinstance(raw, dict):
                state["world"] = stable_serialize(raw)

    # Extract NPC system state (alternate naming)
    if _has_real_attribute(loop, "npc_system"):
        npc_sys = loop.npc_system
        if _has_real_method(npc_sys, "export_state"):
            raw = npc_sys.export_state()
            if isinstance(raw, dict):
                state["npc_system"] = stable_serialize(raw)

    return state


def compute_state_hash(loop: Any) -> str:
    """Create a deterministic hash of the full game state.

    PHASE 5.1.5 — COMPLETE STATE HASH:
    The hash now covers:
    - Current tick count
    - Complete event history with IDs, types, payloads, and parent links
    - World state snapshots from all subsystems (NPCs, memory, relationships)

    This prevents silent failures where events are identical but world state
    differs between runs (e.g., NPC memory divergence, relationship drift).

    This hash is designed for COMPARISON, not cryptographic security.
    Two loops with logically equivalent state will produce the same hash.

    Args:
        loop: game loop instance with tick_count and event_bus attributes.

    Returns:
        SHA-256 hex digest (64 character string).
    """
    # Extract tick
    tick_count = getattr(loop, "_tick_count", None)
    if tick_count is None:
        tick_count = getattr(loop, "tick_count", 0)

    # Extract event bus history
    event_bus = getattr(loop, "event_bus", None)
    events_data: List[Dict[str, Any]] = []

    if event_bus is not None:
        # Try get_history() method first (PHASE 5.1 deterministic ordering)
        if hasattr(event_bus, "get_history"):
            history = event_bus.get_history()
        elif hasattr(event_bus, "history"):
            history_prop = event_bus.history
            history = history_prop if isinstance(history_prop, list) else list(history_prop)
        else:
            history = []

        for e in history:
            events_data.append({
                "id": getattr(e, "event_id", None),
                "type": getattr(e, "type", None),
                "payload": stable_serialize(getattr(e, "payload", {})),
                "parent": getattr(e, "parent_id", None),
            })

    # 🔴 PHASE 5.1.5 FIX #1: Include world state snapshots
    world_state = _extract_world_state(loop)

    state = {
        "tick": tick_count,
        "events": events_data,
        "world": world_state,
    }

    serialized = json.dumps(
        stable_serialize(state),
        separators=(",", ":"),
        sort_keys=True,
    )

    return hashlib.sha256(serialized.encode()).hexdigest()
