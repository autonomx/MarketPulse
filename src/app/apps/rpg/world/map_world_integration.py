"""Session-scoped map definitions sourced from the authoritative RPG world graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import ceil, sqrt
from typing import Any, cast

from app.apps.rpg.world.map_contracts import (
    MapBackground,
    MapBounds,
    MapDefinition,
    MapLabelDefinition,
    MapObjectDefinition,
    MapPolygon,
    MapRenderOrder,
    MapRouteGeometry,
    MapSprite,
)
from app.apps.rpg.world.map_repository import MapDefinitionRepository, default_map_repository
from app.apps.rpg.world.map_serialization import with_definition_revision
from app.apps.rpg.world.map_settlement_assembler import assemble_settlement_map
from app.apps.rpg.world.world_graph import RpgLocationNode, RpgRegionGraph, RpgRoute

WORLD_GRAPH_SCHEMA_VERSION = 1
_REGION_WIDTH = 12000
_REGION_HEIGHT = 7600
_SETTLEMENT_TAGS = {"settlement", "town", "village", "city", "hamlet", "outpost"}


class MapWorldIntegrationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@dataclass(frozen=True)
class CanonicalWorldMapModel:
    seed: int
    graph: RpgRegionGraph
    current_location_id: str
    discovered_location_ids: tuple[str, ...]
    map_bindings: Mapping[str, str]


def canonical_world_map_model(session: Mapping[str, Any]) -> CanonicalWorldMapModel | None:
    state = _mapping(session.get("state"))
    raw = _mapping(state.get("world_graph"))
    if not raw:
        return None
    version = _int(raw.get("schema_version"), fallback=-1)
    if version != WORLD_GRAPH_SCHEMA_VERSION:
        raise MapWorldIntegrationError("unsupported_world_graph_schema", str(version))

    locations = _locations(raw.get("locations"))
    routes = _routes(raw.get("routes"))
    graph = RpgRegionGraph(locations={item.id: item for item in locations}, routes=routes)
    current_location_id = _first_text(
        raw.get("current_location_id"),
        state.get("current_location_id"),
        _mapping(state.get("player")).get("location_id"),
    )
    if current_location_id and current_location_id not in graph.locations:
        raise MapWorldIntegrationError("current_location_not_in_world_graph", current_location_id)
    discovered = {
        item
        for item in _string_sequence(raw.get("discovered_location_ids"))
        if item in graph.locations
    }
    if current_location_id:
        discovered.add(current_location_id)
    bindings = {
        str(key): str(value)
        for key, value in _mapping(raw.get("map_bindings")).items()
        if str(key) in graph.locations and str(value).strip()
    }
    return CanonicalWorldMapModel(
        seed=_int(raw.get("seed"), fallback=0),
        graph=graph,
        current_location_id=current_location_id,
        discovered_location_ids=tuple(sorted(discovered)),
        map_bindings=bindings,
    )


def map_repository_for_session(session: Mapping[str, Any]) -> MapDefinitionRepository:
    model = canonical_world_map_model(session)
    if model is None:
        return default_map_repository()

    definitions = list(default_map_repository().list())
    existing_ids = {definition.map_id for definition in definitions}
    region_ids = sorted({node.region_id for node in model.graph.locations.values()})
    for region_id in region_ids:
        map_id = _region_map_id(region_id)
        if map_id not in existing_ids:
            definitions.append(_region_definition(model, region_id))
            existing_ids.add(map_id)

    for node in sorted(model.graph.locations.values(), key=lambda item: item.id):
        bound = model.map_bindings.get(node.id)
        if bound or not _is_settlement(node):
            continue
        map_id = _settlement_map_id(node.id)
        if map_id in existing_ids:
            continue
        assembly = assemble_settlement_map(model.seed, model.graph, node.id, map_id=map_id)
        definitions.append(_canonical_settlement_definition(model, node, assembly.definition))
        existing_ids.add(map_id)
    return MapDefinitionRepository(definitions)


def resolve_map_id_for_location(
    session: Mapping[str, Any],
    location_id: str,
    repository: MapDefinitionRepository | None = None,
) -> str | None:
    model = canonical_world_map_model(session)
    if model is None or location_id not in model.graph.locations:
        return None
    repository = repository or map_repository_for_session(session)
    bound = model.map_bindings.get(location_id)
    if bound and repository.find(bound):
        return bound
    node = model.graph.locations[location_id]
    settlement_id = _settlement_map_id(location_id)
    if _is_settlement(node) and repository.find(settlement_id):
        return settlement_id
    region_id = _region_map_id(node.region_id)
    return region_id if repository.find(region_id) else None


def canonical_route_id_for_locations(
    session: Mapping[str, Any],
    from_location_id: str,
    to_location_id: str,
) -> str | None:
    model = canonical_world_map_model(session)
    if model is None:
        return None
    routes = model.graph.routes_between(from_location_id, to_location_id)
    return routes[0].id if routes else None


def integrate_canonical_world_map_state(session: dict[str, Any]) -> dict[str, Any]:
    model = canonical_world_map_model(session)
    if model is None or not model.current_location_id:
        return session
    repository = map_repository_for_session(session)
    map_id = resolve_map_id_for_location(session, model.current_location_id, repository)
    if not map_id:
        return session

    state = raw_state if isinstance(raw_state := session.get("state"), dict) else {}
    current = raw_map_state if isinstance(raw_map_state := state.get("map_state"), dict) else {}
    definition = repository.get(map_id)
    discovered_locations = set(model.discovered_location_ids)
    visible_object_ids = {
        item.id
        for item in definition.objects
        if item.location_id in discovered_locations
        or map_id.startswith("settlement:generated:")
        or item.location_id == model.current_location_id
    }
    route_states = {
        route.id: {
            "status": route.status,
            "known": route.known,
            "safe": route.safe,
            "direction": route.direction,
        }
        for route in model.graph.routes
    }
    map_state = {
        **current,
        "schema_version": 1,
        "current_map_id": map_id,
        "current_location_id": model.current_location_id,
        "overlay_revision": max(0, _int(current.get("overlay_revision"), fallback=0)),
        "discovered_object_ids": sorted(visible_object_ids),
        "visible_object_ids": sorted(visible_object_ids),
        "route_states": route_states,
        "object_states": raw_object_states if isinstance(raw_object_states := current.get("object_states"), dict) else {},
        "map_history": _append_unique(_string_sequence(current.get("map_history")), map_id),
        "source": "canonical_world_graph",
    }
    state["map_state"] = map_state
    state["current_location_id"] = model.current_location_id
    player = raw_player if isinstance(raw_player := state.get("player"), dict) else {}
    player["location_id"] = model.current_location_id
    state["player"] = player
    session["state"] = state
    return session


def _canonical_settlement_definition(
    model: CanonicalWorldMapModel,
    node: RpgLocationNode,
    definition: MapDefinition,
) -> MapDefinition:
    center_polygon = MapPolygon(points=((-260, -180), (260, -180), (260, 80), (-260, 80)))
    center = MapObjectDefinition(
        id=f"landmark:{node.id}:center",
        kind="landmark",
        x=definition.bounds.width // 2,
        y=definition.bounds.height // 2,
        location_id=node.id,
        label=node.name,
        description=f"The central district of {node.name}.",
        sprite=MapSprite(asset_id="asset:rpg-map:landmark-01", width=520, height=430),
        footprint=center_polygon,
        hitbox=center_polygon,
        render_order=MapRenderOrder(layer="structures", sort_y=definition.bounds.height // 2),
        tags=("canonical_world", "settlement_center"),
    )
    objects = []
    for item in definition.objects:
        route = model.graph.routes_between(node.id, item.location_id or "")
        tags = item.tags
        if route:
            tags = (*tags, f"route_id:{route[0].id}")
        objects.append(replace(item, tags=tags))

    route_geometry = []
    prefix = f"route:{node.id}:exit:"
    for geometry in definition.route_geometry:
        route_id = geometry.route_id
        if route_id.startswith(prefix):
            target_id = route_id.removeprefix(prefix)
            routes = model.graph.routes_between(node.id, target_id)
            if routes:
                route_id = routes[0].id
        route_geometry.append(replace(geometry, route_id=route_id))
    return with_definition_revision(
        replace(
            definition,
            parent_map_id=_region_map_id(node.region_id),
            objects=(center, *objects),
            route_geometry=tuple(route_geometry),
        )
    )


def _region_definition(model: CanonicalWorldMapModel, region_id: str) -> MapDefinition:
    nodes = tuple(
        sorted(
            (node for node in model.graph.locations.values() if node.region_id == region_id),
            key=lambda item: item.id,
        )
    )
    bounds = MapBounds(width=_REGION_WIDTH, height=_REGION_HEIGHT)
    anchors = _region_anchors(nodes)
    objects = tuple(_region_object(node, anchors[node.id]) for node in nodes)
    routes = []
    for route in sorted(model.graph.routes, key=lambda item: item.id):
        if route.from_id not in anchors or route.to_id not in anchors:
            continue
        routes.append(
            MapRouteGeometry(
                route_id=route.id,
                points=(anchors[route.from_id], anchors[route.to_id]),
                style="trail" if "trail" in route.tags else "road",
            )
        )
    label = region_id.replace("_", " ").replace("-", " ").upper()
    return with_definition_revision(
        MapDefinition(
            map_id=_region_map_id(region_id),
            level="region",
            seed=model.seed,
            bounds=bounds,
            background=MapBackground(
                asset_id="asset:rpg-map:northern-pass-base",
                destination_bounds=bounds,
            ),
            objects=objects,
            route_geometry=tuple(routes),
            labels=(MapLabelDefinition(f"label:region:{region_id}", label, 6000, 560, priority=100),),
        )
    )


def _region_object(node: RpgLocationNode, anchor: tuple[int, int]) -> MapObjectDefinition:
    x, y = anchor
    settlement = _is_settlement(node)
    width = 680 if settlement else 520
    height = 560 if settlement else 430
    half = width // 2
    polygon = MapPolygon(points=((-half, -height // 2), (half, -height // 2), (half, 70), (-half, 70)))
    return MapObjectDefinition(
        id=f"location:{node.id}",
        kind="landmark",
        x=x,
        y=y,
        location_id=node.id,
        child_map_id=_settlement_map_id(node.id) if settlement else None,
        label=node.name,
        description=f"A known location in {node.region_id}.",
        sprite=MapSprite(
            asset_id="asset:rpg-map:settlement-marker-01" if settlement else "asset:rpg-map:landmark-01",
            width=width,
            height=height,
        ),
        footprint=polygon,
        hitbox=polygon,
        render_order=MapRenderOrder(layer="structures", sort_y=y),
        tags=("canonical_world", *node.tags),
    )


def _region_anchors(nodes: Sequence[RpgLocationNode]) -> dict[str, tuple[int, int]]:
    if not nodes:
        return {}
    columns = max(1, ceil(sqrt(len(nodes))))
    rows = max(1, ceil(len(nodes) / columns))
    x_step = 9000 // max(1, columns - 1) if columns > 1 else 0
    y_step = 5000 // max(1, rows - 1) if rows > 1 else 0
    anchors = {}
    for index, node in enumerate(nodes):
        column = index % columns
        row = index // columns
        x = 1500 + (column * x_step if columns > 1 else 4500)
        y = 1500 + (row * y_step if rows > 1 else 2500)
        anchors[node.id] = (x, y)
    return anchors


def _locations(value: object) -> tuple[RpgLocationNode, ...]:
    rows = []
    if isinstance(value, Mapping):
        source = ({"id": key, **_mapping(item)} for key, item in value.items())
    else:
        source = cast(Any, (_mapping(item) for item in _sequence(value)))
    for raw in source:
        location_id = _first_text(raw.get("id"), raw.get("location_id"))
        if not location_id:
            raise MapWorldIntegrationError("world_location_missing_id")
        rows.append(
            RpgLocationNode(
                id=location_id,
                name=_first_text(raw.get("name"), location_id),
                region_id=_first_text(raw.get("region_id"), raw.get("region"), "world"),
                status="expanded" if _first_text(raw.get("status"), "expanded") == "expanded" else "stub",
                tags=_string_sequence(raw.get("tags")),
                services=_string_sequence(raw.get("services")),
                danger=max(0, _int(raw.get("danger"), fallback=0)),
            )
        )
    if len({item.id for item in rows}) != len(rows):
        raise MapWorldIntegrationError("duplicate_world_location_id")
    return tuple(sorted(rows, key=lambda item: item.id))


def _routes(value: object) -> tuple[RpgRoute, ...]:
    rows = []
    for raw_value in _sequence(value):
        raw = _mapping(raw_value)
        route_id = _first_text(raw.get("id"), raw.get("route_id"))
        if not route_id:
            raise MapWorldIntegrationError("world_route_missing_id")
        try:
            rows.append(
                RpgRoute(
                    from_id=_first_text(raw.get("from_id"), raw.get("from")),
                    to_id=_first_text(raw.get("to_id"), raw.get("to")),
                    status=_first_text(raw.get("status"), "open"),  # type: ignore[arg-type]
                    safe=bool(raw.get("safe", True)),
                    known=bool(raw.get("known", True)),
                    tags=_string_sequence(raw.get("tags")),
                    id=route_id,
                    direction=_first_text(raw.get("direction"), "both"),  # type: ignore[arg-type]
                )
            )
        except ValueError as exc:
            raise MapWorldIntegrationError("invalid_world_route", route_id) from exc
    if len({item.id for item in rows}) != len(rows):
        raise MapWorldIntegrationError("duplicate_world_route_id")
    return tuple(sorted(rows, key=lambda item: item.id))


def _is_settlement(node: RpgLocationNode) -> bool:
    return bool(node.services) or bool(_SETTLEMENT_TAGS.intersection(node.tags))


def _region_map_id(region_id: str) -> str:
    return f"region:generated:{region_id}"


def _settlement_map_id(location_id: str) -> str:
    return f"settlement:generated:{location_id}"


def _append_unique(values: Sequence[str], value: str) -> list[str]:
    return [*dict.fromkeys((*values, value))][-16:]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _string_sequence(value: object) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in _sequence(value) if str(item).strip()}))


def _first_text(*values: object) -> str:
    return next((str(value).strip() for value in values if value is not None and str(value).strip()), "")


def _int(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
