from __future__ import annotations

"""Fill missing 1-minute cache rows identified by the strict replay.

Only the exact symbol/session gaps listed in the replay integrity artifact are
requested. Existing cached rows are preserved and the resulting session file
is merged by ``(symbol, start)``.
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.trade import run_leader_momentum_evolving_top_gainers as replay


DEFAULT_CACHE_ROOT = REPOSITORY_ROOT / "resources" / "cache" / "leader-momentum-evolving-top-gainers"
DEFAULT_INTEGRITY = REPOSITORY_ROOT / "artifacts" / "trading" / "leader-momentum-historical-population-replay" / "population-integrity.csv"


def _read_gaps(path: Path) -> dict[str, tuple[str, ...]]:
    gaps: dict[str, tuple[str, ...]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbols = tuple(sorted(item.strip().upper() for item in str(row.get("missing_one_minute_symbols") or "").split(",") if item.strip()))
            if symbols:
                gaps[str(row["session_date"])] = symbols
    return gaps


def _merge_session(path: Path, new_rows: list[replay.RawBar]) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != replay.CACHE_SCHEMA:
        raise RuntimeError(f"invalid 1m cache session: {path}")
    existing = payload.get("bars")
    if not isinstance(existing, list):
        raise RuntimeError(f"invalid 1m bar list: {path}")
    by_key: dict[tuple[str, str], dict[str, object]] = {}
    for item in existing:
        if not isinstance(item, dict):
            raise RuntimeError(f"invalid cached bar in {path}")
        key = (str(item.get("symbol") or "").upper(), str(item.get("start") or ""))
        by_key[key] = item
    before = len(by_key)
    for bar in new_rows:
        item = replay._raw_to_json(bar)
        by_key[(bar.symbol, bar.start.isoformat())] = item
    payload["bars"] = sorted(by_key.values(), key=lambda item: (str(item["symbol"]), str(item["start"])))
    payload["bar_count"] = len(payload["bars"])
    replay._write_json(path, payload)
    return len(by_key) - before


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill exact missing SIP 1-minute cache gaps.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--integrity-report", type=Path, default=DEFAULT_INTEGRITY)
    args = parser.parse_args()

    cache_root = args.cache_root.resolve()
    gaps = _read_gaps(args.integrity_report.resolve())
    if not gaps:
        print(json.dumps({"sessions": 0, "network_fetches": 0, "message": "no missing 1-minute gaps"}))
        return 0

    fetched_by_session: dict[str, list[replay.RawBar]] = defaultdict(list)
    failures: dict[str, str] = {}
    for session_date, symbols in sorted(gaps.items()):
        session_path = cache_root / "alpaca-sip" / "1m" / f"{session_date}.json"
        if not session_path.exists():
            failures[session_date] = "one_minute_session_cache_missing"
            continue
        day = replay.date.fromisoformat(session_date)
        start, end = replay._et_bounds(day, replay.time(9, 30), replay.time(16, 0))
        try:
            by_symbol = replay._fetch_chunk(list(symbols), timeframe="1m", start=start, end=end)
        except Exception as exc:
            failures[session_date] = f"{type(exc).__name__}: {exc}"
            continue
        rows = [bar for symbol in symbols for bar in by_symbol.get(symbol, ())]
        fetched_by_session[session_date].extend(rows)
        print(
            f"{session_date}: requested {len(symbols)} symbols, "
            f"received {len(rows)} bars for {len({bar.symbol for bar in rows})} symbols",
            flush=True,
        )

    merged: dict[str, int] = {}
    for session_date, rows in sorted(fetched_by_session.items()):
        if not rows:
            failures.setdefault(session_date, "no_bars_returned_for_requested_symbols")
            continue
        path = cache_root / "alpaca-sip" / "1m" / f"{session_date}.json"
        merged[session_date] = _merge_session(path, rows)

    manifest_path = cache_root / "alpaca-sip" / "1m" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bar_count"] = int(manifest.get("bar_count", 0)) + sum(merged.values())
    manifest["symbol_count"] = int(manifest.get("symbol_count", 0)) + len({bar.symbol for rows in fetched_by_session.values() for bar in rows})
    manifest["selection"] = "evolving-top20 entrants plus post-fingerprint winner diagnostics plus strict replay gap fills"
    replay._write_json(manifest_path, manifest)

    report = {
        "cache_root": str(cache_root),
        "requested_sessions": len(gaps),
        "requested_symbol_sessions": sum(len(symbols) for symbols in gaps.values()),
        "merged_new_bars": sum(merged.values()),
        "merged_sessions": merged,
        "failures": failures,
        "network_fetches": len(gaps),
        "complete": not failures and len(merged) == len(gaps),
    }
    replay._write_json(cache_root / "historical-population" / "gap-fill-report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
