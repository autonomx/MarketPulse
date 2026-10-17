"""A worker process that runs Omnix Scripts (TVP-11.1).

Scripts run outside the server process (``OMNIX_SCRIPTS_SPEC.md`` §4): a runaway script is killed with its worker
instead of starving the server, and its memory goes with it. The service (``scripts_service.py``) keeps a few of
these processes and talks to each over stdin and stdout, one JSON line per job and per answer.

A job: ``{"id", "source", "bars": {"time": [epoch ms], "open", "high", "low", "close", "volume", "session"},
"inputs", "symbol", "timeframe", "limits": {...}, "profile"}``. An answer: ``{"id", "result"}`` or ``{"id", "error":
{"kind", "message", "line", "column"}}``.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Any

from ..indicators.registry import BarSeries
from .errors import ScriptError
from .runtime import ScriptLimits, ScriptResult, SecurityBars, compile_script, run_script


def jsonable(value: Any) -> Any:
    """A value as JSON: non-finite numbers are null; script arrays and maps become lists and objects."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    items = getattr(value, "items", None)
    if isinstance(items, list):
        return [jsonable(item) for item in items]
    if isinstance(items, dict):
        return {str(key): jsonable(item) for key, item in items.items()}
    return str(value)


def result_payload(result: ScriptResult) -> dict[str, Any]:
    """A run's result as JSON: plots with one value (and color, where it changes) per bar, drawings, logs."""
    return {
        "declaration": jsonable(result.declaration),
        "inputs": [jsonable({"title": item.title, "type": item.type, "default": item.default, "options": item.options}) for item in result.inputs],
        "plots": [
            jsonable({"index": plot.index, "kind": plot.kind, "title": plot.title, "options": plot.options, "values": plot.values, "colors": plot.colors})
            for plot in result.plots
        ],
        "hlines": jsonable(result.hlines),
        "fills": jsonable(result.fills),
        "drawings": [jsonable({"kind": drawing.kind, "id": drawing.id, "fields": drawing.fields}) for drawing in result.drawings],
        "alerts": jsonable(result.alerts),
        "logs": jsonable(result.logs),
        "profile": jsonable(result.profile),
        "strategy": jsonable(result.strategy),
        "bars": result.bars,
        "seconds": result.seconds,
    }


def bar_series(bars: dict[str, Any]) -> BarSeries:
    return BarSeries(
        start_times=tuple(datetime.fromtimestamp(time / 1000, tz=timezone.utc) for time in bars["time"]),
        open=tuple(float(value) for value in bars["open"]),
        high=tuple(float(value) for value in bars["high"]),
        low=tuple(float(value) for value in bars["low"]),
        close=tuple(float(value) for value in bars["close"]),
        volume=tuple(float(value) for value in bars["volume"]),
        sessions=tuple(str(value) for value in bars.get("session") or ["regular"] * len(bars["time"])),
    )


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    try:
        program = compile_script(str(job["source"]))
        limits = ScriptLimits(**dict(job.get("limits") or {}))
        # request.security() contexts' bars, loaded by the service: {"<symbol>|<timeframe>": {"symbol", "timeframe", "bars"}}.
        securities = {
            str(key): SecurityBars(bar_series(item["bars"]), str(item.get("symbol") or ""), str(item.get("timeframe") or ""))
            for key, item in dict(job.get("securities") or {}).items()
        }
        result = run_script(
            program, bar_series(job["bars"]), dict(job.get("inputs") or {}), limits,
            symbol=str(job.get("symbol") or ""), timeframe=str(job.get("timeframe") or ""), profile=bool(job.get("profile")),
            securities=securities,
        )
        return {"id": job.get("id"), "result": result_payload(result)}
    except ScriptError as error:
        return {"id": job.get("id"), "error": {"kind": error.kind, "message": error.message, "line": error.line, "column": error.column}}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            job = json.loads(line)
            answer = run_job(job)
        except Exception as error:  # noqa: BLE001 - a broken job is answered, the worker keeps serving
            answer = {"id": None, "error": {"kind": "error", "message": f"{type(error).__name__}: {error}", "line": 0, "column": 0}}
        sys.stdout.write(json.dumps(answer, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
