"""Omnix Scripts builtins: argument binding, values and colours (moved out of builtins.py)."""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any
from . import ta
from .errors import ScriptRuntimeError, ScriptSyntaxError
from .runtime import Closure, Context, Drawing, _na, truthy
from .syntax import Call

Factory = Callable[[Any, Call], Closure]

RESERVED_NAMES = {"open", "high", "low", "close", "volume", "time", "bar_index", "na", "true", "false"}

# Namespaces Omnix Scripts doesn't support yet, so their names say so instead of "unknown".
UNSUPPORTED_PREFIXES = ("request.", "strategy.risk.", "matrix.", "ticker.", "polyline.", "chart.", "library")


# request.security() contexts a script may use besides the chart's own.
MAX_SECURITIES = 5


def is_unsupported(name: str) -> bool:
    return name.startswith(UNSUPPORTED_PREFIXES)


# Argument binding


def _spec(text: str) -> tuple[list[str], dict[str, Any]]:
    params: list[str] = []
    defaults: dict[str, Any] = {}
    for part in (item.strip() for item in text.split(",") if item.strip()):
        if "=" in part:
            name, raw = (piece.strip() for piece in part.split("=", 1))
            defaults[name] = _DEFAULT_LITERALS.get(raw, raw) if not _is_number(raw) else (float(raw) if "." in raw else int(raw))
        else:
            name = part
        params.append(name)
    return params, defaults


_DEFAULT_LITERALS: dict[str, Any] = {"na": None, "true": True, "false": False, '""': ""}


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _bind(c: Any, node: Call, params: list[str], defaults: dict[str, Any], strict: bool = True) -> list[Closure]:
    if len(node.args) > len(params):
        raise ScriptSyntaxError(f"{node.callee}() takes at most {len(params)} arguments", node.line)
    bound: dict[str, Closure] = {}
    for name, arg in zip(params, node.args, strict=False):
        bound[name] = c.expression(arg)
    for key, arg in node.kwargs.items():
        if key not in params:
            if strict:
                raise ScriptSyntaxError(f"{node.callee}() has no argument {key!r}", node.line)
            continue
        if key in bound:
            raise ScriptSyntaxError(f"{node.callee}() got {key!r} twice", node.line)
        bound[key] = c.expression(arg)
    closures: list[Closure] = []
    for name in params:
        if name in bound:
            closures.append(bound[name])
        elif name in defaults:
            value = defaults[name]

            def constant(ctx: Any, value: Any = value) -> Any:
                return value

            closures.append(constant)
        else:
            raise ScriptSyntaxError(f"{node.callee}() needs a value for {name!r}", node.line)
    return closures


def pure(spec: str, function: Callable[..., Any]) -> Factory:
    params, defaults = _spec(spec)

    def factory(c: Any, node: Call) -> Closure:
        closures = _bind(c, node, params, defaults)
        if len(closures) == 1:
            (a,) = closures
            return lambda ctx: function(a(ctx))
        if len(closures) == 2:
            a, b = closures
            return lambda ctx: function(a(ctx), b(ctx))
        return lambda ctx: function(*[closure(ctx) for closure in closures])

    # Its parameters, for the editor's hover and signature help (TVP-11.2).
    factory.signature = spec  # type: ignore[attr-defined]
    return factory


def variadic(function: Callable[..., Any]) -> Factory:
    def factory(c: Any, node: Call) -> Closure:
        if node.kwargs:
            raise ScriptSyntaxError(f"{node.callee}() takes values only", node.line)
        closures = [c.expression(arg) for arg in node.args]
        return lambda ctx: function(*[closure(ctx) for closure in closures])

    return factory


BarInputs = Callable[[Any, int], tuple[Any, ...]]


def _site_closure(node_id: int, step: ta.Step, closures: list[Closure], bar: BarInputs | None) -> Closure:
    def call(ctx: Context) -> Any:
        sites = ctx.sites
        state = sites.get(node_id)
        if state is None:
            state = sites[node_id] = ta.Site()
        run = ctx.run
        t = run.t
        values = [closure(ctx) for closure in closures]
        if bar is not None:
            return state.run(t, step, *bar(run, t), *values)
        return state.run(t, step, *values)

    return call


def site(spec: str, step: ta.Step, bar: BarInputs | None = None) -> Factory:
    params, defaults = _spec(spec)

    def factory(c: Any, node: Call) -> Closure:
        return _site_closure(node.id, step, _bind(c, node, params, defaults), bar)

    # Its parameters, for the editor's hover and signature help (TVP-11.2).
    factory.signature = spec  # type: ignore[attr-defined]
    return factory


def site_with_optional_source(spec: str, step: ta.Step, source: str) -> Factory:
    """ta.highest(length) reads `high`; ta.highest(source, length) the source. Same for the pivots."""
    full_params, defaults = _spec(spec)
    short_params = full_params[1:]

    def factory(c: Any, node: Call) -> Closure:
        provided = len(node.args) + sum(1 for key in node.kwargs if key in full_params)
        if provided < len(full_params) - len(defaults) and "source" not in node.kwargs:
            closures = _bind(c, node, short_params, defaults)
            return _site_closure(node.id, step, closures, lambda run, t: (run.series(source, t),))
        return _site_closure(node.id, step, _bind(c, node, full_params, defaults), None)

    # Its parameters, for the editor's hover and signature help (TVP-11.2).
    factory.signature = spec  # type: ignore[attr-defined]
    return factory


