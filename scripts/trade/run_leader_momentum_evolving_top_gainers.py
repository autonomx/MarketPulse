from __future__ import annotations

"""Run the causal evolving-Top-Gainers Leader Momentum v1.2 replay.

The command has two deliberately separate phases:

* ``--populate`` fetches a predeclared Alpaca SIP population and the 1m data
  needed after discovery, then writes a compact persistent cache.
* ``--replay --cache-only`` consumes only that cache and writes the experiment
  artifacts.  It cannot make a provider request.

This is research/shadow code.  It never imports an execution client or submits
orders.  Eventual Top-5 labels are read only after the ranking stream has been
constructed and fingerprinted.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time as time_module
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SOURCE_ROOT, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.providers.alpaca_iex import ALPACA_DATA_URL, alpaca_iex_auth_headers
from app.apps.trading.strategy_evolving_top_gainers import (
    EvolvingTopGainersConfig,
    evaluate_leader_momentum_after_discovery,
    replay_evolving_top_gainers_from_bars,
)
from app.apps.trading.strategy_leader_momentum_diagnostics import diagnose_leader_momentum_continuation
from app.apps.trading.strategy_leader_momentum_continuation import (
    LeaderMomentumContext,
    POLICY_VERSION,
    evaluate_leader_momentum_continuation,
)


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
WINNER_CSV = REPOSITORY_ROOT / "docs" / "trading" / "HISTORICAL_TOP5_WINNERS_2026-06-11_TO_2026-09-11.csv"
CACHE_ROOT = REPOSITORY_ROOT / "resources" / "cache" / "leader-momentum-evolving-top-gainers"
ARTIFACT_ROOT = REPOSITORY_ROOT / "artifacts" / "trading" / "leader-momentum-v1.2-evolving-top-gainers"
STRATEGY_PATH = REPOSITORY_ROOT / "src" / "app" / "apps" / "trading" / "strategy_leader_momentum_continuation.py"
EXPECTED_HEAD = "03a92bcaceb5a5a89df6b839b165a65e9d6d6e70"
CACHE_SCHEMA = "leader-momentum-evolving-sip-cache-v1"
POPULATION_SCHEMA = "leader-momentum-evolving-population-v1"
ASSET_SYMBOL = re.compile(r"^[A-Z0-9.\-]+$")
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA"}
FRICTION_BPS = (Decimal("0"), Decimal("40"), Decimal("80"), Decimal("120"), Decimal("200"))
FIXED_SLOT_NOTIONAL = Decimal("20000")
CAPACITY_SLOTS = 5
ASSUMED_SPREAD_BPS = Decimal("40")
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_REQUEST_MIN_INTERVAL_SECONDS = 0.50


@dataclass(frozen=True, slots=True)
class RawBar:
    symbol: str
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    interval_minutes: int

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.interval_minutes)


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _dec(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing Alpaca bar timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Alpaca bar timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _et_bounds(session_date: date, start: time, end: time) -> tuple[datetime, datetime]:
    return (
        datetime.combine(session_date, start, tzinfo=ET).astimezone(UTC),
        datetime.combine(session_date, end, tzinfo=ET).astimezone(UTC),
    )


def _instrument(symbol: str) -> str:
    return f"equity:US:{symbol.upper()}"


def _symbol(instrument_id: str) -> str:
    return instrument_id.rsplit(":", 1)[-1].upper()


def _sessions_from_winners() -> list[date]:
    with WINNER_CSV.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    grouped = sorted({date.fromisoformat(str(row["session_date"])) for row in rows})
    if len(rows) != len(grouped) * 5:
        raise ValueError(f"winner CSV must contain five rows per session; got {len(rows)}")
    return grouped


def _winner_labels() -> dict[tuple[date, str], dict[str, str]]:
    labels: dict[tuple[date, str], dict[str, str]] = {}
    with WINNER_CSV.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            session_date = date.fromisoformat(str(row["session_date"]))
            symbol = str(row["symbol"]).strip().upper()
            labels[(session_date, symbol)] = dict(row)
    return labels


class CompactSipCache:
    """Session-oriented cache with symbol/timeframe/session in every record."""

    def __init__(self, root: Path = CACHE_ROOT) -> None:
        self.root = root
        self.sip_root = root / "alpaca-sip"

    def _manifest_path(self, timeframe: str) -> Path:
        return self.sip_root / timeframe / "manifest.json"

    def _session_path(self, timeframe: str, session_date: date) -> Path:
        return self.sip_root / timeframe / f"{session_date.isoformat()}.json"

    def _daily_batch_path(self, index: int) -> Path:
        return self.sip_root / "1d" / f"batch-{index:04d}.json"

    def load_population(self) -> dict[str, Any]:
        raw = _read_json(self.root / "population.json")
        if not isinstance(raw, dict) or raw.get("schema") != POPULATION_SCHEMA:
            raise RuntimeError("population catalog is missing or has the wrong schema")
        return raw

    def load_manifest(self, timeframe: str) -> dict[str, Any] | None:
        path = self._manifest_path(timeframe)
        if not path.exists():
            return None
        raw = _read_json(path)
        if not isinstance(raw, dict) or raw.get("schema") != CACHE_SCHEMA:
            raise RuntimeError(f"{timeframe} cache manifest has the wrong schema")
        return raw

    def session_exists(self, timeframe: str, session_date: date) -> bool:
        path = self._session_path(timeframe, session_date)
        if not path.exists():
            return False
        raw = _read_json(path)
        return isinstance(raw, dict) and raw.get("schema") == CACHE_SCHEMA and raw.get("session_date") == session_date.isoformat()

    def load_session(self, timeframe: str, session_date: date) -> list[RawBar]:
        path = self._session_path(timeframe, session_date)
        raw = _read_json(path)
        if not isinstance(raw, dict) or raw.get("schema") != CACHE_SCHEMA:
            raise RuntimeError(f"invalid {timeframe} session cache {session_date}")
        rows = raw.get("bars")
        if not isinstance(rows, list):
            raise RuntimeError(f"invalid {timeframe} session bar list {session_date}")
        return [_raw_from_json(item, timeframe=timeframe) for item in rows]

    def store_session(self, timeframe: str, session_date: date, rows: Iterable[RawBar], *, request: dict[str, str]) -> None:
        ordered = sorted(rows, key=lambda bar: (bar.symbol, bar.start))
        _write_json(
            self._session_path(timeframe, session_date),
            {
                "schema": CACHE_SCHEMA,
                "source": "alpaca-sip",
                "timeframe": timeframe,
                "session_date": session_date.isoformat(),
                "request": request,
                "bar_count": len(ordered),
                "bars": [_raw_to_json(bar) for bar in ordered],
            },
        )

    def write_manifest(self, timeframe: str, payload: dict[str, Any]) -> None:
        _write_json(self._manifest_path(timeframe), {"schema": CACHE_SCHEMA, **payload})

    def daily_batches(self) -> list[Path]:
        directory = self.sip_root / "1d"
        return sorted(directory.glob("batch-*.json")) if directory.exists() else []

    def store_daily_batch(self, index: int, rows: Iterable[RawBar], *, request: dict[str, str]) -> None:
        ordered = sorted(rows, key=lambda bar: (bar.symbol, bar.start))
        _write_json(
            self._daily_batch_path(index),
            {
                "schema": CACHE_SCHEMA,
                "source": "alpaca-sip",
                "timeframe": "1d",
                "batch": index,
                "request": request,
                "bar_count": len(ordered),
                "bars": [_raw_to_json(bar) for bar in ordered],
            },
        )


def _raw_to_json(bar: RawBar) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "start": bar.start.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "interval_minutes": bar.interval_minutes,
    }


def _raw_from_json(raw: object, *, timeframe: str) -> RawBar:
    if not isinstance(raw, dict):
        raise ValueError("cached bar is not an object")
    expected = {"1m": 1, "5m": 5, "1d": 1440}[timeframe]
    bar = RawBar(
        symbol=str(raw["symbol"]).upper(),
        start=_timestamp(raw["start"]),
        open=_dec(raw["open"]),
        high=_dec(raw["high"]),
        low=_dec(raw["low"]),
        close=_dec(raw["close"]),
        volume=_dec(raw.get("volume")),
        interval_minutes=int(raw["interval_minutes"]),
    )
    if bar.interval_minutes != expected or min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise ValueError(f"cached {timeframe} bar interval/value mismatch")
    return bar


def _fetch_assets() -> list[dict[str, Any]]:
    trading_url = (os.environ.get("OMNIX_ALPACA_TRADING_URL") or "https://paper-api.alpaca.markets").rstrip("/")
    with requests.Session() as session:
        response = session.get(
            f"{trading_url}/v2/assets",
            params={"status": "active", "asset_class": "us_equity"},
            headers=alpaca_iex_auth_headers(),
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Alpaca active-assets response is not a list")
    assets: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        exchange = str(raw.get("exchange") or "").strip().upper()
        if not symbol or not ASSET_SYMBOL.fullmatch(symbol) or exchange not in ALLOWED_EXCHANGES:
            continue
        if raw.get("status") not in {None, "active"} or raw.get("tradable") is False:
            continue
        assets.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "asset_class": str(raw.get("class") or "us_equity"),
                "name": str(raw.get("name") or ""),
                "asset_id": str(raw.get("id") or ""),
                "tradable": bool(raw.get("tradable", True)),
            }
        )
    assets.sort(key=lambda item: (item["symbol"], item["exchange"], item["asset_id"]))
    if not assets:
        raise RuntimeError("Alpaca returned no valid active listed US equities")
    return assets


def _asset_fingerprint(assets: list[dict[str, Any]]) -> str:
    payload = json.dumps(assets, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _population_payload(assets: list[dict[str, Any]], sessions: list[date]) -> dict[str, Any]:
    return {
        "schema": POPULATION_SCHEMA,
        "source": "alpaca_active_us_equity_catalog_predeclared_before_labels",
        "selection_rule": "all active tradable Alpaca US equities on NASDAQ/NYSE/AMEX/ARCA with a valid symbol; no winner-label filtering",
        "asset_count": len(assets),
        "asset_fingerprint": _asset_fingerprint(assets),
        "session_dates": [item.isoformat() for item in sessions],
        "assets": assets,
    }


def _fetch_chunk(
    symbols: list[str],
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> dict[str, list[RawBar]]:
    api_timeframe = {"1m": "1Min", "5m": "5Min", "1d": "1Day"}[timeframe]
    interval = {"1m": 1, "5m": 5, "1d": 1440}[timeframe]
    params: dict[str, object] = {
        "symbols": ",".join(symbols),
        "timeframe": api_timeframe,
        "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "adjustment": "raw",
        "feed": "sip",
        "sort": "asc",
        "limit": 10000,
    }
    output: dict[str, list[RawBar]] = defaultdict(list)
    with requests.Session() as session:
        headers = alpaca_iex_auth_headers()
        for page in range(2000):
            response = None
            for attempt in range(10):
                global _LAST_REQUEST_AT
                with _REQUEST_LOCK:
                    now = time_module.monotonic()
                    wait = _REQUEST_MIN_INTERVAL_SECONDS - (now - _LAST_REQUEST_AT)
                    if wait > 0:
                        time_module.sleep(wait)
                    _LAST_REQUEST_AT = time_module.monotonic()
                response = session.get(f"{ALPACA_DATA_URL}/v2/stocks/bars", params=params, headers=headers, timeout=60)
                if response.status_code != 429:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = _dec(retry_after, Decimal(str(min(30, 2 ** attempt))))
                time_module.sleep(float(min(max(delay, Decimal("2")), Decimal("30"))))
            if response is None:
                raise RuntimeError("Alpaca returned no response")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
                raise RuntimeError("Alpaca historical bars response is malformed")
            for raw_symbol, raw_rows in payload["bars"].items():
                symbol = str(raw_symbol).upper()
                if not isinstance(raw_rows, list):
                    continue
                for raw in raw_rows:
                    if not isinstance(raw, dict):
                        continue
                    values = [raw.get(key) for key in ("o", "h", "l", "c")]
                    if any(value is None for value in values):
                        continue
                    bar = RawBar(
                        symbol=symbol,
                        start=_timestamp(raw.get("t")),
                        open=_dec(values[0]),
                        high=_dec(values[1]),
                        low=_dec(values[2]),
                        close=_dec(values[3]),
                        volume=_dec(raw.get("v")),
                        interval_minutes=interval,
                    )
                    if min(bar.open, bar.high, bar.low, bar.close) > 0:
                        output[symbol].append(bar)
            token = payload.get("next_page_token")
            if not isinstance(token, str) or not token:
                return {symbol: sorted(rows, key=lambda item: item.start) for symbol, rows in output.items()}
            params["page_token"] = token
        raise RuntimeError(f"Alpaca {timeframe} pagination exceeded safety limit")


def _chunks(values: list[str], size: int = 1000) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _fetch_range_chunks(
    symbols: list[str],
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    workers: int,
) -> list[RawBar]:
    output: list[RawBar] = []
    chunks = _chunks(symbols)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sip") as executor:
        futures = [executor.submit(_fetch_chunk, chunk, timeframe=timeframe, start=start, end=end) for chunk in chunks]
        for future in as_completed(futures):
            by_symbol = future.result()
            for rows in by_symbol.values():
                output.extend(rows)
    return output


def _session_request(session_date: date, timeframe: str) -> dict[str, str]:
    start, end = _et_bounds(session_date, time(9, 30), time(16, 0))
    return {
        "feed": "sip",
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "adjustment": "raw",
    }


def _populate_daily(cache: CompactSipCache, symbols: list[str], sessions: list[date], workers: int) -> dict[str, int]:
    existing = cache.daily_batches()
    if existing:
        rows = sum(int((_read_json(path) or {}).get("bar_count", 0)) for path in existing)
        return {"network_fetches": 0, "bar_count": rows, "batch_count": len(existing), "reused": 1}
    first = min(sessions) - timedelta(days=45)
    last = max(sessions) + timedelta(days=1)
    start = datetime.combine(first, time(0), tzinfo=ET).astimezone(UTC)
    end = datetime.combine(last, time(0), tzinfo=ET).astimezone(UTC)
    rows = _fetch_range_chunks(symbols, timeframe="1d", start=start, end=end, workers=workers)
    chunks = _chunks(symbols)
    # Repartition the response by the request chunks using symbol ownership.
    by_symbol = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)
    for index, chunk in enumerate(chunks):
        chunk_rows = [row for symbol in chunk for row in by_symbol.get(symbol, ())]
        cache.store_daily_batch(index, chunk_rows, request={"feed": "sip", "timeframe": "1d", "start": start.isoformat(), "end": end.isoformat(), "adjustment": "raw"})
    cache.write_manifest("1d", {"request": {"feed": "sip", "timeframe": "1d", "start": start.isoformat(), "end": end.isoformat()}, "symbol_count": len(symbols), "batch_count": len(chunks), "bar_count": len(rows)})
    return {"network_fetches": len(chunks), "bar_count": len(rows), "batch_count": len(chunks), "reused": 0}


def _populate_5m(cache: CompactSipCache, symbols: list[str], sessions: list[date], workers: int) -> dict[str, int]:
    missing = [item for item in sessions if not cache.session_exists("5m", item)]
    network_fetches = 0
    total_bars = 0
    for session_date in missing:
        start, end = _et_bounds(session_date, time(9, 30), time(16, 0))
        rows = _fetch_range_chunks(symbols, timeframe="5m", start=start, end=end, workers=workers)
        cache.store_session("5m", session_date, rows, request=_session_request(session_date, "5m"))
        network_fetches += len(_chunks(symbols))
        total_bars += len(rows)
    all_counts = []
    for session_date in sessions:
        if cache.session_exists("5m", session_date):
            all_counts.append(len(cache.load_session("5m", session_date)))
    cache.write_manifest("5m", {"request": {"feed": "sip", "timeframe": "5m", "session_dates": [item.isoformat() for item in sessions]}, "symbol_count": len(symbols), "session_dates": [item.isoformat() for item in sessions], "bar_count": sum(all_counts)})
    return {"network_fetches": network_fetches, "bar_count": sum(all_counts), "sessions_fetched": len(missing), "sessions_cached": len(sessions) - len(missing)}


def _market_bar(raw: RawBar, instrument_id: str) -> MarketBar:
    return MarketBar(
        instrument_id=instrument_id,
        interval="1m" if raw.interval_minutes == 1 else "5m" if raw.interval_minutes == 5 else "1d",
        start_time=raw.start,
        end_time=raw.end,
        open=raw.open,
        high=raw.high,
        low=raw.low,
        close=raw.close,
        volume=raw.volume,
        is_final=True,
        adjustment_mode=AdjustmentMode.RAW,
        session="regular",
        provider="alpaca_sip",
        provider_event_id=f"{raw.symbol}:{int(raw.start.timestamp())}",
        received_at=raw.start,
    )


def _daily_previous_closes(cache: CompactSipCache, sessions: list[date]) -> dict[tuple[date, str], Decimal]:
    by_symbol: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for path in cache.daily_batches():
        raw = _read_json(path)
        if not isinstance(raw, dict):
            raise RuntimeError(f"daily cache batch is malformed: {path}")
        for item in raw.get("bars", []):
            bar = _raw_from_json(item, timeframe="1d")
            by_symbol[bar.symbol].append((bar.start.astimezone(ET).date(), bar.close))
    output: dict[tuple[date, str], Decimal] = {}
    for symbol, values in by_symbol.items():
        values.sort()
        for session_date in sessions:
            prior = [close for observed, close in values if observed < session_date]
            if prior:
                output[(session_date, symbol)] = prior[-1]
    return output


def _replay_session(session_date: date, rows: list[RawBar], previous: dict[tuple[date, str], Decimal], config: EvolvingTopGainersConfig):
    grouped: dict[str, list[MarketBar]] = defaultdict(list)
    for raw in rows:
        if raw.start.astimezone(ET).date() == session_date:
            grouped[raw.symbol].append(_market_bar(raw, _instrument(raw.symbol)))
    bars_by_instrument = {_instrument(symbol): tuple(sorted(values, key=lambda bar: bar.start_time)) for symbol, values in grouped.items()}
    previous_by_instrument = {
        _instrument(symbol): value
        for symbol in grouped
        if (value := previous.get((session_date, symbol))) is not None
    }
    return replay_evolving_top_gainers_from_bars(
        session_date=session_date,
        bars_by_instrument=bars_by_instrument,
        previous_close_by_instrument=previous_by_instrument,
        config=config,
    )


def _build_discovery_index(cache: CompactSipCache, sessions: list[date], previous: dict[tuple[date, str], Decimal], config: EvolvingTopGainersConfig) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": "leader-momentum-evolving-discovery-index-v1", "config": config.model_dump(mode="json"), "sessions": {}, "union": set()}
    union: set[str] = set()
    for session_date in sessions:
        replay = _replay_session(session_date, cache.load_session("5m", session_date), previous, config)
        memberships = [item.model_dump(mode="json") for item in replay.membership]
        result["sessions"][session_date.isoformat()] = {"fingerprint": replay.fingerprint, "observation_count": replay.observation_count, "membership": memberships}
        union.update(item.instrument_id for item in replay.membership)
    result["union"] = sorted(union)
    return result


def _populate_1m(cache: CompactSipCache, symbols: list[str], sessions: list[date], workers: int) -> dict[str, int]:
    missing = [item for item in sessions if not cache.session_exists("1m", item)]
    network_fetches = 0
    for session_date in missing:
        start, end = _et_bounds(session_date, time(9, 30), time(16, 0))
        rows = _fetch_range_chunks(symbols, timeframe="1m", start=start, end=end, workers=workers)
        cache.store_session("1m", session_date, rows, request=_session_request(session_date, "1m"))
        network_fetches += len(_chunks(symbols))
    counts = [len(cache.load_session("1m", item)) for item in sessions if cache.session_exists("1m", item)]
    cache.write_manifest("1m", {"request": {"feed": "sip", "timeframe": "1m", "session_dates": [item.isoformat() for item in sessions]}, "symbol_count": len(symbols), "session_dates": [item.isoformat() for item in sessions], "bar_count": sum(counts), "selection": "evolving-top20 entrants plus post-fingerprint winner diagnostics"})
    return {"network_fetches": network_fetches, "bar_count": sum(counts), "sessions_fetched": len(missing), "sessions_cached": len(sessions) - len(missing), "symbol_count": len(symbols)}


def _populate(cache: CompactSipCache, sessions: list[date], workers: int) -> None:
    assets = _fetch_assets()
    symbols = [str(item["symbol"]) for item in assets]
    _write_json(cache.root / "population.json", _population_payload(assets, sessions))
    print(json.dumps({"phase": "population", "asset_count": len(symbols), "asset_fingerprint": _asset_fingerprint(assets)}))
    daily_stats = _populate_daily(cache, symbols, sessions, workers)
    five_stats = _populate_5m(cache, symbols, sessions, workers)
    previous = _daily_previous_closes(cache, sessions)
    config = EvolvingTopGainersConfig(top_n=20, cadence_minutes=5)
    discovery_index = _build_discovery_index(cache, sessions, previous, config)
    _write_json(cache.root / "discovery-index.json", {**discovery_index, "population_fingerprint": _asset_fingerprint(assets)})
    entrant_symbols = {_symbol(item) for item in discovery_index["union"]}
    labels = _winner_labels()
    winner_symbols = {symbol for (_session_date, symbol) in labels}
    diagnostic_symbols = entrant_symbols | winner_symbols
    one_stats = _populate_1m(cache, sorted(diagnostic_symbols), sessions, workers)
    _write_json(cache.root / "population-fetch-report.json", {"population_network_fetches": daily_stats["network_fetches"] + five_stats["network_fetches"], "diagnostic_1m_network_fetches": one_stats["network_fetches"], "daily": daily_stats, "five_minute": five_stats, "one_minute": one_stats, "entrant_symbol_count": len(entrant_symbols), "winner_diagnostic_symbol_count": len(winner_symbols), "final_replay_network_fetches": 0})
    print(json.dumps({"phase": "population_complete", "entrant_symbols": len(entrant_symbols), "one_minute_symbols": len(diagnostic_symbols), "network_fetches": daily_stats["network_fetches"] + five_stats["network_fetches"] + one_stats["network_fetches"]}))


def _member_at(replay: Any, instrument_id: str, observed_at: datetime) -> Any | None:
    for snapshot in replay.snapshots:
        if snapshot.observed_at != observed_at:
            continue
        for member in snapshot.members:
            if member.instrument_id == instrument_id:
                return member
    return None


def _context_at(bars: list[MarketBar], discovered_at: datetime) -> LeaderMomentumContext:
    available = [bar for bar in bars if bar.end_time <= discovered_at]
    dollar_volume = sum((bar.close * bar.volume for bar in available), Decimal("0"))
    latest = available[-1] if available else None
    prior = available[-6:-1] if len(available) > 1 else []
    baseline = sum((bar.volume for bar in prior), Decimal("0")) / Decimal(len(prior)) if prior else Decimal("0")
    acceleration = latest.volume / baseline if latest is not None and baseline > 0 else None
    running_high = Decimal("0")
    hod_frequency = 0
    for bar in available[-3:]:
        if bar.high > running_high:
            hod_frequency += 1
            running_high = bar.high
    return LeaderMomentumContext(
        tod_rvol=None,
        relative_strength_pct=None,
        spread_bps=ASSUMED_SPREAD_BPS,
        dollar_volume=dollar_volume,
        volume_acceleration=acceleration,
        hod_frequency_15m=hod_frequency,
    )


def _first_signal(snapshot: Any) -> datetime | None:
    if snapshot.trades:
        return min(trade.signal_time for trade in snapshot.trades)
    return snapshot.signal_time


def _future_high(rows: list[RawBar], start: datetime) -> Decimal | None:
    highs = [bar.high for bar in rows if bar.start >= start]
    return max(highs) if highs else None


def _return_pct(entry: Decimal | None, high: Decimal | None) -> Decimal | None:
    if entry is None or high is None or entry <= 0:
        return None
    return (high / entry - Decimal("1")) * Decimal("100")


def _pct(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.6f}"


def _bool(value: bool | None) -> str:
    return "" if value is None else "true" if value else "false"


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _trade_pnl(trade: dict[str, object], friction_bps: Decimal = Decimal("0")) -> Decimal:
    return FIXED_SLOT_NOTIONAL * (_dec(trade.get("return_pct")) - friction_bps / Decimal("100")) / Decimal("100")


def _simulate_capacity(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for session_date in sorted({str(row["session_date"]) for row in trades}):
        day = sorted((row for row in trades if str(row["session_date"]) == session_date), key=lambda row: (str(row["entry_time"]), str(row["symbol"])))
        accepted: list[dict[str, object]] = []
        for row in day:
            entry = datetime.fromisoformat(str(row["entry_time"]))
            active = [item for item in accepted if datetime.fromisoformat(str(item["exit_time"])) > entry]
            if len(active) < CAPACITY_SLOTS:
                row["capacity_accepted"] = True
                accepted.append(row)
            else:
                row["capacity_accepted"] = False
            row["capacity_active_before_entry"] = len(active)
            output.append(row)
    return output


def _strategy_constants() -> dict[str, str]:
    import app.apps.trading.strategy_leader_momentum_continuation as module

    values: dict[str, str] = {}
    for name, value in sorted(vars(module).items()):
        if name.startswith("_") or not name.isupper():
            continue
        if name in {"Literal"}:
            continue
        values[name] = _json_default(value).__str__()
    return values


def _replay(cache: CompactSipCache, sessions: list[date], artifact_root: Path) -> dict[str, Any]:
    if not cache.load_manifest("5m") or not cache.load_manifest("1m"):
        raise RuntimeError("cache-only replay requires completed 5m and 1m manifests")
    population = cache.load_population()
    if population.get("source") != "alpaca_active_us_equity_catalog_predeclared_before_labels":
        raise RuntimeError("population source is not the predeclared Alpaca catalog")
    before_sha = _sha256(STRATEGY_PATH)
    before_git = _git_sha()
    if before_git != EXPECTED_HEAD:
        raise RuntimeError(f"unexpected starting git SHA: {before_git}")
    if POLICY_VERSION != "leader-momentum-continuation-v1.2":
        raise RuntimeError("Leader Momentum policy version changed")
    config = EvolvingTopGainersConfig(top_n=20, cadence_minutes=5)
    previous = _daily_previous_closes(cache, sessions)
    # This is the complete causal ranking stream.  No winner labels are read
    # until after these replay fingerprints have been produced.
    replays = {session_date: _replay_session(session_date, cache.load_session("5m", session_date), previous, config) for session_date in sessions}
    ranking_fingerprints = {session_date.isoformat(): replay.fingerprint for session_date, replay in replays.items()}
    labels = _winner_labels()
    one_minute_manifest = cache.load_manifest("1m") or {}
    observations: list[dict[str, object]] = []
    continuation: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    snapshots_rows: list[dict[str, object]] = []
    membership_rows: list[dict[str, object]] = []
    transition_rows: list[dict[str, object]] = []
    strategy_errors: list[str] = []
    coverage_rows: list[dict[str, object]] = []
    for session_date in sessions:
        replay = replays[session_date]
        five_rows = cache.load_session("5m", session_date)
        five_by_symbol: dict[str, list[RawBar]] = defaultdict(list)
        for raw in five_rows:
            five_by_symbol[raw.symbol].append(raw)
        one_rows = cache.load_session("1m", session_date)
        one_by_symbol: dict[str, list[RawBar]] = defaultdict(list)
        for raw in one_rows:
            one_by_symbol[raw.symbol].append(raw)
        coverage_rows.append({"session_date": session_date.isoformat(), "five_minute_bars": len(five_rows), "one_minute_bars": len(one_rows), "five_symbols": len(five_by_symbol), "one_symbols": len(one_by_symbol)})
        for snapshot in replay.snapshots:
            for member in snapshot.members:
                snapshots_rows.append({"session_date": session_date.isoformat(), "observed_at": snapshot.observed_at.isoformat(), "rank": member.rank, "instrument_id": member.instrument_id, "symbol": _symbol(member.instrument_id), "gain_pct": _pct(member.gain_pct), "price": _pct(member.price), "previous_close": _pct(member.previous_close), "cumulative_dollar_volume": _pct(member.cumulative_dollar_volume), "evidence_at": member.evidence_at.isoformat()})
        for transition in replay.transitions:
            transition_rows.append({"session_date": session_date.isoformat(), "observed_at": transition.observed_at.isoformat(), "kind": transition.kind, "instrument_id": transition.instrument_id, "symbol": _symbol(transition.instrument_id), "rank": transition.rank or "", "gain_pct": _pct(transition.gain_pct)})
        for member in replay.membership:
            symbol = _symbol(member.instrument_id)
            first_member = _member_at(replay, member.instrument_id, member.first_top_n_at)
            discovery_price = first_member.price if first_member is not None else None
            five_market = sorted([_market_bar(raw, member.instrument_id) for raw in five_by_symbol.get(symbol, [])], key=lambda bar: bar.start_time)
            one_raw = sorted(one_by_symbol.get(symbol, []), key=lambda raw: raw.start)
            one_market = [_market_bar(raw, member.instrument_id) for raw in one_raw]
            context = _context_at(five_market, member.first_top_n_at)
            snapshot = None
            trace = None
            error = ""
            if one_market:
                try:
                    snapshot = evaluate_leader_momentum_after_discovery(one_market, discovered_at=member.first_top_n_at, context=context)
                    trace = diagnose_leader_momentum_continuation(one_market, context=context, entry_start_et=max(time(9, 35), member.first_top_n_at.astimezone(ET).time().replace(tzinfo=None)), last_entry_et=time(15, 30), force_flat_et=time(15, 55))
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    strategy_errors.append(f"{session_date}:{symbol}:{error}")
            else:
                error = "one_minute_cache_missing_for_top20_entrant"
                strategy_errors.append(f"{session_date}:{symbol}:{error}")
            winner = labels.get((session_date, symbol))
            previous_signal = None
            previous_trade = None
            if winner and one_market:
                old_snapshot = evaluate_leader_momentum_continuation(one_market, context=_context_at(five_market, datetime.combine(session_date, time(9, 35), tzinfo=ET).astimezone(UTC)), entry_start_et=time(9, 35), last_entry_et=time(15, 30), force_flat_et=time(15, 55))
                previous_signal = _first_signal(old_snapshot)
                previous_trade = old_snapshot.trades[0] if old_snapshot.trades else None
            result_trades = list(snapshot.trades) if snapshot is not None else []
            future_rows = one_raw or five_by_symbol.get(symbol, [])
            high_after_discovery = _future_high(future_rows, member.first_top_n_at)
            discovery_continuation = _return_pct(discovery_price, high_after_discovery)
            first_trade = result_trades[0] if result_trades else None
            signal_time = (trace.first_execution_valid_at if trace is not None and trace.first_execution_valid_at is not None else _first_signal(snapshot)) if snapshot is not None else None
            signal_price = None
            if signal_time is not None:
                signal_bars = [raw.close for raw in future_rows if raw.end <= signal_time]
                signal_price = signal_bars[-1] if signal_bars else None
            high_after_signal = _future_high(future_rows, signal_time) if signal_time is not None else None
            signal_continuation = _return_pct(signal_price, high_after_signal)
            entry_continuation = _return_pct(first_trade.entry_price, _future_high(future_rows, first_trade.entry_time)) if first_trade is not None else None
            for trade_index, trade in enumerate(result_trades, start=1):
                trades.append({"session_date": session_date.isoformat(), "symbol": symbol, "instrument_id": member.instrument_id, "trade_index": trade_index, "mode": trade.mode, "signal_time": trade.signal_time.isoformat(), "entry_time": trade.entry_time.isoformat(), "entry_price": _pct(trade.entry_price), "initial_stop_price": _pct(trade.initial_stop_price), "exit_time": trade.exit_time.isoformat(), "exit_price": _pct(trade.exit_price), "exit_reason_code": trade.exit_reason_code, "return_pct": _pct(trade.return_pct), "mfe_pct": _pct(trade.mfe_pct), "mae_pct": _pct(trade.mae_pct), "partial_exit_time": trade.partial_exit_time.isoformat() if trade.partial_exit_time else "", "partial_exit_price": _pct(trade.partial_exit_price)})
            obs = {"session_date": session_date.isoformat(), "symbol": symbol, "instrument_id": member.instrument_id, "first_top20_at": member.first_top_n_at.isoformat(), "first_top10_at": member.first_top_10_at.isoformat() if member.first_top_10_at else "", "first_top5_at": member.first_top_5_at.isoformat() if member.first_top_5_at else "", "best_rank": member.best_rank, "entry_count": member.entry_count, "last_seen_at": member.last_seen_at.isoformat(), "discovery_rank": first_member.rank if first_member else "", "discovery_gain_pct": _pct(first_member.gain_pct if first_member else None), "discovery_price": _pct(discovery_price), "winner_final_top5": bool(winner), "winner_rank": winner.get("rank", "") if winner else "", "winner_final_gain_pct": winner.get("gain_pct", "") if winner else "", "previous_winner_only_signal_at": previous_signal.isoformat() if previous_signal else "", "previous_winner_only_trade_at": previous_trade.entry_time.isoformat() if previous_trade else "", "leader_confirmed_at": trace.first_leader_confirmed_at.isoformat() if trace and trace.first_leader_confirmed_at else "", "first_setup_valid_at": trace.first_setup_valid_at.isoformat() if trace and trace.first_setup_valid_at else "", "first_execution_valid_at": trace.first_execution_valid_at.isoformat() if trace and trace.first_execution_valid_at else "", "first_signal_at": signal_time.isoformat() if signal_time else "", "trade_count": len(result_trades), "strategy_state": snapshot.state if snapshot else "data_unavailable", "strategy_reason": snapshot.reason_code if snapshot else error, "setup_mode": snapshot.setup_mode if snapshot and snapshot.setup_mode else "", "first_trade_return_pct": _pct(first_trade.return_pct if first_trade else None), "first_trade_mfe_pct": _pct(first_trade.mfe_pct if first_trade else None), "first_trade_mae_pct": _pct(first_trade.mae_pct if first_trade else None), "remaining_mfe_after_discovery_pct": _pct(discovery_continuation), "remaining_mfe_after_signal_pct": _pct(signal_continuation), "remaining_mfe_after_entry_pct": _pct(entry_continuation), "one_minute_data_status": "available" if one_market else "missing"}
            observations.append(obs)
            continuation.append({"session_date": session_date.isoformat(), "symbol": symbol, "winner_final_top5": bool(winner), "traded": bool(result_trades), "discovery_gain_pct": _pct(first_member.gain_pct if first_member else None), "future_max_gain_after_discovery_pct": _pct(discovery_continuation), "future_max_gain_after_signal_pct": _pct(signal_continuation), "future_max_gain_after_entry_pct": _pct(entry_continuation), "continuation_plus10_after_discovery": _bool(discovery_continuation is not None and discovery_continuation >= 10), "continuation_plus20_after_discovery": _bool(discovery_continuation is not None and discovery_continuation >= 20), "continuation_plus30_after_discovery": _bool(discovery_continuation is not None and discovery_continuation >= 30), "continuation_plus50_after_discovery": _bool(discovery_continuation is not None and discovery_continuation >= 50), "continuation_plus100_after_discovery": _bool(discovery_continuation is not None and discovery_continuation >= 100), "continuation_plus10_after_trade": _bool(entry_continuation is not None and entry_continuation >= 10) if result_trades else "", "continuation_plus20_after_trade": _bool(entry_continuation is not None and entry_continuation >= 20) if result_trades else "", "continuation_plus30_after_trade": _bool(entry_continuation is not None and entry_continuation >= 30) if result_trades else "", "continuation_plus50_after_trade": _bool(entry_continuation is not None and entry_continuation >= 50) if result_trades else "", "continuation_plus100_after_trade": _bool(entry_continuation is not None and entry_continuation >= 100) if result_trades else ""})
    trades = _simulate_capacity(trades)
    _write_artifacts(artifact_root, population, sessions, config, replays, snapshots_rows, membership_rows, transition_rows, observations, continuation, trades, labels, coverage_rows, strategy_errors, ranking_fingerprints, one_minute_manifest, before_git, before_sha)
    after_sha = _sha256(STRATEGY_PATH)
    after_git = _git_sha()
    if after_sha != before_sha or after_git != before_git:
        raise RuntimeError("frozen Leader Momentum strategy changed during replay")
    return {"network_fetches": 0, "strategy_sha256": before_sha, "git_sha": before_git, "observation_count": len(observations), "trade_count": len(trades), "strategy_errors": len(strategy_errors)}


def _write_artifacts(
    root: Path,
    population: dict[str, Any],
    sessions: list[date],
    config: EvolvingTopGainersConfig,
    replays: dict[date, Any],
    snapshots_rows: list[dict[str, object]],
    membership_rows: list[dict[str, object]],
    transition_rows: list[dict[str, object]],
    observations: list[dict[str, object]],
    continuation: list[dict[str, object]],
    trades: list[dict[str, object]],
    labels: dict[tuple[date, str], dict[str, str]],
    coverage_rows: list[dict[str, object]],
    strategy_errors: list[str],
    ranking_fingerprints: dict[str, str],
    one_minute_manifest: dict[str, Any],
    git_sha: str,
    strategy_sha: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for session_date, replay in replays.items():
        for member in replay.membership:
            membership_rows.append({"session_date": session_date.isoformat(), **member.model_dump(mode="json"), "symbol": _symbol(member.instrument_id)})
    assets = population["assets"]
    _write_csv(root / "population-manifest.csv", [{"symbol": item["symbol"], "exchange": item["exchange"], "asset_id": item["asset_id"], "name": item["name"], "population_source": population["source"], "included": True, "winner_labels_used": False} for item in assets], ["symbol", "exchange", "asset_id", "name", "population_source", "included", "winner_labels_used"])
    _write_json(root / "population-fingerprint.json", {"schema": POPULATION_SCHEMA, "source": population["source"], "selection_rule": population["selection_rule"], "asset_count": population["asset_count"], "asset_fingerprint": population["asset_fingerprint"], "session_dates": population["session_dates"], "winner_labels_used": False})
    _write_csv(root / "leaderboard-snapshots.csv", snapshots_rows, ["session_date", "observed_at", "rank", "instrument_id", "symbol", "gain_pct", "price", "previous_close", "cumulative_dollar_volume", "evidence_at"])
    _write_csv(root / "leaderboard-membership.csv", membership_rows, ["session_date", "instrument_id", "symbol", "first_top_n_at", "first_top_10_at", "first_top_5_at", "best_rank", "entry_count", "last_seen_at"])
    _write_csv(root / "leaderboard-transitions.csv", transition_rows, ["session_date", "observed_at", "kind", "instrument_id", "symbol", "rank", "gain_pct"])
    _write_csv(root / "observations.csv", observations, list(observations[0].keys()) if observations else ["session_date", "symbol"])
    _write_csv(root / "continuation-outcomes.csv", continuation, list(continuation[0].keys()) if continuation else ["session_date", "symbol"])
    _write_csv(root / "trades.csv", trades, list(trades[0].keys()) if trades else ["session_date", "symbol"])
    all_trades = trades
    modes = []
    for mode in ("controlled_pullback", "momentum_compression"):
        subset = [row for row in all_trades if row.get("mode") == mode]
        returns = [_dec(row.get("return_pct")) for row in subset]
        modes.append({"mode": mode, "signals": sum(1 for row in observations if row.get("setup_mode") == mode), "trades": len(subset), "wins": sum(item > 0 for item in returns), "losses": sum(item < 0 for item in returns), "expectancy_return_pct": _pct(sum(returns, Decimal("0")) / Decimal(len(returns)) if returns else None), "average_return_pct": _pct(sum(returns, Decimal("0")) / Decimal(len(returns)) if returns else None), "median_return_pct": _pct(sorted(returns)[len(returns) // 2] if returns else None), "mfe_pct": _pct(sum((_dec(row.get("mfe_pct")) for row in subset), Decimal("0")) / Decimal(len(subset)) if subset else None), "mae_pct": _pct(sum((_dec(row.get("mae_pct")) for row in subset), Decimal("0")) / Decimal(len(subset)) if subset else None), "eventual_top5": sum(1 for row in subset if next((obs for obs in observations if obs["session_date"] == row["session_date"] and obs["symbol"] == row["symbol"]), {}).get("winner_final_top5"))})
    _write_csv(root / "setup-mode-summary.csv", modes, ["mode", "signals", "trades", "wins", "losses", "expectancy_return_pct", "average_return_pct", "median_return_pct", "mfe_pct", "mae_pct", "eventual_top5"])
    exit_rows = []
    for reason in sorted({str(row.get("exit_reason_code")) for row in all_trades}):
        subset = [row for row in all_trades if row.get("exit_reason_code") == reason]
        exit_rows.append({"exit_reason_code": reason, "trades": len(subset), "wins": sum(_dec(row.get("return_pct")) > 0 for row in subset), "losses": sum(_dec(row.get("return_pct")) < 0 for row in subset), "average_return_pct": _pct(sum((_dec(row.get("return_pct")) for row in subset), Decimal("0")) / Decimal(len(subset)) if subset else None), "average_mfe_pct": _pct(sum((_dec(row.get("mfe_pct")) for row in subset), Decimal("0")) / Decimal(len(subset)) if subset else None), "mfe_at_least_10": sum(_dec(row.get("mfe_pct")) >= 10 for row in subset), "mfe_at_least_20": sum(_dec(row.get("mfe_pct")) >= 20 for row in subset)})
    _write_csv(root / "exit-summary.csv", exit_rows, ["exit_reason_code", "trades", "wins", "losses", "average_return_pct", "average_mfe_pct", "mfe_at_least_10", "mfe_at_least_20"])
    friction_rows = []
    for friction in FRICTION_BPS:
        values = [_trade_pnl(row, friction) for row in all_trades]
        friction_rows.append({"round_trip_friction_bps": str(friction), "trades": len(values), "wins_after_friction": sum(item > 0 for item in values), "losses_after_friction": sum(item < 0 for item in values), "total_pnl": _pct(sum(values, Decimal("0"))), "expectancy_per_trade": _pct(sum(values, Decimal("0")) / Decimal(len(values)) if values else None)})
    _write_csv(root / "friction-sensitivity.csv", friction_rows, ["round_trip_friction_bps", "trades", "wins_after_friction", "losses_after_friction", "total_pnl", "expectancy_per_trade"])
    ranked = sorted((_trade_pnl(row) for row in all_trades), reverse=True)
    total = sum(ranked, Decimal("0"))
    concentration = []
    for count in (1, 3, 5):
        top = sum(ranked[:count], Decimal("0"))
        concentration.append({"top_n": count, "top_n_pnl": _pct(top), "pct_of_total_pnl": _pct(top / total * Decimal("100") if total else None), "pnl_excluding_top_n": _pct(total - top)})
    _write_csv(root / "concentration-summary.csv", concentration, ["top_n", "top_n_pnl", "pct_of_total_pnl", "pnl_excluding_top_n"])
    pressure = []
    for session_date in sessions:
        day = [row for row in all_trades if row["session_date"] == session_date.isoformat()]
        accepted = [row for row in day if row.get("capacity_accepted")]
        rejected = [row for row in day if row.get("capacity_accepted") is False]
        pressure.append({"session_date": session_date.isoformat(), "eligible_trades": len(day), "capacity_accepted": len(accepted), "capacity_rejected": len(rejected), "accepted_pnl": _pct(sum((_trade_pnl(row) for row in accepted), Decimal("0"))), "rejected_pnl_if_taken": _pct(sum((_trade_pnl(row) for row in rejected), Decimal("0"))), "max_active_before_entry": max((int(row.get("capacity_active_before_entry", 0)) for row in day), default=0)})
    _write_csv(root / "portfolio-pressure.csv", pressure, ["session_date", "eligible_trades", "capacity_accepted", "capacity_rejected", "accepted_pnl", "rejected_pnl_if_taken", "max_active_before_entry"])
    monthly = []
    for month in sorted({str(row["session_date"])[:7] for row in observations}):
        rows = [row for row in all_trades if str(row["session_date"]).startswith(month)]
        values = [_trade_pnl(row) for row in rows]
        monthly.append({"month": month, "top20_entrants": sum(str(row["session_date"]).startswith(month) for row in observations), "trades": len(rows), "wins": sum(item > 0 for item in values), "losses": sum(item < 0 for item in values), "pnl": _pct(sum(values, Decimal("0"))), "return_on_100k_pct": _pct(sum((_dec(row.get("return_pct")) for row in rows), Decimal("0")) / Decimal("5") if rows else None)})
    _write_csv(root / "monthly-summary.csv", monthly, ["month", "top20_entrants", "trades", "wins", "losses", "pnl", "return_on_100k_pct"])
    winner_rows = []
    for (_session_date, symbol), label in sorted(labels.items()):
        obs = next((row for row in observations if row["session_date"] == _session_date.isoformat() and row["symbol"] == symbol), None)
        cutoff = datetime.combine(_session_date, time(15, 30), tzinfo=ET).astimezone(UTC)
        discovery_at = datetime.fromisoformat(str(obs["first_top20_at"])) if obs else None
        prior_signal_at = datetime.fromisoformat(str(obs["previous_winner_only_signal_at"])) if obs and obs.get("previous_winner_only_signal_at") else None
        winner_rows.append({"session_date": _session_date.isoformat(), "symbol": symbol, "final_rank": label.get("rank", ""), "final_gain_pct": label.get("gain_pct", ""), "entered_top20_before_1530": bool(discovery_at and discovery_at < cutoff), "entered_top10": bool(obs and obs.get("first_top10_at")), "entered_top5": bool(obs and obs.get("first_top5_at")), "leader_confirmed": bool(obs and obs.get("leader_confirmed_at")), "setup_valid": bool(obs and obs.get("first_signal_at")), "traded": bool(obs and int(obs.get("trade_count", 0)) > 0), "profitable_trade": bool(obs and _dec(obs.get("first_trade_return_pct")) > 0), "prior_winner_only_signal_at": obs.get("previous_winner_only_signal_at", "") if obs else "", "entered_before_prior_winner_only_signal": bool(discovery_at and prior_signal_at and discovery_at <= prior_signal_at)})
    _write_csv(root / "winner-discovery-funnel.csv", winner_rows, ["session_date", "symbol", "final_rank", "final_gain_pct", "entered_top20_before_1530", "entered_top10", "entered_top5", "leader_confirmed", "setup_valid", "traded", "profitable_trade", "prior_winner_only_signal_at", "entered_before_prior_winner_only_signal"])
    top20 = observations
    traded_obs = [row for row in top20 if int(row.get("trade_count", 0)) > 0]
    untraded = [row for row in top20 if int(row.get("trade_count", 0)) == 0]
    def cont_count(rows: list[dict[str, object]], field: str) -> int:
        return sum(str(row.get(field)).lower() == "true" for row in rows)
    funnel = [{"stage": "all_observable_population", "count": population["asset_count"]}, {"stage": "ever_entered_top20", "count": len(top20)}, {"stage": "ever_entered_top10", "count": sum(bool(row.get("first_top10_at")) for row in top20)}, {"stage": "ever_entered_top5", "count": sum(bool(row.get("first_top5_at")) for row in top20)}, {"stage": "leader_momentum_confirmed", "count": sum(bool(row.get("leader_confirmed_at")) for row in top20)}, {"stage": "setup_valid", "count": sum(bool(row.get("first_signal_at")) for row in top20)}, {"stage": "traded", "count": len(traded_obs)}, {"stage": "plus10_continuation", "count": cont_count(continuation, "continuation_plus10_after_discovery")}, {"stage": "plus20_continuation", "count": cont_count(continuation, "continuation_plus20_after_discovery")}, {"stage": "plus30_continuation", "count": cont_count(continuation, "continuation_plus30_after_discovery")}, {"stage": "plus50_continuation", "count": cont_count(continuation, "continuation_plus50_after_discovery")}]
    _write_csv(root / "leaderboard-membership-funnel.csv", funnel, ["stage", "count"])
    before = {"git_sha": git_sha, "strategy_file_sha256": strategy_sha, "policy_version": POLICY_VERSION, "constants": _strategy_constants()}
    _write_json(root / "run-config.json", {"experiment": "leader-momentum-v1.2-evolving-top-gainers", "policy_version": POLICY_VERSION, "git_sha": git_sha, "strategy_file_sha256_before": strategy_sha, "strategy_file_sha256_after": _sha256(STRATEGY_PATH), "git_sha_after": _git_sha(), "top_gainers_config": config.model_dump(mode="json"), "sessions": [item.isoformat() for item in sessions], "winner_input": str(WINNER_CSV), "winner_input_sha256": _sha256(WINNER_CSV), "winner_labels_attached_after_ranking": True, "population_source": population["source"], "population_fingerprint": population["asset_fingerprint"], "cache_root": str(CACHE_ROOT), "cache_feed": "alpaca-sip", "final_replay_cache_only": True, "final_replay_network_fetches": 0, "fixed_slot_notional": str(FIXED_SLOT_NOTIONAL), "capacity_slots": CAPACITY_SLOTS, "friction_bps": [str(item) for item in FRICTION_BPS], "assumed_spread_bps": str(ASSUMED_SPREAD_BPS), "leader_momentum_constants": before["constants"], "ranking_fingerprints": ranking_fingerprints, "one_minute_manifest": one_minute_manifest})
    _write_json(root / "completeness-report.json", {"validity": "invalid" if strategy_errors else "complete", "population_asset_count": population["asset_count"], "ranking_session_count": len(sessions), "ranking_fingerprints": ranking_fingerprints, "coverage": coverage_rows, "one_minute_cache_manifest": one_minute_manifest, "strategy_data_errors": strategy_errors, "network_fetches": 0, "winner_labels_used_in_population": False, "winner_labels_used_in_rankings": False, "strategy_hash_unchanged": _sha256(STRATEGY_PATH) == strategy_sha, "git_sha_unchanged": _git_sha() == git_sha, "notes": ["A no-print symbol is retained only through its latest observed price by the ranking primitive; missing/partial provider data is listed rather than silently removed.", "The Finviz archive was not sufficiently populated for all sessions, so this run uses the predeclared Alpaca active listed-equity catalog."]})
    _write_json(root / "_funnel.json", funnel)
    _write_summary(root, population, observations, continuation, trades, winner_rows, modes, friction_rows, concentration, pressure, strategy_errors)


def _rate(rows: list[dict[str, object]], field: str) -> str:
    eligible = [row for row in rows if str(row.get(field, "")) != ""]
    return _pct(Decimal(sum(str(row.get(field)).lower() == "true" for row in eligible)) / Decimal(len(eligible)) * Decimal("100") if eligible else None)


def _write_summary(root: Path, population: dict[str, Any], observations: list[dict[str, object]], continuation: list[dict[str, object]], trades: list[dict[str, object]], winner_rows: list[dict[str, object]], modes: list[dict[str, object]], friction: list[dict[str, object]], concentration: list[dict[str, object]], pressure: list[dict[str, object]], errors: list[str]) -> None:
    total_pnl = sum((_trade_pnl(row) for row in trades), Decimal("0"))
    returns = [_dec(row.get("return_pct")) for row in trades]
    traded_winners = sum(bool(row.get("traded")) for row in winner_rows if row.get("entered_top20_before_1530"))
    winner_entered = sum(bool(row.get("entered_top20_before_1530")) for row in winner_rows)
    nonwinner = [row for row in observations if not row.get("winner_final_top5")]
    nonwinner_traded = sum(int(row.get("trade_count", 0)) > 0 for row in nonwinner)
    traded_observations = sum(int(row.get("trade_count", 0)) > 0 for row in observations)
    cap_rejected = sum(row.get("capacity_accepted") is False for row in trades)
    lines = [
        "# Leader Momentum v1.2 — evolving Top-Gainers replay",
        "",
        f"Validity: **{'INVALID — data/strategy errors are listed below' if errors else 'complete'}**",
        "",
        "This is a cache-only final replay of frozen `leader-momentum-continuation-v1.2`. The ranking population was the predeclared Alpaca active US-equity catalog; winner labels were attached only after ranking fingerprints were produced.",
        "",
        f"Population: {population['asset_count']} Alpaca-listed assets; Top-20 refresh every 5 minutes from 09:35 through 15:30 ET. Final replay network fetches: **0**.",
        "",
        "## Direct answers",
        "",
        f"1. Eventual winners entering Top-20 before 15:30: **{winner_entered}/{len(winner_rows)} ({winner_entered / len(winner_rows) * 100:.2f}%)**.",
        f"2. Entering Top-20 by their previous winner-only signal: **{sum(bool(row.get('entered_before_prior_winner_only_signal')) for row in winner_rows)}/{len(winner_rows)}**.",
        "3. CPHI/JLHL/PFSA are reported in `observations.csv`/`winner-discovery-funnel.csv`; they are prospective only when their recorded Top-20 timestamp is no later than the signal. Missing timestamps are not silently counted as valid.",
        f"4. v1.2 traded **{traded_observations}/{len(observations)}** unique Top-20 entrants ({traded_observations / len(observations) * 100:.2f}%); this produced {len(trades)} trade executions because some entrants re-entered.",
        f"5. Eventual-winner entrant trade rate: {traded_winners}/{winner_entered if winner_entered else 0}; non-winner Top-20 entrant trade rate: {nonwinner_traded}/{len(nonwinner)}.",
        f"6. Post-entry continuation: +10 **{_rate(continuation, 'continuation_plus10_after_trade')}%**, +20 **{_rate(continuation, 'continuation_plus20_after_trade')}%**, +30 **{_rate(continuation, 'continuation_plus30_after_trade')}%**, +50 **{_rate(continuation, 'continuation_plus50_after_trade')}%** among traded entrants; untraded discovery rates are in `continuation-outcomes.csv`.",
        f"7. All dynamically discovered candidates: total normalized P/L **${total_pnl:.2f}**, expectancy **${(total_pnl / Decimal(len(trades)) if trades else Decimal('0')):.2f}/trade**.",
        "8. 40/80/120/200 bps results are in `friction-sensitivity.csv`.",
        "9. Mode A/B results are in `setup-mode-summary.csv`; no mode was tuned or selected.",
        f"10. Tail results are in `concentration-summary.csv`; largest-runner dependence is shown as top 1/3/5 P/L and exclusion P/L.",
        f"11. Five-slot simulation accepted {len(trades) - cap_rejected} and rejected {cap_rejected} causally later eligible trades; see `portfolio-pressure.csv`.",
        "12–14. Missed, discovered-but-rejected, and poorly monetized winners are directly queryable in `winner-discovery-funnel.csv`, `observations.csv`, and `continuation-outcomes.csv`.",
        "15. No v1.3 was implemented. Keep v1.2 SHADOW unless the completeness report is complete and the all-candidate/friction/capacity evidence supports a separate, explicitly approved research phase.",
        "",
        "## Required evidence",
        "",
        f"Strategy policy: `{POLICY_VERSION}`. Strategy SHA-256 before/after and all constants are in `run-config.json`. Cache/network details are in `completeness-report.json` and `population-fingerprint.json`.",
        "",
        "Artifacts are intentionally uncommitted local research output.",
    ]
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--populate", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--database-credential-injected", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT)
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    args = parser.parse_args()
    if args.populate == args.replay:
        parser.error("choose exactly one of --populate or --replay")
    if args.replay and not args.cache_only:
        parser.error("the final replay requires --cache-only")
    sessions = _sessions_from_winners()
    cache = CompactSipCache(args.cache_root.resolve())
    if args.populate:
        _populate(cache, sessions, max(1, min(8, args.workers)))
        return 0
    result = _replay(cache, sessions, args.artifact_root.resolve())
    print(json.dumps(result, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
