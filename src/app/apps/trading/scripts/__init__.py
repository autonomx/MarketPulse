"""Omnix Scripts (TVP-11): a Pine Script-compatible subset, interpreted on the server.

``parse_script`` reads a script into a syntax tree, ``compile_script`` turns it into a program, and ``run_script``
runs the program bar by bar over a ``BarSeries``. The supported subset is described in
``docs/trading/OMNIX_SCRIPTS_SPEC.md``.

Names load on first use, so importing a submodule (``errors``, ``limits``) loads no interpreter.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .errors import ScriptError, ScriptLimitError, ScriptSyntaxError, ScriptUnsupportedError
    from .limits import ScriptLimits
    from .parser import parse_script
    from .runtime import ScriptResult, ScriptRun, compile_script, run_script

_LAZY_EXPORTS = {
    "ScriptError": "app.apps.trading.scripts.errors",
    "ScriptLimitError": "app.apps.trading.scripts.errors",
    "ScriptLimits": "app.apps.trading.scripts.limits",
    "ScriptResult": "app.apps.trading.scripts.runtime",
    "ScriptRun": "app.apps.trading.scripts.runtime",
    "ScriptSyntaxError": "app.apps.trading.scripts.errors",
    "ScriptUnsupportedError": "app.apps.trading.scripts.errors",
    "compile_script": "app.apps.trading.scripts.runtime",
    "parse_script": "app.apps.trading.scripts.parser",
    "run_script": "app.apps.trading.scripts.runtime",
}

__all__ = [
    "ScriptError",
    "ScriptLimitError",
    "ScriptLimits",
    "ScriptResult",
    "ScriptRun",
    "ScriptSyntaxError",
    "ScriptUnsupportedError",
    "compile_script",
    "parse_script",
    "run_script",
]


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)
