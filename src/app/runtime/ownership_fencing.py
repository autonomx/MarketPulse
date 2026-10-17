"""Fencing tokens for advisory-lock ownership (WP-8.3).

An advisory lock says who owns a background worker or scheduled task now; it
cannot stop a process that lost the lock (its connection dropped) from
finishing writes it already started. Each acquisition therefore bumps an
epoch, and every background transaction checks its owner's epoch with
``FOR SHARE`` in the same transaction as its writes. A stale owner's
transaction fails the check, and a new owner's bump waits until the stale
owner's in-flight transactions have committed or rolled back.
"""
from __future__ import annotations

import os
import socket
from typing import Any


def holder_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim_epoch(connection: Any, lock_key: int) -> int:
    """Bump and return the epoch for ``lock_key``; call right after taking the lock."""
    row = connection.execute(
        """
        INSERT INTO omnix_ownership_epochs (lock_key, epoch, holder)
        VALUES (%s, 1, %s)
        ON CONFLICT (lock_key) DO UPDATE
           SET epoch = omnix_ownership_epochs.epoch + 1,
               holder = EXCLUDED.holder,
               acquired_at = CURRENT_TIMESTAMP
        RETURNING epoch
        """,
        (lock_key, holder_name()),
    ).fetchone()
    return int(row[0])


def epoch_is_current(connection: Any, lock_key: int, epoch: int) -> bool:
    """Whether ``epoch`` still owns ``lock_key``; holds a share lock until the transaction ends."""
    row = connection.execute(
        "SELECT epoch FROM omnix_ownership_epochs WHERE lock_key = %s FOR SHARE",
        (lock_key,),
    ).fetchone()
    return row is not None and int(row[0]) == epoch