def _hlc(run: Any, t: int) -> tuple[Any, ...]:
    return run.high[t], run.low[t], run.close[t]


# Values


class ScriptArray:
    __slots__ = ("items",)

    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def __repr__(self) -> str:
        return f"array({self.items!r})"


class ScriptMap:
    __slots__ = ("items",)

    def __init__(self) -> None:
        self.items: dict[Any, Any] = {}


def kind_of(value: Any) -> str | None:
    if isinstance(value, ScriptArray):
        return "array"
    if isinstance(value, ScriptMap):
        return "map"
    if isinstance(value, Drawing):
        return value.kind
    if isinstance(value, str):
        return "str"
    return None


def _nz(value: Any, replacement: Any = 0) -> Any:
    return replacement if _na(value) else value


def _number_or_na(function: Callable[..., float]) -> Callable[..., Any]:
    def wrapped(*values: Any) -> Any:
        if any(_na(value) for value in values):
            return None
        try:
            result = function(*values)
        except (ValueError, ZeroDivisionError, OverflowError):
            return None
        return None if isinstance(result, float) and (math.isnan(result) or math.isinf(result)) else result

    return wrapped


def _round(value: Any, precision: Any = None) -> Any:
    if _na(value) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    if precision is None or _na(precision):
        # Pine rounds halves up, like JavaScript.
        return int(math.floor(value + 0.5))
    factor = 10 ** max(0, min(15, int(precision)))
    return math.floor(value * factor + 0.5) / factor


def _max(*values: Any) -> Any:
    if not values or any(_na(value) for value in values):
        return None
    return max(values)


def _min(*values: Any) -> Any:
    if not values or any(_na(value) for value in values):
        return None
    return min(values)


def _avg(*values: Any) -> Any:
    if not values or any(_na(value) for value in values):
        return None
    return ta.js_sum(values) / len(values)


def _sign(value: Any) -> Any:
    if _na(value):
        return None
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def _int(value: Any) -> Any:
    if _na(value):
        return None
    if not isinstance(value, int | float):
        raise ScriptRuntimeError("int() takes a number")
    return int(value) if math.isfinite(value) else None


def _float(value: Any) -> Any:
    if _na(value):
        return None
    if not isinstance(value, int | float):
        raise ScriptRuntimeError("float() takes a number")
    return float(value)


def _bool(value: Any) -> Any:
    return truthy(value)


# Colors


COLORS = {
    "aqua": "#00BCD4", "black": "#363A45", "blue": "#2196F3", "fuchsia": "#E040FB", "gray": "#787B86",
    "green": "#4CAF50", "lime": "#00E676", "maroon": "#880E4F", "navy": "#311B92", "olive": "#808000",
    "orange": "#FF9800", "purple": "#9C27B0", "red": "#F23645", "silver": "#B2B5BE", "teal": "#089981",
    "white": "#FFFFFF", "yellow": "#FDD835",
}


def _rgba(color: Any) -> tuple[int, int, int, float] | None:
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", color):
        return None
    red, green, blue = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    alpha = int(color[7:9], 16) if len(color) == 9 else 255
    return red, green, blue, alpha / 255


def _color(red: float, green: float, blue: float, transparency: float) -> str:
    alpha = round(255 * (1 - max(0.0, min(100.0, transparency)) / 100))
    channels = [max(0, min(255, int(round(channel)))) for channel in (red, green, blue)]
    return "#" + "".join(f"{channel:02X}" for channel in channels) + ("" if alpha == 255 else f"{alpha:02X}")


def _color_new(color: Any, transparency: Any) -> Any:
    rgba = _rgba(color)
    if rgba is None or _na(transparency):
        return None
    return _color(rgba[0], rgba[1], rgba[2], float(transparency))


def _color_rgb(red: Any, green: Any, blue: Any, transparency: Any = 0) -> Any:
    if any(_na(value) for value in (red, green, blue)):
        return None
    return _color(red, green, blue, float(_nz(transparency)))


def _color_from_gradient(value: Any, bottom: Any, top: Any, bottom_color: Any, top_color: Any) -> Any:
    low, high = _rgba(bottom_color), _rgba(top_color)
    if _na(value) or _na(bottom) or _na(top) or low is None or high is None:
        return None
    share = 0.0 if top == bottom else max(0.0, min(1.0, (value - bottom) / (top - bottom)))
    mixed = [low[i] + (high[i] - low[i]) * share for i in range(4)]
    return _color(mixed[0], mixed[1], mixed[2], 100 * (1 - mixed[3]))


def _channel(index: int) -> Callable[[Any], Any]:
    def read(color: Any) -> Any:
        rgba = _rgba(color)
        if rgba is None:
            return None
        return (100 * (1 - rgba[3])) if index == 3 else rgba[index]

    return read
