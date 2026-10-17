"""Omnix Scripts builtins: strings, arrays, maps and drawings (moved out of builtins.py)."""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any
from . import ta
from .errors import ScriptRuntimeError
from .runtime import Closure, Context, Drawing, _na, allocate, check_string, current_limits
from .syntax import Call
from .builtins_core import (
    Factory,
    ScriptArray,
    ScriptMap,
    _bind,
    _spec,
)

# Strings


def _tostring(value: Any, format: Any = "") -> Any:
    if value is None:
        return "NaN"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if format in ("format.percent",):
            return f"{value:.2f}%"
        if isinstance(format, str) and format.startswith("#"):
            decimals = len(format.split(".", 1)[1]) if "." in format else 0
            text = f"{value:.{decimals}f}"
            return text.rstrip("0").rstrip(".") if "." in text and "0" not in format.split(".", 1)[-1] else text
        if format == "format.mintick":
            return f"{value:.2f}"
        return repr(value) if not value.is_integer() else str(int(value)) if abs(value) < 1e16 else repr(value)
    if isinstance(value, ScriptArray):
        return "[" + _bounded_join(", ", (str(_tostring(item)) for item in value.items), 2) + "]"
    return str(value)


def _bounded_join(separator: str, parts: Any, reserve: int = 0) -> str:
    """Joins parts, stopping as soon as the result would pass the string limit (never building more)."""
    limit = current_limits().max_string_length - reserve
    pieces: list[str] = []
    total = 0
    for index, part in enumerate(parts):
        total += len(part) + (len(separator) if index else 0)
        if total > limit:
            check_string(" " * (limit + reserve + 1))
        pieces.append(part)
    return separator.join(pieces)


_PLACEHOLDER = re.compile(r"\{(\d+)(?:,([^{}]*))?\}")


def _format(template: Any, *values: Any) -> Any:
    """{0}, {1,number,#.##}: one pass over the template, so a value containing braces is never re-read."""
    if not isinstance(template, str):
        return None

    limit = current_limits().max_string_length
    pieces: list[str] = []
    total = 0
    position = 0
    for match in _PLACEHOLDER.finditer(template):
        pieces.append(template[position : match.start()])
        index = int(match.group(1))
        if index >= len(values):
            text = match.group(0)
        else:
            value = values[index]
            parts = (match.group(2) or "").split(",")
            pattern = parts[1] if len(parts) > 1 else ""
            text = str(_tostring(float(value) if isinstance(value, int | float) and not isinstance(value, bool) else value, pattern))
        pieces.append(text)
        total += match.start() - position + len(text)
        # Stops as soon as the result passes the limit, before building it.
        if total > limit:
            check_string(" " * (limit + 1))
        position = match.end()
    pieces.append(template[position:])
    return check_string("".join(pieces))


def _substring(source: Any, begin: Any, end: Any = None) -> Any:
    if not isinstance(source, str) or _na(begin) or not isinstance(begin, int | float) or not (end is None or isinstance(end, int | float)):
        return None
    return source[int(begin) :] if end is None else source[int(begin) : int(end)]


def _tonumber(text: Any) -> Any:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _split(text: Any, separator: Any) -> Any:
    if not isinstance(text, str) or not isinstance(separator, str):
        return None
    parts = list(text) if separator == "" else text.split(separator)
    allocate(len(parts), len(parts))
    return ScriptArray(parts)


def _replace(source: Any, target: Any, replacement: Any, occurrence: Any = 0) -> Any:
    if not all(isinstance(value, str) for value in (source, target, replacement)):
        return None
    if not isinstance(occurrence, int | float) and occurrence is not None:
        return None
    start = -1
    for _ in range(max(0, int(occurrence or 0)) + 1):
        start = source.find(target, start + 1)
        if start < 0:
            return source
    return check_string(source[:start] + replacement + source[start + len(target) :])


# Arrays


