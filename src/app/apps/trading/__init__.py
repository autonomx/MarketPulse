"""Native Omnix Trading domain.

Names load on first use, so importing a submodule (such as the module's
declarations.py) loads nothing else (PA-2.1).
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import (
        CanonicalInstrument,
        DatasetProvenance,
        MarketBar,
        ProviderBinding,
        ProviderPolicy,
    )

_LAZY_EXPORTS = {
    "CanonicalInstrument": "app.apps.trading.models",
    "DatasetProvenance": "app.apps.trading.models",
    "MarketBar": "app.apps.trading.models",
    "ProviderBinding": "app.apps.trading.models",
    "ProviderPolicy": "app.apps.trading.models",
}

__all__ = [
    "CanonicalInstrument",
    "DatasetProvenance",
    "MarketBar",
    "ProviderBinding",
    "ProviderPolicy",
]


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)
