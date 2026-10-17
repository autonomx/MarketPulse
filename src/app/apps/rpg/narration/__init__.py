"""RPG Narration — Narrative text generation from events.

This module provides the Narrator Agent for converting events into
compelling narrative prose.
"""
# Exports load on first use (PEP 562): importing a submodule of this
# package does not import every module re-exported here.
from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.apps.rpg.narration.narrator import NarratorAgent

_EXPORTS = {
    "NarratorAgent": ("app.apps.rpg.narration.narrator", "NarratorAgent"),
}

__all__ = ["NarratorAgent"]


def __getattr__(name: str) -> Any:
    # The import system caches the module; nothing is cached here.
    try:
        module, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return getattr(importlib.import_module(module, __name__), attribute)


def __dir__() -> list[str]:
    return sorted(set(vars(sys.modules[__name__])) | set(_EXPORTS))