def _array_new(initial: Any = None) -> Callable[[Any, Any], Any]:
    def create(size: Any = 0, initial_value: Any = initial) -> Any:
        if not _na(size) and not isinstance(size, int | float):
            raise ScriptRuntimeError("an array's size is a number")
        count = 0 if _na(size) else int(size)
        if count < 0:
            raise ScriptRuntimeError("an array's size can't be negative")
        allocate(count, count)
        return ScriptArray([initial_value] * count)

    return create


def _items(array: Any) -> list[Any]:
    if not isinstance(array, ScriptArray):
        raise ScriptRuntimeError("expected an array, got na")
    return array.items


def _index(array: Any, index: Any) -> int:
    items = _items(array)
    if _na(index) or not isinstance(index, int | float):
        raise ScriptRuntimeError("array index is na")
    position = int(index)
    if position < 0:
        position += len(items)
    if not 0 <= position < len(items):
        raise ScriptRuntimeError(f"array index {int(index)} is out of bounds (size {len(items)})")
    return position


def _grow(items: list[Any], count: int) -> None:
    allocate(count, len(items) + count)


def _push(array: Any, value: Any) -> None:
    items = _items(array)
    _grow(items, 1)
    items.append(value)


def _pop(array: Any) -> Any:
    items = _items(array)
    if not items:
        raise ScriptRuntimeError("pop() on an empty array")
    return items.pop()


def _shift(array: Any) -> Any:
    items = _items(array)
    if not items:
        raise ScriptRuntimeError("shift() on an empty array")
    return items.pop(0)


def _unshift(array: Any, value: Any) -> None:
    items = _items(array)
    _grow(items, 1)
    items.insert(0, value)


def _get(array: Any, index: Any) -> Any:
    return _items(array)[_index(array, index)]


def _set(array: Any, index: Any, value: Any) -> None:
    _items(array)[_index(array, index)] = value


def _insert(array: Any, index: Any, value: Any) -> None:
    items = _items(array)
    if _na(index) or not isinstance(index, int | float) or not 0 <= int(index) <= len(items):
        raise ScriptRuntimeError("array index is out of bounds")
    _grow(items, 1)
    items.insert(int(index), value)


def _remove(array: Any, index: Any) -> Any:
    return _items(array).pop(_index(array, index))


def _numbers(array: Any) -> list[float]:
    return [item for item in _items(array) if not _na(item)]


def _array_sum(array: Any) -> Any:
    values = _numbers(array)
    return ta.js_sum(values) if values else None


def _array_avg(array: Any) -> Any:
    values = _numbers(array)
    return ta.js_sum(values) / len(values) if values else None


def _array_max(array: Any, nth: Any = 0) -> Any:
    values = sorted(_numbers(array), reverse=True)
    return values[int(nth)] if len(values) > int(nth) else None


def _array_min(array: Any, nth: Any = 0) -> Any:
    values = sorted(_numbers(array))
    return values[int(nth)] if len(values) > int(nth) else None


def _array_stdev(array: Any, biased: Any = True) -> Any:
    values = _numbers(array)
    if not values:
        return None
    center = ta.js_sum(values) / len(values)
    divisor = len(values) if biased or len(values) == 1 else len(values) - 1
    return math.sqrt(ta.js_sum([(value - center) * (value - center) for value in values]) / divisor)


def _array_median(array: Any) -> Any:
    values = sorted(_numbers(array))
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _array_sort(array: Any, order: Any = "order.ascending") -> None:
    _items(array).sort(key=lambda item: (item is None, item), reverse=order == "order.descending")


def _array_slice(array: Any, start: Any, end: Any) -> Any:
    items = _items(array)
    if any(_na(value) or not isinstance(value, int | float) for value in (start, end)):
        raise ScriptRuntimeError("slice() takes two indexes")
    part = items[int(start) : int(end)]
    allocate(len(part), len(part))
    return ScriptArray(part)


