from __future__ import annotations

"""Recovery-aware entry point for the deterministic interday SHADOW replay.

The historical runner originally keyed its persistent cache to one exact request
range and hard-coded Yahoo 1-minute download windows.  That made a perfectly
valid cached session look missing when a replay requested a narrower range, and
prevented newer sessions (for example 2026-09-16) from ever being requested.

Keep the large research runner stable in ``run_interday_winner_shadow_replay_core``
and adapt only its market-data boundary here:

* cache reads are session-addressable instead of exact-request-addressable;
* Yahoo 1m requests are generated from the requested replay dates;
* missing/gappy 1m data can be reconciled from the alternate consolidated
  research source (Yahoo <-> Alpaca SIP), from cache even in ``--cache-only``;
* provider provenance is retained per recovered minute;
* no 1m candle is synthesized from a coarser 5m candle;
* a session with unresolved 1m gaps remains unavailable to 1m-dependent arms.

This module is research/replay infrastructure only.  It does not broaden paper
or live execution authority.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.trade import run_interday_winner_shadow_replay_core as _core

from app.apps.trading.market_data_recovery import detect_session_gaps, reconcile_recovery
from app.apps.trading.models import AdjustmentMode, MarketBar


# Public compatibility surface used by existing tests and ad-hoc research tools.
MarketDataCache = _core.MarketDataCache
RawBar = _core.RawBar
SymbolReplayData = _core.SymbolReplayData
cache_stats = _core.cache_stats
reset_cache_stats = _core.reset_cache_stats

# Keep per-bar provenance after reconciling RawBar inputs.  The legacy RawBar
# cache schema intentionally stays unchanged; provenance is an in-memory replay
# property and is reconstructed deterministically each run.
_BAR_PROVIDER_BY_KEY: dict[tuple[str, datetime], str] = {}
_RECOVERY_SOURCES: set[str] = set()


def _provider_id(source: str) -> str:
    return "alpaca_sip" if source == "alpaca-sip" else "yahoo"


def _session_aware_cache_load(
    self: MarketDataCache,
    symbol: str,
    timeframe: str,
    *,
    start: datetime,
    end: datetime,
    query_profile: str,
) -> tuple[RawBar, ...] | None:
    """Load requested session files even when the manifest range differs.

    ``MarketDataCache.store`` already writes one immutable-ish JSON payload per
    local session.  Requiring the latest manifest's *entire* request range to be
    byte-for-byte identical defeated that layout: a broad historical populate
    followed by a one-day replay produced a false cache miss.  The query profile
    and every session payload still have to match the source/schema/symbol/
    timeframe contract, and every requested local date must exist.
    """

    directory = self._timeframe_dir(symbol, timeframe)
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest is not an object")
        if manifest.get("schema_version") != _core.CACHE_SCHEMA_VERSION:
            raise ValueError("cache schema mismatch")
        if manifest.get("source") != self.source:
            raise ValueError("cache source mismatch")
        if manifest.get("symbol") != symbol.upper():
            raise ValueError("cache symbol mismatch")
        if manifest.get("timeframe") != timeframe:
            raise ValueError("cache timeframe mismatch")
        request = manifest.get("request")
        if not isinstance(request, dict) or request.get("query_profile") != query_profile:
            raise ValueError("cache query profile mismatch")

        bars: list[RawBar] = []
        for requested_date in self._session_dates(start, end):
            session_text = requested_date.isoformat()
            session_path = directory / f"{session_text}.json"
            payload = json.loads(session_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("cache session payload malformed")
            if payload.get("schema_version") != _core.CACHE_SCHEMA_VERSION:
                raise ValueError("cache session schema mismatch")
            if payload.get("source") != self.source:
                raise ValueError("cache session source mismatch")
            if payload.get("symbol") != symbol.upper():
                raise ValueError("cache session symbol mismatch")
            if payload.get("timeframe") != timeframe:
                raise ValueError("cache session timeframe mismatch")
            if payload.get("session_date") != session_text:
                raise ValueError("cache session date mismatch")
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, list):
                raise ValueError("cache session bars malformed")
            for raw in raw_bars:
                bar = self._deserialize_bar(raw, symbol=symbol, timeframe=timeframe)
                if start <= bar.start < end:
                    bars.append(bar)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError):
        _core._increment_cache_stat("misses")
        return None

    _core._increment_cache_stat("hits")
    return tuple(sorted(bars, key=lambda item: item.start))


# Install before the core runner creates any cache instances.
MarketDataCache.load = _session_aware_cache_load


def _yahoo_1m_chunks(
    first_session: date,
    last_session: date,
    *,
    maximum_calendar_days: int = 7,
) -> tuple[tuple[date, date], ...]:
    """Return bounded [start, end) date windows covering the requested sessions."""

    if last_session < first_session:
        raise ValueError("last session must not precede first session")
    if maximum_calendar_days < 1:
        raise ValueError("maximum_calendar_days must be positive")

    exclusive_end = last_session + timedelta(days=1)
    cursor = first_session
    chunks: list[tuple[date, date]] = []
    while cursor < exclusive_end:
        chunk_end = min(cursor + timedelta(days=maximum_calendar_days), exclusive_end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return tuple(chunks)


def _fetch_yahoo_1m(
    session: requests.Session,
    symbol: str,
    *,
    first_session: date,
    last_session: date,
) -> tuple[tuple[RawBar, ...], dict[str, str]]:
    """Fetch the actual replay dates instead of a frozen historical date list."""

    fetched: dict[datetime, RawBar] = {}
    errors: dict[str, str] = {}
    for chunk_start, chunk_end in _yahoo_1m_chunks(first_session, last_session):
        start_at = datetime.combine(chunk_start, time(9, 30), tzinfo=_core.ET).astimezone(_core.UTC)
        # chunk_end is exclusive; midnight after the last included date covers
        # every regular-session bar while keeping the Yahoo interval bounded.
        end_at = datetime.combine(chunk_end, time(0), tzinfo=_core.ET).astimezone(_core.UTC)
        try:
            _meta, chunk_raw = _core._fetch_chart(
                session,
                symbol,
                interval="1m",
                start=start_at,
                end=end_at,
                include_prepost=False,
                label=f"{symbol} 1m {chunk_start.isoformat()}..{(chunk_end - timedelta(days=1)).isoformat()}",
            )
        except Exception as exc:
            errors[f"{chunk_start.isoformat()}..{chunk_end.isoformat()}"] = (
                f"{type(exc).__name__}: {exc}"
            )
            continue
        for bar in chunk_raw:
            local = _core._local_start(bar)
            if (
                first_session <= local.date() <= last_session
                and time(9, 30) <= local.time() < time(16, 0)
            ):
                fetched[bar.start] = bar
    return tuple(sorted(fetched.values(), key=lambda item: item.start)), errors


def _fetch_source_1m(
    source: str,
    session: requests.Session,
    symbol: str,
    *,
    first_session: date,
    last_session: date,
    start: datetime,
    end: datetime,
) -> tuple[tuple[RawBar, ...], dict[str, str]]:
    if source == "alpaca-sip":
        try:
            _meta, bars = _core._fetch_alpaca_bars(
                session,
                symbol,
                timeframe="1Min",
                start=start,
                end=end,
                label=f"{symbol} SIP 1m",
            )
        except Exception as exc:
            return (), {f"{first_session}..{last_session}": f"{type(exc).__name__}: {exc}"}
        return bars, {}
    return _fetch_yahoo_1m(
        session,
        symbol,
        first_session=first_session,
        last_session=last_session,
    )


def _raw_as_market_bars(
    raw_bars: tuple[RawBar, ...],
    *,
    instrument_id: str,
    provider: str,
) -> list[MarketBar]:
    return [
        MarketBar(
            instrument_id=instrument_id,
            interval="1m",
            start_time=bar.start,
            end_time=bar.end,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            is_final=True,
            adjustment_mode=AdjustmentMode.RAW,
            session="regular",
            provider=provider,
            provider_event_id=str(int(bar.start.timestamp())),
            received_at=bar.end,
        )
        for bar in raw_bars
    ]


def _market_as_raw(bar: MarketBar) -> RawBar:
    return RawBar(
        start=bar.start_time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        interval_minutes=1,
    )


def _session_close(session_date: date) -> datetime:
    return datetime.combine(session_date, time(16, 0), tzinfo=_core.ET).astimezone(_core.UTC)


def _needs_one_minute_recovery(
    symbol: str,
    sessions: list[date],
    raw_bars: tuple[RawBar, ...],
    *,
    source: str,
) -> bool:
    instrument_id = f"equity:US:{symbol}"
    provider = _provider_id(source)
    for session_date in sessions:
        session_raw = _core._session_bars(raw_bars, session_date, regular=True)
        if not session_raw:
            return True
        gaps = detect_session_gaps(
            _raw_as_market_bars(
                session_raw,
                instrument_id=instrument_id,
                provider=provider,
            ),
            session_date=session_date,
            interval="1m",
            as_of=_session_close(session_date),
        )
        if gaps:
            return True
    return False


def _recover_one_minute_sessions(
    symbol: str,
    sessions: list[date],
    *,
    primary_raw: tuple[RawBar, ...],
    fallback_raw: tuple[RawBar, ...],
    primary_source: str,
    fallback_source: str | None,
) -> tuple[dict[date, tuple[RawBar, ...]], dict[date, str]]:
    """Reconcile factual 1m sources and fail closed on any unresolved minute."""

    instrument_id = f"equity:US:{symbol}"
    output: dict[date, tuple[RawBar, ...]] = {}
    unresolved: dict[date, str] = {}
    primary_provider = _provider_id(primary_source)
    fallback_provider = _provider_id(fallback_source) if fallback_source else None

    for session_date in sessions:
        primary_session = _core._session_bars(primary_raw, session_date, regular=True)
        fallback_session = _core._session_bars(fallback_raw, session_date, regular=True)
        recovered = reconcile_recovery(
            instrument_id=instrument_id,
            interval="1m",
            session_date=session_date,
            as_of=_session_close(session_date),
            primary_bars=_raw_as_market_bars(
                primary_session,
                instrument_id=instrument_id,
                provider=primary_provider,
            ),
            fallback_bars=_raw_as_market_bars(
                fallback_session,
                instrument_id=instrument_id,
                provider=fallback_provider or primary_provider,
            ),
            primary_provider=primary_provider,
            fallback_provider=fallback_provider,
            fallback_attempted=bool(fallback_raw),
            partial_market_fallback=False,
        )
        if recovered.report.recovered_bar_count:
            _core._increment_cache_stat(
                "recovered_1m_bars", recovered.report.recovered_bar_count
            )
            if fallback_source:
                _RECOVERY_SOURCES.add(fallback_source)
        if recovered.report.unresolved_gaps:
            details = ",".join(
                f"{gap.start.isoformat()}..{gap.end.isoformat()}"
                for gap in recovered.report.unresolved_gaps
            )
            unresolved[session_date] = f"UNRESOLVED_1M_GAPS:{details}"
            # Do not let a session-anchored 1m strategy accidentally interpret a
            # partial tape as complete.  Individual rolling strategies should use
            # the shared StrategyDataRequirement path instead of this replay arm.
            output[session_date] = ()
            continue

        rows = tuple(_market_as_raw(bar) for bar in recovered.bars)
        output[session_date] = rows
        for bar in recovered.bars:
            _BAR_PROVIDER_BY_KEY[(instrument_id, bar.start_time)] = bar.provider
    return output, unresolved


def _market_bars(
    raw_bars: tuple[RawBar, ...],
    instrument_id: str,
    interval: str,
) -> list[MarketBar]:
    default_provider = _core._bar_provider()
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
            provider=_BAR_PROVIDER_BY_KEY.get(
                (instrument_id, bar.start), default_provider
            ),
            provider_event_id=str(int(bar.start.timestamp())),
            received_at=bar.end,
        )
        for bar in raw_bars
    ]


def _data_source(interval: str) -> str:
    base = f"{_core.ACTIVE_SOURCE}-{interval}"
    if interval == "1m" and _RECOVERY_SOURCES:
        return base + "+" + "+".join(sorted(_RECOVERY_SOURCES)) + "-recovery"
    return base


def _parse_source(
    path: Path,
    *,
    expected_symbols_per_session: int | None = 5,
) -> tuple[list[dict[str, object]], list[date], dict[date, list[dict[str, object]]]]:
    """Accept a prospective variable-size cohort without importing outcome labels."""

    rows: list[dict[str, object]] = []
    grouped: dict[date, list[dict[str, object]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            session_date = date.fromisoformat(str(raw["session_date"]))
            gain_raw = str(raw.get("gain_pct") or "").strip()
            gain_pct: Decimal | None = Decimal(gain_raw) if gain_raw else None
            row = {
                "session_date": session_date,
                "rank": int(str(raw["rank"])),
                "symbol": str(raw["symbol"]).strip().upper(),
                "gain_pct": gain_pct,
                "source_url": str(raw.get("source_url") or ""),
            }
            rows.append(row)
            grouped[session_date].append(row)
    if not rows:
        raise ValueError("replay input contains no observations")
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
    return rows, sorted(grouped), grouped


def _load_symbol(
    symbol: str,
    source_rows: dict[date, dict[str, object]],
    sessions: list[date],
) -> SymbolReplayData:
    session = requests.Session()
    cache = MarketDataCache(_core.CACHE_DIR, _core.ACTIVE_SOURCE)
    five_minute_error: str | None = None
    one_minute_errors: dict[str, str] = {}
    first_session = min(sessions)
    last_session = max(sessions)

    five_start = datetime.combine(
        first_session - timedelta(days=30), time(0), tzinfo=_core.ET
    ).astimezone(_core.UTC)
    five_end = datetime.combine(
        last_session + timedelta(days=1), time(0), tzinfo=_core.ET
    ).astimezone(_core.UTC)
    try:
        five_raw = cache.load(
            symbol,
            "5m",
            start=five_start,
            end=five_end,
            query_profile="extended_session",
        )
        if five_raw is None:
            if _core.CACHE_ONLY:
                raise RuntimeError(f"CACHE_MISS:{_core.ACTIVE_SOURCE}:{symbol}:5m")
            _core._increment_cache_stat("network_fetches")
            if _core.ACTIVE_SOURCE == "alpaca-sip":
                _meta, five_raw = _core._fetch_alpaca_bars(
                    session,
                    symbol,
                    timeframe="5Min",
                    start=five_start,
                    end=five_end,
                    label=f"{symbol} SIP 5m",
                )
            else:
                _meta, five_raw = _core._fetch_chart(
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
        candidates = _core._build_candidates(symbol, source_rows, five_raw)
    except Exception as exc:
        five_minute_error = f"{type(exc).__name__}: {exc}"
        candidates = {}
        five_raw = ()

    bars_5m = {
        session_date: _core._session_bars(five_raw, session_date, regular=True)
        for session_date in sessions
    }
    bars_5m_history = _core._build_5m_history(five_raw, sessions)
    bars_1m: dict[date, tuple[RawBar, ...]] = {
        session_date: () for session_date in sessions
    }

    if five_minute_error is None:
        one_start = datetime.combine(
            first_session, time(9, 30), tzinfo=_core.ET
        ).astimezone(_core.UTC)
        one_end = datetime.combine(
            last_session, time(16, 0), tzinfo=_core.ET
        ).astimezone(_core.UTC)

        primary_raw = cache.load(
            symbol,
            "1m",
            start=one_start,
            end=one_end,
            query_profile="regular_session",
        )
        if primary_raw is None:
            primary_raw = ()
            if _core.CACHE_ONLY:
                one_minute_errors["primary_cache"] = (
                    f"CACHE_MISS:{_core.ACTIVE_SOURCE}:{symbol}:1m"
                )
            else:
                _core._increment_cache_stat("network_fetches")
                fetched, fetch_errors = _fetch_source_1m(
                    _core.ACTIVE_SOURCE,
                    session,
                    symbol,
                    first_session=first_session,
                    last_session=last_session,
                    start=one_start,
                    end=one_end,
                )
                primary_raw = fetched
                one_minute_errors.update(
                    {f"primary:{key}": value for key, value in fetch_errors.items()}
                )
                if primary_raw and not fetch_errors:
                    cache.store(
                        symbol,
                        "1m",
                        primary_raw,
                        start=one_start,
                        end=one_end,
                        query_profile="regular_session",
                    )

        fallback_source: str | None = None
        fallback_raw: tuple[RawBar, ...] = ()
        if _needs_one_minute_recovery(
            symbol,
            sessions,
            primary_raw,
            source=_core.ACTIVE_SOURCE,
        ):
            fallback_source = (
                "alpaca-sip" if _core.ACTIVE_SOURCE == "yahoo" else "yahoo"
            )
            fallback_cache = MarketDataCache(_core.CACHE_DIR, fallback_source)
            fallback_raw = fallback_cache.load(
                symbol,
                "1m",
                start=one_start,
                end=one_end,
                query_profile="regular_session",
            ) or ()
            if fallback_raw:
                _core._increment_cache_stat("recovery_cache_hits")
            elif _core.CACHE_ONLY:
                one_minute_errors["fallback_cache"] = (
                    f"CACHE_MISS:{fallback_source}:{symbol}:1m"
                )
            else:
                _core._increment_cache_stat("recovery_network_fetches")
                fetched, fetch_errors = _fetch_source_1m(
                    fallback_source,
                    session,
                    symbol,
                    first_session=first_session,
                    last_session=last_session,
                    start=one_start,
                    end=one_end,
                )
                fallback_raw = fetched
                one_minute_errors.update(
                    {f"fallback:{key}": value for key, value in fetch_errors.items()}
                )
                if fallback_raw and not fetch_errors:
                    fallback_cache.store(
                        symbol,
                        "1m",
                        fallback_raw,
                        start=one_start,
                        end=one_end,
                        query_profile="regular_session",
                    )

        recovered_sessions, unresolved = _recover_one_minute_sessions(
            symbol,
            sessions,
            primary_raw=primary_raw,
            fallback_raw=fallback_raw,
            primary_source=_core.ACTIVE_SOURCE,
            fallback_source=fallback_source,
        )
        bars_1m.update(recovered_sessions)
        for session_date, reason in unresolved.items():
            one_minute_errors[f"unresolved:{session_date.isoformat()}"] = reason

        # Successful recovery supersedes acquisition diagnostics for the usable
        # target sessions; retain only unresolved/fetch errors that still matter.
        if all(bars_1m.get(session_date) for session_date in sessions):
            one_minute_errors = {
                key: value
                for key, value in one_minute_errors.items()
                if key.startswith("fallback:") or key.startswith("primary:")
            }

    return SymbolReplayData(
        symbol=symbol,
        candidates=candidates,
        bars_5m=bars_5m,
        bars_1m=bars_1m,
        bars_5m_history=bars_5m_history,
        five_minute_error=five_minute_error,
        one_minute_errors=one_minute_errors,
    )


# Patch only the data boundary and provenance helpers used by the stable core.
_core.MarketDataCache.load = _session_aware_cache_load
_core._parse_source = _parse_source
_core._load_symbol = _load_symbol
_core._market_bars = _market_bars
_core._data_source = _data_source


def main() -> int:
    _BAR_PROVIDER_BY_KEY.clear()
    _RECOVERY_SOURCES.clear()
    return _core.main()


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


if __name__ == "__main__":
    raise SystemExit(main())
