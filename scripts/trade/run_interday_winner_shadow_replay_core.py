from __future__ import annotations

"""Replay the deterministic arms of the interday SHADOW strategy.

This is a research/benchmark runner.  It never imports a broker execution
client and never creates paper or live orders.  The input universe is the
outcome-labelled daily-winner CSV in ``docs/trading``; that means the universe
itself is hindsight-biased even though each strategy evaluator is run causally
over the intraday tape.
"""

import argparse
import csv
import json
import sys
from threading import Lock
import time as time_module
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SOURCE_ROOT, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.providers.alpaca_iex import ALPACA_DATA_URL, alpaca_iex_auth_headers
from app.apps.trading.strategies.models import StochRsi5mConfig, StrategyRiskProfile
from app.apps.trading.strategy_backtest import run_gap_pullback_backtest, freeze_backtest_session
from app.apps.trading.strategy_leader_momentum_continuation import (
    LeaderMomentumContext,
    POLICY_VERSION as LEADER_MOMENTUM_POLICY_VERSION,
    evaluate_leader_momentum_continuation,
)
from app.apps.trading.strategy_stoch_rsi_5m_late_stage import evaluate_stoch_rsi_5m_late_stage
from app.apps.trading.strategy_stoch_rsi_5m_early_single import evaluate_stoch_rsi_5m_early_single
from app.apps.trading.strategy_stoch_rsi_5m import evaluate_stoch_rsi_5m
from app.apps.trading.strategy_stoch_trend_capture import evaluate_stoch_trend_capture
from app.apps.trading.strategy_v2_qualification import managed_finviz_v2_config


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
ACTIVE_SOURCE = "yahoo"
ARMS = (
    "deterministic-v2",
    "stoch-trend-capture",
    "leader-momentum-continuation",
    "stoch-rsi-5min",
    "stoch-rsi-5min-early-single",
    "stoch-rsi-5min-late-stage",
    "gap-pullback-v2-prospective-20260825",
)
FIXED_DAILY_CAPITAL = Decimal("100000")
FIXED_SLOT_NOTIONAL = Decimal("20000")
ASSUMED_SPREAD_BPS = Decimal("40")
CACHE_SCHEMA_VERSION = "interday-market-data-cache-v1"
CACHE_DIR = Path("resources/cache/interday-market-data")
CACHE_ONLY = False

_CACHE_STATS: defaultdict[str, int] = defaultdict(int)
_CACHE_STATS_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class RawBar:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.interval_minutes)

    interval_minutes: int = 1


@dataclass(frozen=True)
class SymbolReplayData:
    symbol: str
    candidates: dict[date, GapperCandidate]
    bars_5m: dict[date, tuple[RawBar, ...]]
    bars_1m: dict[date, tuple[RawBar, ...]]
    five_minute_error: str | None = None
    one_minute_errors: dict[str, str] | None = None
    # Current-session bars remain the availability/coverage contract.  This
    # causal view additionally carries regular 5m history through each target
    # session so rolling indicators can warm up early in the session.
    bars_5m_history: dict[date, tuple[RawBar, ...]] = field(default_factory=dict)


def reset_cache_stats() -> None:
    with _CACHE_STATS_LOCK:
        _CACHE_STATS.clear()


def cache_stats() -> dict[str, int]:
    with _CACHE_STATS_LOCK:
        return dict(_CACHE_STATS)


def _increment_cache_stat(name: str, amount: int = 1) -> None:
    with _CACHE_STATS_LOCK:
        _CACHE_STATS[name] += amount


