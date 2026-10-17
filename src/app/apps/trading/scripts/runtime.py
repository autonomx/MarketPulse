"""Compiles a script into closures and runs it bar by bar.

Series semantics: every variable is a series. A ``Slot`` holds a variable's value on the current bar and its
committed history; at the end of each bar every slot commits (a ``var`` keeps its value, others reset to ``na``).
A user function's variables and ``ta.*`` calls live in a ``Context`` per call site (Pine gives each call of a function
its own series), and so does the history buffer of any ``expr[n]``.

A compiled ``Program`` doesn't depend on a run: compile once (per script and version) and run it on any bars with
any inputs. A ``ScriptRun`` can then take new bars one at a time (``append_bar``), which only runs the new bar.
"""

from __future__ import annotations

import contextvars
import copy
import math
import re
import time as clock
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from ..indicators.registry import BarSeries
from .limits import ScriptLimits
from .errors import ScriptError, ScriptLimitError, ScriptRuntimeError, ScriptSyntaxError, ScriptUnsupportedError
from .parser import parse_script
from .syntax import (
    Script,
)

Closure = Callable[["Context"], Any]


# Pine's ints are 64-bit; Python's grow without bound, so arithmetic past this is an error.
INT_LIMIT = 2**63 - 1

# The run on this thread, for built-ins that count allocations against its limits.
CURRENT_RUN: contextvars.ContextVar[ScriptRun | None] = contextvars.ContextVar("omnix_script_run", default=None)


def current_limits() -> ScriptLimits:
    run = CURRENT_RUN.get()
    return run.limits if run is not None else ScriptLimits()


def check_string(value: str) -> str:
    limit = current_limits().max_string_length
    if len(value) > limit:
        raise ScriptLimitError(f"a string holds at most {limit} characters")
    return value


def allocate(items: int, size_after: int = 0) -> None:
    """Counts `items` new array or map entries against the run's budget, and the collection's size after the change."""
    run = CURRENT_RUN.get()
    limits = run.limits if run is not None else ScriptLimits()
    if size_after > limits.max_collection_size:
        raise ScriptLimitError(f"an array or map holds at most {limits.max_collection_size} values")
    if run is not None:
        run.allocated += max(0, items)
        if run.allocated > limits.max_allocated_items:
            raise ScriptLimitError(f"the script created more than {limits.max_allocated_items} array values")


# Runtime state


class Slot:
    __slots__ = ("current", "history", "persistent", "initialized")

    def __init__(self, persistent: bool) -> None:
        self.current: Any = None
        self.history: list[Any] = []
        self.persistent = persistent
        self.initialized = False

    def at(self, offset: int) -> Any:
        if offset == 0:
            return self.current
        history = self.history
        return history[-offset] if 0 < offset <= len(history) else None


class Context:
    """The script's own series (the root), or one call site of a user function. The root's series advance once
    per bar; a function's advance once per call, as Pine's do (`x[1]` in a function is its previous call's value)."""

    __slots__ = ("run", "root", "slots", "sites", "children", "calls")

    def __init__(self, run: ScriptRun, root: Context | None) -> None:
        self.run = run
        self.root = root or self
        self.slots: dict[int, Slot] = {}
        self.sites: dict[int, Any] = {}
        self.children: dict[int, Context] = {}
        # Calls committed so far (function call sites only).
        self.calls = 0

    def slot(self, key: int, persistent: bool) -> Slot:
        slot = self.slots.get(key)
        if slot is None:
            slot = self.slots[key] = Slot(persistent)
            # Back-fill: a variable first set on a later bar (or call) was na before.
            if self.root is self:
                slot.history = [None] * self.run.committed
                self.run.all_slots.append(slot)
            else:
                slot.history = [None] * self.calls
        return slot

    def commit_call(self) -> None:
        """Ends one call of a function: its series move on, as the root's do at the end of a bar."""
        for slot in self.slots.values():
            slot.history.append(slot.current)
            if not slot.persistent:
                slot.current = None
        self.calls += 1

    def child(self, key: int) -> Context:
        child = self.children.get(key)
        if child is None:
            child = self.children[key] = Context(self.run, self.root)
        return child


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