def _array_copy(array: Any) -> Any:
    items = _items(array)
    allocate(len(items), len(items))
    return ScriptArray(list(items))


def _array_concat(array: Any, other: Any) -> Any:
    items, extra = _items(array), _items(other)
    _grow(items, len(extra))
    items.extend(list(extra))
    return array


def _array_join(array: Any, separator: Any = "") -> Any:
    if not isinstance(separator, str):
        separator = ""
    return _bounded_join(separator, (str(_tostring(item)) for item in _items(array)))


def _array_from(*values: Any) -> Any:
    allocate(len(values), len(values))
    return ScriptArray(list(values))


def _array_fill(array: Any, value: Any, start: Any = 0, end: Any = None) -> None:
    items = _items(array)
    first = 0 if _na(start) else max(0, int(start))
    last = len(items) if _na(end) else min(len(items), int(end))
    for index in range(first, last):
        items[index] = value


ARRAY_METHODS: dict[str, Callable[..., Any]] = {
    "push": _push, "pop": _pop, "shift": _shift, "unshift": _unshift, "get": _get, "set": _set,
    "insert": _insert, "remove": _remove, "size": lambda a: len(_items(a)), "clear": lambda a: _items(a).clear(),
    "sum": _array_sum, "avg": _array_avg, "max": _array_max, "min": _array_min, "stdev": _array_stdev,
    "median": _array_median, "includes": lambda a, v: v in _items(a),
    "indexof": lambda a, v: _items(a).index(v) if v in _items(a) else -1,
    "lastindexof": lambda a, v: len(_items(a)) - 1 - _items(a)[::-1].index(v) if v in _items(a) else -1,
    "first": lambda a: _get(a, 0), "last": lambda a: _get(a, -1), "copy": _array_copy,
    "reverse": lambda a: _items(a).reverse(), "sort": _array_sort, "slice": _array_slice, "fill": _array_fill,
    "concat": _array_concat,
    "join": _array_join,
}


def _map_put(target: Any, key: Any, value: Any) -> Any:
    if not isinstance(target, ScriptMap):
        raise ScriptRuntimeError("expected a map, got na")
    if key not in target.items:
        allocate(1, len(target.items) + 1)
    previous = target.items.get(key)
    target.items[key] = value
    return previous


MAP_METHODS: dict[str, Callable[..., Any]] = {
    "put": lambda m, k, v: _map_put(m, k, v),
    "get": lambda m, k: m.items.get(k),
    "contains": lambda m, k: k in m.items,
    "remove": lambda m, k: m.items.pop(k, None),
    "size": lambda m: len(m.items),
    "keys": lambda m: _array_from(*m.items),
    "values": lambda m: _array_from(*m.items.values()),
    "clear": lambda m: m.items.clear(),
}


def _replace_all(source: Any, target: Any, replacement: Any) -> Any:
    if not all(isinstance(value, str) for value in (source, target, replacement)):
        return None
    if target and source.count(target) * max(0, len(replacement) - len(target)) + len(source) > current_limits().max_string_length:
        check_string(" " * (current_limits().max_string_length + 1))
    return source.replace(target, replacement) if target else source


def _repeat(source: Any, count: Any, separator: Any = "") -> Any:
    if not isinstance(source, str) or _na(count) or not isinstance(count, int | float) or not isinstance(separator, str):
        return None
    times = max(0, int(count))
    # Checked before the string is built.
    if times * (len(source) + len(separator)) > current_limits().max_string_length:
        check_string(" " * (current_limits().max_string_length + 1))
    return separator.join([source] * times)


