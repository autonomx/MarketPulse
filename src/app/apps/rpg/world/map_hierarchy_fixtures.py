"""Deterministic parent/child map hierarchy fixtures."""

from __future__ import annotations

from dataclasses import replace

from app.apps.rpg.world.map_contracts import (
    MapBackground,
    MapBounds,
    MapDefinition,
    MapLabelDefinition,
    MapObjectDefinition,
    MapPolygon,
    MapRenderOrder,
    MapSprite,
)
from app.apps.rpg.world.map_fixtures import FROST_HAVEN_MAP_ID, starter_map_definitions

FROSTED_FLAGON_INTERIOR_MAP_ID = "interior:frosted_flagon"


def hierarchical_starter_map_definitions() -> tuple[MapDefinition, ...]:
    definitions = list(starter_map_definitions())
    settlement_index = next(index for index, item in enumerate(definitions) if item.map_id == FROST_HAVEN_MAP_ID)
    settlement = definitions[settlement_index]
    objects = tuple(
        replace(item, child_map_id=FROSTED_FLAGON_INTERIOR_MAP_ID)
        if item.id == "building:frost_haven_inn"
        else item
        for item in settlement.objects
    )
    definitions[settlement_index] = replace(settlement, objects=objects)
    definitions.append(_frosted_flagon_interior())
    return tuple(definitions)


def _frosted_flagon_interior() -> MapDefinition:
    bounds = MapBounds(width=2400, height=1500)
    return MapDefinition(
        map_id=FROSTED_FLAGON_INTERIOR_MAP_ID,
        level="interior",
        parent_map_id=FROST_HAVEN_MAP_ID,
        seed=824194,
        bounds=bounds,
        background=MapBackground(
            asset_id="asset:rpg-map:frosted-flagon-interior-base",
            destination_bounds=bounds,
        ),
        objects=(
            _hotspot(
                "interior:flagon_entry",
                "entrance",
                320,
                1260,
                "rusty_flagon_tavern",
                "Front Door",
                "The door opens back onto Frost Haven.",
                child_map_id=FROST_HAVEN_MAP_ID,
                tags=("exit", "travel"),
            ),
            _hotspot(
                "interior:flagon_counter",
                "prop",
                1120,
                760,
                "rusty_flagon_counter",
                "Service Counter",
                "Bran serves meals, rooms, and local news from the worn oak counter.",
                tags=("service", "trade", "talk"),
            ),
            _hotspot(
                "interior:flagon_hearth",
                "landmark",
                1900,
                620,
                "rusty_flagon_hearth",
                "Stone Hearth",
                "A broad hearth keeps the common room warm through mountain nights.",
                tags=("rest", "landmark"),
            ),
            _hotspot(
                "interior:flagon_tables",
                "prop",
                1420,
                1120,
                "rusty_flagon_common_room",
                "Common Room Tables",
                "Travelers gather here to eat, argue, and trade rumors.",
                tags=("occupants", "talk"),
            ),
            _hotspot(
                "interior:flagon_stairs",
                "entrance",
                2070,
                1180,
                "rusty_flagon_rooms",
                "Guest Room Stairs",
                "A narrow stair climbs to the rented rooms.",
                tags=("rooms", "rest"),
            ),
        ),
        labels=(MapLabelDefinition("label:frosted_flagon", "THE FROSTED FLAGON", 1200, 180, priority=100),),
    )


def _hotspot(
    object_id: str,
    kind: str,
    x: int,
    y: int,
    location_id: str,
    label: str,
    description: str,
    *,
    child_map_id: str | None = None,
    tags: tuple[str, ...] = (),
) -> MapObjectDefinition:
    footprint = MapPolygon(points=((-150, -110), (150, -110), (150, 40), (-150, 40)))
    hitbox = MapPolygon(points=((-190, -190), (190, -190), (190, 70), (-190, 70)))
    return MapObjectDefinition(
        id=object_id,
        kind=kind,  # type: ignore[arg-type]
        x=x,
        y=y,
        location_id=location_id,
        child_map_id=child_map_id,
        label=label,
        description=description,
        sprite=MapSprite(asset_id=f"asset:rpg-map:{object_id.replace(':', '-')}", width=360, height=280),
        footprint=footprint,
        hitbox=hitbox,
        render_order=MapRenderOrder(layer="structures", sort_y=y),
        tags=tags,
    )
