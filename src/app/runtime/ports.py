"""Typed extension points composed at startup (ADR-0016).

A consumer declares a ``Port`` in its own contract. Modules implement it by
declaring a ``ContributionSpec`` in ``FeatureModule.contributions``; the
composition root builds every enabled contribution and installs the result as
one immutable ``PortBindings``. Consumers ask for implementations by port,
never by a string name, and handle absence explicitly.

- ``many``: every implementation, ordered by ``(priority, feature id)``.
- ``at_most_one``: one implementation or ``None``; two is a startup error.
- ``exactly_one``: bound only by the composition layer. A module's
  implementation can always be disabled on its own, so a module contribution
  to an exactly-one port is rejected when the feature is declared.

Registration grants no authority: a bound capability executor still enforces
grants, approvals and the tool policy itself.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Generic, Literal, Mapping, TypeVar, get_args

T = TypeVar("T")
PortCardinality = Literal["exactly_one", "at_most_one", "many"]
COMPOSITION = "composition"


class PortBindingError(RuntimeError):
    """Bound implementations do not match a port's cardinality."""


class PortUnbound(RuntimeError):
    """An exactly-one port has no implementation."""


@dataclass(frozen=True, slots=True, eq=False)
class Port(Generic[T]):
    """A typed extension point; ports compare by identity, not by name."""

    name: str
    protocol: Any
    cardinality: PortCardinality

    def __post_init__(self) -> None:
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError("port name must be normalized")
        if self.cardinality not in get_args(PortCardinality):
            raise ValueError(f"Port {self.name} has invalid cardinality: {self.cardinality!r}")


@dataclass(frozen=True, slots=True)
class ContributionSpec:
    """A module's implementation of a port, built from its FeatureContext."""

    port: Port[Any]
    factory: Callable[..., object]
    priority: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.port, Port):
            raise TypeError("a contribution must name a Port")
        if self.port.cardinality == "exactly_one":
            raise ValueError(
                f"Port {self.port.name} is exactly-one; only the composition layer binds it"
            )
        if type(self.priority) is not int:
            raise TypeError("contribution priority must be an int")


@dataclass(frozen=True, slots=True)
class PortBinding:
    port: Port[Any]
    implementation: object
    owner: str = COMPOSITION
    priority: int = 100


class PortBindings:
    """An immutable, validated set of bindings for one composed process."""

    __slots__ = ("_bindings",)

    def __init__(self, bindings: Mapping[Port[Any], tuple[object, ...]]) -> None:
        self._bindings = MappingProxyType(dict(bindings))

    @classmethod
    def build(cls, bindings: Iterable[PortBinding], *, required: Iterable[Port[Any]] = ()) -> "PortBindings":
        grouped: dict[Port[Any], list[PortBinding]] = {}
        for binding in bindings:
            if binding.port.cardinality == "exactly_one" and binding.owner != COMPOSITION:
                raise PortBindingError(f"Port {binding.port.name} is exactly-one; {binding.owner} cannot bind it")
            grouped.setdefault(binding.port, []).append(binding)
        for port, items in grouped.items():
            if port.cardinality != "many" and len(items) > 1:
                owners = ", ".join(sorted(item.owner for item in items))
                raise PortBindingError(f"Port {port.name} accepts one implementation; bound by {owners}")
        for port in required:
            if port.cardinality != "exactly_one":
                raise PortBindingError(f"Only exactly-one ports can be required: {port.name}")
            if port not in grouped:
                raise PortUnbound(f"Port {port.name} has no implementation")
        return cls({
            port: tuple(item.implementation for item in sorted(items, key=lambda item: (item.priority, item.owner)))
            for port, items in grouped.items()
        })

    def implementations(self, port: Port[T]) -> tuple[T, ...]:
        return self._bindings.get(port, ())  # type: ignore[return-value]

    def owners(self) -> dict[str, int]:
        """Port name to implementation count, for diagnostics."""
        return {port.name: len(items) for port, items in self._bindings.items()}


_LOCK = RLock()
_INSTALLED = PortBindings({})


def install_port_bindings(bindings: PortBindings) -> None:
    """Replace the process's bindings; called by the composition root."""
    global _INSTALLED
    if not isinstance(bindings, PortBindings):
        raise TypeError("install_port_bindings requires PortBindings")
    with _LOCK:
        _INSTALLED = bindings


def installed_port_bindings() -> PortBindings:
    with _LOCK:
        return _INSTALLED


def implementations(port: Port[T]) -> tuple[T, ...]:
    """Every implementation of a ``many`` port, in priority order."""
    if port.cardinality != "many":
        raise TypeError(f"Port {port.name} is {port.cardinality}; use optional() or required()")
    return installed_port_bindings().implementations(port)


def optional(port: Port[T]) -> T | None:
    """The implementation of an ``at_most_one`` port, or ``None``."""
    if port.cardinality != "at_most_one":
        raise TypeError(f"Port {port.name} is {port.cardinality}; use implementations() or required()")
    bound = installed_port_bindings().implementations(port)
    return bound[0] if bound else None


def required(port: Port[T]) -> T:
    """The implementation of an ``exactly_one`` port."""
    if port.cardinality != "exactly_one":
        raise TypeError(f"Port {port.name} is {port.cardinality}; use implementations() or optional()")
    bound = installed_port_bindings().implementations(port)
    if not bound:
        raise PortUnbound(f"Port {port.name} has no implementation")
    return bound[0]


def reset_port_bindings_for_tests() -> None:
    install_port_bindings(PortBindings({}))


__all__ = [
    "COMPOSITION",
    "ContributionSpec",
    "Port",
    "PortBinding",
    "PortBindingError",
    "PortBindings",
    "PortCardinality",
    "PortUnbound",
    "implementations",
    "install_port_bindings",
    "installed_port_bindings",
    "optional",
    "required",
    "reset_port_bindings_for_tests",
]
