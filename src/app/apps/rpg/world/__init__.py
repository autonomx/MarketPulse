# Exports load on first use (PEP 562): importing a submodule of this
# package does not import every module re-exported here.
from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .economy_system import EconomySystem, Market
    from .faction_system import Faction, FactionSystem
    from .political_system import Leader, PoliticalSystem
    from .reputation_engine import FactionStanding, ReputationEngine
    from .resource_system import ResourceManager, ResourcePool
    from .world_state import WorldState

_EXPORTS = {
    "EconomySystem": (".economy_system", "EconomySystem"),
    "Market": (".economy_system", "Market"),
    "Faction": (".faction_system", "Faction"),
    "FactionSystem": (".faction_system", "FactionSystem"),
    "Leader": (".political_system", "Leader"),
    "PoliticalSystem": (".political_system", "PoliticalSystem"),
    "FactionStanding": (".reputation_engine", "FactionStanding"),
    "ReputationEngine": (".reputation_engine", "ReputationEngine"),
    "ResourceManager": (".resource_system", "ResourceManager"),
    "ResourcePool": (".resource_system", "ResourcePool"),
    "WorldState": (".world_state", "WorldState"),
}

__all__ = ["EconomySystem", "Market", "Faction", "FactionSystem", "Leader", "PoliticalSystem", "FactionStanding", "ReputationEngine", "ResourceManager", "ResourcePool", "WorldState"]


def __getattr__(name: str) -> Any:
    # The import system caches the module; nothing is cached here.
    try:
        module, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return getattr(importlib.import_module(module, __name__), attribute)


def __dir__() -> list[str]:
    return sorted(set(vars(sys.modules[__name__])) | set(_EXPORTS))
