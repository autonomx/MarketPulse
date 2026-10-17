"""The strategy registry (WP-8.3).

Two kinds of entries:

- a ``Strategy`` (see ``contract``) is evaluated by the generic
  ``StrategyRunner``;
- a ``MonitorOwnedStrategy`` names a kind that a bespoke monitor still runs
  (gap pullback and 5m Stoch RSI in the strategy monitor). It declares the
  kind's configuration model and allowed modes so persisted configurations
  are validated the same way, and moves to the runner when its monitor does.

Entries are registered explicitly in ``registrations.py``; nothing registers
itself on import.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from .contract import Strategy
from .models import StrategyMode


class UnknownStrategyKind(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MonitorOwnedStrategy:
    kind: str
    config_model: type[BaseModel]
    allowed_modes: tuple[StrategyMode, ...]
    owner: str
    # The validation error a disallowed mode reports.
    mode_rejection: str = "strategy_mode_not_allowed"


RegisteredStrategy = Strategy | MonitorOwnedStrategy


class StrategyRegistry:
    def __init__(self, entries: Iterable[RegisteredStrategy]) -> None:
        self._entries: dict[str, RegisteredStrategy] = {}
        for entry in entries:
            if not isinstance(entry, MonitorOwnedStrategy) and not isinstance(entry, Strategy):
                raise TypeError(f"{entry!r} implements neither the strategy contract nor a monitor-owned entry")
            if entry.kind in self._entries:
                raise ValueError(f"strategy kind registered twice: {entry.kind}")
            if not isinstance(entry, MonitorOwnedStrategy) and "auto_paper" in entry.allowed_modes:
                # Runner strategies only record proposals until entries from
                # the runner go through the order gateway's authorization.
                raise ValueError(f"{entry.kind}: runner strategies are shadow-only")
            self._entries[entry.kind] = entry

    @property
    def entries(self) -> tuple[RegisteredStrategy, ...]:
        return tuple(self._entries.values())

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def get(self, kind: str) -> RegisteredStrategy:
        try:
            return self._entries[kind]
        except KeyError:
            raise UnknownStrategyKind(f"unknown strategy kind: {kind}") from None

    def config_models(self) -> tuple[type[BaseModel], ...]:
        models: list[type[BaseModel]] = []
        for entry in self._entries.values():
            if entry.config_model not in models:
                models.append(entry.config_model)
        return tuple(models)

    def runner_strategies(self) -> tuple[Strategy, ...]:
        return tuple(entry for entry in self._entries.values() if not isinstance(entry, MonitorOwnedStrategy))

    def with_entries(self, *entries: RegisteredStrategy) -> "StrategyRegistry":
        return StrategyRegistry((*self._entries.values(), *entries))
