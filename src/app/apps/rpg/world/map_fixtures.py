"""Deterministic starter map definitions for the RPG interactive map vertical slice."""

from __future__ import annotations

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

FROST_HAVEN_MAP_ID = "settlement:frost_haven"
NORTHERN_PASS_MAP_ID = "region:northern_pass"


def starter_map_definitions() -> tuple[MapDefinition, ...]:
    return (_northern_pass_definition(), _frost_haven_definition())


def _box(width: int, depth: int) -> MapPolygon:
    half_width = width // 2
    return MapPolygon(
        points=(
            (-half_width, -depth),
            (half_width, -depth),
            (half_width, 0),
            (-half_width, 0),
        )
    )


def _object(
    object_id: str,
    kind: str,
    x: int,
    y: int,
    *,
    location_id: str | None = None,
    label: str,
    description: str,
    asset_id: str,
    width: int = 620,
    height: int = 500,
    depth: int = 220,
    tags: tuple[str, ...] = (),
) -> MapObjectDefinition:
    footprint = _box(max(180, width - 120), depth)
    hitbox = _box(width, max(depth + 120, height // 2))
    return MapObjectDefinition(
        id=object_id,
        kind=kind,  # type: ignore[arg-type]
        x=x,
        y=y,
        location_id=location_id,
        label=label,
        description=description,
        sprite=MapSprite(asset_id=asset_id, width=width, height=height),
        footprint=footprint,
        hitbox=hitbox,
        render_order=MapRenderOrder(layer="structures", sort_y=y),
        tags=tags,
    )


def _frost_haven_definition() -> MapDefinition:
    bounds = MapBounds(width=10000, height=6000)
    objects = (
        _object(
            "building:frost_haven_inn",
            "building",
            2150,
            4220,
            location_id="rusty_flagon_tavern",
            label="The Frosted Flagon",
            description="A timber inn serving travelers from the western road.",
            asset_id="asset:rpg-map:timber-inn-01",
            width=760,
            height=620,
            tags=("inn", "service", "rest"),
        ),
        _object(
            "building:frost_haven_smithy",
            "building",
            3370,
            3500,
            location_id="frost_haven_smithy",
            label="Ember & Iron Smithy",
            description="A low stone forge with a broad yard and smoking chimney.",
            asset_id="asset:rpg-map:smithy-01",
            width=720,
            height=570,
            tags=("smithy", "service", "trade"),
        ),
        _object(
            "building:frost_haven_market",
            "building",
            4700,
            3920,
            location_id="market_district",
            label="Market Hall",
            description="Covered stalls and store rooms surrounding a busy square.",
            asset_id="asset:rpg-map:market-hall-01",
            width=820,
            height=580,
            tags=("market", "service", "trade"),
        ),
        _object(
            "building:frost_haven_shrine",
            "building",
            5630,
            3050,
            location_id="frost_haven_shrine",
            label="Shrine of the First Flame",
            description="A small sanctuary maintained by road wardens.",
            asset_id="asset:rpg-map:shrine-01",
            width=560,
            height=590,
            tags=("shrine", "service"),
        ),
        _object(
            "building:frost_haven_keep",
            "building",
            7130,
            2560,
            location_id="frost_haven_keep",
            label="Frost Haven Keep",
            description="The watch command and fortified refuge for the settlement.",
            asset_id="asset:rpg-map:keep-01",
            width=980,
            height=820,
            depth=320,
            tags=("keep", "guard", "authority"),
        ),
        _object(
            "building:frost_haven_storehouse",
            "building",
            6550,
            4190,
            location_id="frost_haven_storehouse",
            label="North Storehouse",
            description="A guarded warehouse for grain, salt, and caravan supplies.",
            asset_id="asset:rpg-map:storehouse-01",
            width=720,
            height=500,
            tags=("storage", "trade"),
        ),
        _object(
            "building:frost_haven_healer",
            "building",
            7900,
            3710,
            location_id="frost_haven_healer",
            label="Juniper House",
            description="A healer's lodge surrounded by hardy mountain herbs.",
            asset_id="asset:rpg-map:healer-01",
            width=620,
            height=520,
            tags=("healer", "service"),
        ),
        _object(
            "landmark:frost_haven_well",
            "landmark",
            5170,
            4540,
            location_id="frost_haven_square",
            label="Old Well",
            description="The settlement well and common meeting point.",
            asset_id="asset:rpg-map:well-01",
            width=300,
            height=250,
            depth=120,
            tags=("landmark",),
        ),
        _object(
            "gate:frost_haven_west",
            "gate",
            950,
            4620,
            location_id="frost_haven_west_gate",
            label="West Gate",
            description="The road gate leading toward the market road and lowlands.",
            asset_id="asset:rpg-map:gate-wood-01",
            width=620,
            height=680,
            tags=("gate", "travel"),
        ),
        _object(
            "gate:frost_haven_north",
            "gate",
            8380,
            1820,
            location_id="frost_haven_north_gate",
            label="North Gate",
            description="A reinforced gate opening onto the mountain pass.",
            asset_id="asset:rpg-map:gate-stone-01",
            width=680,
            height=720,
            tags=("gate", "travel"),
        ),
        _object(
            "landmark:frost_haven_watchtower",
            "landmark",
            8920,
            2720,
            location_id="frost_haven_watchtower",
            label="East Watchtower",
            description="A signal tower watching the northern road and lower valley.",
            asset_id="asset:rpg-map:watchtower-01",
            width=520,
            height=900,
            tags=("watchtower", "guard"),
        ),
        _object(
            "building:frost_haven_caravan_yard",
            "building",
            3000,
            4860,
            location_id="frost_haven_caravan_yard",
            label="Caravan Yard",
            description="Stables, sheds, and a counting office beside the west road.",
            asset_id="asset:rpg-map:caravan-yard-01",
            width=900,
            height=520,
            depth=280,
            tags=("stable", "travel", "trade"),
        ),
    )
    routes = (
        MapRouteGeometry(
            route_id="route:frost_haven:west_gate_market",
            points=((950, 4620), (2200, 4430), (3500, 4170), (4700, 3920)),
        ),
        MapRouteGeometry(
            route_id="route:frost_haven:market_keep",
            points=((4700, 3920), (5400, 3600), (6300, 3060), (7130, 2560)),
        ),
        MapRouteGeometry(
            route_id="route:frost_haven:market_north_gate",
            points=((4700, 3920), (6000, 3890), (7300, 3300), (8380, 1820)),
        ),
        MapRouteGeometry(
            route_id="route:frost_haven:market_inn",
            points=((4700, 3920), (3550, 4030), (2150, 4220)),
            style="street",
        ),
    )
    return MapDefinition(
        map_id=FROST_HAVEN_MAP_ID,
        level="settlement",
        parent_map_id=NORTHERN_PASS_MAP_ID,
        seed=824193,
        bounds=bounds,
        background=MapBackground(
            asset_id="asset:rpg-map:frost-haven-base",
            destination_bounds=bounds,
        ),
        objects=objects,
        route_geometry=routes,
        labels=(
            MapLabelDefinition("label:frost_haven", "FROST HAVEN", 5200, 2600, priority=100),
            MapLabelDefinition("label:frost_haven_market", "MARKET QUARTER", 4520, 4380, priority=60),
        ),
    )


def _northern_pass_definition() -> MapDefinition:
    bounds = MapBounds(width=12000, height=7600)
    objects = (
        _object(
            "settlement:frost_haven_marker",
            "landmark",
            5300,
            4300,
            location_id="frost_haven",
            label="Frost Haven",
            description="A fortified mountain settlement and caravan refuge.",
            asset_id="asset:rpg-map:settlement-marker-01",
            width=700,
            height=560,
            tags=("settlement", "known"),
        ),
        _object(
            "landmark:glimmerdeep_arch",
            "landmark",
            8850,
            1730,
            location_id="glimmerdeep_pass",
            label="Glimmerdeep Pass",
            description="An ancient arch marks the highest known crossing.",
            asset_id="asset:rpg-map:ancient-arch-01",
            width=720,
            height=680,
            tags=("pass", "danger"),
        ),
        _object(
            "landmark:old_quarry",
            "landmark",
            8200,
            5200,
            location_id="old_quarry",
            label="Old Quarry",
            description="Abandoned workings cut into the eastern ridge.",
            asset_id="asset:rpg-map:quarry-01",
            width=760,
            height=520,
            tags=("quarry", "mystery"),
        ),
        _object(
            "settlement:frostpine_hollow",
            "landmark",
            2650,
            2850,
            location_id="frostpine_hollow",
            label="Frostpine Hollow",
            description="A wooded hamlet below the exposed pass.",
            asset_id="asset:rpg-map:hamlet-marker-01",
            width=620,
            height=500,
            tags=("settlement",),
        ),
        _object(
            "landmark:northern_watch",
            "landmark",
            10100,
            3150,
            location_id="northern_watchtower",
            label="Northern Watch",
            description="A remote watchtower overlooking the high road.",
            asset_id="asset:rpg-map:watchtower-01",
            width=480,
            height=760,
            tags=("watchtower", "guard"),
        ),
    )
    return MapDefinition(
        map_id=NORTHERN_PASS_MAP_ID,
        level="region",
        seed=519044,
        bounds=bounds,
        background=MapBackground(
            asset_id="asset:rpg-map:northern-pass-base",
            destination_bounds=bounds,
        ),
        objects=objects,
        route_geometry=(
            MapRouteGeometry(
                "route:northern_pass:frostpine_frost_haven",
                ((2650, 2850), (3900, 3500), (5300, 4300)),
                style="road",
            ),
            MapRouteGeometry(
                "route:northern_pass:frost_haven_glimmerdeep",
                ((5300, 4300), (6700, 3400), (7700, 2450), (8850, 1730)),
                style="mountain_road",
            ),
            MapRouteGeometry(
                "route:northern_pass:frost_haven_quarry",
                ((5300, 4300), (6700, 4700), (8200, 5200)),
                style="trail",
            ),
            MapRouteGeometry(
                "route:northern_pass:glimmerdeep_watch",
                ((8850, 1730), (9550, 2300), (10100, 3150)),
                style="trail",
            ),
        ),
        labels=(MapLabelDefinition("label:northern_pass", "THE NORTHERN PASS", 6100, 900, priority=100),),
    )
