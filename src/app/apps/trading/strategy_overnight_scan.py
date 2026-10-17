"""Market data for ``overnight-trading-strategy-shadow`` (Alpaca SIP, read-only).

The free SIP feed withholds the most recent 15 minutes, so the 15:50 ET decision
reads regular-session 30-minute bars that ended by 15:30 and prior-session daily
bars. One-minute bars for labelling are fetched the next morning, long after the
entry and exit minutes. Nothing here places, sizes or routes an order.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from .historical_gapper_reconstruction import _alpaca_assets, _alpaca_bars
from .providers.alpaca_iex import alpaca_iex_auth_headers
from .providers.http_runtime import ProviderHttpRuntime
from .strategies.models import OvernightShadowConfig
from .strategy_overnight_shadow import OvernightBar, OvernightCandidate, build_candidate
from app.apps.trading.us_equity_calendar import EASTERN as _ET

SCAN_BAR_MINUTES = 30
_CHUNK = 200
_ETF_EXCHANGES = {"ARCA", "BATS"}
_ETF_NAME = re.compile(
    r"\bETF\b|\bETN\b|exchange traded|index fund|proshares|direxion|ishares|leveraged|inverse| 2x| 3x", re.I
)
_DERIVATIVE_NAME = re.compile(r"warrant|\bwts?\b|\bunits?\b|\brights?\b|preferred|preference", re.I)


def is_common_stock(asset: dict[str, Any]) -> bool:
    """Exclude ETFs/ETNs, and warrants/units/rights/preferreds whose suffix the name confirms."""

    symbol = str(asset.get("symbol") or "").upper()
    name = str(asset.get("name") or "")
    exchange = str(asset.get("exchange") or "").upper()
    if exchange in _ETF_EXCHANGES or _ETF_NAME.search(name):
        return False
    suffix = (
        (exchange == "NASDAQ" and len(symbol) == 5 and symbol[-1] in "WUR")
        or re.search(r"[./ ](WS|U|R|RT|WT)$", symbol) is not None
        or re.search(r"[./ -]P(R)?[A-Z]?$", symbol) is not None
    )
    return not (suffix and _DERIVATIVE_NAME.search(name))


def _bar(raw: dict[str, Any]) -> OvernightBar | None:
    try:
        start = datetime.fromisoformat(str(raw["t"]).replace("Z", "+00:00"))
        return OvernightBar(
            start=start,
            open=Decimal(str(raw["o"])),
            high=Decimal(str(raw["h"])),
            low=Decimal(str(raw["l"])),
            close=Decimal(str(raw["c"])),
            volume=Decimal(str(raw.get("v") or 0)),
        )
    except (KeyError, ValueError, ArithmeticError):
        return None


def _bars(raw: Sequence[dict[str, Any]]) -> list[OvernightBar]:
    return [bar for item in raw if (bar := _bar(item)) is not None]


@dataclass(frozen=True, slots=True)
class OvernightScanResult:
    session_date: date
    eligible: int
    scanned: int
    candidates: tuple[OvernightCandidate, ...]


class OvernightMarketData:
    """Alpaca SIP reads for the overnight monitor; the runtime bounds concurrency and retries."""

    def __init__(self, runtime: ProviderHttpRuntime | None = None) -> None:
        self.runtime = runtime or ProviderHttpRuntime("alpaca_overnight_shadow", max_concurrency=2)

    def _headers(self) -> dict[str, str]:
        return alpaca_iex_auth_headers()

    def scan(self, session_date: date, prior_session: date, config: OvernightShadowConfig) -> OvernightScanResult:
        headers = self._headers()
        assets = {
            str(a["symbol"]).upper(): str(a["exchange"]).upper()
            for a in _alpaca_assets(self.runtime, headers)
            if is_common_stock(a)
        }
        # Prior-session daily bars only: prior close, ADV, and a price prefilter (a stock
        # above max_price / (1 + smallest arm change) cannot qualify today).
        min_change = min((arm.min_change for arm in config.arms if arm.enabled), default=Decimal("0"))
        max_prior = config.maximum_price / (1 + min_change)
        daily = _alpaca_bars(
            self.runtime, headers, sorted(assets), timeframe="1Day",
            start=datetime.combine(prior_session - timedelta(days=config.adv_sessions * 2 + 10), time(0), tzinfo=_ET),
            end=datetime.combine(prior_session + timedelta(days=1), time(0), tzinfo=_ET),
            chunk_size=_CHUNK, feed="sip",
        )
        history: dict[str, list[OvernightBar]] = {}
        for symbol, raw in daily.items():
            rows = [b for b in _bars(raw) if b.start.astimezone(_ET).date() <= prior_session]
            if rows and rows[-1].start.astimezone(_ET).date() == prior_session and 0 < rows[-1].close <= max_prior:
                history[symbol] = rows
        intraday = _alpaca_bars(
            self.runtime, headers, sorted(history), timeframe=f"{SCAN_BAR_MINUTES}Min",
            start=datetime.combine(session_date, time(9, 30), tzinfo=_ET),
            end=datetime.combine(session_date, config.data_cutoff_et, tzinfo=_ET),
            chunk_size=_CHUNK, feed="sip",
        )
        candidates = []
        for symbol, raw in intraday.items():
            rows = history.get(symbol)
            if not rows:
                continue
            candidate = build_candidate(
                symbol=symbol,
                instrument_id=f"equity:{assets[symbol]}:{symbol}",
                prior_close=rows[-1].close,
                bars=_bars(raw),
                adv_volumes=[b.volume for b in rows[-config.adv_sessions:]],
                session_date=session_date,
                config=config,
                bar_minutes=SCAN_BAR_MINUTES,
            )
            if candidate is not None:
                candidates.append(candidate)
        return OvernightScanResult(
            session_date=session_date, eligible=len(history), scanned=len(intraday),
            candidates=tuple(sorted(candidates, key=lambda c: c.symbol)),
        )

    def minute_bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> dict[str, list[OvernightBar]]:
        """One-minute SIP bars; ``end`` is capped 16 minutes in the past (free SIP delay)."""

        capped = min(end, datetime.now(timezone.utc) - timedelta(minutes=16))
        if not symbols or capped <= start:
            return {}
        raw = _alpaca_bars(
            self.runtime, self._headers(), sorted(set(symbols)), timeframe="1Min",
            start=start, end=capped, chunk_size=_CHUNK, feed="sip",
        )
        return {symbol: _bars(rows) for symbol, rows in raw.items()}


__all__ = ["OvernightMarketData", "OvernightScanResult", "SCAN_BAR_MINUTES", "is_common_stock"]
