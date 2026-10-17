from __future__ import annotations

"""Build reconstructed point-in-time populations for Leader Momentum replay.

The existing evolving-leader cache was seeded from today's active Alpaca
catalog.  This utility keeps that bar cache intact and creates a separate
historical population authority:

* Alpaca's active and inactive equity assets are fetched once as an identifier
  seed;
* each session is queried with ``asof=<session date>`` for SIP daily bars;
* a symbol enters that session's population only when a prior regular-session
  close is available before the session opens; and
* no same-day outcome, final rank, or winner label is read while constructing
  the population.

The result is a research reconstruction, not an exchange security-master
snapshot.  It is deliberately stored beside, rather than over, the legacy
active-catalog cache.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time as time_module
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

from app.apps.trading.providers.alpaca_iex import ALPACA_DATA_URL, alpaca_iex_auth_headers
from app.apps.trading.strategy_evolving_top_gainers_research import (
    HistoricalPopulationManifest,
)


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "NYSEARCA"}
ASSET_SYMBOL = re.compile(r"^[A-Z0-9.\-]+$")
ASSET_SCHEMA = "leader-momentum-historical-asset-seed-v1"
POPULATION_SCHEMA = "leader-momentum-historical-population-v1"
REPORT_SCHEMA = "leader-momentum-historical-population-report-v1"
DEFAULT_CACHE_ROOT = REPOSITORY_ROOT / "resources" / "cache" / "leader-momentum-evolving-top-gainers"
DEFAULT_OUTPUT_ROOT = DEFAULT_CACHE_ROOT / "historical-population"
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_REQUEST_MIN_INTERVAL_SECONDS = 0.50
_INACTIVE_NON_COMMON_MARKERS = (
    " WARRANT",
    " RIGHT",
    " UNIT",
    " CONTRA",
    " DEBENTURE",
    " NOTE",
    " PREFERRED",
    " PREF",
    " CONTINGENT VALUE",
)


@dataclass(frozen=True, slots=True)
class DailyClose:
    symbol: str
    bar_start: datetime
    session_date: date
    close: Decimal


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, default=_json_default, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("historical population source bar is missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("historical population source bar timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"historical population source bar has invalid close: {value!r}") from exc
    if parsed <= 0:
        raise ValueError("historical population source bar close must be positive")
    return parsed


def _instrument_id(symbol: str) -> str:
    return f"equity:US:{symbol.upper()}"


def normalize_asset_payload(payload: object) -> list[dict[str, Any]]:
    """Normalize all-status Alpaca assets while retaining inactive assets."""

    if not isinstance(payload, list):
        raise ValueError("Alpaca all-status-assets response is not a list")
    assets: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        exchange = str(raw.get("exchange") or "").strip().upper()
        normalized_exchange = "ARCA" if exchange == "NYSEARCA" else exchange
        asset_class = str(raw.get("class") or raw.get("asset_class") or "us_equity").strip().lower()
        if (
            not symbol
            or not ASSET_SYMBOL.fullmatch(symbol)
            or normalized_exchange not in {"NASDAQ", "NYSE", "AMEX", "ARCA"}
            or asset_class != "us_equity"
        ):
            continue
        status = str(raw.get("status") or "unknown").strip().lower()
        if status not in {"active", "inactive", "unknown"}:
            continue
        assets.append(
            {
                "symbol": symbol,
                "exchange": normalized_exchange,
                "asset_class": asset_class,
                "name": str(raw.get("name") or ""),
                "asset_id": str(raw.get("id") or raw.get("asset_id") or ""),
                "status": status,
                "tradable": bool(raw.get("tradable", False)),
            }
        )
    assets.sort(key=lambda item: (item["symbol"], item["exchange"], item["asset_id"], item["status"]))
    if not assets:
        raise ValueError("Alpaca returned no valid all-status listed US equities")
    return assets


def unique_asset_symbols(assets: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted({str(item["symbol"]).upper() for item in assets}))


def queryable_seed_symbols(assets: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """Choose likely priceable symbols while retaining the complete seed.

    Alpaca's all-status catalog includes legacy warrants, rights, units and
    other instruments that the stock-bars endpoint rejects. Active symbols are
    retained as before. Inactive symbols are retained when they look like
    ordinary equity tickers; the complete unfiltered seed remains on disk for
    auditability.
    """

    symbols: set[str] = set()
    for asset in assets:
        symbol = str(asset["symbol"]).upper()
        status = str(asset.get("status") or "").lower()
        name = f" {str(asset.get('name') or '').upper()}"
        if status == "active":
            symbols.add(symbol)
            continue
        if status == "inactive" and symbol[:1].isalpha() and not any(
            marker in name for marker in _INACTIVE_NON_COMMON_MARKERS
        ):
            symbols.add(symbol)
    return tuple(sorted(symbols))


def previous_close_by_symbol(
    closes: Iterable[DailyClose],
    *,
    session_date: date,
) -> dict[str, DailyClose]:
    """Return the latest close strictly before the requested session."""

    latest: dict[str, DailyClose] = {}
    for row in closes:
        if row.session_date >= session_date:
            continue
        existing = latest.get(row.symbol)
        if existing is None or (row.session_date, row.bar_start) > (existing.session_date, existing.bar_start):
            latest[row.symbol] = row
    return latest


def build_session_manifest(
    *,
    session_date: date,
    assets: list[dict[str, Any]],
    closes: Iterable[DailyClose],
    asset_seed_fingerprint: str,
) -> tuple[HistoricalPopulationManifest, dict[str, Any]]:
    prior = previous_close_by_symbol(closes, session_date=session_date)
    asset_by_symbol: dict[str, dict[str, Any]] = {}
    for asset in assets:
        asset_by_symbol.setdefault(str(asset["symbol"]).upper(), asset)
    eligible_symbols = tuple(sorted(set(asset_by_symbol) & set(prior)))
    if not eligible_symbols:
        raise ValueError(f"no prior SIP closes available for {session_date}")

    close_payload = {
        symbol: {
            "close": str(prior[symbol].close),
            "bar_start": prior[symbol].bar_start.isoformat(),
            "bar_session_date": prior[symbol].session_date.isoformat(),
        }
        for symbol in eligible_symbols
    }
    source_fingerprint = _sha256_payload(
        {
            "asset_seed_fingerprint": asset_seed_fingerprint,
            "session_date": session_date.isoformat(),
            "previous_closes": close_payload,
        }
    )
    session_open = datetime.combine(session_date, time(9, 30), tzinfo=ET).astimezone(UTC)
    source_locator = (
        "alpaca:/v2/assets?asset_class=us_equity&status=all"
        f" + /v2/stocks/bars?feed=sip&timeframe=1Day&asof={session_date.isoformat()}"
    )
    manifest = HistoricalPopulationManifest(
        session_date=session_date,
        authority="reconstructed_alpaca_sip_population",
        instrument_ids=tuple(_instrument_id(symbol) for symbol in eligible_symbols),
        point_in_time=True,
        outcome_conditioned=False,
        captured_as_of=session_open,
        source_locator=source_locator,
        source_fingerprint=source_fingerprint,
    )
    envelope = {
        "schema": POPULATION_SCHEMA,
        "manifest": manifest.model_dump(mode="json"),
        "manifest_fingerprint": manifest.fingerprint,
        "asset_seed_fingerprint": asset_seed_fingerprint,
        "eligible_symbol_count": len(eligible_symbols),
        "eligible_symbols": list(eligible_symbols),
        "previous_closes": close_payload,
        "selection_rule": "all-status Alpaca equity seed intersected with symbols having a prior SIP daily close strictly before the session; no same-day outcome or winner labels",
    }
    return manifest, envelope


def _asset_seed_path(output_root: Path) -> Path:
    return output_root / "asset-seed.json"


def _session_path(output_root: Path, session_date: date) -> Path:
    return output_root / "sessions" / f"{session_date.isoformat()}.json"


def _invalid_symbols_path(output_root: Path) -> Path:
    return output_root / "data-unavailable-symbols.json"


def _query_filter_path(output_root: Path) -> Path:
    return output_root / "query-filter.json"


def _fetch_all_assets() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trading_url = (os.environ.get("OMNIX_ALPACA_TRADING_URL") or "https://paper-api.alpaca.markets").rstrip("/")
    with requests.Session() as session:
        response = session.get(
            f"{trading_url}/v2/assets",
            params={"status": "all", "asset_class": "us_equity"},
            headers=alpaca_iex_auth_headers(),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    assets = normalize_asset_payload(payload)
    seed_fingerprint = _sha256_payload(assets)
    seed = {
        "schema": ASSET_SCHEMA,
        "source": "alpaca_all_status_us_equity_catalog_identifier_seed",
        "request": {"status": "all", "asset_class": "us_equity"},
        "captured_at": datetime.now(UTC).isoformat(),
        "asset_count": len(assets),
        "symbol_count": len(unique_asset_symbols(assets)),
        "asset_fingerprint": seed_fingerprint,
        "assets": assets,
    }
    return assets, seed


def _rate_limited_get(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str],
) -> requests.Response:
    global _LAST_REQUEST_AT
    for attempt in range(10):
        with _REQUEST_LOCK:
            now = time_module.monotonic()
            wait = _REQUEST_MIN_INTERVAL_SECONDS - (now - _LAST_REQUEST_AT)
            if wait > 0:
                time_module.sleep(wait)
            _LAST_REQUEST_AT = time_module.monotonic()
        response = session.get(url, params=params, headers=headers, timeout=90)
        if response.status_code != 429:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            delay = min(30.0, max(2.0, float(retry_after or 2**attempt)))
        except ValueError:
            delay = min(30.0, max(2.0, float(2**attempt)))
        time_module.sleep(delay)
    raise RuntimeError("Alpaca historical population request exceeded retry limit")


def _fetch_daily_chunk(
    symbols: list[str],
    *,
    session_date: date,
    headers: dict[str, str],
) -> tuple[list[DailyClose], tuple[str, ...]]:
    start = datetime.combine(session_date - timedelta(days=14), time(0), tzinfo=ET).astimezone(UTC)
    end = datetime.combine(session_date, time(9, 30), tzinfo=ET).astimezone(UTC)
    params: dict[str, object] = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "asof": session_date.isoformat(),
        "adjustment": "raw",
        "feed": "sip",
        "sort": "asc",
        "limit": 10_000,
    }
    requested = set(symbols)
    active_symbols = list(symbols)
    invalid_symbols: set[str] = set()
    latest: dict[str, DailyClose] = {}
    with requests.Session() as session:
        while True:
            params["symbols"] = ",".join(active_symbols)
            response = _rate_limited_get(
                session,
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                params=params,
                headers=headers,
            )
            if not response.ok:
                detail = response.text[:500].replace("\r", " ").replace("\n", " ")
                invalid_match = re.search(r"invalid symbol:\s*([A-Za-z0-9.\-]+)", detail, re.IGNORECASE)
                if response.status_code == 400 and invalid_match is not None:
                    invalid = invalid_match.group(1).upper()
                    if invalid in requested:
                        invalid_symbols.add(invalid)
                        active_symbols = [symbol for symbol in active_symbols if symbol != invalid]
                        if not active_symbols:
                            break
                        params.pop("page_token", None)
                        continue
                raise RuntimeError(
                    f"Alpaca historical daily-bars request failed ({response.status_code}): {detail}"
                )
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
                raise ValueError("Alpaca historical daily-bars payload is malformed")
            for raw_symbol, raw_rows in payload["bars"].items():
                symbol = str(raw_symbol).upper()
                if symbol not in requested or not isinstance(raw_rows, list):
                    continue
                for raw in raw_rows:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        bar_start = _timestamp(raw.get("t"))
                        close = _decimal(raw.get("c"))
                    except ValueError:
                        continue
                    bar_session_date = bar_start.astimezone(ET).date()
                    if bar_session_date >= session_date:
                        continue
                    candidate = DailyClose(symbol, bar_start, bar_session_date, close)
                    existing = latest.get(symbol)
                    if existing is None or (candidate.session_date, candidate.bar_start) > (existing.session_date, existing.bar_start):
                        latest[symbol] = candidate
            token = payload.get("next_page_token")
            if not isinstance(token, str) or not token:
                break
            params["page_token"] = token
    return list(latest.values()), tuple(sorted(invalid_symbols))


def _fetch_session_closes(
    *,
    session_date: date,
    symbols: tuple[str, ...],
    workers: int,
) -> tuple[list[DailyClose], tuple[str, ...]]:
    headers = alpaca_iex_auth_headers()
    # The all-status catalog contains longer legacy/structured-product symbols;
    # keep the encoded request below common proxy URL limits while avoiding an
    # unnecessary request per small symbol group.
    chunks = [list(symbols[index : index + 500]) for index in range(0, len(symbols), 500)]
    closes: list[DailyClose] = []
    invalid_symbols: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunks))), thread_name_prefix="population") as executor:
        futures = [
            executor.submit(_fetch_daily_chunk, chunk, session_date=session_date, headers=headers)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            chunk_closes, chunk_invalid = future.result()
            closes.extend(chunk_closes)
            invalid_symbols.update(chunk_invalid)
    return closes, tuple(sorted(invalid_symbols))


def _session_dates_from_cache(cache_root: Path) -> list[date]:
    for candidate in (
        cache_root / "alpaca-sip" / "1m" / "manifest.json",
        cache_root / "alpaca-sip" / "5m" / "manifest.json",
    ):
        if not candidate.exists():
            continue
        payload = _read_json(candidate)
        dates = payload.get("session_dates") if isinstance(payload, dict) else None
        if isinstance(dates, list) and dates:
            return sorted(date.fromisoformat(str(value)) for value in dates)
    raise ValueError("no cached session dates found; pass --start-date and --end-date")


def _parse_session_dates(args: argparse.Namespace) -> list[date]:
    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("--start-date and --end-date must be supplied together")
    if args.start_date and args.end_date:
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
        if end < start:
            raise ValueError("--end-date must be on or after --start-date")
        cached_dates = _session_dates_from_cache(args.cache_root)
        dates = [item for item in cached_dates if start <= item <= end]
        if not dates:
            raise ValueError("requested dates do not overlap cached exchange sessions")
        return dates
    return _session_dates_from_cache(args.cache_root)


def _load_or_fetch_seed(output_root: Path, *, refresh: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _asset_seed_path(output_root)
    if path.exists() and not refresh:
        seed = _read_json(path)
        if not isinstance(seed, dict) or seed.get("schema") != ASSET_SCHEMA:
            raise ValueError(f"invalid asset seed cache: {path}")
        assets = normalize_asset_payload(seed.get("assets"))
        if seed.get("asset_fingerprint") != _sha256_payload(assets):
            raise ValueError("asset seed fingerprint mismatch")
        return assets, seed
    assets, seed = _fetch_all_assets()
    _write_json(path, seed)
    return assets, seed


def _load_invalid_symbols(output_root: Path) -> set[str]:
    path = _invalid_symbols_path(output_root)
    if not path.exists():
        return set()
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"invalid data-unavailable symbol cache: {path}")
    return {str(item).upper() for item in payload}


def _load_session_envelope(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != POPULATION_SCHEMA:
        raise ValueError(f"invalid historical population cache: {path}")
    HistoricalPopulationManifest.model_validate(payload.get("manifest"))
    return payload


def _write_session_cache(
    output_root: Path,
    *,
    session_date: date,
    assets: list[dict[str, Any]],
    seed_fingerprint: str,
    closes: list[DailyClose],
) -> dict[str, Any]:
    _, envelope = build_session_manifest(
        session_date=session_date,
        assets=assets,
        closes=closes,
        asset_seed_fingerprint=seed_fingerprint,
    )
    _write_json(_session_path(output_root, session_date), envelope)
    return envelope


def _load_discovery_members(cache_root: Path, session_date: date) -> set[str]:
    path = cache_root / "discovery-index.json"
    if not path.exists():
        return set()
    payload = _read_json(path)
    session = payload.get("sessions", {}).get(session_date.isoformat(), {}) if isinstance(payload, dict) else {}
    members = session.get("membership", []) if isinstance(session, dict) else []
    output: set[str] = set()
    for item in members:
        if isinstance(item, dict) and item.get("instrument_id"):
            output.add(str(item["instrument_id"]))
    return output


def _cached_bar_symbols(cache_root: Path, session_date: date) -> set[str] | None:
    path = cache_root / "alpaca-sip" / "5m" / f"{session_date.isoformat()}.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
        raise ValueError(f"invalid 5m cache session: {path}")
    return {
        _instrument_id(str(item["symbol"]).upper())
        for item in payload["bars"]
        if isinstance(item, dict) and item.get("symbol")
    }


def verify_cached_population(
    *,
    cache_root: Path,
    session_date: date,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    allowed = set(str(item) for item in envelope["manifest"]["instrument_ids"])
    observed = _cached_bar_symbols(cache_root, session_date)
    leaderboard = _load_discovery_members(cache_root, session_date)
    previous_close_symbols = {
        _instrument_id(str(symbol).upper())
        for symbol in envelope.get("previous_closes", {})
    }
    reasons: list[str] = []
    if observed is None:
        reasons.append("five_minute_cache_missing")
        observed = set()
    # A raw bar without a prior close cannot become a Top-Gainer observation;
    # keep it visible as a coverage note but do not misclassify it as a strict
    # ranking observation.
    observed_without_prior_close = sorted(observed - previous_close_symbols)
    observed = observed & previous_close_symbols
    outside_observed = sorted(observed - allowed)
    outside_leaderboard = sorted(leaderboard - allowed)
    if outside_observed:
        reasons.append("observations_outside_population")
    return {
        "session_date": session_date.isoformat(),
        "population_fingerprint": envelope["manifest_fingerprint"],
        "population_size": len(allowed),
        "cached_observation_symbol_count": len(observed),
        "cached_leaderboard_symbol_count": len(leaderboard),
        "cached_bar_symbols_without_prior_close": observed_without_prior_close,
        "observation_symbols_outside_population": outside_observed,
        # This index predates the reconstructed manifests and is informational
        # only. The strict replay recomputes leaderboard membership causally.
        "legacy_leaderboard_symbols_outside_population": outside_leaderboard,
        "leaderboard_symbols_outside_population": [],
        "warnings": ["legacy_discovery_index_not_used_for_strict_validation"] if outside_leaderboard else [],
        "valid_for_cached_replay": not reasons,
        "reason_codes": reasons,
    }


def _build_report(
    *,
    output_root: Path,
    session_dates: list[date],
    seed: dict[str, Any],
    envelopes: list[dict[str, Any]],
    verification: list[dict[str, Any]],
) -> dict[str, Any]:
    query_filter = _read_json(_query_filter_path(output_root))
    report = {
        "schema": REPORT_SCHEMA,
        "authority": "reconstructed_alpaca_sip_population",
        "point_in_time": True,
        "outcome_conditioned": False,
        "cache_root": str(output_root),
        "asset_seed_fingerprint": seed["asset_fingerprint"],
        "asset_seed_symbol_count": seed["symbol_count"],
        "queryable_symbol_count": query_filter["queryable_symbol_count"],
        "data_unavailable_symbol_count": query_filter["data_unavailable_symbol_count"],
        "session_dates": [item.isoformat() for item in session_dates],
        "session_count": len(session_dates),
        "session_population_counts": {
            item["manifest"]["session_date"]: item["eligible_symbol_count"] for item in envelopes
        },
        "session_population_fingerprints": {
            item["manifest"]["session_date"]: item["manifest_fingerprint"] for item in envelopes
        },
        "verification": verification,
        "valid_for_cached_replay": all(item["valid_for_cached_replay"] for item in verification),
        "winner_labels_used": False,
        "selection_rule": "all-status Alpaca equity seed intersected with prior SIP daily closes queried as-of each session; no same-day outcome or winner labels",
    }
    _write_json(output_root / "manifest.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build reconstructed historical Leader Momentum population manifests.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--refresh-assets", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_root = args.cache_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    session_dates = _parse_session_dates(args)
    assets, seed = _load_or_fetch_seed(output_root, refresh=args.refresh_assets)
    invalid_symbols = _load_invalid_symbols(output_root)
    all_status_symbols = unique_asset_symbols(assets)
    queryable_symbols = queryable_seed_symbols(assets)
    symbols = tuple(item for item in queryable_symbols if item not in invalid_symbols)
    all_status_symbols_set = set(all_status_symbols)
    queryable_symbols_set = set(queryable_symbols)
    _write_json(
        _query_filter_path(output_root),
        {
            "schema": "leader-momentum-historical-population-query-filter-v1",
            "all_status_symbol_count": len(all_status_symbols),
            "queryable_symbol_count": len(queryable_symbols),
            "data_unavailable_symbol_count": len(invalid_symbols),
            "excluded_by_query_filter": sorted(all_status_symbols_set - queryable_symbols_set),
            "excluded_by_data_endpoint": sorted(invalid_symbols),
            "rule": "retain active assets; retain inactive alphabetic ordinary-equity-looking assets; exclude inactive symbols whose names identify warrants, rights, units, contra, debentures, notes, preferreds, or contingent-value rights; exclude symbols explicitly rejected by Alpaca SIP bars",
        },
    )
    print(
        json.dumps(
            {
                "phase": "asset_seed",
                "asset_count": seed["asset_count"],
                "symbol_count": len(symbols),
                "all_status_symbol_count": len(all_status_symbols),
                "queryable_symbol_count": len(queryable_symbols),
                "asset_fingerprint": seed["asset_fingerprint"],
                "data_unavailable_symbol_count": len(invalid_symbols),
                "session_count": len(session_dates),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    missing = [item for item in session_dates if not _session_path(output_root, item).exists()]
    envelopes: dict[date, dict[str, Any]] = {}
    for session_date in session_dates:
        path = _session_path(output_root, session_date)
        if path.exists():
            envelopes[session_date] = _load_session_envelope(path)
    if missing:
        print(f"Fetching prior SIP closes for {len(missing)} sessions", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(missing))), thread_name_prefix="population-session") as executor:
            futures = {
                executor.submit(
                    _fetch_session_closes,
                    session_date=session_date,
                    symbols=symbols,
                    workers=1,
                ): session_date
                for session_date in missing
            }
            for future in as_completed(futures):
                session_date = futures[future]
                closes, newly_invalid = future.result()
                invalid_symbols.update(newly_invalid)
                envelope = _write_session_cache(
                    output_root,
                    session_date=session_date,
                    assets=assets,
                    seed_fingerprint=seed["asset_fingerprint"],
                    closes=closes,
                )
                envelopes[session_date] = envelope
                print(
                    f"{session_date}: eligible={envelope['eligible_symbol_count']} manifest={envelope['manifest_fingerprint']}",
                    flush=True,
                )

        _write_json(_invalid_symbols_path(output_root), sorted(invalid_symbols))

    ordered_envelopes = [envelopes[item] for item in session_dates]
    verification = [
        verify_cached_population(
            cache_root=cache_root,
            session_date=session_date,
            envelope=envelopes[session_date],
        )
        for session_date in session_dates
    ]
    report = _build_report(
        output_root=output_root,
        session_dates=session_dates,
        seed=seed,
        envelopes=ordered_envelopes,
        verification=verification,
    )
    print(
        json.dumps(
            {
                "phase": "population_complete",
                "output_root": str(output_root),
                "session_count": len(session_dates),
                "valid_for_cached_replay": report["valid_for_cached_replay"],
                "invalid_sessions": [item["session_date"] for item in verification if not item["valid_for_cached_replay"]],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if report["valid_for_cached_replay"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