# Outputs


@dataclass
class PlotOutput:
    index: int
    kind: str  # plot, plotshape, plotchar, plotarrow, plotcandle, plotbar, bgcolor, barcolor, alertcondition
    title: str
    options: dict[str, Any]
    values: list[Any] = field(default_factory=list)
    colors: list[Any] | None = None


@dataclass
class Drawing:
    kind: str  # label, line, box, table
    id: int
    fields: dict[str, Any]
    deleted: bool = False


@dataclass
class ScriptInput:
    title: str
    type: str
    default: Any
    options: dict[str, Any]


@dataclass
class ScriptResult:
    declaration: dict[str, Any]
    inputs: list[ScriptInput]
    plots: list[PlotOutput]
    hlines: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    drawings: list[Drawing]
    alerts: list[dict[str, Any]]
    bars: int
    seconds: float
    # log.info/warning/error calls, the newest max_logs: {"time", "bar", "level", "message"} (TVP-11.2).
    logs: list[dict[str, Any]] = field(default_factory=list)
    # With profiling on, seconds spent in each top-level statement, by its line (TVP-11.2).
    profile: list[dict[str, Any]] = field(default_factory=list)
    # A strategy() script's backtest: trades, equity, drawdown and the performance summary (TVP-11.5).
    strategy: dict[str, Any] | None = None


# The program


@dataclass
class _Binding:
    kind: str  # "slot", "function"
    key: int = 0
    persistent: bool = False
    is_global: bool = True
    function: _Function | None = None


@dataclass
class _Function:
    name: str
    params: list[tuple[str, int, Closure | None]]  # name, slot key, default (compiled in the definition's scope)
    body: Closure | None = None


class _Scope:
    def __init__(self, parent: _Scope | None, in_function: bool) -> None:
        self.parent = parent
        self.in_function = in_function
        self.names: dict[str, _Binding] = {}

    def lookup(self, name: str) -> _Binding | None:
        scope: _Scope | None = self
        while scope is not None:
            binding = scope.names.get(name)
            if binding is not None:
                return binding
            scope = scope.parent
        return None


@dataclass
class Program:
    script: Script
    body: list[Closure]
    declaration: dict[str, Any]
    inputs: list[ScriptInput]
    plot_specs: list[tuple[str, str, dict[str, Any]]]  # kind, title, static options
    uses_drawings: bool
    # The line of each top-level statement in `body`, for runtime errors.
    lines: list[int] = field(default_factory=list)
    # Reads the last bar (barstate.islast, last_bar_index, ...): a run can't be extended bar by bar, since bars that
    # were last when they ran no longer are; such a script runs again in full on new bars.
    uses_last_bar: bool = False
    # request.security() contexts, as "<symbol>|<timeframe>" written in the script ("" is the chart's own, and
    # "{input:<title>}" an input's value: see resolve_security_key).
    securities: list[str] = field(default_factory=list)


def compile_script(source: str | Script) -> Program:
    """Parses and compiles a script. Any problem is a ScriptError with a line."""
    try:
        script = parse_script(source) if isinstance(source, str) else source
        if script.version is None:
            raise ScriptUnsupportedError("a script starts with //@version=5 or //@version=6", 1)
        if script.version < 5:
            raise ScriptUnsupportedError(f"Pine v{script.version} scripts are not supported: use v5 or v6", 1)
        # The compiler builds on this module's run types, so it loads here.
        from .compiler import _Compiler

        return _Compiler(script).program()
    except ScriptError:
        raise
    except RecursionError:
        raise ScriptSyntaxError("the script is nested too deeply", 0) from None
    except Exception as error:  # noqa: BLE001 - anything else is a bug reported against the script, not a crash
        raise ScriptSyntaxError(f"the script can't be compiled: {error}", 0) from None


