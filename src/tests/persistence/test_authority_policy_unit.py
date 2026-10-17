from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.persistence.authority import (
    AuthorityOperation,
    PostgresAuthorityError,
    authority_policy,
    initialize_fresh_install_authority,
    require_authority_operation,
)
from app.persistence.unit_of_work import unit_of_work


class _Result:
    def __init__(self, row=None, rows=None, *, rowcount: int = 1) -> None:
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _AuthorityConnection:
    def __init__(self, mode: str, state: str, *, imports: int = 0, domain_rows: int = 0) -> None:
        self.mode = mode
        self.state = state
        self.imports = imports
        self.domain_rows = domain_rows
        self.transitions: list[tuple[str, str]] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, sql: str, parameters=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT version FROM omnix_schema_migrations"):
            return _Result(("0016_data_lifecycle_capacity",))
        if "SELECT mode, authority_state FROM omnix_persistence_cutover" in normalized:
            return _Result((self.mode, self.state))
        if normalized == "SELECT COUNT(*) FROM omnix_legacy_import_runs":
            return _Result((self.imports,))
        if normalized.startswith("SELECT (SELECT COUNT(*) FROM omnix_chat_sessions)"):
            values = [0] * 11
            values[0] = self.domain_rows
            return _Result(tuple(values))
        if normalized.startswith("UPDATE omnix_persistence_cutover"):
            self.mode = "postgresql"
            self.state = "postgresql_stabilized"
            return _Result()
        if normalized.startswith("INSERT INTO omnix_cutover_transitions"):
            self.transitions.append(("legacy_preflight", "postgresql_stabilized"))
            return _Result()
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _Database:
    def __init__(self, connection: _AuthorityConnection) -> None:
        self._connection = connection

    @contextmanager
    def connection(self):
        yield self._connection


@pytest.mark.parametrize(
    ("state", "writes_allowed"),
    [
        ("legacy_preflight", False),
        ("imported_unverified", False),
        ("imported_verified", False),
        ("postgresql_activated_frozen", False),
        ("postgresql_open_for_writes", True),
        ("postgresql_stabilized", True),
        ("rollback_recorded", False),
    ],
)
def test_authority_policy_only_opens_runtime_writes_in_two_states(
    state: str,
    writes_allowed: bool,
) -> None:
    mode = "postgresql" if state.startswith("postgresql_") else state
    if state in {"legacy_preflight", "imported_unverified", "imported_verified"}:
        mode = "legacy_preflight"
    policy = authority_policy(mode=mode, authority_state=state)
    assert policy.runtime_writes_allowed is writes_allowed


def test_frozen_uow_rejects_mutation_and_open_uow_accepts_it() -> None:
    frozen = _AuthorityConnection("postgresql", "postgresql_activated_frozen")
    with pytest.raises(PostgresAuthorityError, match="postgresql_activated_frozen"):
        with unit_of_work(_Database(frozen)):
            pass

    opened = _AuthorityConnection("postgresql", "postgresql_open_for_writes")
    with unit_of_work(_Database(opened)) as work:
        assert work.connection is opened
        work.rollback()


def test_frozen_state_allows_explicit_diagnostic_read() -> None:
    connection = _AuthorityConnection("postgresql", "postgresql_activated_frozen")
    policy = require_authority_operation(connection, AuthorityOperation.DIAGNOSTIC_READ)
    assert policy.diagnostic_reads_allowed is True


def test_fresh_install_initializes_mode_and_authority_state_coherently() -> None:
    connection = _AuthorityConnection("legacy_preflight", "legacy_preflight")
    policy = initialize_fresh_install_authority(
        connection,
        software_revision="test-head",
        schema_version="0016_data_lifecycle_capacity",
    )
    assert policy.mode == "postgresql"
    assert policy.authority_state == "postgresql_stabilized"
    assert connection.transitions == [("legacy_preflight", "postgresql_stabilized")]


def test_nonempty_legacy_install_is_not_auto_initialized() -> None:
    connection = _AuthorityConnection(
        "legacy_preflight",
        "legacy_preflight",
        domain_rows=1,
    )
    policy = initialize_fresh_install_authority(
        connection,
        software_revision="test-head",
        schema_version="0016_data_lifecycle_capacity",
    )
    assert policy.authority_state == "legacy_preflight"
    assert connection.transitions == []