STR_METHODS: dict[str, Callable[..., Any]] = {
    "length": lambda s: len(s) if isinstance(s, str) else None,
    "contains": lambda s, part: isinstance(s, str) and isinstance(part, str) and part in s,
    "startswith": lambda s, part: isinstance(s, str) and isinstance(part, str) and s.startswith(part),
    "endswith": lambda s, part: isinstance(s, str) and isinstance(part, str) and s.endswith(part),
    "pos": lambda s, part: s.find(part) if isinstance(s, str) and s.find(part) >= 0 else None,
    "upper": lambda s: s.upper() if isinstance(s, str) else None,
    "lower": lambda s: s.lower() if isinstance(s, str) else None,
    "trim": lambda s: s.strip() if isinstance(s, str) else None,
    "substring": _substring,
    "replace": _replace,
    "replace_all": lambda s, a, b: _replace_all(s, a, b),
    "split": _split,
    "tonumber": _tonumber,
    "tostring": _tostring,
    "format": _format,
    "repeat": lambda s, count, separator="": _repeat(s, count, separator),
}


# Drawings


DRAWING_FIELDS = {
    "label": "x, y, text=\"\", xloc=xloc.bar_index, yloc=yloc.price, color=na, style=label.style_label_down, textcolor=na, size=size.normal, textalign=text.align_center, tooltip=na, text_font_family=na, force_overlay=false, text_formatting=na",
    "line": "x1, y1, x2, y2, xloc=xloc.bar_index, extend=extend.none, color=na, style=line.style_solid, width=1, force_overlay=false",
    "box": "left, top, right, bottom, border_color=na, border_width=1, border_style=line.style_solid, extend=extend.none, xloc=xloc.bar_index, bgcolor=na, text=\"\", text_size=size.auto, text_color=na, text_halign=text.align_center, text_valign=text.align_center, text_wrap=text.wrap_none, text_font_family=na, force_overlay=false, text_formatting=na",
    "table": "position, columns, rows, bgcolor=na, frame_color=na, frame_width=0, border_color=na, border_width=0, force_overlay=false",
    "linefill": "line1, line2, color",
}


def _drawing_factory(kind: str) -> Factory:
    params, defaults = _spec(DRAWING_FIELDS[kind])
    # Constants given in the spec as names (xloc.bar_index): stored as their name.
    defaults = {key: value for key, value in defaults.items()}

    def factory(c: Any, node: Call) -> Closure:
        c.uses_drawings = True
        closures = _bind(c, node, params, defaults)

        def create(ctx: Context) -> Any:
            fields = {name: closure(ctx) for name, closure in zip(params, closures, strict=True)}
            if kind == "table":
                fields["cells"] = {}
            return ctx.run.add_drawing(kind, fields)

        return create

    # Its parameters, for the editor's hover and signature help (TVP-11.2).
    factory.signature = DRAWING_FIELDS[kind]  # type: ignore[attr-defined]
    return factory


def _drawing_setter(kind: str, *names: str) -> Callable[..., None]:
    def set_fields(ctx: Context, drawing: Any, *values: Any) -> None:
        if isinstance(drawing, Drawing) and not drawing.deleted:
            for name, value in zip(names, values, strict=False):
                drawing.fields[name] = value

    return set_fields


def _drawing_getter(name: str) -> Callable[..., Any]:
    def get_field(ctx: Context, drawing: Any) -> Any:
        return drawing.fields.get(name) if isinstance(drawing, Drawing) else None

    return get_field


def _delete(ctx: Context, drawing: Any) -> None:
    if isinstance(drawing, Drawing):
        drawing.deleted = True


def _line_price(ctx: Context, line: Any, x: Any) -> Any:
    if not isinstance(line, Drawing):
        return None
    f = line.fields
    if f["x2"] == f["x1"]:
        return f["y1"]
    return f["y1"] + (f["y2"] - f["y1"]) * (x - f["x1"]) / (f["x2"] - f["x1"])


def _table_cell(ctx: Context, table: Any, column: Any, row: Any, *values: Any, **options: Any) -> None:
    if isinstance(table, Drawing):
        cell = table.fields["cells"].setdefault((int(column), int(row)), {})
        names = ("text", "width", "height", "text_color", "text_halign", "text_valign", "text_size", "bgcolor", "tooltip")
        cell.update(dict(zip(names, values, strict=False)))
        cell.update(options)