class MarketDataCache:
    """Persistent raw-bar cache shared by all deterministic replay arms.

    The cache is intentionally below the strategy layer.  Each symbol/timeframe
    request is fetched once, then split into local-session files so later replay
    runs can load the exact same raw bars without another Alpaca request.  Empty
    session files are retained only after a successful source response; a failed
    request never becomes a cached data gap.
    """

    def __init__(self, root: Path, source: str) -> None:
        self.root = root
        self.source = source

    def _timeframe_dir(self, symbol: str, timeframe: str) -> Path:
        safe_symbol = symbol.strip().upper()
        if not safe_symbol or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in safe_symbol):
            raise ValueError(f"unsafe cache symbol: {symbol!r}")
        if timeframe not in {"1m", "5m"}:
            raise ValueError(f"unsupported cache timeframe: {timeframe!r}")
        return self.root / self.source / safe_symbol / timeframe

    @staticmethod
    def _request_key(start: datetime, end: datetime, query_profile: str) -> dict[str, str]:
        return {
            "start": start.astimezone(UTC).isoformat(),
            "end": end.astimezone(UTC).isoformat(),
            "query_profile": query_profile,
        }

    @staticmethod
    def _session_dates(start: datetime, end: datetime) -> list[date]:
        first = start.astimezone(ET).date()
        last = (end - timedelta(microseconds=1)).astimezone(ET).date()
        count = (last - first).days + 1
        return [first + timedelta(days=offset) for offset in range(max(0, count))]

    @staticmethod
    def _serialize_bar(bar: RawBar) -> dict[str, object]:
        return {
            "start": bar.start.astimezone(UTC).isoformat(),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": str(bar.volume),
            "interval_minutes": bar.interval_minutes,
        }

    @staticmethod
    def _deserialize_bar(raw: object, *, symbol: str, timeframe: str) -> RawBar:
        if not isinstance(raw, dict):
            raise ValueError(f"{symbol} {timeframe}: malformed cached bar")
        try:
            start = datetime.fromisoformat(str(raw["start"])).astimezone(UTC)
            interval_minutes = int(raw["interval_minutes"])
            bar = RawBar(
                start=start,
                open=Decimal(str(raw["open"])),
                high=Decimal(str(raw["high"])),
                low=Decimal(str(raw["low"])),
                close=Decimal(str(raw["close"])),
                volume=Decimal(str(raw["volume"])),
                interval_minutes=interval_minutes,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise ValueError(f"{symbol} {timeframe}: malformed cached OHLCV") from exc
        expected_interval = 1 if timeframe == "1m" else 5
        if bar.interval_minutes != expected_interval:
            raise ValueError(f"{symbol} {timeframe}: cached interval mismatch")
        return bar

    def load(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime,
        end: datetime,
        query_profile: str,
    ) -> tuple[RawBar, ...] | None:
        directory = self._timeframe_dir(symbol, timeframe)
        manifest_path = directory / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest is not an object")
            if manifest.get("schema_version") != CACHE_SCHEMA_VERSION:
                raise ValueError("cache schema mismatch")
            if manifest.get("source") != self.source:
                raise ValueError("cache source mismatch")
            if manifest.get("symbol") != symbol.upper():
                raise ValueError("cache symbol mismatch")
            if manifest.get("timeframe") != timeframe:
                raise ValueError("cache timeframe mismatch")
            if manifest.get("request") != self._request_key(start, end, query_profile):
                raise ValueError("cache request mismatch")
            session_dates = manifest.get("session_dates")
            if not isinstance(session_dates, list) or not all(isinstance(item, str) for item in session_dates):
                raise ValueError("cache session index malformed")
            bars: list[RawBar] = []
            for session_date in session_dates:
                session_path = directory / f"{session_date}.json"
                payload = json.loads(session_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("session_date") != session_date:
                    raise ValueError("cache session payload malformed")
                raw_bars = payload.get("bars")
                if not isinstance(raw_bars, list):
                    raise ValueError("cache session bars malformed")
                bars.extend(
                    self._deserialize_bar(item, symbol=symbol, timeframe=timeframe)
                    for item in raw_bars
                )
        except (OSError, ValueError, json.JSONDecodeError, UnicodeError):
            _increment_cache_stat("misses")
            return None
        _increment_cache_stat("hits")
        return tuple(sorted(bars, key=lambda item: item.start))

    def store(
        self,
        symbol: str,
        timeframe: str,
        raw_bars: tuple[RawBar, ...],
        *,
        start: datetime,
        end: datetime,
        query_profile: str,
    ) -> None:
        directory = self._timeframe_dir(symbol, timeframe)
        directory.mkdir(parents=True, exist_ok=True)
        by_session: dict[date, list[RawBar]] = defaultdict(list)
        for bar in raw_bars:
            by_session[bar.start.astimezone(ET).date()].append(bar)
        session_dates = self._session_dates(start, end)
        for session_date in session_dates:
            payload = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "source": self.source,
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "session_date": session_date.isoformat(),
                "bars": [
                    self._serialize_bar(bar)
                    for bar in sorted(by_session.get(session_date, ()), key=lambda item: item.start)
                ],
            }
            (directory / f"{session_date.isoformat()}.json").write_text(
                json.dumps(payload, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        manifest = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source": self.source,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "request": self._request_key(start, end, query_profile),
            "session_dates": [session_date.isoformat() for session_date in session_dates],
            "bar_count": len(raw_bars),
        }
        manifest_path = directory / "manifest.json"
        temporary_path = directory / "manifest.json.tmp"
        temporary_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
        _increment_cache_stat("writes")


def _decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _parse_source(
    path: Path,
    *,
    expected_symbols_per_session: int | None = 5,
) -> tuple[list[dict[str, object]], list[date], dict[date, list[dict[str, object]]]]:
    rows: list[dict[str, object]] = []
    grouped: dict[date, list[dict[str, object]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            session_date = date.fromisoformat(str(raw["session_date"]))
            row = {
                "session_date": session_date,
                "rank": int(str(raw["rank"])),
                "symbol": str(raw["symbol"]).strip().upper(),
                "gain_pct": (
                    Decimal(str(raw["gain_pct"]).strip())
                    if str(raw.get("gain_pct") or "").strip()
                    else None
                ),
                "source_url": str(raw.get("source_url") or ""),
            }
            rows.append(row)
            grouped[session_date].append(row)
    sessions = sorted(grouped)
    if expected_symbols_per_session is not None:
        counts = {session_date: len(items) for session_date, items in grouped.items()}
        invalid = {
            session_date: count
            for session_date, count in counts.items()
            if count != expected_symbols_per_session
        }
        if invalid:
            details = ", ".join(
                f"{session_date.isoformat()}={count}"
                for session_date, count in sorted(invalid.items())
            )
            raise ValueError(
                f"expected {expected_symbols_per_session} benchmark symbols per session; "
                f"observed {details}"
            )
    return rows, sessions, grouped


def _epoch(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp())


def _fetch_chart(
    session: requests.Session,
    symbol: str,
    *,
    interval: str,
    start: datetime,
    end: datetime,
    include_prepost: bool,
    label: str,
) -> tuple[dict[str, Any], tuple[RawBar, ...]]:
    params = {
        "period1": _epoch(start),
        "period2": _epoch(end),
        "interval": interval,
        "includePrePost": str(include_prepost).lower(),
        "events": "",
    }
    last_error = ""
    for attempt in range(6):
        try:
            response = session.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params=params,
                headers={"User-Agent": "Omnix interday shadow research/1.0"},
                timeout=45,
            )
            if response.status_code == 429:
                last_error = f"HTTP 429 for {label}"
                time_module.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart") if isinstance(payload, dict) else None
            error = chart.get("error") if isinstance(chart, dict) else None
            if error:
                description = error.get("description") if isinstance(error, dict) else str(error)
                raise RuntimeError(f"{label}: {description}")
            result = ((chart or {}).get("result") or [None])[0] if isinstance(chart, dict) else None
            if not isinstance(result, dict):
                raise RuntimeError(f"{label}: Yahoo returned no chart result")
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            if not isinstance(quote, dict) or not isinstance(timestamps, list):
                raise RuntimeError(f"{label}: malformed chart payload")
            bars: list[RawBar] = []
            series = {field: quote.get(field) or [] for field in ("open", "high", "low", "close", "volume")}
            for index, raw_timestamp in enumerate(timestamps):
                if any(not isinstance(values, list) or index >= len(values) for values in series.values()):
                    continue
                if any(series[field][index] is None for field in ("open", "high", "low", "close")):
                    continue
                start_time = datetime.fromtimestamp(int(raw_timestamp), tz=UTC)
                bars.append(
                    RawBar(
                        start=start_time,
                        open=_decimal(series["open"][index]),
                        high=_decimal(series["high"][index]),
                        low=_decimal(series["low"][index]),
                        close=_decimal(series["close"][index]),
                        volume=_decimal(series["volume"][index]),
                        interval_minutes=1 if interval == "1m" else 5,
                    )
                )
            return result.get("meta") or {}, tuple(bars)
        except (requests.RequestException, ValueError) as exc:
            last_error = f"{label}: {type(exc).__name__}: {exc}"
            if attempt < 5:
                time_module.sleep(1.0 * (attempt + 1))
            else:
                break
        except RuntimeError:
            raise
    raise RuntimeError(last_error or f"{label}: Yahoo request failed")


def _parse_alpaca_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label}: Alpaca bar is missing timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{label}: Alpaca bar has invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label}: Alpaca bar timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _fetch_alpaca_bars(
    session: requests.Session,
    symbol: str,
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    label: str,
) -> tuple[dict[str, Any], tuple[RawBar, ...]]:
    """Fetch finalized consolidated SIP bars with bounded pagination."""

    headers = alpaca_iex_auth_headers()
    params: dict[str, object] = {
        "symbols": symbol,
        "timeframe": timeframe,
        "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "adjustment": "raw",
        "feed": "sip",
        "sort": "asc",
        "limit": 10000,
    }
    all_bars: list[RawBar] = []
    page_token: str | None = None
    last_meta: dict[str, Any] = {}
    for page in range(100):
        if page_token:
            params["page_token"] = page_token
        response = None
        for attempt in range(6):
            response = session.get(
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                params=params,
                headers=headers,
                timeout=45,
            )
            if response.status_code != 429:
                break
            retry_after = _decimal(response.headers.get("Retry-After"), Decimal("0"))
            delay = retry_after if retry_after > 0 else Decimal(str(2 ** attempt))
            time_module.sleep(float(min(delay, Decimal("30"))))
        if response is None or response.status_code == 429:
            raise RuntimeError(f"{label}: Alpaca SIP rate limited the request after retries")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label}: Alpaca returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{label}: Alpaca returned malformed JSON")
        raw_bars = payload.get("bars")
        if not isinstance(raw_bars, dict):
            raise RuntimeError(f"{label}: Alpaca returned no bars map")
        symbol_bars = raw_bars.get(symbol, [])
        if not isinstance(symbol_bars, list):
            raise RuntimeError(f"{label}: Alpaca returned malformed bars for {symbol}")
        for raw in symbol_bars:
            if not isinstance(raw, dict):
                continue
            start_time = _parse_alpaca_timestamp(raw.get("t"), label=label)
            values = [raw.get(field) for field in ("o", "h", "l", "c")]
            if any(value is None for value in values):
                continue
            try:
                open_value, high, low, close = (Decimal(str(value)) for value in values)
                volume = Decimal(str(raw.get("v") or 0))
            except Exception as exc:
                raise RuntimeError(f"{label}: Alpaca returned invalid OHLCV") from exc
            all_bars.append(
                RawBar(
                    start=start_time,
                    open=open_value,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    interval_minutes=1 if timeframe == "1Min" else 5,
                )
            )
        last_meta = {
            "feed": "sip",
            "timeframe": timeframe,
            "start": params["start"],
            "end": params["end"],
        }
        page_token = payload.get("next_page_token") if isinstance(payload.get("next_page_token"), str) else None
        if not page_token:
            return last_meta, tuple(sorted(all_bars, key=lambda item: item.start))
    raise RuntimeError(f"{label}: Alpaca SIP pagination exceeded safety limit")


