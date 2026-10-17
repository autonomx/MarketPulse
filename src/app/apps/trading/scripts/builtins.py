"""Built-in variables and functions of Omnix Scripts.

A function is a factory ``factory(compiler, call_node) -> closure``. ``pure`` builds one for a stateless function,
``site`` for a ``ta.*`` function that keeps per-call-site state (``ta.Site``), and outputs (``plot`` and the like)
register what they produce with the compiler.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from . import ta
from .errors import ScriptLimitError, ScriptRuntimeError, ScriptSyntaxError, ScriptUnsupportedError
from .strategy import Broker, StrategyError, StrategySettings
from .runtime import CURRENT_RUN, SERIES_NAMES, Closure, Context, ScriptInput, _na, check_string, truthy
from .syntax import Call, ColorLiteral, Literal, Name, Node, TupleExpr, Unary
from .builtins_core import (
    COLORS,
    Factory,
    MAX_SECURITIES,
    ScriptMap,
    _avg,
    _bind,
    _bool,
    _channel,
    _color_from_gradient,
    _color_new,
    _color_rgb,
    _float,
    _hlc,
    _int,
    _max,
    _min,
    _number_or_na,
    _nz,
    _round,
    _sign,
    _site_closure,
    _spec,
    pure,
    site,
    site_with_optional_source,
    variadic,
)
from .builtins_collections import (
    ARRAY_METHODS,
    DRAWING_METHODS,
    MAP_METHODS,
    STR_METHODS,
    _array_from,
    _array_new,
    _drawing_factory,
    _format,
    _namespace_function,
    _replace,
    _split,
    _substring,
    _tonumber,
    _tostring,
)
# The compiler reads these through this module (``self.builtins``).
from .builtins_collections import method as method
from .builtins_core import RESERVED_NAMES as RESERVED_NAMES
from .builtins_core import is_unsupported as is_unsupported
from .builtins_core import kind_of as kind_of

# Outputs


def _literal(node: Node | None) -> Any:
    """The value of a constant argument (literal, color, -literal, or a named constant such as shape.circle)."""
    if node is None:
        return None
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, ColorLiteral):
        return node.value
    if isinstance(node, Unary) and node.op == "-" and isinstance(node.operand, Literal) and isinstance(node.operand.value, int | float):
        return -node.operand.value
    if isinstance(node, Name):
        return _constant(node.name)
    if isinstance(node, TupleExpr):
        items = [_literal(item) for item in node.items]
        return _MISSING if any(item is _MISSING for item in items) else items
    return _MISSING


_MISSING = object()


_MAIN_ARGUMENTS = {
    "plotcandle": ("open", "high", "low", "close"),
    "plotbar": ("open", "high", "low", "close"),
    "bgcolor": ("color",),
    "barcolor": ("color",),
    "alertcondition": ("condition",),
}


def _store(values: list[Any], t: int, value: Any) -> None:
    if len(values) <= t:
        values.extend([None] * (t - len(values) + 1))
    values[t] = value


def _output(kind: str, spec: str) -> Factory:
    params, _ = _spec(spec)
    main_keys = _MAIN_ARGUMENTS.get(kind, ("series",))

    def factory(c: Any, node: Call) -> Closure:
        if c.scope.parent is not None or c.function_depth:
            raise ScriptSyntaxError(f"{node.callee}() can only be used at the top level of a script", node.line)
        if len(node.args) > len(params):
            raise ScriptSyntaxError(f"{node.callee}() takes at most {len(params)} arguments", node.line)
        arguments: dict[str, Node] = dict(zip(params, node.args, strict=False))
        for key, value in node.kwargs.items():
            if key not in params:
                raise ScriptSyntaxError(f"{node.callee}() has no argument {key!r}", node.line)
            arguments[key] = value
        missing = [key for key in main_keys if key not in arguments]
        if missing:
            raise ScriptSyntaxError(f"{node.callee}() needs a value for {missing[0]!r}", node.line)
        title_value = _literal(arguments.get("title"))
        title = str(title_value) if isinstance(title_value, str) else f"{kind} {len(c.plot_specs) + 1}"
        options: dict[str, Any] = {}
        dynamic: dict[str, Closure] = {}
        for key, arg in arguments.items():
            if key == "title" or key in main_keys:
                continue
            value = _literal(arg)
            if value is _MISSING:
                dynamic[key] = c.expression(arg)
            else:
                options[key] = value
        if len(c.plot_specs) >= 64:
            raise ScriptLimitError("a script has at most 64 plots", node.line)
        index = len(c.plot_specs)
        c.plot_specs.append((kind, title, options))
        main = [c.expression(arguments[key]) for key in main_keys]
        single = main[0] if len(main) == 1 else None

        def emit(ctx: Context) -> Any:
            run = ctx.run
            plot = run.plots[index]
            t = run.t
            value = single(ctx) if single is not None else tuple(closure(ctx) for closure in main)
            _store(plot.values, t, value)
            if dynamic:
                if plot.colors is None:
                    plot.colors = []
                _store(plot.colors, t, {key: closure(ctx) for key, closure in dynamic.items()})
            if kind == "alertcondition" and truthy(value) and t == len(run.close) - 1:
                run.alerts.append({"kind": "alertcondition", "title": plot.title, "bar": t, "message": plot.options.get("message")})
                if len(run.alerts) > run.limits.max_alerts:
                    del run.alerts[0]
            return index

        return emit

    # Its parameters, for the editor's hover and signature help (TVP-11.2).
    factory.signature = spec  # type: ignore[attr-defined]
    return factory


_SAME_SYMBOL = ("syminfo.tickerid", "syminfo.ticker")
_SAME_TIMEFRAME = ("timeframe.period",)


def _security_spec(c: Any, node: Node | None, same: tuple[str, ...]) -> str | None:
    """A request.security() symbol or timeframe as written: a string, "" for the chart's own, or "{input:<title>}" for
    an input (a top-level variable set by input.*(), or an input.*() call in place); None otherwise."""
    if isinstance(node, Literal) and isinstance(node.value, str):
        return node.value.strip().replace("|", "")
    if isinstance(node, Name) and node.name in same:
        return ""
    if isinstance(node, Name) and node.name in c.input_variables:
        title, binding = c.input_variables[node.name]
        if c.scope.lookup(node.name) is binding:  # not a local of the same name
            c.security_bindings.add(id(binding))
            return f"{{input:{title}}}"
    if isinstance(node, Call) and node.callee.startswith("input."):
        before = len(c.inputs)
        c.expression(node)  # declares the input
        if len(c.inputs) == before + 1:
            return f"{{input:{c.inputs[-1].title}}}"
    return None


def _security(c: Any, node: Call) -> Closure:
    """request.security(symbol, timeframe, expression, gaps, lookahead): the expression in another symbol's or
    timeframe's context. The script runs once in each requested context (ScriptRun._run_securities); here the chart's
    run reads that run's value for the bar."""
    params = ["symbol", "timeframe", "expression", "gaps", "lookahead", "ignore_invalid_symbol", "currency", "calc_bars_count"]
    if len(node.args) > len(params):
        raise ScriptSyntaxError(f"request.security() takes at most {len(params)} arguments", node.line)
    arguments: dict[str, Node] = dict(zip(params, node.args, strict=False))
    for key, value in node.kwargs.items():
        if key not in params:
            raise ScriptSyntaxError(f"request.security() has no argument {key!r}", node.line)
        arguments[key] = value
    if "expression" not in arguments:
        raise ScriptSyntaxError("request.security() needs an expression", node.line)
    symbol = _security_spec(c, arguments.get("symbol"), _SAME_SYMBOL)
    timeframe = _security_spec(c, arguments.get("timeframe"), _SAME_TIMEFRAME)
    if symbol is None or timeframe is None:
        raise ScriptUnsupportedError(
            "request.security() needs its symbol and timeframe written in the script, from an input, or as syminfo.tickerid"
            " and timeframe.period",
            node.line,
        )
    gaps = _literal(arguments.get("gaps")) == "barmerge.gaps_on"
    lookahead = _literal(arguments.get("lookahead")) == "barmerge.lookahead_on"
    template = f"{symbol}|{timeframe}"
    if template != "|" and template not in c.securities:
        if len(c.securities) >= MAX_SECURITIES:
            raise ScriptLimitError(f"a script requests at most {MAX_SECURITIES} other symbols or timeframes", node.line)
        c.securities.append(template)
    expression = c.expression(arguments["expression"])
    site = node.id
    # A tuple expression reads a tuple of na where its context has no value yet, so `[a, b] = request.security(...)` works.
    missing = tuple(None for _ in arguments["expression"].items) if isinstance(arguments["expression"], TupleExpr) else None

    def security(ctx: Context) -> Any:
        run = ctx.run
        key = run.security_key(template)
        if key == "|" or run.context_key == key:
            # This run is the requested context: the expression is its own.
            value = expression(ctx)
            if run.context_key is not None:
                run.record_security(site, value)
            return value
        if run.context_key is not None:
            return missing  # a request from inside another request's context: not nested
        value = run.security_value(site, key, gaps, lookahead)
        return missing if value is None else value

    return security


