from __future__ import annotations

import logging

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from .gapper_dataset import GapperCandidate
from .historical_gapper_reconstruction import _alpaca_bars
from .models import AdjustmentMode, MarketBar
from .providers.alpaca_iex import alpaca_iex_auth_headers
from .providers.errors import ProviderDataUnavailableError
from .providers.http_runtime import ProviderHttpRuntime
from .strategy_replay_reliability import historical_replay_http_runtime
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time

logger = logging.getLogger(__name__)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def alpaca_historical_session_bars(
    candidates: tuple[GapperCandidate, ...] | list[GapperCandidate],
    session_date: date,
    *,
    runtime: ProviderHttpRuntime | None = None,
) -> dict[str, list[MarketBar]]:
    """Fetch one reconstructed session's finalized 1m Alpaca IEX bars in batches.

    Reconstructed universes are already explicitly approximate/IEX-scoped, so
    using the same provider for regular-session replay avoids Yahoo's shorter 1m
    retention window and keeps provider fidelity internally consistent.

    Historical research uses a deliberately conservative retry profile when the
    caller does not inject a runtime. Cached callers can avoid provider traffic
    entirely; uncached callers get lower concurrency plus a larger bounded retry
    envelope for 429/5xx responses.
    """

    if not candidates:
        return {}
    active_runtime = runtime or historical_replay_http_runtime(
        "alpaca_strategy_range_backtest"
    )
    headers = alpaca_iex_auth_headers()
    close_time = regular_close_time(session_date)
    start = datetime.combine(session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    end = datetime.combine(session_date, close_time, tzinfo=_ET).astimezone(timezone.utc)
    symbol_to_candidate = {
        candidate.instrument_id.split(":")[-1].upper(): candidate
        for candidate in candidates
    }
    raw = _alpaca_bars(
        active_runtime,
        headers,
        list(symbol_to_candidate),
        timeframe="1Min",
        start=start,
        end=end,
        chunk_size=25,
    )
    received_at = datetime.now(timezone.utc)
    output: dict[str, list[MarketBar]] = {}
    for symbol, candidate in symbol_to_candidate.items():
        bars: list[MarketBar] = []
        for item in raw.get(symbol, []):
            timestamp = _parse_timestamp(item.get("t"))
            if timestamp is None:
                continue
            local = timestamp.astimezone(_ET)
            if local.date() != session_date or local.timetz().replace(tzinfo=None) < time(9, 30):
                continue
            if timestamp >= end:
                continue
            values = [item.get(field) for field in ("o", "h", "l", "c")]
            if any(value is None for value in values):
                continue
            try:
                open_value, high, low, close = (Decimal(str(value)) for value in values)
                volume = Decimal(str(item.get("v") or 0))
            except Exception:
                logger.debug("suppressed error in %s", "alpaca_historical_session_bars", exc_info=True)
                continue
            bars.append(
                MarketBar(
                    instrument_id=candidate.instrument_id,
                    interval="1m",
                    start_time=timestamp,
                    end_time=timestamp + timedelta(minutes=1),
                    open=open_value,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    is_final=True,
                    adjustment_mode=AdjustmentMode.RAW,
                    session="regular",
                    provider="alpaca_iex",
                    provider_event_id=str(item.get("t") or timestamp.isoformat()),
                    received_at=received_at,
                )
            )
        if not bars:
            raise ProviderDataUnavailableError(
                f"Alpaca IEX returned no regular-session 1m bars for {symbol} on {session_date}"
            )
        output[candidate.instrument_id] = sorted(bars, key=lambda bar: bar.start_time)
    return output


__all__ = ["alpaca_historical_session_bars"]
