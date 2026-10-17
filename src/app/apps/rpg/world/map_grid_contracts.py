"""Minimum deterministic square-grid contracts for campaign map instances."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

GRID_MAP_SCHEMA_VERSION: Final = 1
GridPoint = tuple[int, int]


def _canonical_hash(value: BaseModel | Mapping[str, Any]) -> str:
    payload = (
        value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, BaseModel)
        else dict(value)
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FrozenGridModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GridTransform(FrozenGridModel):
    cell_width: int = Field(default=100, gt=0)
    cell_height: int = Field(default=100, gt=0)
    visual_origin_x: int = 0
    visual_origin_y: int = 0
    display_offset_x: int = 1
    display_offset_y: int = 1

class TerrainRule(FrozenGridModel):
    code: str = Field(min_length=1, max_length=1)
    terrain_id: str = Field(min_length=1)
    walkable: bool = True
    movement_cost: int = Field(default=10, ge=1)
    blocks_sight: bool = False


class GridPortalEndpoint(FrozenGridModel):
    map_id: str = Field(min_length=1)
    cell: GridPoint


class GridPortal(FrozenGridModel):
    portal_id: str = Field(min_length=1)
    source: GridPortalEndpoint
    target: GridPortalEndpoint
    direction: Literal["both", "forward"] = "both"
    state: Literal["open", "closed", "locked", "blocked"] = "open"
    secret: bool = False


class GridSpawnPoint(FrozenGridModel):
    spawn_point_id: str = Field(min_length=1)
    cell: GridPoint
    tags: tuple[str, ...] = ()
    secret: bool = False


class GridActorPlacement(FrozenGridModel):
    actor_id: str = Field(min_length=1)
    cell: GridPoint
    facing: Literal[
        "north",
        "northeast",
        "east",
        "southeast",
        "south",
        "southwest",
        "west",
        "northwest",
    ] = "south"
    footprint_width: int = Field(default=1, ge=1)
    footprint_height: int = Field(default=1, ge=1)
    hidden: bool = False


class GridZone(FrozenGridModel):
    zone_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    cells: tuple[GridPoint, ...]
    secret: bool = False


class GridMapDefinition(FrozenGridModel):
    schema_version: Literal[1] = GRID_MAP_SCHEMA_VERSION
    map_id: str = Field(min_length=1)
    level: Literal["settlement", "dungeon", "interior", "encounter"]
    definition_revision: int = Field(ge=1)
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    transform: GridTransform = Field(default_factory=GridTransform)
    terrain_palette: tuple[TerrainRule, ...]
    terrain_rows: tuple[str, ...]
    portals: tuple[GridPortal, ...] = ()
    spawn_points: tuple[GridSpawnPoint, ...] = ()
    zones: tuple[GridZone, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    definition_hash: str = ""
    semantic_interface_hash: str = ""

    @model_validator(mode="after")
    def validate_grid(self) -> "GridMapDefinition":
        palette_codes = [rule.code for rule in self.terrain_palette]
        if len(palette_codes) != len(set(palette_codes)):
            raise ValueError("duplicate_terrain_code")
        if len(self.terrain_rows) != self.height:
            raise ValueError("terrain_row_count_mismatch")
        known = set(palette_codes)
        for row in self.terrain_rows:
            if len(row) != self.width:
                raise ValueError("terrain_column_count_mismatch")
            if set(row) - known:
                raise ValueError("unknown_terrain_code")
        portal_ids = [portal.portal_id for portal in self.portals]
        spawn_ids = [spawn.spawn_point_id for spawn in self.spawn_points]
        zone_ids = [zone.zone_id for zone in self.zones]
        if len(portal_ids) != len(set(portal_ids)):
            raise ValueError("duplicate_portal_id")
        if len(spawn_ids) != len(set(spawn_ids)):
            raise ValueError("duplicate_spawn_point_id")
        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError("duplicate_zone_id")
        for portal in self.portals:
            if portal.source.map_id == self.map_id:
                self.require_inside(portal.source.cell)
            if portal.target.map_id == self.map_id:
                self.require_inside(portal.target.cell)
        for spawn in self.spawn_points:
            self.require_inside(spawn.cell)
            if not self.is_walkable(spawn.cell):
                raise ValueError(f"spawn_point_not_walkable:{spawn.spawn_point_id}")
        for zone in self.zones:
            for cell in zone.cells:
                self.require_inside(cell)
        if self.definition_hash and not self.definition_hash.startswith("sha256:"):
            raise ValueError("grid_definition_hash_invalid")
        if self.semantic_interface_hash and not self.semantic_interface_hash.startswith(
            "sha256:"
        ):
            raise ValueError("grid_semantic_interface_hash_invalid")
        return self

    def require_inside(self, cell: GridPoint) -> None:
        column, row = cell
        if column < 0 or row < 0 or column >= self.width or row >= self.height:
            raise ValueError(f"grid_cell_out_of_bounds:{column}:{row}")

    def terrain_rule(self, cell: GridPoint) -> TerrainRule:
        self.require_inside(cell)
        column, row = cell
        code = self.terrain_rows[row][column]
        return next(rule for rule in self.terrain_palette if rule.code == code)

    def is_walkable(self, cell: GridPoint) -> bool:
        return self.terrain_rule(cell).walkable

    def movement_cost(self, cell: GridPoint) -> int:
        return self.terrain_rule(cell).movement_cost


def with_grid_definition_hashes(definition: GridMapDefinition) -> GridMapDefinition:
    semantic = {
        "map_id": definition.map_id,
        "world_id": definition.world_id,
        "world_revision": definition.world_revision,
        "portal_ids": sorted(portal.portal_id for portal in definition.portals),
        "spawn_point_ids": sorted(
            spawn.spawn_point_id for spawn in definition.spawn_points
        ),
        "zone_ids": sorted(zone.zone_id for zone in definition.zones),
    }
    normalized = definition.model_copy(
        update={
            "definition_hash": "",
            "semantic_interface_hash": _canonical_hash(semantic),
        }
    )
    return normalized.model_copy(update={"definition_hash": _canonical_hash(normalized)})
