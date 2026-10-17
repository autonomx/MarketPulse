"""Importing an Omnix workspace's own trading data (roadmap MP-10.2), between two disposable databases.

The schema is a cut-down copy of Omnix's: the same tables, keys and foreign keys, fewer columns.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from marketpulse.import_omnix_accounts import AccountImportError, import_account_data

pytestmark = pytest.mark.postgres

ADMIN_URL = os.environ.get("OMNIX_TEST_DATABASE_URL", "")
SCHEMA = """
CREATE TABLE omnix_users (id text PRIMARY KEY);
CREATE TABLE omnix_workspaces (id text PRIMARY KEY);
CREATE TABLE omnix_module_records (
    workspace_id text NOT NULL REFERENCES omnix_workspaces(id), module text NOT NULL, record_type text NOT NULL,
    record_id text NOT NULL, owner_user_id text REFERENCES omnix_users(id), payload jsonb NOT NULL, revision bigint NOT NULL,
    PRIMARY KEY (workspace_id, module, record_type, record_id));
CREATE TABLE omnix_trading_script_versions (
    workspace_id text NOT NULL, script_id text NOT NULL, revision bigint NOT NULL, source text NOT NULL,
    PRIMARY KEY (workspace_id, script_id, revision));
CREATE TABLE omnix_trading_alerts (
    workspace_id text NOT NULL REFERENCES omnix_workspaces(id), alert_id text NOT NULL,
    owner_user_id text REFERENCES omnix_users(id), message text NOT NULL, PRIMARY KEY (workspace_id, alert_id));
CREATE TABLE omnix_trading_alert_conditions (
    workspace_id text NOT NULL, alert_id text NOT NULL, position int NOT NULL, operator text NOT NULL,
    PRIMARY KEY (workspace_id, alert_id, position),
    FOREIGN KEY (workspace_id, alert_id) REFERENCES omnix_trading_alerts(workspace_id, alert_id) ON DELETE CASCADE);
CREATE TABLE omnix_trading_paper_accounts (
    workspace_id text NOT NULL REFERENCES omnix_workspaces(id), account_id text NOT NULL,
    owner_user_id text REFERENCES omnix_users(id), name text NOT NULL, PRIMARY KEY (workspace_id, account_id));
