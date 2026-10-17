"""One PostgreSQL database per pytest-xdist worker.

Many persistence tests reset shared state (truncate tables, flip the
persistence or memory authority). Run in parallel against one database they
corrupt each other's data at random, so a parallel suite cannot be green.
Each xdist worker therefore copies the migrated test database (used as a
template) into ``<name>_<worker>`` and points every database URL variable
that named the template at its copy; inside one worker tests run serially, as
they do in a single-process run. ``OMNIX_TEST_DATABASE_PER_WORKER=0`` turns
this off.
"""
from __future__ import annotations

import os
import random
import time
from urllib.parse import urlsplit, urlunsplit

_URL_KEYS = (
    "OMNIX_TEST_DATABASE_URL",
    "OMNIX_DATABASE_URL",
    "OMNIX_MIGRATION_DATABASE_URL",
    "OMNIX_TEST_ADMIN_DATABASE_URL",
)
_created: list[tuple[str, str]] = []


def _with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def _admin_url(base: str) -> str:
    return (
        os.environ.get("OMNIX_TEST_ADMIN_DATABASE_URL")
        or os.environ.get("OMNIX_MIGRATION_DATABASE_URL")
        or base
    )


def configure_worker_database() -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    base = os.environ.get("OMNIX_TEST_DATABASE_URL")
    if not worker or not base or os.environ.get("OMNIX_TEST_DATABASE_PER_WORKER", "1") == "0":
        return
    import psycopg

    template = urlsplit(base).path.lstrip("/")
    name = f"{template}_{worker}"
    maintenance = _with_database(_admin_url(base), "postgres")
    deadline = time.monotonic() + 120
    while True:
        try:
            with psycopg.connect(maintenance, autocommit=True) as connection:
                connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                connection.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
            break
        except psycopg.errors.ObjectInUse:
            # Another worker is copying the template right now.
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2 + random.random())
    for key in _URL_KEYS:
        value = os.environ.get(key)
        if value and urlsplit(value).path.lstrip("/") == template:
            os.environ[key] = _with_database(value, name)
    _created.append((maintenance, name))


def drop_worker_databases() -> None:
    if not _created:
        return
    import psycopg

    for maintenance, name in _created:
        try:
            with psycopg.connect(maintenance, autocommit=True) as connection:
                connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        except psycopg.Error:
            pass
    _created.clear()


_DISPOSABLE_NAMES = ("omnix_test", "omnix_refactor_baseline")


def is_disposable_test_database(url: str) -> bool:
    """A test database these tests may reset: a known disposable name, or its per-worker copy."""
    import re

    name = urlsplit(url).path.lstrip("/")
    return any(re.fullmatch(rf"{base}(?:_gw\d+)?", name) for base in _DISPOSABLE_NAMES)
