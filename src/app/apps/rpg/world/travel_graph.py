# N82-N84 — Travel Graph + Location Progression v1

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class TravelEdge:
    from_location: str
    to_location: str
    label: str = ""
    direction: str = ""
    aliases: Tuple[str, ...] = ()
    required_flags: Tuple[str, ...] = ()
    blocked_by_flags: Tuple[str, ...] = ()
    travel_time_ticks: int = 1
    difficulty: str = "normal"


@dataclass
class TravelGraph:
    locations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    edges: List[TravelEdge] = field(default_factory=list)

    def add_location(self, location_id: str, **metadata: Any) -> None:
        if not location_id:
            return
        existing = self.locations.setdefault(location_id, {})
        existing.update(metadata)

    def add_edge(
        self,
        from_location: str,
        to_location: str,
        *,
        label: str = "",
        direction: str = "",
        aliases: Iterable[str] = (),
        required_flags: Iterable[str] = (),
        blocked_by_flags: Iterable[str] = (),
        travel_time_ticks: int = 1,
        difficulty: str = "normal",
        bidirectional: bool = True,
    ) -> None:
        edge = TravelEdge(
            from_location=from_location,
            to_location=to_location,
            label=label or to_location,
            direction=direction,
            aliases=tuple(aliases),
            required_flags=tuple(required_flags),
            blocked_by_flags=tuple(blocked_by_flags),
            travel_time_ticks=max(1, int(travel_time_ticks or 1)),
            difficulty=difficulty or "normal",
        )
        self.edges.append(edge)

        if bidirectional:
            reverse_direction = _reverse_direction(direction)
            self.edges.append(
                TravelEdge(
                    from_location=to_location,
                    to_location=from_location,
                    label=self.locations.get(from_location, {}).get("name") or from_location,
                    direction=reverse_direction,
                    aliases=tuple(aliases),
                    required_flags=tuple(required_flags),
                    blocked_by_flags=tuple(blocked_by_flags),
                    travel_time_ticks=max(1, int(travel_time_ticks or 1)),
                    difficulty=difficulty or "normal",
                )
            )

    def outgoing(self, location_id: str) -> List[TravelEdge]:
        return [edge for edge in self.edges if edge.from_location == location_id]

    def location_name(self, location_id: str) -> str:
        meta = self.locations.get(location_id) or {}
        return str(meta.get("name") or location_id)


def _reverse_direction(direction: str) -> str:
    value = (direction or "").strip().lower()
    return {
        "north": "south",
        "south": "north",
        "east": "west",
        "west": "east",
        "up": "down",
        "down": "up",
        "inside": "outside",
        "outside": "inside",
    }.get(value, "")


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9:_\-\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _state_flags(state: Mapping[str, Any]) -> set[str]:
    flags: set[str] = set()
    raw_flags = state.get("flags")
    if isinstance(raw_flags, Mapping):
        flags.update(str(k) for k, v in raw_flags.items() if v)
    elif isinstance(raw_flags, list):
        flags.update(str(v) for v in raw_flags)

    world_flags = state.get("world_flags")
    if isinstance(world_flags, Mapping):
        flags.update(str(k) for k, v in world_flags.items() if v)
    elif isinstance(world_flags, list):
        flags.update(str(v) for v in world_flags)

    return flags


def _edge_is_available(edge: TravelEdge, state: Mapping[str, Any]) -> bool:
    flags = _state_flags(state)
    if any(flag not in flags for flag in edge.required_flags):
        return False
    if any(flag in flags for flag in edge.blocked_by_flags):
        return False
    return True


def build_default_travel_graph(seed: Optional[str] = None) -> TravelGraph:
    """Small deterministic starter graph for 100-turn readiness.

    Keep this intentionally compact. Future bundles can load this from scenario packs.
    """
    graph = TravelGraph()

    graph.add_location(
        "location:rusty_flagon_tavern",
        name="The Rusty Flagon Tavern",
        aliases=["tavern", "rusty flagon", "inn", "bran's tavern"],
        kind="settlement",
    )
    graph.add_location(
        "location:village_square",
        name="Village Square",
        aliases=["square", "village", "market square", "outside"],
        kind="settlement",
    )
    graph.add_location(
        "location:north_road",
        name="North Road",
        aliases=["road", "north road", "trail", "path"],
        kind="road",
    )
    graph.add_location(
        "location:old_mill",
        name="Old Mill",
        aliases=["mill", "old mill", "haunted mill", "abandoned mill"],
        kind="quest_site",
    )
    graph.add_location(
        "location:forest_edge",
        name="Forest Edge",
        aliases=["forest", "woods", "forest edge", "tree line"],
        kind="wilderness",
    )
    graph.add_location(
        "location:river_crossing",
        name="River Crossing",
        aliases=["river", "crossing", "bridge", "ford"],
        kind="road",
    )

    graph.add_edge(
        "location:rusty_flagon_tavern",
        "location:village_square",
        label="step outside to the village square",
        direction="outside",
        aliases=["outside", "leave tavern", "village square", "square"],
        travel_time_ticks=1,
    )
    graph.add_edge(
        "location:village_square",
        "location:north_road",
        label="take the north road",
        direction="north",
        aliases=["north", "north road", "road", "follow the road"],
        travel_time_ticks=1,
    )
    graph.add_edge(
        "location:north_road",
        "location:old_mill",
        label="follow the track to the old mill",
        direction="east",
        aliases=["mill", "old mill", "haunted mill", "track"],
        travel_time_ticks=2,
    )
    graph.add_edge(
        "location:north_road",
        "location:forest_edge",
        label="turn toward the forest edge",
        direction="west",
        aliases=["forest", "woods", "west", "tree line"],
        travel_time_ticks=1,
    )
    graph.add_edge(
        "location:forest_edge",
        "location:river_crossing",
        label="follow the game trail to the river crossing",
        direction="north",
        aliases=["river", "crossing", "bridge", "ford"],
        travel_time_ticks=2,
    )

    return graph


