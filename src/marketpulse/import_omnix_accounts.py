"""Import one Omnix workspace's own trading data into MarketPulse (roadmap MP-10.2).

What comes over, remapped to the MarketPulse owner's workspace and user:

- trading documents: layouts, watchlists and their flags, drawings, indicator presets and scripts, with each
  script's version history;
- alerts, with their conditions, per-symbol state and trigger history;
- paper accounts, with their simulation epochs, balances, positions, orders, fills, ledger, protections, epoch
  archives, equity snapshots and trade records.

Each document, alert and account comes over whole or not at all: one that MarketPulse already has (same id) is kept,
and none of its rows are touched, so re-running the import adds nothing. The Omnix side is read in a read-only
transaction, scoped to the source workspace by its own row-level security. A dry run (the default) inserts
everything inside a transaction, so foreign keys are checked, reports the counts and rolls back.

Not imported: notification settings and deliveries, push subscriptions, strategies, scanners, backtests and
research, which belong to an install rather than to the data a user would carry across.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marketpulse.import_omnix import ReferenceImportError, _quote

LOCAL_WORKSPACE = "workspace:local"
LOCAL_USER = "user:local"
USER_COLUMNS = ("owner_user_id", "created_by", "user_id")
DOCUMENT_TYPES = ("workspace", "watchlist", "watchlist_flag_set", "drawing", "indicator_preset", "script")

_COLUMNS = """
SELECT a.attname, a.attnotnull,
       a.atthasdef OR a.attidentity <> '',
       a.attidentity <> '' OR coalesce(pg_get_expr(d.adbin, d.adrelid), '') LIKE 'nextval(%%'
