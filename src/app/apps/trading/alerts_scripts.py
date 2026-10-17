"""Alerts on Omnix Scripts (TVP-11.4): a script's plots, ``alertcondition()`` and ``alert()`` calls as alert sources.

An alert reads the script as it was saved when the alert was made (``ScriptSource.revision``, from the script's
versions), so editing the script later doesn't change the alert. The monitor runs it in a script worker
(``scripts_service.py``) on the bars it evaluates, with no file, network or order access, once per alert and pass.
A run that fails (the version is gone, the script errors, a worker is busy) gives no values: the condition is false.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .alert_conditions import AlertConditionSpec, ScriptSource, condition_sources
from .repositories import TradingDocumentRepository, default_trading_repository
from .scripts_service import ScriptRunService, ScriptServiceError, bars_for_script, check_script, default_script_service
from .scripts_security import load_script_securities

logger = logging.getLogger(__name__)

# The worker slot alert runs share, apart from each person's chart runs.
ALERT_RUNS_USER = "alert-monitor"
_SOURCES_KEPT = 128


@dataclass(frozen=True)
class ScriptAlertSeries:
    """A script output's value by bar index (signals only where they fire), and alert messages by bar index."""

    values: dict[int, float] = field(default_factory=dict)
    messages: dict[int, str] = field(default_factory=dict)
    error: str | None = None


# Script versions never change, so an entry expires only to bound how long it is held.
_SOURCES_TTL_SECONDS = 3_600.0
_sources: OrderedDict[tuple[str, str, int], tuple[float, str]] = OrderedDict()
_sources_guard = threading.Lock()


def script_source_at(repository: TradingDocumentRepository, script_id: str, revision: int) -> str | None:
    """A script's source at a revision; versions never change, so they are kept in memory."""
    key = (str(repository.context.workspace_id), script_id, revision)
    now = time.monotonic()
    with _sources_guard:
        cached = _sources.get(key)
        if cached is not None and now - cached[0] < _SOURCES_TTL_SECONDS:
            _sources.move_to_end(key)
            return cached[1]
    version = repository.script_version_at(script_id, revision)
    if version is None:
        return None
    with _sources_guard:
        _sources[key] = (now, version["source"])
        _sources.move_to_end(key)
        while len(_sources) > _SOURCES_KEPT:
            _sources.popitem(last=False)
    return version["source"]


def clear_script_sources() -> None:
    """Forget every cached script source (tests, or after a restore)."""
    with _sources_guard:
        _sources.clear()


def series_from_result(result: dict[str, Any], output: str) -> ScriptAlertSeries:
    """One output of a run's result (``scripts/worker.py`` ``result_payload``) as alert values."""
    if output == "alert":
        calls = [item for item in result.get("alerts") or [] if item.get("kind") == "alert"]
        return ScriptAlertSeries(
            values={int(item["bar"]): 1.0 for item in calls},
            messages={int(item["bar"]): str(item.get("message") or "") for item in calls},
        )
    # The index is the call's position among all of the script's plot*, bgcolor, barcolor and alertcondition calls.
    kind, _, position = output.partition(":")
    index = int(position)
    plot = next((item for item in result.get("plots") or [] if item.get("index") == index), None)
    if plot is None or (plot.get("kind") == "alertcondition") != (kind == "alertcondition"):
        return ScriptAlertSeries(error=f"the script has no {kind} at position {index}")
    values: dict[int, float] = {}
    for bar, value in enumerate(plot.get("values") or []):
        if value is True:
            values[bar] = 1.0
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values[bar] = float(value)
    message = (plot.get("options") or {}).get("message") if kind == "alertcondition" else None
    return ScriptAlertSeries(values=values, messages={bar: str(message) for bar in values} if message else {})


@dataclass(frozen=True)
class ScriptAlertContext:
    """What a script alert runs on besides its bars: the alert's instrument and interval, and the market data service."""

    instrument_id: str
    interval: str
    market_service: Any


def script_alert_series(
    source: ScriptSource,
    bars: Sequence[Any],
    *,
    repository_factory: Callable[[], TradingDocumentRepository] = default_trading_repository,
    service_factory: Callable[[], ScriptRunService] = default_script_service,
    context: ScriptAlertContext | None = None,
) -> ScriptAlertSeries:
    """Run the alert's script version on ``bars``; its ``output`` by bar index.

    ``context``: the alert's instrument, interval and market data, for syminfo and request.security() (TVP-11.1)."""
    if not bars:
        return ScriptAlertSeries()
    try:
        text = script_source_at(repository_factory(), source.script_id, source.revision)
        if text is None:
            return ScriptAlertSeries(error=f"script {source.script_id} has no version at revision {source.revision}")
        extra: dict[str, Any] = {}
        if context is not None:
            extra = {
                "symbol": context.instrument_id,
                "timeframe": context.interval,
                "securities": load_script_securities(text, context.instrument_id, context.interval, bars, context.market_service, dict(source.inputs)),
            }
        result = service_factory().run(text, bars_for_script(bars), inputs=dict(source.inputs), user_id=ALERT_RUNS_USER, **extra)
    except ScriptServiceError as error:
        logger.info("script_alert_run_failed", extra={"script_id": source.script_id, "error": error.message})
        return ScriptAlertSeries(error=error.message)
    except Exception as error:  # noqa: BLE001 - an alert that can't run has no values; the monitor keeps going
        logger.warning("script_alert_run_failed", exc_info=True)
        return ScriptAlertSeries(error=f"{type(error).__name__}: {error}")
    return series_from_result(result, source.output)


def script_sources(conditions: Iterable[AlertConditionSpec]) -> list[ScriptSource]:
    return [source for condition in conditions for source in condition_sources(condition) if isinstance(source, ScriptSource)]


def validate_script_sources(conditions: Iterable[AlertConditionSpec], repository: TradingDocumentRepository) -> None:
    """Write-time check: each script version exists and compiles, and its output can exist (ValueError otherwise)."""
    for source in script_sources(conditions):
        text = script_source_at(repository, source.script_id, source.revision)
        if text is None:
            raise ValueError(f"script {source.script_id} has no saved version at revision {source.revision}")
        checked = check_script(text)
        if checked["diagnostics"]:
            problem = checked["diagnostics"][0]
            raise ValueError(f"the script has a problem on line {problem['line']}: {problem['message']}")
        if source.output == "alert" and "alert(" not in text:
            raise ValueError("the script never calls alert()")
        if source.output.startswith("alertcondition:") and "alertcondition(" not in text:
            raise ValueError("the script has no alertcondition()")


__all__ = ["ScriptAlertSeries", "script_alert_series", "series_from_result", "validate_script_sources"]