def _static_options(c: Any, arguments: dict[str, Node], skip: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Closure]]:
    """The options of an output drawn once (hline, fill): constants as written, and the other expressions
    (``color.new(color.teal, 85)``, an input) compiled, to evaluate on the first bar, when the output is recorded."""
    constants: dict[str, Any] = {}
    computed: dict[str, Closure] = {}
    for key, value in arguments.items():
        if key in skip:
            continue
        literal = _literal(value)
        if literal is _MISSING:
            computed[key] = c.expression(value)
        else:
            constants[key] = literal
    return constants, computed


def _hline(c: Any, node: Call) -> Closure:
    params = ["price", "title", "color", "linestyle", "linewidth", "editable", "display"]
    arguments: dict[str, Node] = dict(zip(params, node.args, strict=False))
    arguments.update(node.kwargs)
    price = c.expression(arguments["price"]) if "price" in arguments else None
    if price is None:
        raise ScriptSyntaxError("hline() needs a price", node.line)
    options, computed = _static_options(c, arguments, ("price",))

    def emit(ctx: Context) -> Any:
        # Drawn once: recorded on the first bar (fill() reads the handle there too).
        run = ctx.run
        if run.statics_done:
            return ("hline", None)
        run.hlines.append({"price": price(ctx), **options, **{key: value(ctx) for key, value in computed.items()}})
        return ("hline", len(run.hlines) - 1)

    return emit


def _fill(c: Any, node: Call) -> Closure:
    params = ["plot1", "plot2", "color", "title", "editable", "show_last", "fillgaps", "display"]
    arguments: dict[str, Node] = dict(zip(params, node.args, strict=False))
    arguments.update(node.kwargs)
    first, second = c.expression(arguments["plot1"]), c.expression(arguments["plot2"])
    options, computed = _static_options(c, arguments, ("plot1", "plot2"))

    def emit(ctx: Context) -> Any:
        run = ctx.run
        if not run.statics_done:
            # A colour that changes bar by bar is drawn in its first bar's colour.
            run.fills.append({"from": first(ctx), "to": second(ctx), **options, **{key: value(ctx) for key, value in computed.items()}})
        return None

    return emit


