"""Versioned deterministic repository for RPG map definitions."""

from __future__ import annotations

from app.caching.bounded_cache import bounded_lru_cache
from typing import Iterable

from app.apps.rpg.world.map_contracts import MapContractError, MapDefinition
from app.apps.rpg.world.map_hierarchy_fixtures import hierarchical_starter_map_definitions
from app.apps.rpg.world.map_serialization import with_definition_revision


class MapDefinitionNotFound(KeyError):
    def __init__(self, map_id: str) -> None:
        self.map_id = map_id
        super().__init__(map_id)


class MapDefinitionRepository:
    """Read-only-by-default registry with deterministic revision assignment."""

    def __init__(self, definitions: Iterable[MapDefinition] = ()) -> None:
        self._definitions: dict[str, MapDefinition] = {}
        for definition in definitions:
            self.register(definition)
        self.validate_hierarchy()

    def register(self, definition: MapDefinition) -> MapDefinition:
        normalized = with_definition_revision(definition)
        current = self._definitions.get(normalized.map_id)
        if current is not None and current.definition_revision != normalized.definition_revision:
            raise MapContractError("map_definition_id_collision", normalized.map_id)
        self._definitions[normalized.map_id] = normalized
        return normalized

    def get(self, map_id: str) -> MapDefinition:
        try:
            return self._definitions[map_id]
        except KeyError as exc:
            raise MapDefinitionNotFound(map_id) from exc

    def find(self, map_id: str) -> MapDefinition | None:
        return self._definitions.get(map_id)

    def list(self) -> tuple[MapDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def revisions(self) -> dict[str, str]:
        return {
            definition.map_id: definition.definition_revision
            for definition in self.list()
        }

    def validate_hierarchy(self) -> None:
        map_ids = set(self._definitions)
        for definition in self._definitions.values():
            if definition.parent_map_id and definition.parent_map_id not in map_ids:
                raise MapContractError("missing_parent_map_definition", definition.parent_map_id)
            for item in definition.objects:
                if item.child_map_id and item.child_map_id not in map_ids:
                    raise MapContractError("missing_child_map_definition", item.child_map_id)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_map_repository() -> MapDefinitionRepository:
    return MapDefinitionRepository(hierarchical_starter_map_definitions())
