from __future__ import annotations

"""Fetch daily top-five gainers from Stock Market Watch digest pages.

The resulting CSV is an outcome-labelled research universe.  It is intended
for the deterministic interday replay and is never passed into live execution.
"""

import argparse
import csv
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests


_DIGEST_URL = "https://stockmarketwatch.com/digest/{session_date}"
_USER_AGENT = "Omnix research cache builder/1.0"
_GAINERS_HEADING = re.compile(
    r"<p[^>]*>\s*Gainers\s*</p>(?P<section>.*?)<p[^>]*>\s*Losers\s*</p>",
    re.IGNORECASE | re.DOTALL,
)
_GAINER_ENTRY = re.compile(
    r'<a href="/stock/(?P<symbol>[^"]+)"[^>]*>[^<]+</a>.*?'
    r'<strong[^>]*>\+(?P<gain>\d+(?:\.\d+)?)%</strong>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class DigestResult:
    session_date: date
    rows: tuple[dict[str, object], ...]
    missing_reason: str | None = None


def _weekday_dates(start: date, end: date) -> list[date]:
    current = start
    dates: list[date] = []
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _fetch_digest(session_date: date, top_n: int) -> DigestResult:
    url = _DIGEST_URL.format(session_date=session_date.isoformat())
    last_reason = "request failed"
    for attempt in range(4):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=60,
            )
            if response.status_code == 404:
                return DigestResult(session_date, (), "HTTP 404")
            if response.status_code in {429, 500, 502, 503, 504}:
                last_reason = f"HTTP {response.status_code}"
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
                return DigestResult(session_date, (), last_reason)
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            last_reason = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                time.sleep(2**attempt)
                continue
            return DigestResult(session_date, (), last_reason)
    else:
        return DigestResult(session_date, (), last_reason)

    heading = re.search(
        r"<h2[^>]*>\s*The day(?:['’]|&#x27;|&#39;)s top movers\s*</h2>",
        response.text,
        re.IGNORECASE,
    )
    if heading is None:
        return DigestResult(session_date, (), "top-movers heading not found")
    section_match = _GAINERS_HEADING.search(response.text, heading.end())
    if section_match is None:
        return DigestResult(session_date, (), "gainers section not found")

    entries = _GAINER_ENTRY.findall(section_match.group("section"))
    rows = tuple(
        {
            "session_date": session_date.isoformat(),
            "rank": rank,
            "symbol": symbol.upper(),
            "gain_pct": gain,
            "source_url": url,
        }
        for rank, (symbol, gain) in enumerate(entries[:top_n], start=1)
    )
    if len(rows) < top_n:
        return DigestResult(
            session_date,
            rows,
            f"only {len(rows)} gainers parsed; expected {top_n}",
        )
    return DigestResult(session_date, rows)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "session_date",
        "rank",
        "symbol",
        "gain_pct",
        "source_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Stock Market Watch daily top-five gainers."
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Write available pages while reporting missing weekday pages.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start > args.end:
        raise SystemExit("--start must be on or before --end")
    if args.top_n <= 0:
        raise SystemExit("--top-n must be positive")

    session_dates = _weekday_dates(args.start, args.end)
    results: list[DigestResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(_fetch_digest, session_date, args.top_n): session_date
            for session_date in session_dates
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            status = "ok" if result.missing_reason is None else result.missing_reason
            print(f"{result.session_date}: {status}", flush=True)

    results.sort(key=lambda item: item.session_date)
    missing = [item for item in results if item.missing_reason is not None]
    if missing and not args.allow_missing:
        details = ", ".join(
            f"{item.session_date.isoformat()} ({item.missing_reason})"
            for item in missing
        )
        raise RuntimeError(
            "digest pages were missing or malformed; rerun with --allow-missing "
            f"only after review: {details}"
        )

    rows = [row for result in results for row in result.rows]
    _write_csv(args.output, rows)
    print(
        f"Wrote {len(rows)} rows across {len(results) - len(missing)} digest sessions "
        f"to {args.output}",
        flush=True,
    )
    if missing:
        print(
            "Skipped missing weekday pages: "
            + ", ".join(item.session_date.isoformat() for item in missing),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
