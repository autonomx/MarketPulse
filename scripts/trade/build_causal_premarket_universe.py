from __future__ import annotations

"""Build a causal premarket-gapper universe from the cached interday tapes.

For each session, every cached symbol is ranked by its premarket gap at the
scan time (last premarket price from 5m bars that finished by the scan time,
versus the prior regular-session close). Nothing after the scan time is used,
so unlike the end-of-day top-gainer lists the daily selection has no lookahead.
The symbol pool is still every symbol the cache holds, which was assembled from
historical top-gainer lists; that pool-level bias remains and is reported.

Output columns are compatible with the interday replay input
(``session_date,rank,symbol,gain_pct,source_url``) plus the scan evidence.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
PREMARKET_OPEN = time(4, 0)
FIVE_MINUTES = timedelta(minutes=5)


def _bars(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("bars") or ())


def _start_et(bar: dict[str, object]) -> datetime:
    return datetime.fromisoformat(str(bar["start"])).astimezone(ET)


def _load(cache_dir: Path) -> dict[str, dict[date, list[dict[str, object]]]]:
    tapes: dict[str, dict[date, list[dict[str, object]]]] = {}
    for symbol_dir in sorted(p for p in cache_dir.iterdir() if (p / "5m").is_dir()):
        by_date: dict[date, list[dict[str, object]]] = {}
        for path in (symbol_dir / "5m").glob("*.json"):
            try:
                session = date.fromisoformat(path.stem)
            except ValueError:
                continue
            bars = _bars(path)
            if bars:
                by_date[session] = bars
        tapes[symbol_dir.name] = by_date
    return tapes


def _regular(bars: list[dict[str, object]]) -> list[dict[str, object]]:
    return [b for b in bars if REGULAR_OPEN <= _start_et(b).time() < REGULAR_CLOSE]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default="resources/cache/interday-market-data/alpaca-sip")
    parser.add_argument("--sessions-from", required=True, help="Replay input whose session dates to cover.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--scan-time-et", default="09:15")
    parser.add_argument("--min-price", type=Decimal, default=Decimal("0.50"))
    parser.add_argument("--max-price", type=Decimal, default=Decimal("20"))
    parser.add_argument("--min-premarket-dollar-volume", type=Decimal, default=Decimal("500000"))
    args = parser.parse_args()

    scan = time.fromisoformat(args.scan_time_et)
    with open(args.sessions_from, newline="", encoding="utf-8-sig") as handle:
        reference = list(csv.DictReader(handle))
    sessions = sorted({date.fromisoformat(row["session_date"]) for row in reference})
    eod_by_session: dict[date, set[str]] = defaultdict(set)
    for row in reference:
        eod_by_session[date.fromisoformat(row["session_date"])].add(row["symbol"].strip().upper())

    tapes = _load(Path(args.cache_dir))
    regular_days = Counter(d for by_date in tapes.values() for d, bars in by_date.items() if _regular(bars))
    calendar = sorted(d for d, count in regular_days.items() if count >= len(tapes) // 4)

    rows: list[dict[str, object]] = []
    overlap: list[float] = []
    for session in sessions:
        prior_days = [d for d in calendar if d < session]
        if not prior_days:
            continue
        prior = prior_days[-1]
        scan_at = datetime.combine(session, scan, tzinfo=ET)
        scored = []
        for symbol, by_date in tapes.items():
            prior_regular = _regular(by_date.get(prior, []))
            # Only 5m bars that had finished by the scan time.
            premarket = [
                b
                for b in by_date.get(session, [])
                if _start_et(b).date() == session
                and PREMARKET_OPEN <= _start_et(b).time()
                and _start_et(b) + FIVE_MINUTES <= scan_at
            ]
            if not prior_regular or not premarket:
                continue
            prior_close = Decimal(str(prior_regular[-1]["close"]))
            price = Decimal(str(premarket[-1]["close"]))
            dollar_volume = sum(
                (Decimal(str(b["close"])) * Decimal(str(b["volume"])) for b in premarket), Decimal("0")
            )
            if prior_close <= 0 or not (args.min_price <= price <= args.max_price):
                continue
            if dollar_volume < args.min_premarket_dollar_volume:
                continue
            gap = (price / prior_close - 1) * 100
            scored.append((gap, dollar_volume, symbol, price, prior_close))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        chosen = scored[: args.top]
        for rank, (gap, dollar_volume, symbol, price, prior_close) in enumerate(chosen, start=1):
            rows.append(
                {
                    "session_date": session.isoformat(),
                    "rank": rank,
                    "symbol": symbol,
                    "gain_pct": "",
                    "source_url": f"causal:premarket-gap@{args.scan_time_et}ET",
                    "premarket_gap_pct": f"{gap:.2f}",
                    "premarket_price": str(price),
                    "prior_close": str(prior_close),
                    "premarket_dollar_volume": f"{dollar_volume:.0f}",
                }
            )
        if chosen:
            overlap.append(len({c[2] for c in chosen} & eod_by_session[session]) / len(chosen))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(str(r["session_date"]) for r in rows)
    print(
        json.dumps(
            {
                "output": output.as_posix(),
                "symbol_pool": len(tapes),
                "sessions": len(counts),
                "short_sessions": {k: v for k, v in counts.items() if v < args.top},
                "mean_overlap_with_eod_list": round(sum(overlap) / len(overlap), 3) if overlap else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