DRAWING_METHODS: dict[str, dict[str, Callable[..., Any]]] = {
    "label": {
        "set_x": _drawing_setter("label", "x"), "set_y": _drawing_setter("label", "y"),
        "set_xy": _drawing_setter("label", "x", "y"), "set_text": _drawing_setter("label", "text"),
        "set_color": _drawing_setter("label", "color"), "set_textcolor": _drawing_setter("label", "textcolor"),
        "set_style": _drawing_setter("label", "style"), "set_size": _drawing_setter("label", "size"),
        "set_tooltip": _drawing_setter("label", "tooltip"), "set_yloc": _drawing_setter("label", "yloc"),
        "set_xloc": _drawing_setter("label", "x", "xloc"), "set_textalign": _drawing_setter("label", "textalign"),
        "get_x": _drawing_getter("x"), "get_y": _drawing_getter("y"), "get_text": _drawing_getter("text"),
        "delete": _delete,
    },
    "line": {
        "set_x1": _drawing_setter("line", "x1"), "set_y1": _drawing_setter("line", "y1"),
        "set_x2": _drawing_setter("line", "x2"), "set_y2": _drawing_setter("line", "y2"),
        "set_xy1": _drawing_setter("line", "x1", "y1"), "set_xy2": _drawing_setter("line", "x2", "y2"),
        "set_color": _drawing_setter("line", "color"), "set_width": _drawing_setter("line", "width"),
        "set_style": _drawing_setter("line", "style"), "set_extend": _drawing_setter("line", "extend"),
        "set_xloc": _drawing_setter("line", "x1", "x2", "xloc"),
        "get_x1": _drawing_getter("x1"), "get_y1": _drawing_getter("y1"), "get_x2": _drawing_getter("x2"),
        "get_y2": _drawing_getter("y2"), "get_price": _line_price, "delete": _delete,
    },
    "box": {
        "set_left": _drawing_setter("box", "left"), "set_top": _drawing_setter("box", "top"),
        "set_right": _drawing_setter("box", "right"), "set_bottom": _drawing_setter("box", "bottom"),
        "set_lefttop": _drawing_setter("box", "left", "top"), "set_rightbottom": _drawing_setter("box", "right", "bottom"),
        "set_bgcolor": _drawing_setter("box", "bgcolor"), "set_border_color": _drawing_setter("box", "border_color"),
        "set_text": _drawing_setter("box", "text"), "set_extend": _drawing_setter("box", "extend"),
        "get_left": _drawing_getter("left"), "get_top": _drawing_getter("top"), "get_right": _drawing_getter("right"),
        "get_bottom": _drawing_getter("bottom"), "delete": _delete,
    },
    "table": {"cell": _table_cell, "delete": _delete, "clear": lambda ctx, t, *a: t.fields["cells"].clear()},
    "linefill": {"delete": _delete},
}


def method(kind: str | None, name: str) -> Callable[..., Any] | None:
    if kind == "array":
        function = ARRAY_METHODS.get(name)
    elif kind == "map":
        function = MAP_METHODS.get(name)
    elif kind == "str":
        function = STR_METHODS.get(name)
    elif kind in DRAWING_METHODS:
        return DRAWING_METHODS[kind].get(name)
    else:
        return None
    if function is None:
        return None
    return lambda ctx, *args, **kwargs: function(*args, **kwargs)


def _namespace_function(kind: str, name: str) -> Factory:
    def factory(c: Any, node: Call) -> Closure:
        closures = [c.expression(arg) for arg in node.args]
        named = {key: c.expression(value) for key, value in node.kwargs.items()}
        function = method(kind, name)
        assert function is not None

        def call(ctx: Context) -> Any:
            return function(ctx, *[closure(ctx) for closure in closures], **{key: value(ctx) for key, value in named.items()})

        return call

    return factory