FROM pg_attribute a
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
ORDER BY a.attnum
"""


class AccountImportError(ReferenceImportError):
    """The import cannot run: an unknown workspace, or a table MarketPulse requires a column Omnix lacks."""


@dataclass(frozen=True)
class Child:
    table: str
    # (child column, root column) pairs that tie a row to its document, alert or account.
    match: tuple[tuple[str, str], ...]
    # Only roots matching this condition have rows here (script versions belong to scripts).
    root_where: str = ""


@dataclass(frozen=True)
class Group:
    name: str
    table: str
    key: tuple[str, ...]
    where: str
    children: tuple[Child, ...]


_ALERT = (("alert_id", "alert_id"),)
_ACCOUNT = (("account_id", "account_id"),)
# Children in foreign-key order: a row's parent is inserted before it.
GROUPS = (
    Group("documents", "omnix_module_records", ("module", "record_type", "record_id"),
          "module = 'trading' AND record_type IN (" + ", ".join(f"'{name}'" for name in DOCUMENT_TYPES) + ")",
          (Child("omnix_trading_script_versions", (("script_id", "record_id"),), "record_type = 'script'"),)),
    Group("alerts", "omnix_trading_alerts", ("alert_id",), "", (
        Child("omnix_trading_alert_conditions", _ALERT),
        Child("omnix_trading_alert_symbol_states", _ALERT),
        Child("omnix_trading_alert_triggers", _ALERT),
    )),
    Group("paper_accounts", "omnix_trading_paper_accounts", ("account_id",), "", (
        Child("omnix_trading_paper_simulation_epochs", _ACCOUNT),
        Child("omnix_trading_paper_balances", _ACCOUNT),
        Child("omnix_trading_paper_positions", _ACCOUNT),
        Child("omnix_trading_paper_orders", _ACCOUNT),
        Child("omnix_trading_paper_fills", _ACCOUNT),
        Child("omnix_trading_paper_ledger", _ACCOUNT),
        Child("omnix_trading_paper_protections", _ACCOUNT),
        Child("omnix_trading_paper_epoch_archives", _ACCOUNT),
        Child("omnix_trading_paper_equity_snapshots", _ACCOUNT),
        Child("omnix_trading_paper_trade_records", _ACCOUNT),
    )),
)


def _exists(connection: Any, table: str) -> bool:
    return connection.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


def _load(source: Any, target: Any, table: str, temp: str, workspace: str, where: str) -> list[str]:
    """Copies the source workspace's rows of ``table`` into the temporary table ``temp``; returns the columns.

    Columns filled by a sequence are left out, so MarketPulse numbers those rows itself."""
    source_columns = {row[0] for row in source.execute(_COLUMNS, (table,)).fetchall()}
    target_columns = target.execute(_COLUMNS, (table,)).fetchall()
    missing = [name for name, not_null, has_default, _ in target_columns
               if not_null and not has_default and name not in source_columns]
    if missing:
        raise AccountImportError(f"{table}: Omnix has no {', '.join(missing)} (update Omnix or MarketPulse first)")
    columns = [name for name, _, _, numbered in target_columns if name in source_columns and not numbered]
    column_list = ", ".join(_quote(name) for name in columns)
    target.execute(f"DROP TABLE IF EXISTS {temp}")
    target.execute(f"CREATE TEMP TABLE {temp} AS SELECT {column_list} FROM {_quote(table)} WITH NO DATA")
    from psycopg import sql

    condition = sql.SQL("workspace_id = {}").format(sql.Literal(workspace)).as_string(source)
    if where:
        condition += f" AND ({where})"
    with source.cursor().copy(f"COPY (SELECT {column_list} FROM {_quote(table)} WHERE {condition}) TO STDOUT") as reader, \
            target.cursor().copy(f"COPY {temp} ({column_list}) FROM STDIN") as writer:
        for block in reader:
            writer.write(block)
    return columns


def _remap(target: Any, temp: str, columns: list[str], workspace: str, user: str) -> None:
    target.execute(f"UPDATE {temp} SET workspace_id = %s", (workspace,))
    for column in USER_COLUMNS:
        if column in columns:
            target.execute(f"UPDATE {temp} SET {_quote(column)} = %s WHERE {_quote(column)} IS NOT NULL", (user,))


def import_account_data(
    source_url: str,
    target_url: str,
    *,
    apply: bool,
    source_workspace: str = LOCAL_WORKSPACE,
    target_workspace: str = LOCAL_WORKSPACE,
    target_user: str = LOCAL_USER,
) -> dict[str, Any]:
    """Per group: documents, alerts or accounts in Omnix, how many MarketPulse lacks, and the rows each adds."""
    import psycopg

    groups: list[dict[str, Any]] = []
    with psycopg.connect(source_url, options="-c default_transaction_read_only=on") as source, \
            psycopg.connect(target_url) as target:
        # Both sides keep tenant rows under row-level security: each reads and writes as its own workspace.
        source.execute("SELECT set_config('omnix.workspace_id', %s, false)", (source_workspace,))
        target.execute("SELECT set_config('omnix.workspace_id', %s, false)", (target_workspace,))
        if not source.execute("SELECT 1 FROM omnix_workspaces WHERE id = %s", (source_workspace,)).fetchone():
            raise AccountImportError(f"Omnix has no workspace {source_workspace}")
        if not target.execute("SELECT 1 FROM omnix_users WHERE id = %s", (target_user,)).fetchone():
            raise AccountImportError(f"MarketPulse has no user {target_user}: finish the first run first")
        for group in GROUPS:
            if not (_exists(source, group.table) and _exists(target, group.table)):
                continue
            columns = _load(source, target, group.table, "import_root", source_workspace, group.where)
            _remap(target, "import_root", columns, target_workspace, target_user)
            match = " AND ".join(f"existing.{_quote(name)} = incoming.{_quote(name)}" for name in group.key)
            target.execute("DROP TABLE IF EXISTS import_new")
            target.execute(f"CREATE TEMP TABLE import_new AS SELECT * FROM import_root incoming WHERE NOT EXISTS "
                           f"(SELECT 1 FROM {_quote(group.table)} existing "
                           f"WHERE existing.workspace_id = incoming.workspace_id AND {match})")
            in_omnix = target.execute("SELECT count(*) FROM import_root").fetchone()[0]
            new = target.execute("SELECT count(*) FROM import_new").fetchone()[0]
            column_list = ", ".join(_quote(name) for name in columns)
            target.execute(f"INSERT INTO {_quote(group.table)} ({column_list}) SELECT {column_list} FROM import_new")
            rows: dict[str, int] = {group.table: new}
            for child in group.children:
                if not (_exists(source, child.table) and _exists(target, child.table)):
                    continue
                child_columns = _load(source, target, child.table, "import_child", source_workspace, "")
                _remap(target, "import_child", child_columns, target_workspace, target_user)
                ties = ", ".join(_quote(column) for column, _ in child.match)
                roots = ", ".join(_quote(column) for _, column in child.match)
                limit = f" WHERE {child.root_where}" if child.root_where else ""
                child_list = ", ".join(_quote(name) for name in child_columns)
                added = target.execute(
                    f"INSERT INTO {_quote(child.table)} ({child_list}) SELECT {child_list} FROM import_child "
                    f"WHERE ({ties}) IN (SELECT {roots} FROM import_new{limit})").rowcount
                rows[child.table] = added
                target.execute("DROP TABLE import_child")
            target.execute("DROP TABLE import_root")
            target.execute("DROP TABLE import_new")
            groups.append({"group": group.name, "in_omnix": in_omnix, "new": new, "rows": rows})
        if apply:
            target.commit()
        else:
            target.rollback()
        source.rollback()
    return {"source_workspace": source_workspace, "applied": apply, "groups": groups}


__all__ = ["AccountImportError", "GROUPS", "LOCAL_USER", "LOCAL_WORKSPACE", "import_account_data"]
