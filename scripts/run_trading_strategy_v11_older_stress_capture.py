from __future__ import annotations

"""Populate the frozen V11 older stress cache one session at a time.

A historical provider hole for one symbol/session must not erase coverage from
other dates. Failed dates are reported explicitly and are never converted into
zero-trade/no-candidate observations. This runner performs no strategy search or
parameter tuning.
"""

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from app.apps.trading.us_equity_calendar import regular_holidays


def parse_args():
    parser = argparse.ArgumentParser(description="Capture older V11 stress sessions independently.")
    parser.add_argument("--start-date", default="2026-03-31")
    parser.add_argument("--end-date", default="2026-04-28")
    parser.add_argument("--dataset-cache-dir", default=".cache/trading-liquidity-datasets")
    parser.add_argument("--output-dir", default="artifacts/v11-older-stress-capture")
    parser.add_argument("--reconstruction-max-age-days", type=int, default=180)
    parser.add_argument("--initial-cash", default="100000")
    parser.add_argument("--assumed-spread-bps", default="40")
    return parser.parse_args()


def _sessions(start: date, end: date) -> list[date]:
    holidays: set[date] = set()
    for year in range(start.year, end.year + 1):
        holidays.update(regular_holidays(year))
    result: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holidays:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("end-date precedes start-date")

    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    session_rows: list[dict[str, object]] = []

    for session_date in _sessions(start, end):
        day = session_date.isoformat()
        day_output = root / "sessions" / day
        day_output.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "scripts/run_trading_strategy_liquidity_sweep_resilient.py",
            "--start-date", day,
            "--end-date", day,
            "--initial-cash", args.initial_cash,
            "--assumed-spread-bps", args.assumed_spread_bps,
            "--max-hold-minutes", "90",
            "--reconstruction-max-age-days", str(args.reconstruction_max_age_days),
            "--max-sessions", "1",
            "--thresholds", "100000",
            "--dataset-cache-dir", args.dataset_cache_dir,
            "--output-dir", str(day_output),
            "--require-covered-session",
        ]
        completed = subprocess.run(command, text=True, capture_output=True)
        (day_output / "capture.stdout.txt").write_text(completed.stdout or "", encoding="utf-8")
        (day_output / "capture.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
        row: dict[str, object] = {
            "session_date": day,
            "status": "captured" if completed.returncode == 0 else "data_unavailable",
            "return_code": completed.returncode,
        }
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-8:]
            row["detail_tail"] = tail
            print(f"{day}: data_unavailable (continuing)")
        else:
            print(f"{day}: captured")
        session_rows.append(row)

    captured = sum(row["status"] == "captured" for row in session_rows)
    unavailable = len(session_rows) - captured
    payload = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "requested_sessions": len(session_rows),
        "captured_sessions": captured,
        "data_unavailable_sessions": unavailable,
        "sessions": session_rows,
        "policy": "failed dates remain unavailable; never imputed as no-trade days",
    }
    (root / "coverage.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# V11 older stress capture coverage",
        "",
        f"- Requested trading sessions: {len(session_rows)}",
        f"- Successfully captured/reused: {captured}",
        f"- Data unavailable: {unavailable}",
        "- Failed dates are excluded from strategy metrics rather than counted as zero-trade days.",
        "- This block remains lower-fidelity reconstructed IEX evidence and is not used to tune V11.",
        "",
        "| Session | Status |",
        "|---|---|",
    ]
    lines.extend(f"| {row['session_date']} | {row['status']} |" for row in session_rows)
    (root / "coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((root / "coverage.md").read_text(encoding="utf-8"))
    # Coverage gaps are evidence, not a process failure. Return success after all
    # sessions have been attempted so the frozen validator can assess what exists.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())