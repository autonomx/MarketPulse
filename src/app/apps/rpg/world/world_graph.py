"""Deterministic RPG world graph and location stub helpers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping, Sequence

LocationStatus = Literal["stub", "expanded"]
RouteStatus = Literal["open", "blocked", "locked"]
RouteDirection = Literal["both", "forward"]
TravelMode = Literal["instant", "blocked", "unknown"]


@dataclass(frozen=True)
class RpgLocationNode:
    id: str
    name: str
    region_id: str
    status: LocationStatus = "stub"
    tags: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    danger: int = 0

    def expanded(self, *, tags: Sequence[str] = (), services: Sequence[str] = (), danger: int | None = None) -> "RpgLocationNode":
        return replace(
            self,
            status="expanded",
            tags=tuple(tags) or self.tags,
            services=tuple(services) or self.services,
            danger=self.danger if danger is None else danger,
        )


@dataclass(frozen=True)
class RpgRoute:
    """Canonical route with backward-compatible legacy ID derivation.

    `id` and `direction` are trailing fields so older positional construction remains
    readable while persisted/new map data supplies explicit stable IDs.
    """

    from_id: str
    to_id: str
    status: RouteStatus = "open"
    safe: bool = True
    known: bool = True
    tags: tuple[str, ...] = ()
    id: str = ""
    direction: RouteDirection = "both"

    def __post_init__(self) -> None:
        if not self.from_id or not self.to_id:
            raise ValueError("route_endpoint_missing")
        if self.status not in {"open", "blocked", "locked"}:
            raise ValueError(f"unsupported_route_status:{self.status}")
        if self.direction not in {"both", "forward"}:
            raise ValueError(f"unsupported_route_direction:{self.direction}")
        if not self.id:
            legacy_id = f"route:{self.from_id}:{self.to_id}"
            object.__setattr__(self, "id", legacy_id)

    def connects(self, location_id: str, target_id: str) -> bool:
        return {self.from_id, self.to_id} == {location_id, target_id}

    def allows(self, location_id: str, target_id: str) -> bool:
        if self.from_id == location_id and self.to_id == target_id:
            return True
        return self.direction == "both" and self.to_id == location_id and self.from_id == target_id

    def other(self, location_id: str) -> str | None:
        if self.from_id == location_id:
            return self.to_id
        if self.direction == "both" and self.to_id == location_id:
            return self.from_id
        return None


@dataclass(frozen=True)
class RpgTravelResult:
    ok: bool
    from_id: str
    to_id: str
    mode: TravelMode
    reason: str
    requires_narration: bool = False
    route_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "mode": self.mode,
            "reason": self.reason,
            "requires_narration": self.requires_narration,
            "route_id": self.route_id,
        }


@dataclass(frozen=True)
class RpgRegionGraph:
    locations: Mapping[str, RpgLocationNode] = field(default_factory=dict)
    routes: tuple[RpgRoute, ...] = ()

    def get_location(self, location_id: str) -> RpgLocationNode | None:
        return self.locations.get(location_id)

    def known_exits(self, location_id: str) -> tuple[str, ...]:
        exits = {
            other
            for route in self.routes
            if route.known and (other := route.other(location_id)) is not None
        }
        return tuple(sorted(exits))

    def routes_between(self, location_id: str, target_id: str) -> tuple[RpgRoute, ...]:
        return tuple(
            sorted(
                (route for route in self.routes if route.allows(location_id, target_id)),
                key=lambda route: route.id,
            )
        )

