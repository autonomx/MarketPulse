"""request.security() for Omnix Scripts (TVP-11.1): the bars of the other symbols and timeframes a script requests.

A script names each context in its source (``Program.securities``: ``"<symbol>|<timeframe>"``, an empty part being the
chart's own). Before a run the service loads each context's bars here, on Omnix's names for them, and the worker runs
the script once per context (``ScriptRun._run_securities``).

Symbols: an Omnix instrument id (``equity:NASDAQ:AAPL``), TradingView's ``EXCHANGE:SYMBOL`` (``NASDAQ:AAPL``,
``BINANCE:BTCUSDT``) or a bare symbol, found in the catalog. Timeframes: Pine's (``"60"``, ``"240"``, ``"D"``, ``"1W"``,
``"M"``, ``"30S"``) or Omnix's (``"1h"``). A context that can't be resolved, or whose provider has no bars, reads na.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from typing import Any

from .catalog import all_instruments, instrument_by_id
from .providers.bar_semantics import interval_duration
from .scripts_service import ScriptSecurity, bars_for_script

logger = logging.getLogger(__name__)

# Bars before the chart's first one, so indicators in the requested context have warmed up.
WARM_UP_BARS = 300
MAX_CONTEXT_BARS = 5_000

_OMNIX_INTERVAL = re.compile(r"^\d+(?:m|h|d|w|mo|s)$")


def resolve_script_timeframe(text: str, chart_interval: str) -> str | None:
    """A Pine (or Omnix) timeframe as an Omnix interval; the chart's for ""."""
    value = text.strip()
    if not value:
        return chart_interval
    if _OMNIX_INTERVAL.match(value):
        return value
    match = re.fullmatch(r"(\d*)([SDWM]?)", value.upper())
    if match is None:
        return None
    count = int(match.group(1) or 1)
    unit = match.group(2)
    if count <= 0:
        return None
    if unit == "":
        # Minutes, written as hours where they divide.
        return f"{count // 60}h" if count % 60 == 0 else f"{count}m"
    return {"S": f"{count}s", "D": f"{count}d", "W": f"{count}w", "M": f"{count}mo"}[unit]


def _normal(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", symbol.upper())


def resolve_script_symbol(text: str, chart_instrument_id: str) -> str | None:
    """A script's symbol as an Omnix instrument id; the chart's for ""."""
    value = text.strip()
    if not value:
        return chart_instrument_id
    if value.count(":") >= 2 and instrument_by_id(value) is not None:
        return value
    exchange, _, symbol = value.rpartition(":")
    wanted = _normal(symbol)
    candidates = [item for item in all_instruments() if _normal(item.display_symbol) == wanted or _normal(item.venue_symbol) == wanted]
    if exchange:
        venue = exchange.upper()
        candidates = [item for item in candidates if item.venue.upper() == venue or item.instrument_id.upper().split(":")[1:2] == [venue]]
        if not candidates and instrument_by_id(f"equity:{venue}:{symbol.upper()}") is not None:
            return f"equity:{venue}:{symbol.upper()}"
    if not candidates:
        return None
    # The chart's asset class first, then the catalog's order.
    chart_class = chart_instrument_id.split(":", 1)[0]
    candidates.sort(key=lambda item: item.instrument_id.split(":", 1)[0] != chart_class)
    return candidates[0].instrument_id


def script_securities_requested(source: str, inputs: dict[str, Any] | None = None) -> list[str]:
    """The contexts a script requests with these inputs; none when it doesn't compile or its inputs are wrong (the run
    reports that)."""
    from .scripts.errors import ScriptError
    from .scripts.runtime import compile_script, resolve_security_key, validate_inputs

    try:
        program = compile_script(source)
        values = validate_inputs(program, dict(inputs or {}))
    except ScriptError:
        return []
    keys = (resolve_security_key(template, program, values) for template in program.securities)
    return [key for key in dict.fromkeys(keys) if key != "|"]


def _bars_needed(chart_bars: Sequence[Any], interval: str) -> int:
    if not chart_bars:
        return WARM_UP_BARS
    try:
        step = interval_duration(interval).total_seconds()
    except ValueError:
        return MAX_CONTEXT_BARS
    span = (chart_bars[-1].start_time - chart_bars[0].start_time).total_seconds()
    return min(MAX_CONTEXT_BARS, math.ceil(span / step) + 1 + WARM_UP_BARS) if step > 0 else MAX_CONTEXT_BARS


def load_script_securities(
    source: str,
    chart_instrument_id: str,
    chart_interval: str,
    chart_bars: Sequence[Any],
    market_service: Any,
    inputs: dict[str, Any] | None = None,
) -> dict[str, ScriptSecurity]:
    """Each requested context's bars, covering the chart's bars and a warm-up before them."""
    securities: dict[str, ScriptSecurity] = {}
    for key in script_securities_requested(source, inputs):
        symbol_text, _, timeframe_text = key.partition("|")
        instrument_id = resolve_script_symbol(symbol_text, chart_instrument_id)
        interval = resolve_script_timeframe(timeframe_text, chart_interval)
        if instrument_id is None or interval is None:
            logger.info("script_security_unresolved", extra={"key": key})
            continue
        try:
            response = market_service.bars(instrument_id, interval, _bars_needed(chart_bars, interval), None, alignment="clock")
        except Exception:  # no bars for it: the script reads na there
            logger.info("script_security_bars_failed", extra={"key": key}, exc_info=True)
            continue
        securities[key] = ScriptSecurity(symbol=instrument_id, timeframe=interval, bars=bars_for_script(list(response.bars)))
    return securities


__all__ = ["load_script_securities", "resolve_script_symbol", "resolve_script_timeframe", "script_securities_requested"]