def _alert(c: Any, node: Call) -> Closure:
    closures = _bind(c, node, ["message", "freq"], {"freq": "alert.freq_once_per_bar"})

    def emit(ctx: Context) -> Any:
        run = ctx.run
        message = closures[0](ctx)
        run.alerts.append({"kind": "alert", "bar": run.t, "message": message if not isinstance(message, str) else check_string(message), "freq": closures[1](ctx)})
        if len(run.alerts) > run.limits.max_alerts:
            del run.alerts[0]
        return None

    return emit


_STRATEGY_PARAMS = [
    "title", "shorttitle", "overlay", "format", "precision", "scale", "pyramiding", "calc_on_order_fills", "calc_on_every_tick",
    "max_bars_back", "backtest_fill_limits_assumption", "default_qty_type", "default_qty_value", "initial_capital", "currency",
    "slippage", "commission_type", "commission_value", "process_orders_on_close", "close_entries_rule", "margin_long",
    "margin_short", "explicit_plot_zorder", "max_lines_count", "max_labels_count", "max_boxes_count", "calc_bars_count",
    "risk_free_rate", "use_bar_magnifier", "fill_orders_on_standard_ohlc", "max_polylines_count", "dynamic_requests", "behind_chart",
]


def _declaration(kind: str) -> Factory:
    params = _STRATEGY_PARAMS if kind == "strategy" else [
        "title", "shorttitle", "overlay", "format", "precision", "scale", "max_bars_back", "timeframe", "timeframe_gaps",
        "explicit_plot_zorder", "max_lines_count", "max_labels_count", "max_boxes_count", "calc_bars_count",
        "max_polylines_count", "dynamic_requests", "behind_chart",
    ]

    def factory(c: Any, node: Call) -> Closure:
        if kind not in ("indicator", "strategy"):
            raise ScriptUnsupportedError(f"{kind}() scripts are not supported yet: Omnix Scripts runs indicators and strategies", node.line)
        if c.declaration:
            raise ScriptSyntaxError("a script has one indicator() or strategy() declaration", node.line)
        if len(node.args) > len(params) or any(key not in params for key in node.kwargs):
            unknown = next((key for key in node.kwargs if key not in params), None)
            raise ScriptSyntaxError(f"{kind}() has no argument {unknown!r}" if unknown else f"{kind}() takes at most {len(params)} arguments", node.line)
        arguments: dict[str, Node] = dict(zip(params, node.args, strict=False))
        arguments.update(node.kwargs)
        declaration: dict[str, Any] = {"kind": kind, "overlay": None if kind == "strategy" else False}
        for key, value in arguments.items():
            literal = _literal(value)
            if literal is _MISSING:
                raise ScriptSyntaxError(f"{kind}() takes constant values; {key!r} isn't one", node.line)
            declaration[key] = literal
        if "title" not in declaration:
            raise ScriptSyntaxError(f"{kind}() needs a title", node.line)
        if declaration.get("timeframe"):
            raise ScriptSyntaxError("indicator(timeframe=...) is not supported yet", node.line)
        if kind == "strategy":
            # TVP-11.5: a backtest on the run's bars, never an order (scripts/strategy.py).
            if declaration.get("overlay") is None:
                declaration["overlay"] = True
            try:
                StrategySettings.from_declaration(declaration)
            except (StrategyError, TypeError, ValueError) as error:
                raise ScriptSyntaxError(f"strategy(): {error}", node.line) from None
        c.declaration = declaration
        return lambda ctx: None

    return factory


# Inputs