def run_script(
    program: Program | str,
    bars: BarSeries,
    inputs: dict[str, Any] | None = None,
    limits: ScriptLimits | None = None,
    symbol: str = "",
    timeframe: str = "",
    profile: bool = False,
    securities: dict[str, SecurityBars] | None = None,
) -> ScriptResult:
    """Runs a program on bars. ``securities``: the bars of each request.security() context (Program.securities)."""
    if isinstance(program, str):
        program = compile_script(program)
    run = ScriptRun(program, bars, inputs or {}, limits or ScriptLimits(), symbol, timeframe, securities=securities)
    run.profiling = profile
    run.run_all()
    return run.result()


def resolve_security_key(template: str, program: Program, inputs: dict[str, Any]) -> str:
    """A request.security() context with its inputs' values: "{input:<title>}" becomes the input's value (its
    default when the run doesn't set it)."""
    defaults = {item.title: item.default for item in program.inputs}

    def value(match: re.Match[str]) -> str:
        title = match.group(1)
        chosen = inputs.get(title, defaults.get(title, ""))
        return "" if chosen is None else str(chosen)

    return _INPUT_REFERENCE.sub(value, template)


_INPUT_REFERENCE = re.compile(r"\{input:([^{}|]*)\}")


@dataclass(frozen=True)
class SecurityBars:
    """Another context's bars for request.security(): its symbol and timeframe as Omnix names them."""

    bars: BarSeries
    symbol: str
    timeframe: str


