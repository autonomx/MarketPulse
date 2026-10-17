"""Alert message placeholders (TVP-1.5), TradingView's ``{{...}}`` set.

The message an alert sends is its template with placeholders replaced by the
values at the trigger: ``{{ticker}}``, ``{{exchange}}``, ``{{interval}}``,
``{{open}}``, ``{{high}}``, ``{{low}}``, ``{{close}}``, ``{{volume}}``,
``{{time}}`` (the bar's start), ``{{timenow}}`` (the evaluation), ``{{alert_name}}``,
``{{plot_N}}`` (the value watched by condition N, from 0) and
``{{plot("name")}}`` (a watched value by its output, such as ``rsi:14``, or by its
indicator id). A placeholder with no value is left as it is, as TradingView does.

The rendered message is stored with the trigger, so the app, the toast and
the webhook all show the same text.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .alerts import TradingAlert
    from .alerts_evaluation import AlertConditionOutcome

# A rendered message is at most this long; a template is at most 500 characters.
MAX_RENDERED_MESSAGE = 4_000

_PLACEHOLDER = re.compile(r"\{\{\s*(?:plot\(\s*\"([^\"{}]{1,240})\"\s*\)|([a-z_]+[0-9]*))\s*\}\}")


def _number(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        return str(value)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def instrument_parts(instrument_id: str) -> tuple[str, str]:
    """``(ticker, exchange)`` of a canonical instrument id such as ``crypto:BINANCE:spot:BTC-USDT``.

    A crypto pair loses its dash (``BTCUSDT``, as TradingView writes it); a share class keeps it (``BRK-B``).
    An id without a venue part has no exchange.
    """
    parts = instrument_id.split(":")
    ticker = parts[-1]
    if parts[0] == "crypto":
        ticker = ticker.replace("-", "")
    exchange = parts[1] if len(parts) >= 3 else ""
    return ticker, exchange


def message_values(
    alert: TradingAlert,
    outcome: AlertConditionOutcome,
    *,
    interval: str,
    evaluated_at: datetime,
) -> tuple[dict[str, str], dict[str, str]]:
    """The placeholder values at a trigger: ``(names, plots by output or indicator id)``."""
    ticker, exchange = instrument_parts(alert.instrument_id)
    values: dict[str, str | None] = {
        "ticker": ticker,
        "exchange": exchange,
        "interval": interval,
        "open": _number(outcome.open),
        "high": _number(outcome.high),
        "low": _number(outcome.low),
        "close": _number(outcome.close),
        "volume": _number(outcome.volume) if outcome.volume_known else None,
        "time": _time(outcome.bar_start),
        "timenow": _time(evaluated_at),
        "alert_name": alert.parameters.name.strip() or None,
    }
    plots: dict[str, str] = {}
    for observation in outcome.observations:
        value = _number(observation.source)
        if value is None:
            continue
        values[f"plot_{observation.position}"] = value
        if observation.position < len(alert.conditions):
            source: Any = alert.conditions[observation.position].source
            if getattr(source, "kind", None) == "indicator":
                plots.setdefault(source.output.lower(), value)
                plots.setdefault(source.indicator_id.lower(), value)
    return {key: value for key, value in values.items() if value is not None}, plots


def render_alert_message(template: str, values: dict[str, str], plots: dict[str, str]) -> str:
    """The template with known placeholders replaced; unknown ones stay as written."""

    def replace(match: re.Match[str]) -> str:
        name, plain = match.group(1), match.group(2)
        value = plots.get(name.strip().lower()) if name is not None else values.get(plain)
        return match.group(0) if value is None else value

    return _PLACEHOLDER.sub(replace, template)[:MAX_RENDERED_MESSAGE]
