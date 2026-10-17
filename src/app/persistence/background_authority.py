"""Fail closed on PostgreSQL operations from a supervised background worker."""

from contextlib import contextmanager
from contextvars import ContextVar

_OWNER = ContextVar("omnix_background_owner", default=None)


@contextmanager
def background_execution(owner):
    token = _OWNER.set(owner)
    try:
        owner.require_live()
        yield
    finally:
        _OWNER.reset(token)


def require_background_owner(connection=None, *, hold: bool = False):
    """Check the current background owner, if any.

    An owner that fences (WP-8.3) checks its epoch on ``connection``. With
    ``hold`` the check runs in the caller's open transaction and its share
    lock lasts until that transaction ends, so a new owner waits for the
    caller's writes and a stale owner's writes fail. Without ``hold`` (a
    connection just checked out) the check runs in its own short transaction
    and the connection is handed back idle. Owners that cannot fence, or a
    call without a connection, prove their lock connection is alive instead.
    """
    owner = _OWNER.get()
    if owner is None:
        return
    fence = getattr(owner, "fence", None)
    if connection is None or fence is None:
        owner.require_live()
        return
    if hold:
        fence(connection)
        return
    try:
        fence(connection)
    finally:
        connection.rollback()