class ScriptRun:
    """One run of a program over bars. A run that raised (any ScriptError) is left part-way through a bar: discard
    it and start a new one rather than extending it."""

    def __init__(
        self,
        program: Program,
        bars: BarSeries,
        inputs: dict[str, Any],
        limits: ScriptLimits,
        symbol: str = "",
        timeframe: str = "",
        *,
        securities: dict[str, SecurityBars] | None = None,
        context_key: str | None = None,
    ) -> None:
        if len(bars) > limits.max_bars:
            raise ScriptLimitError(f"a script runs on at most {limits.max_bars} bars")
        self.program = program
        # request.security(): the context this run computes ("<symbol>|<timeframe>"; None for the chart's own run),
        # the bars of the others, and, for each call site, the values a context's run recorded bar by bar.
        self.context_key = context_key
        self.securities = securities or {}
        self.security_values: dict[int, list[Any]] = {}
        self.security_runs: dict[str, ScriptRun | None] = {}
        self.security_alignment: dict[tuple[str, bool], list[int]] = {}
        self.resolved_securities: dict[str, str] = {}
        self.limits = limits
        self.inputs = validate_inputs(program, inputs)
        self.symbol = symbol
        self.timeframe = timeframe
        self.open = list(bars.open)
        self.high = list(bars.high)
        self.low = list(bars.low)
        self.close = list(bars.close)
        self.volume = list(bars.volume)
        self.starts: list[datetime] = list(bars.start_times)
        self.time = [int(start.timestamp() * 1000) for start in self.starts]
        self.t = 0
        self.committed = 0
        self.all_slots: list[Slot] = []
        self.root = Context(self, None)
        self.plots = [PlotOutput(index, kind, title, dict(options)) for index, (kind, title, options) in enumerate(program.plot_specs)]
        self.hlines: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.statics_done = False
        self.drawings: dict[str, list[Drawing]] = {}
        self.drawing_ids = 0
        self.alerts: list[dict[str, Any]] = []
        self.loop_iterations = 0
        self.bar_loop_iterations = 0
        self.seconds = 0.0
        self.logs: list[dict[str, Any]] = []
        self.profiling = False
        self.statement_seconds = [0.0] * len(program.body)
        self.deadline = 0.0
        self.allocated = 0
        self.last_bar_index = len(self.close) - 1
        self.interval_ms = interval_ms(timeframe)
        # A strategy() script trades a simulated account, filled on these bars only (scripts/strategy.py).
        self.broker: Any = None
        if program.declaration.get("kind") == "strategy" and context_key is None:
            from .strategy import Broker, StrategySettings

            self.broker = Broker(self, StrategySettings.from_declaration(program.declaration))

    @property
    def can_extend(self) -> bool:
        """Whether new bars can be run one at a time (see Program.uses_last_bar); request.security() runs in full."""
        return not self.program.uses_last_bar and not self.program.securities

    def __len__(self) -> int:
        return len(self.close)

    def run_all(self) -> None:
        if self.context_key is None and self.program.securities and not self.security_runs:
            self._run_securities()
        self._run_bars(self.committed, len(self.close))

    # request.security()

    def security_key(self, template: str) -> str:
        """A context as this run resolves it (its input values)."""
        key = self.resolved_securities.get(template)
        if key is None:
            key = resolve_security_key(template, self.program, self.inputs)
            self.resolved_securities[template] = key
        return key

    def _run_securities(self) -> None:
        """Runs the script once in each requested context, on that context's bars, before the chart's own run. The
        time they take counts against this run's limit."""
        for key in dict.fromkeys(self.security_key(template) for template in self.program.securities):
            if key == "|":
                continue  # an input set to the chart's own symbol and timeframe
            context = self.securities.get(key)
            if context is None or len(context.bars) == 0:
                self.security_runs[key] = None  # no bars for it: its values are na
                continue
            remaining = self.limits.max_seconds - self.seconds
            if remaining <= 0:
                raise ScriptLimitError(f"the script ran for more than {self.limits.max_seconds:g} s")
            run = ScriptRun(
                self.program, context.bars, self.inputs, replace(self.limits, max_seconds=remaining),
                context.symbol, context.timeframe, context_key=key,
            )
            try:
                run.run_all()
            finally:
                self.seconds += run.seconds
            self.security_runs[key] = run

    def record_security(self, site: int, value: Any) -> None:
        """In a context's run: the value of a request.security() expression on this bar."""
        values = self.security_values.setdefault(site, [])
        if len(values) <= self.t:
            values.extend([None] * (self.t - len(values) + 1))
        values[self.t] = value

    def _close_times(self, times: list[int], timeframe: str) -> list[int]:
        step = interval_ms(timeframe)
        return [
            times[index + 1] if step is None and index + 1 < len(times) else (time + step if step is not None else time + 1)
            for index, time in enumerate(times)
        ]

    def _alignment(self, key: str, lookahead: bool) -> list[int]:
        """For each of this run's bars, the context bar whose value it reads (-1 for none): the last one closed by the
        bar's close (lookahead off), or the last one started by the bar's start (lookahead on). On the last bar, as on a
        realtime bar, lookahead off reads the latest context bar started before the bar closes, closed or not."""
        cached = self.security_alignment.get((key, lookahead))
        if cached is not None:
            return cached
        run = self.security_runs.get(key)
        indexes: list[int] = []
        if run is not None:
            starts = run.time
            closes = self._close_times(run.time, run.timeframe)
            own_closes = self._close_times(self.time, self.timeframe)
            j = -1
            last = len(self.time) - 1
            for t, start in enumerate(self.time):
                if lookahead:
                    marks, bound = starts, start
                elif t == last:
                    marks, bound = starts, own_closes[t] - 1  # started before the close
                else:
                    marks, bound = closes, own_closes[t]
                while j + 1 < len(marks) and marks[j + 1] <= bound:
                    j += 1
                indexes.append(j)
        else:
            indexes = [-1] * len(self.time)
        self.security_alignment[(key, lookahead)] = indexes
        return indexes

    def security_value(self, site: int, key: str, gaps: bool, lookahead: bool) -> Any:
        """In the chart's run: the context's value for this bar; with gaps on, only on the bar its context bar changes."""
        run = self.security_runs.get(key)
        if run is None:
            return None
        indexes = self._alignment(key, lookahead)
        j = indexes[self.t] if self.t < len(indexes) else -1
        if j < 0 or (gaps and self.t > 0 and indexes[self.t - 1] == j):
            return None
        values = run.security_values.get(site, [])
        return values[j] if j < len(values) else None

    def append_bar(self, bar: Any) -> None:
        """Adds one closed bar and runs only it (incremental execution). A script that reads the last bar can't be
        extended (`can_extend`); run it again in full."""
        if not self.can_extend:
            raise RuntimeError("this script reads the last bar: run it again on all the bars instead of extending it")
        if len(self.close) + 1 > self.limits.max_bars:
            raise ScriptLimitError(f"a script runs on at most {self.limits.max_bars} bars")
        series = BarSeries.from_bars([bar])
        self.open.append(series.open[0])
        self.high.append(series.high[0])
        self.low.append(series.low[0])
        self.close.append(series.close[0])
        self.volume.append(series.volume[0])
        self.starts.append(series.start_times[0])
        self.time.append(int(series.start_times[0].timestamp() * 1000))
        self.last_bar_index = len(self.close) - 1
        self._run_bars(self.committed, len(self.close))

    def _run_bars(self, start: int, end: int) -> None:
        began = clock.perf_counter()
        body = self.program.body
        lines = self.program.lines
        root = self.root
        self.deadline = began + self.limits.max_seconds - self.seconds
        token = CURRENT_RUN.set(self)
        index = 0
        try:
            broker = self.broker
            for t in range(start, end):
                self.t = t
                self.bar_loop_iterations = 0
                if broker is not None:
                    broker.open_bar(t)
                if self.profiling:
                    for index, statement in enumerate(body):
                        began_statement = clock.perf_counter()
                        statement(root)
                        self.statement_seconds[index] += clock.perf_counter() - began_statement
                else:
                    for index, statement in enumerate(body):
                        statement(root)
                self.statics_done = True
                if broker is not None:
                    broker.close_bar(t)
                for slot in self.all_slots:
                    slot.history.append(slot.current)
                    if not slot.persistent:
                        slot.current = None
                self.committed = t + 1
                self.check_time()
        except ScriptError as error:
            if not error.line and index < len(lines):
                error.line = lines[index]
            raise
        except RecursionError:
            raise ScriptLimitError("the script is nested too deeply", lines[index] if index < len(lines) else 0) from None
        except MemoryError:
            raise ScriptLimitError("the script ran out of memory", lines[index] if index < len(lines) else 0) from None
        except (_Break, _Continue):
            raise ScriptSyntaxError("break and continue belong in a loop", lines[index] if index < len(lines) else 0) from None
        except Exception as error:  # noqa: BLE001 - any other failure is the script's error, with its line
            raise ScriptRuntimeError(f"{type(error).__name__}: {error}", lines[index] if index < len(lines) else 0) from None
        finally:
            CURRENT_RUN.reset(token)
            self.seconds += clock.perf_counter() - began

    def check_time(self) -> None:
        if clock.perf_counter() > self.deadline:
            raise ScriptLimitError(f"the script ran for more than {self.limits.max_seconds:g} s")

    def count_loop(self) -> None:
        self.loop_iterations += 1
        self.bar_loop_iterations += 1
        if self.bar_loop_iterations > self.limits.max_loop_iterations_per_bar or self.loop_iterations > self.limits.max_loop_iterations:
            raise ScriptLimitError("a loop ran too many times")
        if self.loop_iterations % 256 == 0:
            self.check_time()

    def add_drawing(self, kind: str, fields: dict[str, Any]) -> Drawing:
        self.drawing_ids += 1
        drawing = Drawing(kind, self.drawing_ids, fields)
        items = self.drawings.setdefault(kind, [])
        items.append(drawing)
        declared = self.program.declaration.get({"label": "max_labels_count", "line": "max_lines_count", "box": "max_boxes_count"}.get(kind, ""), 50)
        cap = min(int(declared) if isinstance(declared, int | float) else 50, self.limits.max_drawings)
        live = [item for item in items if not item.deleted]
        # Like Pine, the oldest drawings are removed once a script has more than its maximum.
        while len(live) > cap:
            live.pop(0).deleted = True
        if len(items) > 4 * cap:
            self.drawings[kind] = [item for item in items if not item.deleted]
        return drawing

    def result(self) -> ScriptResult:
        drawings = [drawing for items in self.drawings.values() for drawing in items if not drawing.deleted]
        drawings.sort(key=lambda drawing: drawing.id)
        return ScriptResult(
            declaration=copy.deepcopy(self.program.declaration),
            inputs=copy.deepcopy(self.program.inputs),
            plots=self.plots,
            hlines=self.hlines,
            fills=self.fills,
            drawings=drawings,
            alerts=self.alerts,
            bars=len(self.close),
            seconds=self.seconds,
            logs=list(self.logs),
            profile=self._profile(),
            strategy=self.broker.report() if self.broker is not None else None,
        )

    def _profile(self) -> list[dict[str, Any]]:
        if not self.profiling:
            return []
        by_line: dict[int, float] = {}
        for index, seconds in enumerate(self.statement_seconds):
            line = self.program.lines[index] if index < len(self.program.lines) else 0
            by_line[line] = by_line.get(line, 0.0) + seconds
        return [{"line": line, "seconds": seconds} for line, seconds in sorted(by_line.items())]

    def log(self, level: str, message: Any) -> None:
        """A log.* call: kept with the bar it ran on, the newest ``max_logs``."""
        self.logs.append({"time": self.time[self.t] if self.t < len(self.time) else None, "bar": self.t, "level": level, "message": str(message)})
        if len(self.logs) > self.limits.max_logs:
            del self.logs[0]

    # Series

    def series(self, name: str, t: int) -> Any:
        if t < 0 or t >= len(self.close):
            return None
        if name == "close":
            return self.close[t]
        if name == "open":
            return self.open[t]
        if name == "high":
            return self.high[t]
        if name == "low":
            return self.low[t]
        if name == "volume":
            return self.volume[t]
        if name == "hl2":
            return (self.high[t] + self.low[t]) / 2
        if name == "hlc3":
            return (self.high[t] + self.low[t] + self.close[t]) / 3
        if name == "ohlc4":
            return (self.open[t] + self.high[t] + self.low[t] + self.close[t]) / 4
        if name == "hlcc4":
            return (self.high[t] + self.low[t] + self.close[t] + self.close[t]) / 4
        if name == "time":
            return self.time[t]
        if name == "time_close":
            # The bar's start plus the interval; na without a fixed interval (months, or no timeframe given), so a
            # bar's value never depends on the bars after it.
            return self.time[t] + self.interval_ms if self.interval_ms is not None else None
        if name == "bar_index":
            return t
        raise ScriptRuntimeError(f"unknown series {name}")


