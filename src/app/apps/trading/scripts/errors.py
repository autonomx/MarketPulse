"""Errors a script can raise, each with the line (and column) it refers to."""

from __future__ import annotations


class ScriptError(Exception):
    """A script that can't be read or run. ``line`` and ``column`` are 1-based; 0 when unknown."""

    kind = "error"

    def __init__(self, message: str, line: int = 0, column: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def __str__(self) -> str:
        where = f"line {self.line}" + (f", column {self.column}" if self.column else "") if self.line else ""
        return f"{self.message} ({where})" if where else self.message


class ScriptSyntaxError(ScriptError):
    kind = "syntax"


class ScriptUnsupportedError(ScriptError):
    """Valid Pine that Omnix Scripts doesn't support (yet)."""

    kind = "unsupported"


class ScriptRuntimeError(ScriptError):
    kind = "runtime"


class ScriptLimitError(ScriptError):
    """A run that went over its bar, loop, drawing or time limit."""

    kind = "limit"
