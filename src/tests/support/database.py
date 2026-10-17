"""Test database URLs.

``OMNIX_TEST_DATABASE_URL`` is the role the application uses. CI also runs
the PostgreSQL tests as a restricted runtime role (DML only, subject to
row-level security); test setup that needs DDL or must bypass row-level
security uses ``OMNIX_TEST_ADMIN_DATABASE_URL`` (the DDL owner), which falls
back to the application URL when the two roles are the same.
"""
from __future__ import annotations

import os


def test_database_url() -> str:
    return os.environ["OMNIX_TEST_DATABASE_URL"]


def admin_database_url() -> str:
    return os.environ.get("OMNIX_TEST_ADMIN_DATABASE_URL") or test_database_url()


def truncate_memory_v2_data(connection) -> None:
    """Clear Memory v2's records, keeping the authority that makes v2 current (WP-8.5).

    Curated records live in Memory v2, keyed by workspace without a foreign key,
    so truncating the workspaces does not clear them.
    """
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
            "AND tablename LIKE 'omnix_memory_v2_%' "
            "AND tablename NOT IN ('omnix_memory_v2_authority_epochs', 'omnix_memory_v2_authority_current')"
        ).fetchall()
    ]
    if tables:
        connection.execute("TRUNCATE " + ", ".join(tables) + " CASCADE")