SERIES_NAMES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4", "time", "time_close", "bar_index"}


def interval_ms(timeframe: str) -> int | None:
    """A fixed interval in milliseconds ("60" minutes, "15S", "D", "W"); None for months or unknown."""
    text = timeframe.strip().upper()
    if not text:
        return None
    unit = text[-1] if not text[-1].isdigit() else ""
    number = text[:-1] if unit else text
    count = int(number) if number.isdigit() else 1 if number == "" else None
    if count is None:
        return None
    seconds = {"": 60, "S": 1, "D": 86_400, "W": 604_800}.get(unit)
    return None if seconds is None else count * seconds * 1000


def validate_inputs(program: Program, inputs: dict[str, Any]) -> dict[str, Any]:
    """Checks a run's inputs against the script's declarations: known titles, the declared type, options and range."""
    declared = {item.title: item for item in program.inputs}
    for title, value in inputs.items():
        item = declared.get(title)
        if item is None:
            raise ScriptRuntimeError(f"the script has no input {title!r}")
        kind = item.type
        if kind == "int" and not (isinstance(value, int) and not isinstance(value, bool)):
            raise ScriptRuntimeError(f"input {title!r} takes a whole number")
        if kind == "any" or kind not in ("int", "float", "price", "time", "bool", "string", "color", "timeframe", "symbol", "session", "text_area", "source"):
            raise ScriptRuntimeError(f"input {title!r} can't be set")
        if kind == "time" and not (isinstance(value, int) and not isinstance(value, bool)):
            raise ScriptRuntimeError(f"input {title!r} takes a time (milliseconds)")
        if kind in ("float", "price") and not (isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)):
            raise ScriptRuntimeError(f"input {title!r} takes a number")
        if kind == "bool" and not isinstance(value, bool):
            raise ScriptRuntimeError(f"input {title!r} takes true or false")
        if kind in ("string", "color", "timeframe", "symbol", "session", "text_area") and not isinstance(value, str):
            raise ScriptRuntimeError(f"input {title!r} takes text")
        if isinstance(value, str) and len(value) > 4_096:
            raise ScriptRuntimeError(f"input {title!r} is too long")
        if kind == "source" and value not in SERIES_NAMES:
            raise ScriptRuntimeError(f"input {title!r} takes a built-in series (close, hl2, ...)")
        options = item.options.get("options")
        if isinstance(options, list) and options and value not in options:
            raise ScriptRuntimeError(f"input {title!r} takes one of {options}")
        if kind in ("int", "float"):
            low, high = item.options.get("minval"), item.options.get("maxval")
            if isinstance(low, int | float) and value < low or isinstance(high, int | float) and value > high:
                raise ScriptRuntimeError(f"input {title!r} is out of its range")
    return dict(inputs)


def truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _na(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _is_number(value: Any) -> bool:
    kind = type(value)
    return kind is int or kind is float


def _not_numbers(op: str, a: Any, b: Any) -> None:
    raise ScriptRuntimeError(f"can't apply {op} to {_type_name(a)} and {_type_name(b)}")


def _type_name(value: Any) -> str:
    return {bool: "bool", int: "int", float: "float", str: "string"}.get(type(value), type(value).__name__.lower().replace("script", ""))


def _checked_int(value: Any) -> Any:
    if type(value) is int and not -INT_LIMIT <= value <= INT_LIMIT:
        raise ScriptRuntimeError("integer overflow: a value went past Pine's 64-bit int range")
    return value


def _divide(a: Any, b: Any) -> Any:
    if _na(a) or _na(b):
        return None
    if not (_is_number(a) and _is_number(b)):
        _not_numbers("/", a, b)
    if b == 0:
        return None
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and a % b == 0:
        return a // b
    return a / b


def _modulo(a: Any, b: Any) -> Any:
    if _na(a) or _na(b):
        return None
    if not (_is_number(a) and _is_number(b)):
        _not_numbers("%", a, b)
    if b == 0:
        return None
    result = math.fmod(a, b)
    return int(result) if isinstance(a, int) and isinstance(b, int) else result


def _add(a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    if type(a) is str and type(b) is str:
        return check_string(a + b)
    if not (_is_number(a) and _is_number(b)):
        _not_numbers("+", a, b)
    return _checked_int(a + b)


def _sub(a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    if not (_is_number(a) and _is_number(b)):
        _not_numbers("-", a, b)
    return _checked_int(a - b)


def _mul(a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    if not (_is_number(a) and _is_number(b)):
        _not_numbers("*", a, b)
    return _checked_int(a * b)


def _compare(op: str) -> Callable[[Any, Any], Any]:
    import operator

    function = {"<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}[op]

    ordering = op in ("<", ">", "<=", ">=")

    def compare(a: Any, b: Any) -> Any:
        if _na(a) or _na(b):
            # na compares false, except na != value.
            return op == "!=" and not (_na(a) and _na(b))
        if ordering and not ((_is_number(a) and _is_number(b)) or (type(a) is str and type(b) is str)):
            _not_numbers(op, a, b)
        return function(a, b)

    return compare


_ARITHMETIC: dict[str, Callable[[Any, Any], Any]] = {"+": _add, "-": _sub, "*": _mul, "/": _divide, "%": _modulo}