def get_current_location(state: Mapping[str, Any]) -> str:
    return str(
        state.get("current_location")
        or state.get("current_location_id")
        or state.get("location")
        or "location:rusty_flagon_tavern"
    )


def list_available_routes(
    *,
    state: Mapping[str, Any],
    graph: Optional[TravelGraph] = None,
) -> List[Dict[str, Any]]:
    graph = graph or build_default_travel_graph()
    current = get_current_location(state)
    routes: List[Dict[str, Any]] = []
    for edge in graph.outgoing(current):
        if not _edge_is_available(edge, state):
            continue
        routes.append(
            {
                "from_location": edge.from_location,
                "to_location": edge.to_location,
                "to_name": graph.location_name(edge.to_location),
                "label": edge.label,
                "direction": edge.direction,
                "aliases": list(edge.aliases),
                "travel_time_ticks": edge.travel_time_ticks,
                "difficulty": edge.difficulty,
            }
        )
    return routes


def resolve_travel_destination(
    *,
    player_input: str,
    state: Mapping[str, Any],
    graph: Optional[TravelGraph] = None,
) -> Dict[str, Any]:
    graph = graph or build_default_travel_graph()
    current = get_current_location(state)
    normalized = _normalize_text(player_input)

    travel_intent = bool(
        re.search(
            r"\b(go|travel|walk|head|leave|enter|follow|move|continue|take|return|north|south|east|west|outside|inside)\b",
            normalized,
        )
    )

    if not travel_intent:
        return {
            "ok": False,
            "reason": "not_travel_intent",
            "current_location": current,
            "available_routes": list_available_routes(state=state, graph=graph),
        }

    candidates: List[Tuple[int, TravelEdge]] = []

    for edge in graph.outgoing(current):
        if not _edge_is_available(edge, state):
            continue

        score = 0
        to_meta = graph.locations.get(edge.to_location) or {}
        names = [
            edge.to_location,
            graph.location_name(edge.to_location),
            edge.direction,
            edge.label,
            *(edge.aliases or ()),
            *list(to_meta.get("aliases") or []),
        ]

        for raw_name in names:
            name = _normalize_text(raw_name)
            if not name:
                continue
            if name == normalized:
                score += 100
            elif name in normalized:
                score += 50
            elif any(part and part in normalized for part in name.split()):
                score += 8

        if edge.direction and re.search(rf"\b{re.escape(edge.direction.lower())}\b", normalized):
            score += 35

        if score > 0:
            candidates.append((score, edge))

    if not candidates:
        return {
            "ok": False,
            "reason": "unknown_or_unreachable_destination",
            "current_location": current,
            "current_location_name": graph.location_name(current),
            "available_routes": list_available_routes(state=state, graph=graph),
        }

    candidates.sort(key=lambda item: item[0], reverse=True)
    score, edge = candidates[0]

    return {
        "ok": True,
        "reason": "route_resolved",
        "score": score,
        "from_location": edge.from_location,
        "from_location_name": graph.location_name(edge.from_location),
        "to_location": edge.to_location,
        "to_location_name": graph.location_name(edge.to_location),
        "label": edge.label,
        "direction": edge.direction,
        "travel_time_ticks": edge.travel_time_ticks,
        "difficulty": edge.difficulty,
        "available_routes": list_available_routes(state=state, graph=graph),
    }


def apply_travel_result_to_state(
    *,
    state: Mapping[str, Any],
    travel_result: Mapping[str, Any],
) -> Dict[str, Any]:
    next_state = dict(state or {})
    if not travel_result.get("ok"):
        return next_state

    to_location = str(travel_result.get("to_location") or "")
    if not to_location:
        return next_state

    previous = get_current_location(next_state)
    next_state["previous_location"] = previous
    next_state["current_location"] = to_location
    next_state["current_location_id"] = to_location
    next_state["current_location_name"] = str(travel_result.get("to_location_name") or to_location)

    visited = list(next_state.get("visited_locations") or [])
    if previous and previous not in visited:
        visited.append(previous)
    if to_location and to_location not in visited:
        visited.append(to_location)
    next_state["visited_locations"] = visited

    tick = int(next_state.get("tick") or 0)
    next_state["tick"] = tick + int(travel_result.get("travel_time_ticks") or 1)

    return next_state


def build_travel_state_delta(travel_result: Mapping[str, Any]) -> Dict[str, Any]:
    if not travel_result.get("ok"):
        return {}

    return {
        "location_changed": True,
        "from_location": travel_result.get("from_location"),
        "from_location_name": travel_result.get("from_location_name"),
        "to_location": travel_result.get("to_location"),
        "to_location_name": travel_result.get("to_location_name"),
        "travel_time_ticks": travel_result.get("travel_time_ticks") or 1,
        "travel_label": travel_result.get("label"),
    }


def build_travel_world_event(travel_result: Mapping[str, Any]) -> Dict[str, Any]:
    if not travel_result.get("ok"):
        return {}

    return {
        "type": "travel",
        "subtype": "location_changed",
        "from_location": travel_result.get("from_location"),
        "to_location": travel_result.get("to_location"),
        "title": f"Travel to {travel_result.get('to_location_name') or travel_result.get('to_location')}",
        "summary": (
            f"The party travels from {travel_result.get('from_location_name') or travel_result.get('from_location')} "
            f"to {travel_result.get('to_location_name') or travel_result.get('to_location')}."
        ),
        "meaningful_progress": True,
        "progress_category": "location_progression",
    }