CREATE TABLE omnix_trading_paper_simulation_epochs (
    workspace_id text NOT NULL, account_id text NOT NULL, epoch_id text NOT NULL,
    PRIMARY KEY (workspace_id, account_id, epoch_id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES omnix_trading_paper_accounts(workspace_id, account_id));
CREATE TABLE omnix_trading_paper_orders (
    workspace_id text NOT NULL, account_id text NOT NULL, order_id text NOT NULL, side text NOT NULL,
    PRIMARY KEY (workspace_id, account_id, order_id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES omnix_trading_paper_accounts(workspace_id, account_id));
CREATE TABLE omnix_trading_paper_fills (
    workspace_id text NOT NULL, account_id text NOT NULL, fill_id text NOT NULL, order_id text NOT NULL,
    PRIMARY KEY (workspace_id, account_id, fill_id),
    FOREIGN KEY (workspace_id, account_id, order_id) REFERENCES omnix_trading_paper_orders(workspace_id, account_id, order_id));
CREATE TABLE omnix_trading_paper_equity_snapshots (
    snapshot_id bigserial PRIMARY KEY, workspace_id text NOT NULL, account_id text NOT NULL, epoch_id text NOT NULL,
    equity numeric NOT NULL,
    FOREIGN KEY (workspace_id, account_id, epoch_id)
        REFERENCES omnix_trading_paper_simulation_epochs(workspace_id, account_id, epoch_id));
"""
OMNIX_DATA = """
INSERT INTO omnix_users VALUES ('user:omnix-owner'), ('user:someone-else');
INSERT INTO omnix_workspaces VALUES ('workspace:omnix'), ('workspace:other');
INSERT INTO omnix_module_records VALUES
  ('workspace:omnix', 'trading', 'workspace', 'layout-1', 'user:omnix-owner', '{"panes": 4}', 3),
  ('workspace:omnix', 'trading', 'script', 'script-1', 'user:omnix-owner', '{"name": "RSI"}', 2),
  ('workspace:omnix', 'rpg', 'save', 'slot-1', 'user:omnix-owner', '{}', 1),
  ('workspace:other', 'trading', 'workspace', 'layout-x', 'user:someone-else', '{}', 1);
INSERT INTO omnix_trading_script_versions VALUES
  ('workspace:omnix', 'script-1', 1, 'plot(close)'), ('workspace:omnix', 'script-1', 2, 'plot(rsi(close))');
INSERT INTO omnix_trading_alerts VALUES
  ('workspace:omnix', 'alert-1', 'user:omnix-owner', 'from Omnix'),
  ('workspace:omnix', 'alert-2', 'user:omnix-owner', 'also from Omnix'),
  ('workspace:other', 'alert-x', 'user:someone-else', 'not ours');
INSERT INTO omnix_trading_alert_conditions VALUES
  ('workspace:omnix', 'alert-1', 0, 'crossing_up'), ('workspace:omnix', 'alert-1', 1, 'greater'),
  ('workspace:omnix', 'alert-2', 0, 'less');
INSERT INTO omnix_trading_paper_accounts VALUES ('workspace:omnix', 'paper-1', 'user:omnix-owner', 'Swing');
INSERT INTO omnix_trading_paper_simulation_epochs VALUES ('workspace:omnix', 'paper-1', 'epoch-1');
INSERT INTO omnix_trading_paper_orders VALUES ('workspace:omnix', 'paper-1', 'order-1', 'buy');
INSERT INTO omnix_trading_paper_fills VALUES ('workspace:omnix', 'paper-1', 'fill-1', 'order-1');
INSERT INTO omnix_trading_paper_equity_snapshots (snapshot_id, workspace_id, account_id, epoch_id, equity)
  VALUES (900, 'workspace:omnix', 'paper-1', 'epoch-1', 100000);
"""
MARKETPULSE_DATA = """
INSERT INTO omnix_users VALUES ('user:local');
INSERT INTO omnix_workspaces VALUES ('workspace:local');
-- MarketPulse already has an alert-2 of its own, with one condition.
INSERT INTO omnix_trading_alerts VALUES ('workspace:local', 'alert-2', 'user:local', 'MarketPulse''s own');
INSERT INTO omnix_trading_alert_conditions VALUES ('workspace:local', 'alert-2', 0, 'greater');
"""


def _database_url(name: str) -> str:
    return urlunsplit(urlsplit(ADMIN_URL)._replace(path=f"/{name}"))


@pytest.fixture
def databases():
    if not ADMIN_URL:
        pytest.skip("OMNIX_TEST_DATABASE_URL is not set")
    import psycopg

    names = [f"mp_accounts_{role}_{uuid.uuid4().hex[:8]}" for role in ("omnix", "marketpulse")]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        for name in names:
            admin.execute(f'CREATE DATABASE "{name}"')
    urls = [_database_url(name) for name in names]
    for url, data in zip(urls, (OMNIX_DATA, MARKETPULSE_DATA), strict=True):
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(SCHEMA)
            connection.execute(data)
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


def _run(omnix: str, marketpulse: str, *, apply: bool) -> dict:
    return import_account_data(omnix, marketpulse, apply=apply, source_workspace="workspace:omnix")


def _summary(report: dict) -> dict:
    return {group["group"]: (group["in_omnix"], group["new"], group["rows"]) for group in report["groups"]}


def test_a_dry_run_reports_whole_documents_alerts_and_accounts_and_changes_nothing(databases):
    omnix, marketpulse = databases

    report = _run(omnix, marketpulse, apply=False)

    assert _summary(report) == {
        "documents": (2, 2, {"omnix_module_records": 2, "omnix_trading_script_versions": 2}),
        "alerts": (2, 1, {"omnix_trading_alerts": 1, "omnix_trading_alert_conditions": 2}),
        "paper_accounts": (1, 1, {
            "omnix_trading_paper_accounts": 1, "omnix_trading_paper_simulation_epochs": 1,
            "omnix_trading_paper_orders": 1, "omnix_trading_paper_fills": 1, "omnix_trading_paper_equity_snapshots": 1,
        }),
    }
    assert _rows(marketpulse, "SELECT count(*) FROM omnix_trading_paper_accounts") == [(0,)]
    assert _rows(marketpulse, "SELECT count(*) FROM omnix_module_records") == [(0,)]


def test_the_import_remaps_to_the_owner_keeps_existing_alerts_whole_and_is_idempotent(databases):
    omnix, marketpulse = databases

    _run(omnix, marketpulse, apply=True)
    again = _run(omnix, marketpulse, apply=True)

    assert {group: new for group, (_, new, _) in _summary(again).items()} == {"documents": 0, "alerts": 0, "paper_accounts": 0}
    # Everything belongs to the MarketPulse owner's workspace and user; other workspaces and modules stayed behind.
    assert _rows(marketpulse, "SELECT DISTINCT workspace_id FROM omnix_module_records") == [("workspace:local",)]
    assert _rows(marketpulse, "SELECT record_type, record_id, owner_user_id FROM omnix_module_records ORDER BY record_id") == [
        ("workspace", "layout-1", "user:local"), ("script", "script-1", "user:local")]
    assert _rows(marketpulse, "SELECT revision, source FROM omnix_trading_script_versions ORDER BY revision") == [
        (1, "plot(close)"), (2, "plot(rsi(close))")]
    assert _rows(marketpulse, "SELECT alert_id, owner_user_id, message FROM omnix_trading_alerts ORDER BY alert_id") == [
        ("alert-1", "user:local", "from Omnix"), ("alert-2", "user:local", "MarketPulse's own")]
    # MarketPulse's own alert-2 kept its single condition: nothing of Omnix's alert-2 was merged into it.
    assert _rows(marketpulse, "SELECT alert_id, position, operator FROM omnix_trading_alert_conditions ORDER BY 1, 2") == [
        ("alert-1", 0, "crossing_up"), ("alert-1", 1, "greater"), ("alert-2", 0, "greater")]
    assert _rows(marketpulse, "SELECT account_id, owner_user_id, name FROM omnix_trading_paper_accounts") == [
        ("paper-1", "user:local", "Swing")]
    assert _rows(marketpulse, "SELECT fill_id, order_id FROM omnix_trading_paper_fills") == [("fill-1", "order-1")]
    # The equity snapshot is numbered by MarketPulse's sequence, not Omnix's.
    [(snapshot_id, equity)] = _rows(marketpulse, "SELECT snapshot_id, equity FROM omnix_trading_paper_equity_snapshots")
    assert snapshot_id != 900 and equity == 100000


def test_an_unknown_source_workspace_or_missing_owner_is_refused(databases):
    omnix, marketpulse = databases

    with pytest.raises(AccountImportError, match="no workspace"):
        import_account_data(omnix, marketpulse, apply=False, source_workspace="workspace:nobody")
    with pytest.raises(AccountImportError, match="no user"):
        import_account_data(omnix, marketpulse, apply=False, source_workspace="workspace:omnix", target_user="user:nobody")
