"""Import Omnix's market reference data into MarketPulse (roadmap MP-10.1).

The tables are the trading tables that Omnix marks tenant-exempt (migration 0154): public company profiles,
financials, filing snapshots, corporate events and market breadth, with no workspace, user or account data. Only
``omnix_trading_*`` tables carrying the marker qualify; other tenant-exempt tables (accounts, passwords, invites) are
never read.

- The Omnix side is read in a read-only transaction.
- Rows are keyed by each table's primary key (ticker, CIK, exchange and session), and a row MarketPulse already has
  is kept: re-running the import changes nothing.
- A dry run (the default) loads everything into temporary tables, reports what would be added, and rolls back.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MARKER = "omnix:tenant-exempt:"
PREFIX = "omnix_trading_"

_TABLES = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_description d ON d.objoid = c.oid AND d.classoid = 'pg_class'::regclass AND d.objsubid = 0
WHERE c.relkind = 'r' AND c.relname LIKE 'omnix\\_trading\\_%%' AND d.description LIKE %s
ORDER BY c.relname
"""
_COLUMNS = """
SELECT a.attname, a.attnotnull, a.atthasdef OR a.attidentity <> ''
FROM pg_attribute a
WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
ORDER BY a.attnum
"""
_PRIMARY_KEY = """
SELECT a.attname
FROM pg_index i
JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
WHERE i.indrelid = %s::regclass AND i.indisprimary
ORDER BY array_position(i.indkey, a.attnum)
"""


class ReferenceImportError(RuntimeError):
    """The import cannot run: no shared tables, or a table MarketPulse requires a column Omnix lacks."""


@dataclass
class TableReport:
    table: str
    source_rows: int
    new_rows: int
    columns: int


def reference_tables(connection: Any) -> list[str]:
    return [row[0] for row in connection.execute(_TABLES, (MARKER + "%",)).fetchall()]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _copy_table(source: Any, target: Any, table: str) -> tuple[list[str], int]:
    """Loads the Omnix table into a temporary ``import_rows`` table on the target; returns (columns, rows)."""
    source_columns = {row[0] for row in source.execute(_COLUMNS, (table,)).fetchall()}
    target_columns = target.execute(_COLUMNS, (table,)).fetchall()
    missing = [name for name, not_null, has_default in target_columns
               if not_null and not has_default and name not in source_columns]
    if missing:
        raise ReferenceImportError(f"{table}: Omnix has no {', '.join(missing)} (update Omnix or MarketPulse first)")
    columns = [name for name, _, _ in target_columns if name in source_columns]
    column_list = ", ".join(_quote(name) for name in columns)
    target.execute("DROP TABLE IF EXISTS import_rows")
    target.execute(f"CREATE TEMP TABLE import_rows AS SELECT {column_list} FROM {_quote(table)} WITH NO DATA")
    with source.cursor().copy(f"COPY (SELECT {column_list} FROM {_quote(table)}) TO STDOUT") as reader, \
            target.cursor().copy(f"COPY import_rows ({column_list}) FROM STDIN") as writer:
        for block in reader:
            writer.write(block)
    rows = target.execute("SELECT count(*) FROM import_rows").fetchone()[0]
    return columns, rows


def import_reference(source_url: str, target_url: str, *, apply: bool) -> list[dict[str, Any]]:
    """Per table: rows in Omnix, rows MarketPulse lacks (added when ``apply``), columns copied."""
    import psycopg

    reports: list[TableReport] = []
    with psycopg.connect(source_url, options="-c default_transaction_read_only=on") as source, \
            psycopg.connect(target_url) as target:
        shared = [table for table in reference_tables(source) if table in set(reference_tables(target))]
        if not shared:
            raise ReferenceImportError("no market reference tables in common: is the source an Omnix database?")
        for table in shared:
            columns, rows = _copy_table(source, target, table)
            key = [row[0] for row in target.execute(_PRIMARY_KEY, (table,)).fetchall()]
            if not key:
                raise ReferenceImportError(f"{table} has no primary key")
            match = " AND ".join(f"existing.{_quote(name)} = incoming.{_quote(name)}" for name in key)
            new_rows = target.execute(
                f"SELECT count(*) FROM import_rows incoming WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_quote(table)} existing WHERE {match})").fetchone()[0]
            if apply and new_rows:
                column_list = ", ".join(_quote(name) for name in columns)
                target.execute(f"INSERT INTO {_quote(table)} ({column_list}) SELECT {column_list} FROM import_rows "
                               f"ON CONFLICT DO NOTHING")
            target.execute("DROP TABLE import_rows")
            reports.append(TableReport(table=table, source_rows=rows, new_rows=new_rows, columns=len(columns)))
        if apply:
            target.commit()
        else:
            target.rollback()
        source.rollback()
    return [asdict(report) for report in reports]


__all__ = ["MARKER", "PREFIX", "ReferenceImportError", "TableReport", "import_reference", "reference_tables"]
