"""One-off import of the file-based market evidence into PostgreSQL (WP-8.3).

    python -m app.apps.trading.evidence_import yahoo [--root resources/trading/yahoo_evidence]
    python -m app.apps.trading.evidence_import ibkr [--root resources/trading/ibkr_evidence]

Each file is recorded in ``omnix_trading_evidence_imports`` with its SHA-256,
so the import can be re-run: bar files whose content changed are imported
again (revisions are deduplicated by content, so nothing is counted twice),
while counter and session files are imported once. An IBKR session that
already has evidence in PostgreSQL is left alone. The files are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from typing import Any

from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work

from .evidence_storage import PostgresEvidenceBackend, PostgresIbkrSessionEvidence

_SESSION_COUNTERS = (
    "evaluation_count",
    "repaired_evaluation_count",
    "genuinely_blocked_evaluation_count",
    "passed_evaluation_count",
    "repaired_but_blocked_evaluation_count",
)


class ImportLedger:
    def __init__(self, uow_factory: Callable[[], AbstractContextManager[PostgresUnitOfWork]] = unit_of_work) -> None:
        self.uow_factory = uow_factory

    def imported_sha(self, source: str) -> str | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                "SELECT content_sha256 FROM omnix_trading_evidence_imports WHERE source = %s", (source,)
            ).fetchone()
        return str(row[0]) if row else None

    def record(self, source: str, kind: str, digest: str) -> None:
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_evidence_imports (source, kind, content_sha256)
                VALUES (%s, %s, %s)
                ON CONFLICT (source) DO UPDATE
                   SET content_sha256 = EXCLUDED.content_sha256, imported_at = CURRENT_TIMESTAMP
                """,
                (source, kind, digest),
            )
            uow.commit()


def _read(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _revisions(value: object) -> Iterator[dict[str, Any]]:
    """Legacy single-record buckets and schema-v2 revision lists."""
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))


_BAR_KEYS = ("start_time", "end_time", "open", "high", "low", "close", "received_at")


def import_yahoo(
    root: Path,
    *,
    backend: PostgresEvidenceBackend | None = None,
    ledger: ImportLedger | None = None,
    report: Callable[[str], None] = print,
) -> dict[str, int]:
    backend = backend or PostgresEvidenceBackend()
    ledger = ledger or ImportLedger()
    totals = {"files": 0, "skipped_files": 0, "revisions": 0, "malformed_records": 0}
    for path in sorted((root / "bars").glob("*/*.json")):
        source = f"yahoo:{path.relative_to(root).as_posix()}"
        payload, digest = _read(path)
        if ledger.imported_sha(source) == digest:
            totals["skipped_files"] += 1
            continue
        symbol = str(payload.get("symbol") or "").upper()
        session_date = date.fromisoformat(str(payload.get("session_date")))
        records = []
        for value in (payload.get("bars") or {}).values():
            for record in _revisions(value):
                if all(record.get(key) for key in _BAR_KEYS):
                    records.append(record)
                else:
                    totals["malformed_records"] += 1
        for start in range(0, len(records), 5_000):
            totals["revisions"] += backend.add_revisions(symbol, session_date, records[start:start + 5_000])
        ledger.record(source, "yahoo_bars", digest)
        totals["files"] += 1
        if totals["files"] % 50 == 0:
            report(json.dumps({"progress": totals}))

    for path in sorted((root / "sessions").glob("*.json")):
        source = f"yahoo:{path.relative_to(root).as_posix()}"
        if ledger.imported_sha(source) is not None:
            totals["skipped_files"] += 1
            continue
        payload, digest = _read(path)
        deltas = {name: int(payload.get(name) or 0) for name in _SESSION_COUNTERS}
        for reason, count in (payload.get("blocked_reason_counts") or {}).items():
            deltas[f"blocked_reason:{reason}"] = int(count or 0)
        backend.add_session_counters("yahoo", date.fromisoformat(path.stem), deltas)
        ledger.record(source, "yahoo_session_counters", digest)
        totals["files"] += 1

    diagnostics = root / "diagnostics.json"
    source = "yahoo:diagnostics.json"
    if diagnostics.exists() and ledger.imported_sha(source) is None:
        payload, digest = _read(diagnostics)
        backend.add_counters("yahoo", {name: int(value or 0) for name, value in payload.items() if isinstance(value, int)})
        ledger.record(source, "yahoo_counters", digest)
        totals["files"] += 1
    return totals


def import_ibkr(
    root: Path,
    *,
    sessions: PostgresIbkrSessionEvidence | None = None,
    ledger: ImportLedger | None = None,
) -> dict[str, int]:
    sessions = sessions or PostgresIbkrSessionEvidence()
    ledger = ledger or ImportLedger()
    totals = {"files": 0, "skipped_files": 0}
    for path in sorted((root / "sessions").glob("*.json")):
        source = f"ibkr:{path.relative_to(root).as_posix()}"
        session_date = date.fromisoformat(path.stem)
        if ledger.imported_sha(source) is not None or sessions.read(session_date) is not None:
            totals["skipped_files"] += 1
            continue
        payload, digest = _read(path)
        sessions.apply(session_date, payload, [])
        ledger.record(source, "ibkr_session", digest)
        totals["files"] += 1
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.apps.trading.evidence_import", description=__doc__.splitlines()[0])
    parser.add_argument("provider", choices=("yahoo", "ibkr"))
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)

    from app.persistence.startup import bootstrap_postgresql_runtime

    bootstrap_postgresql_runtime()
    root = args.root or Path(f"resources/trading/{args.provider}_evidence")
    totals = import_yahoo(root) if args.provider == "yahoo" else import_ibkr(root)
    print(json.dumps({"provider": args.provider, "root": str(root), **totals}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
