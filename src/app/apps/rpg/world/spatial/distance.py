from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def _coordinate(value: Any, key: str, index: int) -> float:
    if isinstance(value, Mapping):
        raw = value.get(key, 0.0)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw = value[index] if len(value) > index else 0.0
    else:
        raw = getattr(value, key, 0.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def euclidean_distance(a: Any, b: Any) -> float:
    """Return deterministic 2D Euclidean distance between two positions.

    Positions may be mappings with x/y keys, objects with x/y attributes, or
    sequences where index 0 is x and index 1 is y. Missing or non-numeric
    coordinates are treated as 0.0 so legacy NPC planner imports remain safe at
    app startup.
    """

    ax = _coordinate(a, "x", 0)
    ay = _coordinate(a, "y", 1)
    bx = _coordinate(b, "x", 0)
    by = _coordinate(b, "y", 1)
    return float(math.hypot(ax - bx, ay - by))
