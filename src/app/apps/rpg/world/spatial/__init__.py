"""Deterministic RPG spatial scene graph helpers.

The spatial package is simulation-authoritative.  It answers questions such as:

- where is an entity?
- can an entity move between areas?
- can an entity see another entity?
- can an entity hear another entity?

LLM narration may describe these results, but must not invent them.
"""

from __future__ import annotations

from typing import Any

from app.apps.rpg.world.spatial.audibility import (
    audible_entities_from,
    can_hear_area,
    can_hear_entity,
)
from app.apps.rpg.world.spatial.distance import euclidean_distance
from app.apps.rpg.world.spatial.graph import (
    ensure_spatial_graph,
    find_connection,
    get_entity_area,
    get_spatial_graph,
    list_area_connections,
    list_entities_in_area,
    set_entity_area,
)
from app.apps.rpg.world.spatial.movement import can_move_between, move_entity
from app.apps.rpg.world.spatial.serialization import normalize_spatial_graph
from app.apps.rpg.world.spatial.visibility import (
    can_see_area,
    can_see_entity,
    visible_entities_from,
)


def distance(a: Any, b: Any) -> float:
    """Compatibility alias for legacy NPC planner imports."""

    return euclidean_distance(a, b)


def astar(start: Any, goal: Any, session: Any | None = None) -> list[Any]:
    """Return a deterministic legacy path between two positions.

    Older NPC planner code imports ``astar`` directly from ``app.apps.rpg.world.spatial``
    during app startup.  The current spatial graph layer owns area movement, so
    this compatibility helper intentionally avoids inventing graph state.  It
    returns the minimal valid path shape expected by the planner: ``[start]``
    when already at the goal, otherwise ``[start, goal]``.
    """

    if start == goal:
        return [start]
    return [start, goal]


__all__ = [
    "astar",
    "audible_entities_from",
    "can_hear_area",
    "can_hear_entity",
    "can_move_between",
    "can_see_area",
    "can_see_entity",
    "distance",
    "ensure_spatial_graph",
    "euclidean_distance",
    "find_connection",
    "get_entity_area",
    "get_spatial_graph",
    "list_area_connections",
    "list_entities_in_area",
    "move_entity",
    "normalize_spatial_graph",
    "set_entity_area",
    "visible_entities_from",
]
