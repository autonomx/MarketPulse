"""Importing Omnix's market reference data (roadmap MP-10.1), between two disposable databases."""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from marketpulse.import_omnix import ReferenceImportError, import_reference

pytestmark = pytest.mark.postgres

ADMIN_URL = os.environ.get("OMNIX_TEST_DATABASE_URL", "")
SCHEMA = """
CREATE TABLE omnix_trading_company_profiles (ticker text PRIMARY KEY, name text NOT NULL, fetched_at timestamptz DEFAULT now());
COMMENT ON TABLE omnix_trading_company_profiles IS 'omnix:tenant-exempt: public company reference data';
CREATE TABLE omnix_trading_market_breadth (exchange text, session_date date, advancers int NOT NULL,
    PRIMARY KEY (exchange, session_date));
COMMENT ON TABLE omnix_trading_market_breadth IS 'omnix:tenant-exempt: market-wide breadth';
CREATE TABLE omnix_user_passwords (user_id text PRIMARY KEY, hash text NOT NULL);
COMMENT ON TABLE omnix_user_passwords IS 'omnix:tenant-exempt: credentials';
CREATE TABLE omnix_trading_paper_accounts (id text PRIMARY KEY, owner text NOT NULL);
"""


def _database_url(name: str) -> str:
    parts = urlsplit(ADMIN_URL)
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture
def databases():
    if not ADMIN_URL:
        pytest.skip("OMNIX_TEST_DATABASE_URL is not set")
    import psycopg

    names = [f"mp_import_{role}_{uuid.uuid4().hex[:8]}" for role in ("omnix", "marketpulse")]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        for name in names:
            admin.execute(f'CREATE DATABASE "{name}"')
    urls = [_database_url(name) for name in names]
    for url in urls:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(SCHEMA)
    try:
        yield urls
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            for name in names:
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _rows(url: str, sql: str) -> list[tuple]:
    import psycopg

    with psycopg.connect(url) as connection:
        return connection.execute(sql).fetchall()


def _execute(url: str, sql: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql)


def test_a_dry_run_reports_and_changes_nothing_then_the_import_is_idempotent(databases):
    omnix, marketpulse = databases
    _execute(omnix, """
        INSERT INTO omnix_trading_company_profiles (ticker, name) VALUES ('AAPL', 'Apple from Omnix'), ('MSFT', 'Microsoft');
        INSERT INTO omnix_trading_market_breadth VALUES ('NASDAQ', '2026-10-15', 2100), ('NYSE', '2026-10-15', 1500);
        INSERT INTO omnix_user_passwords VALUES ('owner', 'secret-hash');
        INSERT INTO omnix_trading_paper_accounts VALUES ('acct', 'owner');
    """)
    # MarketPulse already fetched AAPL itself: its row is kept.
    _execute(marketpulse, "INSERT INTO omnix_trading_company_profiles (ticker, name) VALUES ('AAPL', 'Apple from MarketPulse')")

    dry = import_reference(omnix, marketpulse, apply=False)

    assert {(report["table"], report["source_rows"], report["new_rows"]) for report in dry} == {
        ("omnix_trading_company_profiles", 2, 1), ("omnix_trading_market_breadth", 2, 2)}
    assert _rows(marketpulse, "SELECT count(*) FROM omnix_trading_market_breadth") == [(0,)]

    applied = import_reference(omnix, marketpulse, apply=True)
    again = import_reference(omnix, marketpulse, apply=True)

    assert [report["new_rows"] for report in applied] == [1, 2]
    assert [report["new_rows"] for report in again] == [0, 0]
    assert _rows(marketpulse, "SELECT ticker, name FROM omnix_trading_company_profiles ORDER BY ticker") == [
        ("AAPL", "Apple from MarketPulse"), ("MSFT", "Microsoft")]
    assert _rows(marketpulse, "SELECT count(*) FROM omnix_trading_market_breadth") == [(2,)]
    # Credentials and account data are never read, marker or not.
    assert _rows(marketpulse, "SELECT count(*) FROM omnix_user_passwords") == [(0,)]
    assert _rows(marketpulse, "SELECT count(*) FROM omnix_trading_paper_accounts") == [(0,)]


def test_the_omnix_side_is_opened_read_only(databases, monkeypatch):
    import psycopg

    omnix, marketpulse = databases
    _execute(omnix, "INSERT INTO omnix_trading_company_profiles (ticker, name) VALUES ('NVDA', 'Nvidia')")
    opened = {}
    real_connect = psycopg.connect

    def connect(url, **kwargs):
        connection = real_connect(url, **kwargs)
        opened[url] = connection.execute("SHOW default_transaction_read_only").fetchone()[0]
        connection.rollback()
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    import_reference(omnix, marketpulse, apply=True)

    # The importer's own Omnix connection refuses writes; its MarketPulse one may write.
    assert opened == {omnix: "on", marketpulse: "off"}


def test_a_column_marketpulse_requires_and_omnix_lacks_stops_the_import(databases):
    omnix, marketpulse = databases
    _execute(marketpulse, "ALTER TABLE omnix_trading_market_breadth ADD COLUMN decliners int NOT NULL")

    with pytest.raises(ReferenceImportError, match="decliners"):
        import_reference(omnix, marketpulse, apply=True)


def test_a_source_without_reference_tables_is_refused(databases):
    omnix, marketpulse = databases
    _execute(omnix, "DROP TABLE omnix_trading_company_profiles, omnix_trading_market_breadth")

    with pytest.raises(ReferenceImportError, match="no market reference tables"):
        import_reference(omnix, marketpulse, apply=False)
