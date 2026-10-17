"""Expected PostgreSQL connections against max_connections (WP-5.10)."""
from __future__ import annotations

import logging
from typing import Any

from app.config.env import env_str

from .config import DEDICATED_CONNECTIONS_PER_PROCESS, role_pool_max

logger = logging.getLogger(__name__)

WARN_FRACTION = 0.8


def deployment_process_counts(value: str | None) -> dict[str, int]:
    """``"api=2,worker=1,job-worker=2,scheduler=1"`` -> role counts."""
    counts: dict[str, int] = {}
    for part in (value or "").split(","):
        role, _, count = part.strip().partition("=")
        if role and count.strip().isdigit():
            counts[role.strip().lower()] = int(count)
    return counts


def connection_budget(max_connections: int, process_counts: dict[str, int]) -> dict[str, Any]:
    override = (env_str("OMNIX_DATABASE_POOL_MAX", "") or "").strip()
    pool_override = int(override) if override.isdigit() else 0
    expected = sum(
        count * ((pool_override or role_pool_max(role)) + DEDICATED_CONNECTIONS_PER_PROCESS)
        for role, count in process_counts.items()
    )
    fraction = expected / max_connections if max_connections else 1.0
    return {
        "process_counts": process_counts,
        "expected_connections": expected,
        "max_connections": max_connections,
        "fraction": round(fraction, 3),
        "warning": fraction > WARN_FRACTION,
    }


def check_connection_budget(database: Any) -> dict[str, Any] | None:
    """Log a warning when the deployment may exhaust PostgreSQL connections."""
    counts = deployment_process_counts(env_str("OMNIX_DEPLOYMENT_PROCESS_COUNTS", ""))
    if not counts:
        return None
    with database.connection() as connection:
        max_connections = int(connection.execute("SHOW max_connections").fetchone()[0])
        connection.rollback()
    budget = connection_budget(max_connections, counts)
    if budget["warning"]:
        logger.warning(
            "database_connection_budget expected=%s max_connections=%s (over %d%%); lower pool sizes or raise max_connections",
            budget["expected_connections"], max_connections, int(WARN_FRACTION * 100),
        )
    return budget


__all__ = ["check_connection_budget", "connection_budget", "deployment_process_counts"]