def _data_source(interval: str) -> str:
    return f"{ACTIVE_SOURCE}-{interval}"


def _bar_provider() -> str:
    return "alpaca_sip" if ACTIVE_SOURCE == "alpaca-sip" else "yahoo"


def _local_start(raw: RawBar) -> datetime:
    return raw.start.astimezone(ET)


def _session_bars(raw_bars: tuple[RawBar, ...], session_date: date, *, regular: bool) -> tuple[RawBar, ...]:
    output: list[RawBar] = []
    for bar in raw_bars:
        local = _local_start(bar)
        is_regular = time(9, 30) <= local.time() < time(16, 0)
        if local.date() == session_date and is_regular == regular:
            output.append(bar)
    return tuple(sorted(output, key=lambda item: item.start))


def _build_5m_history(
    raw_bars: tuple[RawBar, ...], sessions: list[date]
) -> dict[date, tuple[RawBar, ...]]:
    """Build a causal regular-session 5m view for each replay session."""

    by_date: defaultdict[date, list[RawBar]] = defaultdict(list)
    for bar in raw_bars:
        local = _local_start(bar)
        if time(9, 30) <= local.time() < time(16, 0):
            by_date[local.date()].append(bar)

    ordered_dates = sorted(by_date)
    ordered_bars = {
        session_date: tuple(sorted(by_date[session_date], key=lambda item: item.start))
        for session_date in ordered_dates
    }
    history_by_session: dict[date, tuple[RawBar, ...]] = {}
    for session_date in sorted(set(sessions)):
        history: list[RawBar] = []
        for available_date in ordered_dates:
            if available_date > session_date:
                break
            history.extend(ordered_bars[available_date])
        history_by_session[session_date] = tuple(history)
    return history_by_session


def _market_bars(raw_bars: tuple[RawBar, ...], instrument_id: str, interval: str) -> list[MarketBar]:
    return [
        MarketBar(
            instrument_id=instrument_id,
            interval=interval,
            start_time=bar.start,
            end_time=bar.start + timedelta(minutes=bar.interval_minutes),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            is_final=True,
            adjustment_mode=AdjustmentMode.RAW,
            session="regular",
            provider=_bar_provider(),
            provider_event_id=str(int(bar.start.timestamp())),
            received_at=datetime.now(UTC),
        )
        for bar in raw_bars
    ]