INPUT_SPECS = {
    "input": ("any", "defval, title=na, tooltip=na, inline=na, group=na, display=na"),
    "input.int": ("int", "defval, title=na, minval=na, maxval=na, step=na, tooltip=na, inline=na, group=na, confirm=false, options=na, display=na, active=true"),
    "input.float": ("float", "defval, title=na, minval=na, maxval=na, step=na, tooltip=na, inline=na, group=na, confirm=false, options=na, display=na, active=true"),
    "input.bool": ("bool", "defval, title=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.string": ("string", "defval, title=na, options=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.color": ("color", "defval, title=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.source": ("source", "defval, title=na, tooltip=na, inline=na, group=na, display=na, confirm=false, active=true"),
    "input.timeframe": ("timeframe", "defval, title=na, options=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.symbol": ("symbol", "defval, title=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.session": ("session", "defval, title=na, options=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.price": ("price", "defval, title=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.time": ("time", "defval, title=na, tooltip=na, inline=na, group=na, confirm=false, display=na, active=true"),
    "input.text_area": ("text_area", "defval, title=na, tooltip=na, group=na, confirm=false, display=na, active=true"),
}


def _input(name: str) -> Factory:
    kind, spec = INPUT_SPECS[name]
    params, _ = _spec(spec)

    def factory(c: Any, node: Call) -> Closure:
        arguments: dict[str, Node] = dict(zip(params, node.args, strict=False))
        for key, value in node.kwargs.items():
            if key not in params:
                raise ScriptSyntaxError(f"{name}() has no argument {key!r}", node.line)
            arguments[key] = value
        if "defval" not in arguments:
            raise ScriptSyntaxError(f"{name}() needs a default value", node.line)
        default_node = arguments["defval"]
        options: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in ("defval", "title"):
                continue
            literal = _literal(value)
            if key == "options" and not isinstance(literal, list) and literal is _MISSING:
                literal = [_literal(item) for item in getattr(value, "items", [])]
            if literal is not _MISSING and literal is not None:
                options[key] = literal
        title_value = _literal(arguments.get("title"))
        title = str(title_value) if isinstance(title_value, str) else f"input {len(c.inputs) + 1}"
        source = kind == "source" or (kind == "any" and isinstance(default_node, Name) and default_node.name in SERIES_NAMES)
        if source:
            if not (isinstance(default_node, Name) and default_node.name in SERIES_NAMES):
                raise ScriptSyntaxError(f"{name}() takes a built-in series (close, hl2, ...) as its default", node.line)
            default: Any = default_node.name
            c.inputs.append(ScriptInput(title, "source", default, options))

            def read_source(ctx: Context) -> Any:
                run = ctx.run
                chosen = run.inputs.get(title, default)
                if chosen not in SERIES_NAMES:
                    raise ScriptRuntimeError(f"input {title!r} must be a built-in series, got {chosen!r}", node.line)
                return run.series(chosen, run.t)

            return read_source
        default = _literal(default_node)
        if default is _MISSING:
            raise ScriptSyntaxError(f"{name}() takes a constant default value", node.line)
        if kind == "int" and isinstance(default, float) and default.is_integer():
            default = int(default)
        inferred = {bool: "bool", int: "int", float: "float", str: "string"}.get(type(default), "any")
        c.inputs.append(ScriptInput(title, kind if kind != "any" else inferred, default, options))
        return lambda ctx: ctx.run.inputs.get(title, default)

    return factory


# Variables


def _calendar(field: str) -> Callable[[int], Any]:
    def read(milliseconds: Any) -> Any:
        if _na(milliseconds):
            return None
        moment = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        if field == "dayofweek":
            return (moment.isoweekday() % 7) + 1  # Pine: Sunday = 1
        if field == "weekofyear":
            return moment.isocalendar()[1]
        return getattr(moment, {"dayofmonth": "day"}.get(field, field))

    return read


def _calendar_function(field: str) -> Factory:
    read = _calendar(field)
    return pure("time, timezone=na", lambda value, zone: read(value))


_CALENDAR_FIELDS = ("year", "month", "weekofyear", "dayofmonth", "dayofweek", "hour", "minute", "second")

_CONSTANT_NAMESPACES = (
    "shape.", "location.", "size.", "plot.style_", "line.style_", "label.style_", "extend.", "position.", "display.",
    "format.", "xloc.", "yloc.", "text.align_", "text.wrap_", "text.format_", "hline.style_", "barmerge.", "alert.freq_",
    "order.", "font.", "currency.", "scale.", "adjustment.", "session.", "settlement_as_close.", "backadjustment.",
)

_STRATEGY_CONSTANTS = {
    "strategy.long", "strategy.short", "strategy.fixed", "strategy.cash", "strategy.percent_of_equity",
    "strategy.commission.percent", "strategy.commission.cash_per_contract", "strategy.commission.cash_per_order",
    "strategy.oca.none", "strategy.oca.cancel", "strategy.oca.reduce",
    "strategy.direction.all", "strategy.direction.long", "strategy.direction.short",
}

_NAMED_CONSTANTS: dict[str, Any] = {
    "math.pi": math.pi, "math.e": math.e, "math.phi": (1 + math.sqrt(5)) / 2, "math.rphi": 2 / (1 + math.sqrt(5)),
    "dayofweek.sunday": 1, "dayofweek.monday": 2, "dayofweek.tuesday": 3, "dayofweek.wednesday": 4,
    "dayofweek.thursday": 5, "dayofweek.friday": 6, "dayofweek.saturday": 7,
    "na": None,
}


def _constant(name: str) -> Any:
    if name in _NAMED_CONSTANTS:
        return _NAMED_CONSTANTS[name]
    if name.startswith("color.") and name[6:] in COLORS:
        return COLORS[name[6:]]
    if name.startswith(_CONSTANT_NAMESPACES) or name in _STRATEGY_CONSTANTS:
        return name
    return _MISSING


def _timeframe_value(name: str, timeframe: str) -> Any:
    unit = timeframe[-1:] if timeframe and not timeframe.isdigit() else ""
    number = timeframe[:-1] if unit else timeframe
    multiplier = int(number) if number.isdigit() else 1
    if name == "timeframe.period":
        return timeframe
    if name == "timeframe.multiplier":
        return multiplier
    if name == "timeframe.isintraday":
        return unit in ("", "S")
    if name == "timeframe.isdaily":
        return unit == "D"
    if name == "timeframe.isweekly":
        return unit == "W"
    if name == "timeframe.ismonthly":
        return unit == "M"
    if name == "timeframe.isdwm":
        return unit in ("D", "W", "M")
    if name == "timeframe.isseconds":
        return unit == "S"
    if name == "timeframe.isminutes":
        return unit == ""
    return _MISSING


def builtin_variable(c: Any, node: Name) -> Closure | None:
    name = node.name
    if name == "close":
        return lambda ctx: ctx.run.close[ctx.run.t]
    if name == "open":
        return lambda ctx: ctx.run.open[ctx.run.t]
    if name == "high":
        return lambda ctx: ctx.run.high[ctx.run.t]
    if name == "low":
        return lambda ctx: ctx.run.low[ctx.run.t]
    if name == "volume":
        return lambda ctx: ctx.run.volume[ctx.run.t]
    if name == "bar_index":
        return lambda ctx: ctx.run.t
    if name in SERIES_NAMES:
        return lambda ctx: ctx.run.series(name, ctx.run.t)
    if name in ("last_bar_index", "last_bar_time", "barstate.islast", "barstate.islastconfirmedhistory", "barstate.isrealtime"):
        c.uses_last_bar = True
    if name == "last_bar_index":
        return lambda ctx: ctx.run.last_bar_index
    if name == "last_bar_time":
        return lambda ctx: ctx.run.time[-1]
    if name.startswith("barstate."):
        state = name[9:]
        if state == "isfirst":
            return lambda ctx: ctx.run.t == 0
        if state in ("islast", "islastconfirmedhistory"):
            return lambda ctx: ctx.run.t == len(ctx.run.close) - 1
        if state in ("isconfirmed", "ishistory", "isnew"):
            return lambda ctx: True
        if state == "isrealtime":
            return lambda ctx: False
        return None
    if name in _CALENDAR_FIELDS:
        read = _calendar(name)
        return lambda ctx: read(ctx.run.time[ctx.run.t])
    if name.startswith("syminfo."):
        field = name[8:]
        if field in ("tickerid", "ticker", "root", "description"):
            return lambda ctx: ctx.run.symbol
        if field == "mintick":
            return lambda ctx: 0.01
        if field == "timezone":
            return lambda ctx: "UTC"
        if field in ("type", "currency", "prefix", "basecurrency", "session"):
            return lambda ctx: None
        return None
    if name.startswith("timeframe."):
        if _timeframe_value(name, "60") is _MISSING:
            return None
        return lambda ctx: _timeframe_value(name, ctx.run.timeframe)
    if name.startswith("strategy.") and name in STRATEGY_VARIABLES:
        read_strategy = STRATEGY_VARIABLES[name]
        return lambda ctx: read_strategy(_broker(ctx.run))
    if name == "ta.tr":
        return _site_closure(node.id, lambda state, h, l, cl: ta.tr(state, h, l, cl, False), [], _hlc)
    if name == "ta.obv":
        return _site_closure(node.id, ta.obv, [], lambda run, t: (run.close[t], run.volume[t]))
    if name == "ta.vwap":
        return _site_closure(node.id, ta.vwap, [], lambda run, t: (run.series("hlc3", t), run.volume[t], run.starts[t]))
    if name == "ta.accdist":
        return _site_closure(node.id, _accdist, [], lambda run, t: (run.high[t], run.low[t], run.close[t], run.volume[t]))
    constant = _constant(name)
    if constant is not _MISSING:
        return lambda ctx: constant
    return None


def _accdist(state: Any, high: Any, low: Any, close: Any, volume: Any) -> tuple[Any, Any]:
    flow = 0.0 if high == low else ((close - low) - (high - close)) / (high - low) * volume
    return ta.cum(state, flow)


def _fixnan(state: Any, value: Any) -> tuple[Any, Any]:
    return (state, state) if _na(value) else (value, value)


def _vwap_function(c: Any, node: Call) -> Closure:
    closures = _bind(c, node, ["source", "anchor", "stdev_mult"], {"anchor": None, "stdev_mult": None})
    source, anchor = closures[0], closures[1]
    has_anchor = len(node.args) > 1 or "anchor" in node.kwargs
    if len(node.args) > 2 or "stdev_mult" in node.kwargs:
        raise ScriptSyntaxError("ta.vwap() bands (stdev_mult) are not supported yet", node.line)

    def step(state: Any, value: Any, volume: Any, start: datetime, reset: Any) -> tuple[Any, Any]:
        if has_anchor:
            # A custom anchor: a new period starts on each bar where it is true.
            period = (state[0] + 1 if state else 0) if truthy(reset) else (state[0] if state else 0)
            inner = (period, state[1], state[2]) if state and state[0] == period else (period, 0.0, 0.0)
            if _na(value) or _na(volume):
                return inner, None
            price_volume = inner[1] + value * volume
            total_volume = inner[2] + volume
            return (period, price_volume, total_volume), value if total_volume == 0 else price_volume / total_volume
        return ta.vwap(state, value, volume, start)

    def call(ctx: Context) -> Any:
        sites = ctx.sites
        state = sites.get(node.id)
        if state is None:
            state = sites[node.id] = ta.Site()
        run = ctx.run
        return state.run(run.t, step, source(ctx), run.volume[run.t], run.starts[run.t], anchor(ctx))

    return call


def _broker(run: Any) -> Broker:
    """The run's simulated account (TVP-11.5); strategy.* belongs in a strategy() script."""
    broker = getattr(run, "broker", None)
    if broker is None:
        raise ScriptRuntimeError("strategy.* functions and variables need a strategy() script")
    return broker


def _strategy_call(method_name: str) -> Callable[..., Any]:
    def call(*arguments: Any) -> None:
        run = CURRENT_RUN.get()
        try:
            getattr(_broker(run), method_name)(*arguments)
        except StrategyError as error:
            raise ScriptRuntimeError(f"strategy.{method_name}(): {error}") from None

    return call


def _trade_field(source: str, name: str) -> Callable[[Any], Any]:
    """strategy.closedtrades.<name>(trade_num) and strategy.opentrades.<name>(trade_num)."""

    def read(number: Any) -> Any:
        trades = getattr(_broker(CURRENT_RUN.get()), "closed_trades" if source == "closedtrades" else "open_trades")
        if _na(number) or not 0 <= int(number) < len(trades):
            return None
        trade = trades[int(number)]
        if name == "size":
            return trade.qty * trade.direction
        if name in ("entry_bar_index", "exit_bar_index"):
            return getattr(trade, name.replace("_index", ""))
        if name == "commission":
            return trade.entry_commission + trade.exit_commission
        if name == "profit" and source == "opentrades":
            run = CURRENT_RUN.get()
            assert run is not None, "a strategy trade is read inside a script run"
            return trade.unrealized(run.close[run.t])
        if name in ("max_runup", "max_drawdown"):
            return getattr(trade, name)
        return getattr(trade, name)

    return read


_TRADE_FIELDS = {
    "closedtrades": ("profit", "entry_price", "exit_price", "entry_bar_index", "exit_bar_index", "entry_time", "exit_time", "size",
                     "entry_id", "exit_id", "commission", "max_runup", "max_drawdown", "entry_comment", "exit_comment"),
    "opentrades": ("profit", "entry_price", "entry_bar_index", "entry_time", "size", "entry_id", "commission", "max_runup",
                   "max_drawdown", "entry_comment"),
}

STRATEGY_VARIABLES: dict[str, Callable[[Broker], Any]] = {
    "strategy.position_size": lambda broker: broker.position_size,
    "strategy.position_avg_price": lambda broker: broker.position_avg_price,
    "strategy.position_entry_name": lambda broker: broker.open_trades[0].entry_id if broker.open_trades else "",
    "strategy.equity": lambda broker: broker.equity_now(),
    "strategy.netprofit": lambda broker: broker.netprofit,
    "strategy.openprofit": lambda broker: broker.openprofit(),
    "strategy.grossprofit": lambda broker: sum(trade.profit for trade in broker.closed_trades if trade.profit > 0),
    "strategy.grossloss": lambda broker: -sum(trade.profit for trade in broker.closed_trades if trade.profit < 0),
    "strategy.closedtrades": lambda broker: len(broker.closed_trades),
    "strategy.opentrades": lambda broker: len(broker.open_trades),
    "strategy.wintrades": lambda broker: sum(1 for trade in broker.closed_trades if trade.profit > 0),
    "strategy.losstrades": lambda broker: sum(1 for trade in broker.closed_trades if trade.profit < 0),
    "strategy.eventrades": lambda broker: sum(1 for trade in broker.closed_trades if trade.profit == 0),
    "strategy.initial_capital": lambda broker: broker.settings.initial_capital,
    "strategy.max_drawdown": lambda broker: broker.max_drawdown,
    "strategy.max_runup": lambda broker: broker.max_runup,
    "strategy.max_contracts_held_all": lambda broker: broker.max_contracts,
}

_ENTRY_SPEC = "id, direction, qty=na, limit=na, stop=na, oca_name=na, oca_type=strategy.oca.none, comment=na, alert_message=na, disable_alert=false"


def _strategy_functions() -> dict[str, Factory]:
    functions: dict[str, Factory] = {
        "strategy.entry": pure(_ENTRY_SPEC, lambda *a: _strategy_call("entry")(*a[:8])),
        "strategy.order": pure(_ENTRY_SPEC, lambda *a: _strategy_call("order")(*a[:8])),
        "strategy.exit": pure(
            "id, from_entry=na, qty=na, qty_percent=na, profit=na, limit=na, loss=na, stop=na, trail_price=na, trail_points=na, "
            "trail_offset=na, oca_name=na, comment=na, comment_profit=na, comment_loss=na, comment_trailing=na, alert_message=na, "
            "alert_profit=na, alert_loss=na, alert_trailing=na, disable_alert=false",
            lambda *a: _strategy_call("exit")(*a[:12], a[12]),
        ),
        "strategy.close": pure("id, comment=na, qty=na, qty_percent=na, alert_message=na, immediately=false, disable_alert=false",
                               lambda *a: _strategy_call("close")(a[0], a[1], a[2], a[3], a[5])),
        "strategy.close_all": pure("comment=na, alert_message=na, immediately=false, disable_alert=false",
                                   lambda *a: _strategy_call("close_all")(a[0], a[2])),
        "strategy.cancel": pure("id", lambda id: _strategy_call("cancel")(id)),
        "strategy.cancel_all": pure("", lambda: _strategy_call("cancel_all")()),
    }
    for source, names in _TRADE_FIELDS.items():
        for name in names:
            functions[f"strategy.{source}.{name}"] = pure("trade_num", _trade_field(source, name))
    return functions


def _runtime_error(message: Any) -> None:
    raise ScriptRuntimeError(str(message))


def _timeframe_change(c: Any, node: Call) -> Closure:
    closures = _bind(c, node, ["timeframe"], {})
    period = closures[0]

    def key(start: datetime, timeframe: Any) -> Any:
        if timeframe in ("D", "1D"):
            return start.date()
        if timeframe in ("W", "1W"):
            return start.isocalendar()[:2]
        if timeframe in ("M", "1M"):
            return (start.year, start.month)
        raise ScriptRuntimeError(f"timeframe.change() supports D, W and M, not {timeframe!r}", node.line)

    def call(ctx: Context) -> Any:
        run = ctx.run
        t = run.t
        if t == 0:
            return True
        timeframe = period(ctx)
        return key(run.starts[t], timeframe) != key(run.starts[t - 1], timeframe)

    return call


def _log(level: str, message: Any) -> None:
    """log.info/warning/error: kept on the run for the editor's console (TVP-11.2)."""
    run = CURRENT_RUN.get()
    if run is not None:
        run.log(level, message)


def _ta_functions() -> dict[str, Factory]:
    """Technical analysis: ``ta.*``, with ``math.sum`` and ``fixnan``."""
    return {
        # ta
        "ta.sma": site("source, length", ta.sma),
        "ta.ema": site("source, length", ta.ema),
        "ta.rma": site("source, length", ta.rma),
        "ta.wma": site("source, length", ta.wma),
        "ta.hma": site("source, length", ta.hma),
        "ta.rsi": site("source, length", ta.rsi),
        "ta.atr": site("length", lambda s, h, l, c, n: ta.atr(s, h, l, c, n), _hlc),
        "ta.tr": site("handle_na=false", lambda s, h, l, c, handle: ta.tr(s, h, l, c, truthy(handle)), _hlc),
        "ta.stdev": site("source, length, biased=true", ta.stdev),
        "ta.variance": site("source, length, biased=true", ta.variance),
        "ta.dev": site("source, length", ta.dev),
        "ta.highest": site_with_optional_source("source, length", ta.highest, "high"),
        "ta.lowest": site_with_optional_source("source, length", ta.lowest, "low"),
        "ta.highestbars": site_with_optional_source("source, length", ta.highestbars, "high"),
        "ta.lowestbars": site_with_optional_source("source, length", ta.lowestbars, "low"),
        "ta.pivothigh": site_with_optional_source("source, leftbars, rightbars", ta.pivothigh, "high"),
        "ta.pivotlow": site_with_optional_source("source, leftbars, rightbars", ta.pivotlow, "low"),
        "ta.change": site("source, length=1", ta.change),
        "ta.mom": site("source, length", ta.change),
        "ta.roc": site("source, length", ta.roc),
        "ta.crossover": site("source1, source2", ta.crossover),
        "ta.crossunder": site("source1, source2", ta.crossunder),
        "ta.cross": site("source1, source2", ta.any_cross),
        "ta.stoch": site("source, high, low, length", ta.stoch),
        "ta.cum": site("source", ta.cum),
        "ta.macd": site("source, fastlen, slowlen, siglen", ta.macd),
        "ta.bb": site("series, length, mult", ta.bb),
        "ta.bbw": site("series, length, mult", lambda s, v, n, m: (lambda r: (r[0], None if r[1][0] in (None, 0) or r[1][1] is None else (r[1][1] - r[1][2]) / r[1][0] * 100))(ta.bb(s, v, n, m))),
        "ta.kc": site("series, length, mult, useTrueRange=true", lambda s, h, l, c, v, n, m, u: ta.kc(s, v, h, l, c, n, m, u), _hlc),
        "ta.linreg": site("source, length, offset=0", ta.linreg),
        "ta.correlation": site("source1, source2, length", ta.correlation),
        "ta.median": site("source, length", ta.median),
        "ta.percentrank": site("source, length", ta.percentrank),
        "ta.rising": site("source, length", ta.rising),
        "ta.falling": site("source, length", ta.falling),
        "ta.barssince": site("condition", ta.barssince),
        "ta.valuewhen": site("condition, source, occurrence", ta.valuewhen),
        "ta.cci": site("source, length", ta.cci),
        "ta.mfi": site("series, length", lambda s, vol, v, n: ta.mfi(s, v, vol, n), lambda run, t: (run.volume[t],)),
        "ta.dmi": site("diLength, adxSmoothing", ta.dmi, _hlc),
        "ta.supertrend": site("factor, atrPeriod", ta.supertrend, _hlc),
        "ta.sar": site("start, inc, max", ta.sar, _hlc),
        "ta.vwap": _vwap_function,
        "math.sum": site("source, length", ta.math_sum),
        "fixnan": site("source", _fixnan),
    }


def _value_functions() -> dict[str, Factory]:
    """Values: ``math.*``, conversions, ``color.*`` and ``str.*``."""
    return {
        # math
        "math.abs": pure("number", _number_or_na(abs)),
        "math.sqrt": pure("number", _number_or_na(math.sqrt)),
        "math.log": pure("number", _number_or_na(math.log)),
        "math.log10": pure("number", _number_or_na(math.log10)),
        "math.exp": pure("number", _number_or_na(math.exp)),
        "math.pow": pure("base, exponent", _number_or_na(math.pow)),
        "math.floor": pure("number", _number_or_na(math.floor)),
        "math.ceil": pure("number", _number_or_na(math.ceil)),
        "math.round": pure("number, precision=na", _round),
        "math.round_to_mintick": pure("number", lambda value: None if _na(value) else _round(value / 0.01) * 0.01),
        "math.sign": pure("number", _sign),
        "math.sin": pure("angle", _number_or_na(math.sin)),
        "math.cos": pure("angle", _number_or_na(math.cos)),
        "math.tan": pure("angle", _number_or_na(math.tan)),
        "math.asin": pure("number", _number_or_na(math.asin)),
        "math.acos": pure("number", _number_or_na(math.acos)),
        "math.atan": pure("number", _number_or_na(math.atan)),
        "math.todegrees": pure("radians", _number_or_na(math.degrees)),
        "math.toradians": pure("degrees", _number_or_na(math.radians)),
        "math.max": variadic(_max),
        "math.min": variadic(_min),
        "math.avg": variadic(_avg),
        "nz": pure("source, replacement=0", _nz),
        "na": pure("x", _na),
        "int": pure("x", _int),
        "float": pure("x", _float),
        "bool": pure("x", _bool),
        # color
        "color.new": pure("color, transp", _color_new),
        "color.rgb": pure("red, green, blue, transp=0", _color_rgb),
        "color.from_gradient": pure("value, bottom_value, top_value, bottom_color, top_color", _color_from_gradient),
        "color.r": pure("color", _channel(0)),
        "color.g": pure("color", _channel(1)),
        "color.b": pure("color", _channel(2)),
        "color.t": pure("color", _channel(3)),
        # str
        "str.tostring": pure("value, format=\"\"", _tostring),
        "str.format": variadic(_format),
        "str.length": pure("string", STR_METHODS["length"]),
        "str.contains": pure("source, str", STR_METHODS["contains"]),
        "str.startswith": pure("source, str", STR_METHODS["startswith"]),
        "str.endswith": pure("source, str", STR_METHODS["endswith"]),
        "str.pos": pure("source, str", STR_METHODS["pos"]),
        "str.upper": pure("source", STR_METHODS["upper"]),
        "str.lower": pure("source", STR_METHODS["lower"]),
        "str.trim": pure("source", STR_METHODS["trim"]),
        "str.substring": pure("source, begin_pos, end_pos=na", _substring),
        "str.replace": pure("source, target, replacement, occurrence=0", _replace),
        "str.replace_all": pure("source, target, replacement", STR_METHODS["replace_all"]),
        "str.split": pure("string, separator", _split),
        "str.tonumber": pure("string", _tonumber),
        "str.repeat": pure("source, repeat, separator=\"\"", STR_METHODS["repeat"]),
    }


def _output_functions() -> dict[str, Factory]:
    """Time, outputs, declarations, logging, and array, map and drawing constructors."""
    return {
        # time
        **{name: _calendar_function(name) for name in _CALENDAR_FIELDS},
        "timeframe.change": _timeframe_change,
        # outputs
        "plot": _output("plot", "series, title, color, linewidth, style, trackprice, histbase, offset, join, editable, show_last, display, format, precision, force_overlay, linestyle"),
        "plotshape": _output("plotshape", "series, title, style, location, color, offset, text, textcolor, editable, size, show_last, display, format, precision, force_overlay"),
        "plotchar": _output("plotchar", "series, title, char, location, color, offset, text, textcolor, editable, size, show_last, display, format, precision, force_overlay"),
        "plotarrow": _output("plotarrow", "series, title, colorup, colordown, offset, minheight, maxheight, editable, show_last, display, format, precision, force_overlay"),
        "plotcandle": _output("plotcandle", "open, high, low, close, title, color, wickcolor, editable, show_last, bordercolor, display, format, precision, force_overlay"),
        "plotbar": _output("plotbar", "open, high, low, close, title, color, editable, show_last, display, format, precision, force_overlay"),
        "bgcolor": _output("bgcolor", "color, offset, editable, show_last, title, display, force_overlay"),
        "barcolor": _output("barcolor", "color, offset, editable, show_last, title, display"),
        "alertcondition": _output("alertcondition", "condition, title, message"),
        "hline": _hline,
        "fill": _fill,
        "request.security": _security,
        "alert": _alert,
        "indicator": _declaration("indicator"),
        "strategy": _declaration("strategy"),
        "library": _declaration("library"),
        "runtime.error": pure("message", _runtime_error),
        "log.info": pure("message", lambda message: _log("info", message)),
        "log.warning": pure("message", lambda message: _log("warning", message)),
        "log.error": pure("message", lambda message: _log("error", message)),
        # arrays and maps
        "array.from": variadic(_array_from),
        "array.new": pure("size=0, initial_value=na", _array_new()),
        "array.new_float": pure("size=0, initial_value=na", _array_new()),
        "array.new_int": pure("size=0, initial_value=na", _array_new()),
        "array.new_bool": pure("size=0, initial_value=false", _array_new(False)),
        "array.new_string": pure("size=0, initial_value=na", _array_new()),
        "array.new_color": pure("size=0, initial_value=na", _array_new()),
        "array.new_line": pure("size=0, initial_value=na", _array_new()),
        "array.new_label": pure("size=0, initial_value=na", _array_new()),
        "array.new_box": pure("size=0, initial_value=na", _array_new()),
        "array.new_table": pure("size=0, initial_value=na", _array_new()),
        "map.new": variadic(lambda: ScriptMap()),
        "label.new": _drawing_factory("label"),
        "line.new": _drawing_factory("line"),
        "box.new": _drawing_factory("box"),
        "table.new": _drawing_factory("table"),
        "linefill.new": _drawing_factory("linefill"),
    }


def _build_functions() -> dict[str, Factory]:
    """Every built-in function, by name; built once at import."""
    functions: dict[str, Factory] = {**_ta_functions(), **_value_functions(), **_output_functions()}
    functions.update(_strategy_functions())
    functions["ta.vwma"] = site("source, length", lambda s, vol, v, n: ta.vwma(s, v, vol, n), lambda run, t: (run.volume[t],))
    for _name in INPUT_SPECS:
        functions[_name] = _input(_name)
    for _name in ARRAY_METHODS:
        functions.setdefault(f"array.{_name}", _namespace_function("array", _name))
    for _name in MAP_METHODS:
        functions.setdefault(f"map.{_name}", _namespace_function("map", _name))
    for _kind, _methods in DRAWING_METHODS.items():
        for _name in _methods:
            functions.setdefault(f"{_kind}.{_name}", _namespace_function(_kind, _name))
    return functions


# Read-only: the table is fixed once the module has loaded.
FUNCTIONS: Mapping[str, Factory] = MappingProxyType(_build_functions())


def builtin_function(name: str) -> Factory | None:
    return FUNCTIONS.get(name)
