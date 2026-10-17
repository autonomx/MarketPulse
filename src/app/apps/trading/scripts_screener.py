"""The script screener (TVP-11.6): one Omnix Script run on each symbol of a list, and each output's latest values.

Every symbol's bars are read as a chart reads them and the script runs in a script worker (``scripts_service.py``),
two symbols at a time, in a worker slot of the person's own apart from their chart runs. Each row gives every plot's
and ``alertcondition()``'s value on the last bar and the bar before, so the screen filters (above, below, crossing,
condition true) and sorts without running the script again. A list is screened within ``SCREEN_DEADLINE_SECONDS``;
symbols not reached by then say so. Research only: a script never reaches orders, files or the network.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, Field

from .scripts_service import ScriptRunService, ScriptServiceError, bars_for_script
from .service import TradingMarketDataService

logger = logging.getLogger(__name__)

SCREEN_MAX_INSTRUMENTS = 50
SCREEN_DEADLINE_SECONDS = 90.0
SCREEN_CONCURRENCY = 2
# Outputs with a value per bar; bgcolor, barcolor and fills colour bars rather than measure them.
SCREEN_KINDS = frozenset({"plot", "plotshape", "plotchar", "plotarrow", "plotcandle", "plotbar", "alertcondition"})


class ScriptScreenOutput(BaseModel):
    # "plot:<index>" or "alertcondition:<index>": the index among the script's plot-like calls, as alerts name them.
    key: str
    title: str
    kind: str


class ScriptScreenRow(BaseModel):
    instrument_id: str
    # The start of the last bar the script ran on (ISO).
    bar_time: str | None = None
    last: dict[str, float | None] = Field(default_factory=dict)
    previous: dict[str, float | None] = Field(default_factory=dict)
    error: str | None = None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def screen_outputs(result: dict[str, Any]) -> list[ScriptScreenOutput]:
    outputs = []
    for plot in result.get("plots") or []:
        kind = str(plot.get("kind") or "")
        if kind not in SCREEN_KINDS:
            continue
        prefix = "alertcondition" if kind == "alertcondition" else "plot"
        title = str(plot.get("title") or "").strip() or f"{kind} {plot.get('index')}"
        outputs.append(ScriptScreenOutput(key=f"{prefix}:{plot.get('index')}", title=title, kind=kind))
    return outputs


def screen_row(instrument_id: str, result: dict[str, Any], times: Sequence[str]) -> ScriptScreenRow:
    """A run's outputs on its last two bars (an alertcondition is 1 where it fires, else 0)."""
    keys = {output.key for output in screen_outputs(result)}
    last: dict[str, float | None] = {}
    previous: dict[str, float | None] = {}
    for plot in result.get("plots") or []:
        alert = plot.get("kind") == "alertcondition"
        key = f"{'alertcondition' if alert else 'plot'}:{plot.get('index')}"
        if key not in keys:
            continue
        values = list(plot.get("values") or [])

        def at(position: int, values: list[Any] = values, alert: bool = alert) -> float | None:
            if len(values) < -position:
                return None
            number = _number(values[position])
            return 0.0 if number is None and alert else number

        last[key], previous[key] = at(-1), at(-2)
    return ScriptScreenRow(instrument_id=instrument_id, bar_time=times[-1] if times else None, last=last, previous=previous)


def screen_script(
    source: str,
    instrument_ids: Sequence[str],
    *,
    interval: str,
    limit: int,
    inputs: dict[str, Any],
    user_id: str,
    script_service: ScriptRunService,
    market_service: TradingMarketDataService,
    deadline_seconds: float = SCREEN_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[ScriptScreenOutput], list[ScriptScreenRow]]:
    """Each symbol's row in list order, and the outputs the runs found (in the script's order)."""
    stop_at = clock() + deadline_seconds
    slot = f"screen:{user_id}"

    def one(instrument_id: str) -> tuple[ScriptScreenRow, list[ScriptScreenOutput]]:
        from .scripts_security import load_script_securities

        if clock() >= stop_at:
            return ScriptScreenRow(instrument_id=instrument_id, error="not screened: the screen's time ran out"), []
        try:
            bars = list(market_service.bars(instrument_id, interval, limit, None, alignment="clock").bars)
        except Exception as error:  # noqa: BLE001 - one symbol's data failing leaves the rest of the screen
            logger.info("script_screen_bars_failed instrument=%s", instrument_id)
            return ScriptScreenRow(instrument_id=instrument_id, error=f"no bars: {error}"[:300]), []
        if not bars:
            return ScriptScreenRow(instrument_id=instrument_id, error="no bars"), []
        times = [bar.start_time.isoformat() for bar in bars]
        # request.security() contexts, from this symbol (TVP-11.1).
        securities = load_script_securities(source, instrument_id, interval, bars, market_service, inputs)
        for attempt in range(2):
            try:
                result = script_service.run(
                    source, bars_for_script(bars), inputs=inputs, symbol=instrument_id, timeframe=interval, user_id=slot, securities=securities,
                )
                return screen_row(instrument_id, result, times), screen_outputs(result)
            except ScriptServiceError as error:
                if error.kind == "busy" and attempt == 0:
                    continue
                where = f" (line {error.line})" if error.line else ""
                return ScriptScreenRow(instrument_id=instrument_id, bar_time=times[-1], error=f"{error.message}{where}"), []
        return ScriptScreenRow(instrument_id=instrument_id, error="the script workers are busy"), []

    with ThreadPoolExecutor(max_workers=SCREEN_CONCURRENCY, thread_name_prefix="script-screen") as pool:
        answers = list(pool.map(one, instrument_ids))
    outputs: dict[str, ScriptScreenOutput] = {}
    for _row, found in answers:
        for output in found:
            outputs.setdefault(output.key, output)
    # In the script's order: the index counts its plot-like calls.
    ordered = sorted(outputs.values(), key=lambda output: int(output.key.partition(":")[2]))
    return ordered, [row for row, _found in answers]


__all__ = ["SCREEN_MAX_INSTRUMENTS", "ScriptScreenOutput", "ScriptScreenRow", "screen_outputs", "screen_row", "screen_script"]
