"""Build a Stock Market Watch top-eight cohort and its Alpaca SIP cache.

The source universe is intentionally outcome-labelled research data: each
session's eight symbols come from Stock Market Watch's completed-day digest.
This script records the source URL beside every row and stores the requested
5-minute and regular-session 1-minute bars in the existing interday cache.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.trade import run_interday_winner_shadow_replay_core as core


SOURCE_URL = "https://stockmarketwatch.com/digest/{session_date}"
SOURCE_NAME = "stockmarketwatch-digest"
TOP_GAINER_COUNT = 8
ET = core.ET
UTC = core.UTC


def _business_dates(first: date, last: date) -> Iterable[date]:
    current = first
    while current <= last:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def parse_digest_top_gainers(html: str) -> list[tuple[str, Decimal]]:
    """Extract the ordered eight gainers from the digest's movers section."""

    marker = re.search(r"The day(?:'|&#x27;|&#39;)s top movers", html)
    if marker is None:
        raise ValueError("digest does not contain the top-movers section")
    section = html[marker.start() :]
    loser_marker = section.find("Losers")
    if loser_marker >= 0:
        section = section[:loser_marker]

    ticker_pattern = re.compile(
        r"(?:href=\\?[\"']?/stock/|/stock/)"
        r"([A-Z][A-Z0-9._-]*)"
    )
    percentage_pattern = re.compile(r"\+([0-9]+(?:\.[0-9]+)?)%")
    gainers: list[tuple[str, Decimal]] = []
    seen: set[str] = set()
    for match in ticker_pattern.finditer(section):
        symbol = match.group(1).upper()
        if symbol in seen:
            continue
        percentage = percentage_pattern.search(section[match.end() : match.end() + 800])
        if percentage is None:
            continue
        gainers.append((symbol, Decimal(percentage.group(1))))
        seen.add(symbol)
        if len(gainers) == TOP_GAINER_COUNT:
            break
    if len(gainers) != TOP_GAINER_COUNT:
        raise ValueError(f"expected {TOP_GAINER_COUNT} gainers, found {len(gainers)}")
    return gainers


def collect_top_gainers(
    first: date,
    last: date,
    *,
    session: requests.Session | None = None,
) -> list[dict[str, object]]:
    client = session or requests.Session()
    rows: list[dict[str, object]] = []
    skipped: list[str] = []
    for session_date in _business_dates(first, last):
        source_url = SOURCE_URL.format(session_date=session_date.isoformat())
        response = client.get(
            source_url,
            headers={"User-Agent": "Omnix interday shadow research/1.0"},
            timeout=45,
        )
        if response.status_code == 404:
            skipped.append(session_date.isoformat())
            continue
        response.raise_for_status()
        try:
            gainers = parse_digest_top_gainers(response.text)
        except ValueError:
            skipped.append(session_date.isoformat())
            continue
        for rank, (symbol, gain_pct) in enumerate(gainers, start=1):
            rows.append(
                {
                    "session_date": session_date.isoformat(),
                    "rank": rank,
                    "symbol": symbol,
                    "gain_pct": str(gain_pct),
                    "source_url": source_url,
                }
            )
    if skipped:
        print(f"Skipped digest dates without eight gainers: {', '.join(skipped)}")
    return rows


def write_cohort(rows: list[dict[str, object]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("session_date", "rank", "symbol", "gain_pct", "source_url"),
        )
        writer.writeheader()
        writer.writerows(rows)


def _fetch_and_cache_symbol(
    symbol: str,
    first_session: date,
    last_session: date,
    cache_dir: Path,
    source: str,
) -> tuple[str, str, int, int]:
    session = requests.Session()
    cache = core.MarketDataCache(cache_dir, source)
    five_start = datetime.combine(
        first_session - timedelta(days=30), time(0), tzinfo=ET
    ).astimezone(UTC)
    five_end = datetime.combine(
        last_session + timedelta(days=1), time(0), tzinfo=ET
    ).astimezone(UTC)
    one_start = datetime.combine(first_session, time(9, 30), tzinfo=ET).astimezone(UTC)
    one_end = datetime.combine(
        last_session + timedelta(days=1), time(16), tzinfo=ET
    ).astimezone(UTC)

    five_raw = cache.load(
        symbol,
        "5m",
        start=five_start,
        end=five_end,
        query_profile="extended_session",
    )
    five_status = "cached"
    if five_raw is None:
        _meta, five_raw = core._fetch_alpaca_bars(
            session,
            symbol,
            timeframe="5Min",
            start=five_start,
            end=five_end,
            label=f"{symbol} SIP 5m",
        )
        cache.store(
            symbol,
            "5m",
            five_raw,
            start=five_start,
            end=five_end,
            query_profile="extended_session",
        )
        five_status = "fetched"

    one_raw = cache.load(
        symbol,
        "1m",
        start=one_start,
        end=one_end,
        query_profile="regular_session",
    )
    one_status = "cached"
    if one_raw is None:
        _meta, one_raw = core._fetch_alpaca_bars(
            session,
            symbol,
            timeframe="1Min",
            start=one_start,
            end=one_end,
            label=f"{symbol} SIP 1m",
        )
        cache.store(
            symbol,
            "1m",
            one_raw,
            start=one_start,
            end=one_end,
            query_profile="regular_session",
        )
        one_status = "fetched"
    return symbol, f"5m={five_status},1m={one_status}", len(five_raw), len(one_raw)


def populate_cache(
    rows: list[dict[str, object]],
    *,
    cache_dir: Path,
    source: str,
    workers: int,
) -> None:
    sessions = sorted({date.fromisoformat(str(row["session_date"])) for row in rows})
    symbols = sorted({str(row["symbol"]) for row in rows})
    if not sessions or not symbols:
        raise ValueError("cohort contains no sessions or symbols")
    print(
        f"Caching {len(symbols)} unique symbols across "
        f"{len(sessions)} sessions ({sessions[0]} through {sessions[-1]})"
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _fetch_and_cache_symbol,
                symbol,
                sessions[0],
                sessions[-1],
                cache_dir,
                source,
            ): symbol
            for symbol in symbols
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                name, status, five_count, one_count = future.result()
            except Exception as exc:
                raise RuntimeError(f"{symbol}: cache population failed: {exc}") from exc
            print(
                f"[{index}/{len(symbols)}] {name}: {status}; "
                f"bars 5m={five_count}, 1m={one_count}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--cohort-output", type=Path, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("resources/cache/interday-market-data"),
    )
    parser.add_argument("--source", default="alpaca-sip")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    if args.workers < 1:
        parser.error("--workers must be positive")

    rows = collect_top_gainers(args.start, args.end)
    write_cohort(rows, args.cohort_output)
    print(f"Wrote {len(rows)} cohort rows to {args.cohort_output}")
    populate_cache(
        rows,
        cache_dir=args.cache_dir,
        source=args.source,
        workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