def _premarket_details(
    raw_bars: tuple[RawBar, ...],
    session_date: date,
    *,
    cutoff: datetime,
) -> tuple[Decimal | None, Decimal, Decimal, int]:
    bars = [
        bar
        for bar in raw_bars
        if (
            _local_start(bar).date() == session_date
            and time(4, 0) <= _local_start(bar).time() < time(9, 30)
            and bar.end <= cutoff
        )
    ]
    if not bars:
        return None, Decimal("0"), Decimal("0"), 0
    dollar_volume = sum(
        (((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume for bar in bars),
        Decimal("0"),
    )
    return bars[-1].close, sum((bar.volume for bar in bars), Decimal("0")), dollar_volume, len(bars)


def _build_candidates(
    symbol: str,
    source_rows: dict[date, dict[str, object]],
    five_minute_bars: tuple[RawBar, ...],
) -> dict[date, GapperCandidate]:
    all_dates = sorted({_local_start(bar).date() for bar in five_minute_bars})
    regular_by_date = {
        session_date: _session_bars(five_minute_bars, session_date, regular=True)
        for session_date in all_dates
    }
    premarket_by_date: dict[date, tuple[Decimal | None, Decimal, Decimal, int]] = {}
    premarket_volume_by_date: dict[date, Decimal] = {}
    for session_date in all_dates:
        cutoff = datetime.combine(session_date, time(9, 15), tzinfo=ET).astimezone(UTC)
        price, volume, dollar_volume, count = _premarket_details(
            five_minute_bars,
            session_date,
            cutoff=cutoff,
        )
        premarket_by_date[session_date] = (price, volume, dollar_volume, count)
        premarket_volume_by_date[session_date] = volume

    candidates: dict[date, GapperCandidate] = {}
    ordered_dates = sorted(source_rows)
    for session_date in ordered_dates:
        regular_prior = [
            (day, bars[-1].close)
            for day, bars in regular_by_date.items()
            if day < session_date and bars
        ]
        previous_close = max(regular_prior, key=lambda item: item[0])[1] if regular_prior else None
        price, premarket_volume, dollar_volume, bar_count = premarket_by_date[session_date]
        flags: list[str] = []
        if previous_close is None:
            previous_close = Decimal("1")
            flags.append("PREVIOUS_CLOSE_UNAVAILABLE")
        if price is None:
            price = previous_close
            flags.append("PREMARKET_DATA_UNAVAILABLE")

        historical_volumes = [
            value
            for day, value in premarket_volume_by_date.items()
            if day < session_date and value > 0
        ]
        tod_rvol = None
        if len(historical_volumes) >= 5:
            baseline = sum(historical_volumes, Decimal("0")) / Decimal(len(historical_volumes))
            if baseline > 0:
                tod_rvol = premarket_volume / baseline
        if tod_rvol is None:
            flags.append("TOD_RVOL_UNAVAILABLE")
        if not regular_by_date.get(session_date):
            flags.append("REGULAR_BARS_UNAVAILABLE")

        evaluation_time = datetime.combine(session_date, time(9, 15), tzinfo=ET).astimezone(UTC)
        gap_pct = (price / previous_close - Decimal("1")) * Decimal("100")
        candidates[session_date] = GapperCandidate(
            instrument_id=f"equity:US:{symbol}",
            binding_id=f"{_bar_provider()}:{symbol}",
            observed_at=evaluation_time,
            previous_close=previous_close,
            premarket_price=price,
            gap_pct=gap_pct,
            premarket_volume=premarket_volume,
            premarket_dollar_volume=dollar_volume,
            premarket_bar_count=bar_count,
            tod_rvol=tod_rvol,
            market_data_complete=not flags,
            data_quality_flags=tuple(flags),
            spread_bps=ASSUMED_SPREAD_BPS,
            discovery_rank=int(source_rows[session_date]["rank"]),
        )
    return candidates


def _load_symbol(
    symbol: str,
    source_rows: dict[date, dict[str, object]],
    sessions: list[date],
) -> SymbolReplayData:
    session = requests.Session()
    cache = MarketDataCache(CACHE_DIR, ACTIVE_SOURCE)
    five_minute_error: str | None = None
    one_minute_errors: dict[str, str] = {}
    first_session = min(sessions)
    last_session = max(sessions)
    five_start = datetime.combine(
        first_session - timedelta(days=30), time(0), tzinfo=ET
    ).astimezone(UTC)
    five_end = datetime.combine(
        last_session + timedelta(days=1), time(0), tzinfo=ET
    ).astimezone(UTC)
    try:
        five_raw = cache.load(
            symbol,
            "5m",
            start=five_start,
            end=five_end,
            query_profile="extended_session",
        )
        if five_raw is None:
            if CACHE_ONLY:
                raise RuntimeError(f"CACHE_MISS:{ACTIVE_SOURCE}:{symbol}:5m")
            _increment_cache_stat("network_fetches")
            if ACTIVE_SOURCE == "alpaca-sip":
                _meta, five_raw = _fetch_alpaca_bars(
                    session,
                    symbol,
                    timeframe="5Min",
                    start=five_start,
                    end=five_end,
                    label=f"{symbol} SIP 5m",
                )
            else:
                _meta, five_raw = _fetch_chart(
                    session,
                    symbol,
                    interval="5m",
                    start=five_start,
                    end=five_end,
                    include_prepost=True,
                    label=f"{symbol} 5m",
                )
            cache.store(
                symbol,
                "5m",
                five_raw,
                start=five_start,
                end=five_end,
                query_profile="extended_session",
            )
        candidates = _build_candidates(symbol, source_rows, five_raw)
    except Exception as exc:
        five_minute_error = f"{type(exc).__name__}: {exc}"
        candidates = {}
        five_raw = ()

    bars_5m = {
        session_date: _session_bars(five_raw, session_date, regular=True)
        for session_date in sessions
    }
    bars_5m_history = _build_5m_history(five_raw, sessions)
    bars_1m: dict[date, tuple[RawBar, ...]] = {session_date: () for session_date in sessions}
    if five_minute_error is None:
        by_start: dict[datetime, RawBar] = {}
        one_start = datetime.combine(
            first_session, time(9, 30), tzinfo=ET
        ).astimezone(UTC)
        one_end = datetime.combine(
            last_session + timedelta(days=1), time(16), tzinfo=ET
        ).astimezone(UTC)
        one_raw = cache.load(
            symbol,
            "1m",
            start=one_start,
            end=one_end,
            query_profile="regular_session",
        )
        if one_raw is None:
            if CACHE_ONLY:
                one_minute_errors["cache"] = f"CACHE_MISS:{ACTIVE_SOURCE}:{symbol}:1m"
                one_raw = ()
            else:
                _increment_cache_stat("network_fetches")
                fetched_one_raw: tuple[RawBar, ...]
                if ACTIVE_SOURCE == "alpaca-sip":
                    try:
                        _meta, fetched_one_raw = _fetch_alpaca_bars(
                            session,
                            symbol,
                            timeframe="1Min",
                            # Query regular-session UTC bounds only.  All benchmark
                            # dates are during EDT, so this stays below Alpaca's
                            # per-page limit while preserving every target minute.
                            start=one_start,
                            end=one_end,
                            label=f"{symbol} SIP 1m",
                        )
                    except Exception as exc:
                        one_minute_errors[
                            f"{first_session}..{last_session}"
                        ] = f"{type(exc).__name__}: {exc}"
                        fetched_one_raw = ()
                    else:
                        cache.store(
                            symbol,
                            "1m",
                            fetched_one_raw,
                            start=one_start,
                            end=one_end,
                            query_profile="regular_session",
                        )
                else:
                    # Yahoo supports only short one-minute windows.  Four bounded
                    # chunks cover the retained 1m history.
                    chunks = (
                        (date(2026, 8, 17), date(2026, 8, 24)),
                        (date(2026, 8, 24), date(2026, 8, 31)),
                        (date(2026, 8, 31), date(2026, 9, 7)),
                        (date(2026, 9, 7), date(2026, 9, 12)),
                    )
                    fetched_bars: dict[datetime, RawBar] = {}
                    yahoo_fetch_failed = False
                    for chunk_start, chunk_end in chunks:
                        try:
                            _meta, chunk_raw = _fetch_chart(
                                session,
                                symbol,
                                interval="1m",
                                start=datetime.combine(chunk_start, time(13, 0), tzinfo=UTC),
                                end=datetime.combine(chunk_end, time(20, 0), tzinfo=UTC),
                                include_prepost=False,
                                label=f"{symbol} 1m {chunk_start.isoformat()}..{chunk_end.isoformat()}",
                            )
                        except Exception as exc:
                            one_minute_errors[f"{chunk_start.isoformat()}..{chunk_end.isoformat()}"] = f"{type(exc).__name__}: {exc}"
                            yahoo_fetch_failed = True
                            continue
                        for bar in chunk_raw:
                            local = _local_start(bar)
                            if time(9, 30) <= local.time() < time(16, 0):
                                fetched_bars[bar.start] = bar
                    fetched_one_raw = tuple(sorted(fetched_bars.values(), key=lambda item: item.start))
                    if not yahoo_fetch_failed:
                        cache.store(
                            symbol,
                            "1m",
                            fetched_one_raw,
                            start=one_start,
                            end=one_end,
                            query_profile="regular_session",
                        )
                one_raw = fetched_one_raw
        for bar in one_raw:
            local = _local_start(bar)
            if time(9, 30) <= local.time() < time(16, 0):
                by_start[bar.start] = bar
        for session_date in sessions:
            bars_1m[session_date] = tuple(
                sorted(
                    (
                        bar
                        for bar in by_start.values()
                        if _local_start(bar).date() == session_date
                    ),
                    key=lambda item: item.start,
                )
            )
    return SymbolReplayData(
        symbol=symbol,
        candidates=candidates,
        bars_5m=bars_5m,
        bars_1m=bars_1m,
        bars_5m_history=bars_5m_history,
        five_minute_error=five_minute_error,
        one_minute_errors=one_minute_errors,
    )


def _base_observation(
    arm: str,
    session_date: date,
    source_row: dict[str, object],
    *,
    symbol: str,
    status: str,
    reason: str,
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
    entry_price: Decimal | None = None,
    exit_price: Decimal | None = None,
    return_pct: Decimal | None = None,
    trade_count: int = 0,
    win_count: int = 0,
    loss_count: int = 0,
    risk_pnl: Decimal | None = None,
    data_source: str | None = None,
) -> dict[str, object]:
    return {
        "arm": arm,
        "session_date": session_date,
        "symbol": symbol,
        "rank": int(source_row["rank"]),
        "benchmark_gain_pct": source_row["gain_pct"],
        "status": status,
        "reason": reason,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return_pct": return_pct,
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "risk_managed_pnl": risk_pnl,
        "normalized_allocation": Decimal("0"),
        "normalized_pnl": Decimal("0"),
        "data_source": data_source or ACTIVE_SOURCE,
    }


def _evaluate_overlay_arms(
    sessions: list[date],
    grouped: dict[date, list[dict[str, object]]],
    loaded: dict[str, SymbolReplayData],
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    stoch_config = StochRsi5mConfig()
    stoch_rsi_variants = (
        ("stoch-rsi-5min", evaluate_stoch_rsi_5m),
        ("stoch-rsi-5min-early-single", evaluate_stoch_rsi_5m_early_single),
        ("stoch-rsi-5min-late-stage", evaluate_stoch_rsi_5m_late_stage),
    )
    for symbol, data in loaded.items():
        source_by_date = {session_date: row for session_date, rows in grouped.items() for row in rows if row["symbol"] == symbol}
        history_1m: list[MarketBar] = []
        for session_date in sessions:
            source_row = source_by_date.get(session_date)
            five_raw = data.bars_5m.get(session_date, ())
            one_raw = data.bars_1m.get(session_date, ())

            # Both Stoch RSI arms consume the same canonical 5m tape. The
            # late-stage variant only changes its entry-start cutoff.
            if source_row is not None:
                for arm, evaluator in stoch_rsi_variants:
                    if data.five_minute_error:
                        observations.append(_base_observation(arm, session_date, source_row, symbol=symbol, status="data_unavailable", reason=data.five_minute_error, data_source=_data_source("5m")))
                    elif not five_raw:
                        observations.append(_base_observation(arm, session_date, source_row, symbol=symbol, status="data_unavailable", reason="STOCH_RSI_5M_REGULAR_BARS_UNAVAILABLE", data_source=_data_source("5m")))
                    else:
                        five_history_raw = data.bars_5m_history.get(session_date) or five_raw
                        snapshot = evaluator(
                            _market_bars(five_history_raw, f"equity:US:{symbol}", "5m"),
                            stoch_config,
                        )
                        trades = tuple(snapshot.trades)
                        factor = Decimal("1")
                        for trade in trades:
                            factor *= Decimal("1") + trade.return_pct / Decimal("100")
                        total_return = (factor - Decimal("1")) * Decimal("100") if trades else None
                        observations.append(
                            _base_observation(
                                arm,
                                session_date,
                                source_row,
                                symbol=symbol,
                                status="completed" if trades else snapshot.state,
                                reason=snapshot.reason_code,
                                entry_time=trades[0].entry_time if trades else snapshot.entry_time,
                                exit_time=trades[-1].exit_time if trades else snapshot.exit_time,
                                entry_price=trades[0].entry_price if trades else snapshot.entry_price,
                                exit_price=trades[-1].exit_price if trades else snapshot.exit_price,
                                return_pct=total_return,
                                trade_count=len(trades),
                                win_count=sum(trade.return_pct > 0 for trade in trades),
                                loss_count=sum(trade.return_pct < 0 for trade in trades),
                                data_source=_data_source("5m"),
                            )
                        )

            # B (stoch-trend-capture) is a 3m overlay built from canonical 1m
            # bars.  Missing early 1m sessions remain explicit data gaps.
            if not one_raw and source_row is not None:
                reason = "STOCH_TREND_1M_BARS_UNAVAILABLE"
                if data.one_minute_errors:
                    reason = "; ".join(data.one_minute_errors.values())
                observations.append(_base_observation("stoch-trend-capture", session_date, source_row, symbol=symbol, status="data_unavailable", reason=reason, data_source=_data_source("1m")))
            elif one_raw:
                day_bars = _market_bars(one_raw, f"equity:US:{symbol}", "1m")
                history_1m.extend(day_bars)
                if source_row is not None:
                    snapshot = evaluate_stoch_trend_capture(history_1m)
                    completed = snapshot.return_pct is not None
                    observations.append(
                        _base_observation(
                            "stoch-trend-capture",
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="completed" if completed else snapshot.state,
                            reason=snapshot.reason_code,
                            entry_time=snapshot.entry_time,
                            exit_time=snapshot.runner_exit_time,
                            entry_price=snapshot.entry_price,
                            exit_price=snapshot.combined_exit_price,
                            return_pct=snapshot.return_pct,
                            trade_count=1 if completed else 0,
                            win_count=1 if completed and snapshot.return_pct > 0 else 0,
                            loss_count=1 if completed and snapshot.return_pct < 0 else 0,
                            data_source=_data_source("1m"),
                        )
                    )

            # C (leader-momentum-continuation) is an independent deterministic
            # 1m evaluator. It receives only causal context reconstructed at
            # the 09:15 ET candidate observation.
            if source_row is not None:
                candidate = data.candidates.get(session_date)
                if data.five_minute_error:
                    observations.append(
                        _base_observation(
                            "leader-momentum-continuation",
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason=data.five_minute_error,
                            data_source=_data_source("1m"),
                        )
                    )
                elif candidate is None:
                    observations.append(
                        _base_observation(
                            "leader-momentum-continuation",
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason="LEADER_MOMENTUM_CANDIDATE_METADATA_UNAVAILABLE",
                            data_source=_data_source("1m"),
                        )
                    )
                elif not one_raw:
                    observations.append(
                        _base_observation(
                            "leader-momentum-continuation",
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="data_unavailable",
                            reason="LEADER_MOMENTUM_1M_REGULAR_BARS_UNAVAILABLE",
                            data_source=_data_source("1m"),
                        )
                    )
                else:
                    context = LeaderMomentumContext(
                        tod_rvol=candidate.tod_rvol,
                        spread_bps=candidate.spread_bps,
                        dollar_volume=candidate.premarket_dollar_volume,
                    )
                    snapshot = evaluate_leader_momentum_continuation(
                        _market_bars(one_raw, f"equity:US:{symbol}", "1m"),
                        context=context,
                    )
                    trades = tuple(snapshot.trades)
                    factor = Decimal("1")
                    for trade in trades:
                        factor *= Decimal("1") + trade.return_pct / Decimal("100")
                    total_return = (factor - Decimal("1")) * Decimal("100") if trades else None
                    observations.append(
                        _base_observation(
                            "leader-momentum-continuation",
                            session_date,
                            source_row,
                            symbol=symbol,
                            status="completed" if trades else snapshot.state,
                            reason=snapshot.reason_code,
                            entry_time=trades[0].entry_time if trades else snapshot.entry_time,
                            exit_time=trades[-1].exit_time if trades else None,
                            entry_price=trades[0].entry_price if trades else snapshot.entry_price,
                            exit_price=trades[-1].exit_price if trades else None,
                            return_pct=total_return,
                            trade_count=len(trades),
                            win_count=sum(trade.return_pct > 0 for trade in trades),
                            loss_count=sum(trade.return_pct < 0 for trade in trades),
                            data_source=_data_source("1m"),
                        )
                    )
    return observations


def _gap_observations(
    sessions: list[date],
    grouped: dict[date, list[dict[str, object]]],
    loaded: dict[str, SymbolReplayData],
    observations: list[dict[str, object]],
    input_path: Path,
) -> tuple[dict[date, Any], dict[date, Decimal], dict[date, str]]:
    config = managed_finviz_v2_config()
    risk = StrategyRiskProfile()
    current_cash = FIXED_DAILY_CAPITAL
    gap_results: dict[date, Any] = {}
    gap_risk_pnl: dict[date, Decimal] = {}
    gap_status: dict[date, str] = {}
    for session_date in sessions:
        rows = sorted(grouped[session_date], key=lambda row: int(row["rank"]))
        candidates = [loaded[str(row["symbol"])].candidates.get(session_date) for row in rows]
        if any(candidate is None for candidate in candidates):
            gap_status[session_date] = "5m candidate metadata unavailable"
            continue
        candidate_list = [candidate for candidate in candidates if candidate is not None]
        bars_by_instrument: dict[str, list[MarketBar]] = {}
        unavailable = []
        for row, candidate in zip(rows, candidate_list):
            data = loaded[str(row["symbol"])]
            one_raw = data.bars_1m.get(session_date, ())
            if not one_raw:
                unavailable.append(str(row["symbol"]))
                continue
            bars_by_instrument[candidate.instrument_id] = _market_bars(one_raw, candidate.instrument_id, "1m")
        if unavailable:
            gap_status[session_date] = "1m bars unavailable: " + ", ".join(unavailable)
            continue
        universe = freeze_gapper_universe(
            universe_id=f"winner-benchmark-{session_date.isoformat()}",
            session_date=session_date,
            evaluation_time=datetime.combine(session_date, time(9, 15), tzinfo=ET).astimezone(UTC),
            discovery_source="import",
            candidates=candidate_list,
            source_locator=f"{input_path.as_posix()}#{session_date.isoformat()}",
            source_candidate_symbols=tuple(str(row["symbol"]) for row in rows),
        )
        dataset = freeze_backtest_session(
            session_date=session_date,
            universe=universe,
            bars_by_instrument=bars_by_instrument,
        )
        result = run_gap_pullback_backtest(
            dataset,
            config,
            PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
            assumed_spread_bps=ASSUMED_SPREAD_BPS,
            max_hold_minutes=config.v2_max_hold_minutes,
            max_concurrent_positions=risk.max_positions,
            risk_profile=risk,
            initial_cash=current_cash,
        )
        pnl = sum((trade.pnl_per_share * trade.entry_fill_quantity for trade in result.trades), Decimal("0"))
        current_cash += pnl
        gap_results[session_date] = result
        gap_risk_pnl[session_date] = pnl
        gap_status[session_date] = "backtested"
        trade_by_symbol = {trade.instrument_id: trade for trade in result.trades}
        decision_by_symbol = {decision.instrument_id: decision for decision in result.candidate_decisions}
        for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
            for row, candidate in zip(rows, candidate_list):
                trade = trade_by_symbol.get(candidate.instrument_id)
                decision = decision_by_symbol.get(candidate.instrument_id)
                if trade is not None:
                    return_pct = (trade.exit_price / trade.entry_price - Decimal("1")) * Decimal("100")
                    observations.append(_base_observation(arm, session_date, row, symbol=str(row["symbol"]), status="completed", reason=trade.exit_reason, entry_time=trade.entry_time, exit_time=trade.exit_time, entry_price=trade.entry_price, exit_price=trade.exit_price, return_pct=return_pct, trade_count=1, win_count=1 if return_pct > 0 else 0, loss_count=1 if return_pct < 0 else 0, risk_pnl=trade.pnl_per_share * trade.entry_fill_quantity, data_source=_data_source("1m")))
                else:
                    reason = (decision.rejection_reason if decision else None) or (decision.state if decision else "NO_DECISION")
                    observations.append(_base_observation(arm, session_date, row, symbol=str(row["symbol"]), status=decision.state if decision else "data_unavailable", reason=str(reason), data_source=_data_source("1m")))
    # Add explicit rows for days that could not be run so every arm remains
    # comparable to the benchmark input.
    for session_date in sessions:
        if session_date in gap_results:
            continue
        for arm in ("deterministic-v2", "gap-pullback-v2-prospective-20260825"):
            reason = gap_status.get(session_date, "gap backtest unavailable")
            for row in sorted(grouped[session_date], key=lambda item: int(item["rank"])):
                observations.append(_base_observation(arm, session_date, row, symbol=str(row["symbol"]), status="data_unavailable", reason=reason, data_source=_data_source("1m")))
    gap_risk_pnl[date.min] = current_cash  # private sentinel consumed by caller
    return gap_results, gap_risk_pnl, gap_status


def _apply_normalized_allocations(observations: list[dict[str, object]], sessions: list[date]) -> None:
    by_arm_day: dict[tuple[str, date], list[dict[str, object]]] = defaultdict(list)
    for observation in observations:
        by_arm_day[(str(observation["arm"]), observation["session_date"])].append(observation)
    for arm in ARMS:
        for session_date in sessions:
            remaining = FIXED_DAILY_CAPITAL
            completed = sorted(
                (
                    item
                    for item in by_arm_day[(arm, session_date)]
                    if item["status"] == "completed" and item["return_pct"] is not None
                ),
                key=lambda item: (item["entry_time"] or datetime.max.replace(tzinfo=UTC), int(item["rank"]), str(item["symbol"])),
            )
            for item in completed:
                allocation = min(FIXED_SLOT_NOTIONAL, remaining)
                item["normalized_allocation"] = allocation
                item["normalized_pnl"] = allocation * Decimal(str(item["return_pct"])) / Decimal("100")
                remaining -= allocation
                if remaining <= 0:
                    break


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})


def _summary_rows(observations: list[dict[str, object]], sessions: list[date]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    daily: list[dict[str, object]] = []
    arm_summary: list[dict[str, object]] = []
    for arm in ARMS:
        arm_rows = [row for row in observations if row["arm"] == arm]
        total_pnl = sum((row["normalized_pnl"] for row in arm_rows), Decimal("0"))
        arm_summary.append({
            "arm": arm,
            "evaluable_sessions": len({row["session_date"] for row in arm_rows if row["status"] != "data_unavailable"}),
            "benchmark_observations": len(arm_rows),
            "completed_trades": sum(int(row["trade_count"]) for row in arm_rows),
            "wins": sum(int(row["win_count"]) for row in arm_rows),
            "losses": sum(int(row["loss_count"]) for row in arm_rows),
            "normalized_pnl": total_pnl,
            "normalized_return_pct": total_pnl / FIXED_DAILY_CAPITAL * Decimal("100"),
        })
    for session_date in sessions:
        row: dict[str, object] = {"session_date": session_date}
        for arm in ARMS:
            values = [item for item in observations if item["arm"] == arm and item["session_date"] == session_date]
            pnl = sum((item["normalized_pnl"] for item in values), Decimal("0"))
            row[f"{arm}_pnl"] = pnl
            row[f"{arm}_return_pct"] = pnl / FIXED_DAILY_CAPITAL * Decimal("100")
            row[f"{arm}_trades"] = sum(int(item["trade_count"]) for item in values)
        daily.append(row)
    return arm_summary, daily


def _write_summary(
    path: Path,
    *,
    input_path: Path,
    sessions: list[date],
    loaded: dict[str, SymbolReplayData],
    arm_summary: list[dict[str, object]],
    daily: list[dict[str, object]],
    gap_risk_pnl: dict[date, Decimal],
    gap_status: dict[date, str],
    replay_cache_stats: dict[str, int],
    benchmark_observations: int,
    expected_symbols_per_session: int | None,
    outcome_labels_present: bool,
) -> None:
    first, last = sessions[0], sessions[-1]
    risk_ending = gap_risk_pnl.get(date.min, FIXED_DAILY_CAPITAL)
    risk_pnl = risk_ending - FIXED_DAILY_CAPITAL
    five_covered = sum(
        any(data.bars_5m.get(session_date) for data in loaded.values())
        for session_date in sessions
    )
    one_covered = sum(
        any(data.bars_1m.get(session_date) for data in loaded.values())
        for session_date in sessions
    )
    backtested_sessions = sum(value == "backtested" for value in gap_status.values())
    source_name = "Alpaca SIP consolidated" if ACTIVE_SOURCE == "alpaca-sip" else "Yahoo"
    lines = [
        "# Interday deterministic SHADOW replay against input cohort",
        "",
        f"- Input: `{input_path.as_posix()}`",
        f"- Period: {first} through {last} ({len(sessions)} trading sessions, {benchmark_observations} input observations)",
        f"- Cohort contract: {expected_symbols_per_session or 'variable-size input cohort'}",
        f"- End-of-day outcome labels present in input: {'yes' if outcome_labels_present else 'no'}",
        "- Strategy: `interday-trading-strategy-shadow`",
        f"- Arms evaluated: {', '.join(f'`{arm}`' for arm in ARMS)}",
        "- LLM arms excluded: `ai-every-minute`, `ai-event-driven`",
        "- LLM calls and order side effects: none",
        f"- Market-data cache: `{CACHE_DIR.as_posix()}` ({replay_cache_stats.get('hits', 0)} timeframe hits, {replay_cache_stats.get('misses', 0)} misses, {replay_cache_stats.get('network_fetches', 0)} network fetches)",
        "",
        "## Results",
        "",
        "Normalized overlay P/L reuses a $100,000 daily basket, assigns $20,000 to each completed symbol (up to five slots), does not reallocate unused slots, and sums daily P/L without compounding. This makes the deterministic arms comparable even though the two overlay evaluators do not define monetary position sizing.",
        "",
        "| Arm | Evaluable sessions | Completed trades | Wins / losses | Normalized P/L | Reused-$100k return |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in arm_summary:
        lines.append(f"| `{row['arm']}` | {row['evaluable_sessions']}/{len(sessions)} | {row['completed_trades']} | {row['wins']} / {row['losses']} | ${row['normalized_pnl']:.2f} | {row['normalized_return_pct']:.2f}% |")
    lines.extend([
        "",
        f"The canonical V2 risk-managed account (deterministic-v2 / child gap-pullback arm), starting at $100,000, ended at **${risk_ending:.2f}** for **${risk_pnl:.2f}** P/L over the {backtested_sessions} sessions with complete 1-minute tapes.",
        "",
        "## Daily normalized P/L",
        "",
        "| Date | " + " | ".join(ARMS) + " |",
        "|---|" + "---:|" * len(ARMS),
    ])
    for row in daily:
        lines.append("| " + " | ".join([
            str(row["session_date"]),
            *(f"${row[f'{arm}_pnl']:.2f}" for arm in ARMS),
        ]) + " |")
    lines.extend([
        "",
        "## Fidelity and caveats",
        "",
        f"- {source_name} historical 5-minute bars covered {five_covered}/{len(sessions)} sessions; {source_name} historical 1-minute bars covered {one_covered}/{len(sessions)} sessions. Session-level and symbol-level gaps remain explicit in the observation output.",
        "- The default benchmark universe is selected by end-of-day winner rank and is therefore not a tradable prospective universe. Variable-size discovery cohorts can omit `gain_pct` so this replay measures only the supplied names without importing an end-of-day label.",
        "- Candidate premarket price, dollar volume, and a same-symbol prior-session 5-minute TOD-RVOL proxy were derived causally at 09:15 ET. Float, catalyst, and dilution evidence were not added; the V2 profile does not require them.",
        "- `deterministic-v2` and `gap-pullback-v2-prospective-20260825` were run with the same canonical `managed_finviz_v2_config` because no distinct persisted child configuration was available to this standalone replay; their fills are consequently identical and are shown separately only for arm attribution.",
        "- The canonical V2 account used the repository backtest engine, default parent risk profile, 40 bps assumed spread, paper-execution-v2 fills with 100% volume participation, and no commission. The overlay returns are evaluator price returns; their evaluators do not expose an execution-cost model.",
        "",
        "This is research/backtest evidence only and must not be interpreted as live or paper execution.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay deterministic interday shadow arms against the daily-winner benchmark.")
    parser.add_argument("--input", default="docs/trading/HISTORICAL_TOP5_WINNERS_2026-08-13_TO_2026-09-11.csv")
    parser.add_argument("--output-dir", default="docs/trading/interday-shadow-replay-2026-08-13_to_2026-09-11")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--source", choices=("yahoo", "alpaca-sip"), default="yahoo")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR))
    parser.add_argument(
        "--cohort-size",
        type=int,
        default=5,
        help=(
            "Expected symbols per session in the input (default: 5). "
            "Use 0 for a variable-size same-day discovery cohort."
        ),
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Reject cache misses instead of requesting market data.",
    )
    return parser.parse_args()


def main() -> int:
    global ACTIVE_SOURCE, CACHE_DIR, CACHE_ONLY
    args = parse_args()
    ACTIVE_SOURCE = args.source
    CACHE_DIR = Path(args.cache_dir)
    CACHE_ONLY = args.cache_only
    reset_cache_stats()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    if args.cohort_size < 0:
        raise ValueError("--cohort-size must be non-negative")
    rows, sessions, grouped = _parse_source(
        input_path,
        expected_symbols_per_session=args.cohort_size or None,
    )
    expected_symbols_per_session = args.cohort_size or None
    outcome_label_count = sum(row["gain_pct"] is not None for row in rows)
    symbols = sorted({str(row["symbol"]) for row in rows})
    source_by_symbol: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    for row in rows:
        source_by_symbol[str(row["symbol"])][row["session_date"]] = row

    loaded: dict[str, SymbolReplayData] = {}
    source_name = "Alpaca SIP" if ACTIVE_SOURCE == "alpaca-sip" else "Yahoo"
    print(f"Fetching {source_name} 5m history and 1m history for {len(symbols)} symbols...", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(_load_symbol, symbol, source_by_symbol[symbol], sessions): symbol
            for symbol in symbols
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                loaded[symbol] = future.result()
            except Exception as exc:
                if CACHE_ONLY:
                    raise RuntimeError(
                        f"cache-only replay could not load {symbol}: {exc}"
                    ) from exc
                print(f"[{index}/{len(symbols)}] {symbol}: FAILED: {type(exc).__name__}: {exc}", flush=True)
                loaded[symbol] = SymbolReplayData(symbol=symbol, candidates={}, bars_5m={}, bars_1m={}, five_minute_error=f"{type(exc).__name__}: {exc}", one_minute_errors={})
            else:
                data = loaded[symbol]
                one_days = sum(bool(value) for value in data.bars_1m.values())
                five_days = sum(bool(value) for value in data.bars_5m.values())
                print(f"[{index}/{len(symbols)}] {symbol}: 5m={five_days}/{len(sessions)} sessions, 1m={one_days}/{len(sessions)} sessions", flush=True)

    observations = _evaluate_overlay_arms(sessions, grouped, loaded)
    gap_results, gap_risk_pnl, gap_status = _gap_observations(
        sessions, grouped, loaded, observations, input_path
    )
    _apply_normalized_allocations(observations, sessions)
    arm_summary, daily = _summary_rows(observations, sessions)

    output_dir.mkdir(parents=True, exist_ok=True)
    observation_fields = [
        "arm", "session_date", "symbol", "rank", "benchmark_gain_pct", "status", "reason",
        "entry_time", "exit_time", "entry_price", "exit_price", "return_pct", "trade_count",
        "win_count", "loss_count", "risk_managed_pnl", "normalized_allocation", "normalized_pnl", "data_source",
    ]
    _write_csv(output_dir / "observations.csv", sorted(observations, key=lambda row: (str(row["arm"]), row["session_date"], int(row["rank"]), str(row["symbol"]))), observation_fields)
    daily_fields = ["session_date"]
    for arm in ARMS:
        daily_fields.extend([f"{arm}_pnl", f"{arm}_return_pct", f"{arm}_trades"])
    _write_csv(output_dir / "daily-summary.csv", daily, daily_fields)
    _write_csv(output_dir / "arm-summary.csv", arm_summary, ["arm", "evaluable_sessions", "benchmark_observations", "completed_trades", "wins", "losses", "normalized_pnl", "normalized_return_pct"])
    (output_dir / "run-config.json").write_text(json.dumps({
        "strategy_id": "interday-trading-strategy-shadow",
        "leader_momentum_policy_version": LEADER_MOMENTUM_POLICY_VERSION,
        "arms": list(ARMS),
        "excluded_llm_arms": ["ai-every-minute", "ai-event-driven"],
        "input": input_path.as_posix(),
        "benchmark_observations": len(rows),
        "expected_symbols_per_session": expected_symbols_per_session,
        "outcome_label_count": outcome_label_count,
        "outcome_labels_present": outcome_label_count == len(rows),
        "sessions": [session_date.isoformat() for session_date in sessions],
        "initial_cash": str(FIXED_DAILY_CAPITAL),
        "normalized_slot_notional": str(FIXED_SLOT_NOTIONAL),
        "assumed_spread_bps": str(ASSUMED_SPREAD_BPS),
        "market_data_source": ACTIVE_SOURCE,
        "market_data_cache_dir": CACHE_DIR.as_posix(),
        "market_data_cache_schema": CACHE_SCHEMA_VERSION,
        "market_data_cache_only": CACHE_ONLY,
        "market_data_cache_stats": cache_stats(),
        "one_minute_sessions": sorted({session_date.isoformat() for data in loaded.values() for session_date, bars in data.bars_1m.items() if bars}),
        "five_minute_sessions": sorted({session_date.isoformat() for data in loaded.values() for session_date, bars in data.bars_5m.items() if bars}),
        "gap_backtested_sessions": sorted(session_date.isoformat() for session_date in gap_results),
        "gap_risk_managed_ending_cash": str(gap_risk_pnl.get(date.min, FIXED_DAILY_CAPITAL)),
        "gap_status": {session_date.isoformat(): value for session_date, value in gap_status.items()},
    }, indent=2) + "\n", encoding="utf-8")
    _write_summary(
        output_dir / "summary.md",
        input_path=input_path,
        sessions=sessions,
        loaded=loaded,
        arm_summary=arm_summary,
        daily=daily,
        gap_risk_pnl=gap_risk_pnl,
        gap_status=gap_status,
        replay_cache_stats=cache_stats(),
        benchmark_observations=len(rows),
        expected_symbols_per_session=expected_symbols_per_session,
        outcome_labels_present=outcome_label_count == len(rows),
    )
    print((output_dir / "summary.md").read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
