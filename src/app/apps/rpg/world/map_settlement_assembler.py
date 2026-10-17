"""Seeded integer-only procedural settlement assembler for RPG maps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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
from app.apps.rpg.world.map_serialization import with_definition_revision
from app.apps.rpg.world.world_graph import RpgRegionGraph

ASSEMBLER_VERSION = 1
_SETTLEMENT_WIDTH = 10000
_SETTLEMENT_HEIGHT = 6400
_ROAD_CENTER = (_SETTLEMENT_WIDTH // 2, _SETTLEMENT_HEIGHT // 2)


@dataclass(frozen=True)
class SettlementZone:
    id: str
    kind: str
    bounds: MapBounds


@dataclass(frozen=True)
class SettlementParcel:
    id: str
    zone_id: str
    bounds: MapBounds
    road_access: tuple[int, int]


@dataclass(frozen=True)
class SettlementAssembly:
    assembler_version: int
    seed: int
    source_location_id: str
    zones: tuple[SettlementZone, ...]
    parcels: tuple[SettlementParcel, ...]
    definition: MapDefinition

class StableMapRng:
    """Small fixed 64-bit generator whose behavior is independent of Python random."""

    def __init__(self, seed: int) -> None:
        self._state = (int(seed) ^ 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        if self._state == 0:
            self._state = 0xD1B54A32D192ED03

    def next_u64(self) -> int:
        value = self._state
        value ^= value >> 12
        value ^= (value << 25) & 0xFFFFFFFFFFFFFFFF
        value ^= value >> 27
        self._state = value & 0xFFFFFFFFFFFFFFFF
        return (self._state * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF

    def between(self, minimum: int, maximum: int) -> int:
        if maximum < minimum:
            raise ValueError("invalid_rng_range")
        return minimum + self.next_u64() % (maximum - minimum + 1)

    def shuffled(self, values: Sequence[str]) -> tuple[str, ...]:
        items = list(values)
        for index in range(len(items) - 1, 0, -1):
            swap = self.between(0, index)
            items[index], items[swap] = items[swap], items[index]
        return tuple(items)


def assemble_settlement_map(
    seed: int,
    graph: RpgRegionGraph,
    settlement_location_id: str,
    *,
    map_id: str | None = None,
) -> SettlementAssembly:
    source = graph.get_location(settlement_location_id)
    if source is None:
        raise ValueError(f"settlement_location_missing:{settlement_location_id}")
    rng = StableMapRng(_mixed_seed(seed, graph, settlement_location_id))
    resolved_map_id = map_id or f"settlement:generated:{settlement_location_id}"
    bounds = MapBounds(width=_SETTLEMENT_WIDTH, height=_SETTLEMENT_HEIGHT)
    zones = _zones()
    parcels = _parcels(zones, rng)
    building_roles = _building_roles(source.services, len(parcels), rng)
    buildings = tuple(
        _building(parcel, role, index, settlement_location_id, rng)
        for index, (parcel, role) in enumerate(zip(parcels, building_roles, strict=True))
    )
    gates = _gate_objects(graph, settlement_location_id)
    roads = _road_geometry(graph, settlement_location_id)
    definition = with_definition_revision(
        MapDefinition(
            map_id=resolved_map_id,
            level="settlement",
            seed=int(seed),
            bounds=bounds,
            background=MapBackground(
                asset_id="asset:rpg-map:frost-haven-base",
                destination_bounds=bounds,
            ),
            objects=(*buildings, *gates),
            route_geometry=roads,
            labels=(
                MapLabelDefinition(
                    id=f"label:{settlement_location_id}",
                    text=source.name.upper(),
                    x=_ROAD_CENTER[0],
                    y=650,
                    priority=100,
                ),
            ),
        )
    )
    assembly = SettlementAssembly(
        assembler_version=ASSEMBLER_VERSION,
        seed=int(seed),
        source_location_id=settlement_location_id,
        zones=zones,
        parcels=parcels,
        definition=definition,
    )
    validate_settlement_assembly(assembly)
    return assembly


def validate_settlement_assembly(assembly: SettlementAssembly) -> None:
    bounds = assembly.definition.bounds
    parcel_ids = {parcel.id for parcel in assembly.parcels}
    if len(parcel_ids) != len(assembly.parcels):
        raise ValueError("duplicate_settlement_parcel_id")
    for parcel in assembly.parcels:
        if not _contains_bounds(bounds, parcel.bounds):
            raise ValueError(f"parcel_out_of_bounds:{parcel.id}")
    for index, left in enumerate(assembly.parcels):
        for right in assembly.parcels[index + 1 :]:
            if _overlaps(left.bounds, right.bounds):
                raise ValueError(f"parcel_collision:{left.id}:{right.id}")
    building_objects = [item for item in assembly.definition.objects if item.id.startswith("building:")]
    if len(building_objects) != len(assembly.parcels):
        raise ValueError("building_parcel_count_mismatch")
    for item, parcel in zip(building_objects, assembly.parcels, strict=True):
        if not parcel.bounds.contains((item.x, item.y)):
            raise ValueError(f"building_anchor_outside_parcel:{item.id}")


def _zones() -> tuple[SettlementZone, ...]:
    return (
        SettlementZone("zone:northwest", "residential", MapBounds(x=900, y=900, width=3300, height=1900)),
        SettlementZone("zone:northeast", "civic", MapBounds(x=5800, y=900, width=3300, height=1900)),
        SettlementZone("zone:southwest", "trade", MapBounds(x=900, y=3700, width=3300, height=1800)),
        SettlementZone("zone:southeast", "craft", MapBounds(x=5800, y=3700, width=3300, height=1800)),
    )


def _parcels(zones: tuple[SettlementZone, ...], rng: StableMapRng) -> tuple[SettlementParcel, ...]:
    parcels: list[SettlementParcel] = []
    for zone in zones:
        columns = 2
        rows = 2
        gutter = 180
        cell_width = (zone.bounds.width - gutter) // columns
        cell_height = (zone.bounds.height - gutter) // rows
        for row in range(rows):
            for column in range(columns):
                jitter_x = rng.between(-70, 70)
                jitter_y = rng.between(-55, 55)
                inset = rng.between(85, 145)
                x = zone.bounds.x + column * (cell_width + gutter) + inset + jitter_x
                y = zone.bounds.y + row * (cell_height + gutter) + inset + jitter_y
                width = cell_width - inset * 2
                height = cell_height - inset * 2
                parcel_id = f"parcel:{zone.id.split(':')[-1]}:{row}:{column}"
                access_x = _ROAD_CENTER[0] if column == (0 if "east" in zone.id else 1) else x + width // 2
                access_y = y + height // 2
                parcels.append(
                    SettlementParcel(
                        id=parcel_id,
                        zone_id=zone.id,
                        bounds=MapBounds(x=x, y=y, width=width, height=height),
                        road_access=(access_x, access_y),
                    )
                )
    return tuple(sorted(parcels, key=lambda item: item.id))


def _building_roles(services: tuple[str, ...], count: int, rng: StableMapRng) -> tuple[str, ...]:
    defaults = ("inn", "market", "smithy", "healer", "shrine", "storehouse", "house", "stable")
    normalized = tuple(sorted({str(item).strip().lower() for item in services if str(item).strip()}))
    pool = (*normalized, *defaults)
    roles = []
    shuffled = rng.shuffled(pool)
    for index in range(count):
        roles.append(shuffled[index % len(shuffled)])
    return tuple(roles)


def _building(
    parcel: SettlementParcel,
    role: str,
    index: int,
    settlement_location_id: str,
    rng: StableMapRng,
) -> MapObjectDefinition:
    width = min(parcel.bounds.width - 80, rng.between(520, 760))
    height = min(parcel.bounds.height - 80, rng.between(430, 620))
    depth = max(180, min(parcel.bounds.height // 2, rng.between(210, 310)))
    x = parcel.bounds.x + parcel.bounds.width // 2
    y = parcel.bounds.y + parcel.bounds.height - 45
    object_id = f"building:{settlement_location_id}:{index:02d}:{role}"
    footprint = _rectangle_polygon(width - 100, depth)
    hitbox = _rectangle_polygon(width, max(depth + 100, height // 2))
    return MapObjectDefinition(
        id=object_id,
        kind="building",
        x=x,
        y=y,
        location_id=f"{settlement_location_id}:{role}:{index:02d}",
        label=_role_label(role, index),
        description=f"A procedurally assembled {role} parcel in {settlement_location_id}.",
        sprite=MapSprite(asset_id=_role_asset(role), width=width, height=height),
        footprint=footprint,
        hitbox=hitbox,
        render_order=MapRenderOrder(layer="structures", sort_y=y),
        tags=("generated", role, parcel.zone_id),
    )


def _gate_objects(graph: RpgRegionGraph, settlement_location_id: str) -> tuple[MapObjectDefinition, ...]:
    exits = graph.known_exits(settlement_location_id)
    anchors = ((5000, 5900), (9500, 3200), (5000, 500), (500, 3200))
    objects = []
    for index, target_id in enumerate(exits[:4]):
        x, y = anchors[index]
        objects.append(
            MapObjectDefinition(
                id=f"gate:{settlement_location_id}:{target_id}",
                kind="gate",
                x=x,
                y=y,
                location_id=target_id,
                label=f"{target_id.replace('_', ' ').title()} Gate",
                description=f"The road from {settlement_location_id} toward {target_id}.",
                sprite=MapSprite(asset_id="asset:rpg-map:gate-stone-01", width=620, height=680),
                footprint=_rectangle_polygon(500, 200),
                hitbox=_rectangle_polygon(650, 420),
                render_order=MapRenderOrder(layer="structures", sort_y=y),
                tags=("generated", "gate", "travel"),
            )
        )
    return tuple(objects)


def _road_geometry(graph: RpgRegionGraph, settlement_location_id: str) -> tuple[MapRouteGeometry, ...]:
    center_x, center_y = _ROAD_CENTER
    roads = [
        MapRouteGeometry(
            route_id=f"route:{settlement_location_id}:main-north-south",
            points=((center_x, 500), (center_x, center_y), (center_x, 5900)),
            style="road",
        ),
        MapRouteGeometry(
            route_id=f"route:{settlement_location_id}:main-east-west",
            points=((500, center_y), (center_x, center_y), (9500, center_y)),
            style="street",
        ),
    ]
    anchors = ((center_x, 5900), (9500, center_y), (center_x, 500), (500, center_y))
    for index, target_id in enumerate(graph.known_exits(settlement_location_id)[:4]):
        roads.append(
            MapRouteGeometry(
                route_id=f"route:{settlement_location_id}:exit:{target_id}",
                points=(_ROAD_CENTER, anchors[index]),
                style="trail" if index % 2 else "road",
            )
        )
    return tuple(roads)


def _mixed_seed(seed: int, graph: RpgRegionGraph, settlement_location_id: str) -> int:
    value = int(seed) & 0xFFFFFFFFFFFFFFFF
    tokens = [settlement_location_id, *sorted(graph.locations), *sorted(route.id for route in graph.routes)]
    for token in tokens:
        for byte in token.encode("utf-8"):
            value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def _rectangle_polygon(width: int, depth: int) -> MapPolygon:
    half = width // 2
    return MapPolygon(points=((-half, -depth), (half, -depth), (half, 0), (-half, 0)))


def _contains_bounds(outer: MapBounds, inner: MapBounds) -> bool:
    return (
        outer.x <= inner.x
        and outer.y <= inner.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def _overlaps(left: MapBounds, right: MapBounds) -> bool:
    return not (
        left.x + left.width <= right.x
        or right.x + right.width <= left.x
        or left.y + left.height <= right.y
        or right.y + right.height <= left.y
    )


def _role_asset(role: str) -> str:
    if role in {"smithy", "shrine", "keep"}:
        return "asset:rpg-map:stone-building-01"
    return "asset:rpg-map:timber-inn-01"


def _role_label(role: str, index: int) -> str:
    base = role.replace("_", " ").title()
    return base if index == 0 else f"{base} {index + 1}"
