"""What the kernel reads about trading without loading it (ADR-0016, PA-2.2): kernel imports only."""
from __future__ import annotations

from typing import Any

from app.persistence.declarations import RetentionDeclaration


def _delete_strategy_events(connection: Any, days: int, batch: int) -> int:
    return int(connection.execute(
        """DELETE FROM omnix_trading_strategy_events WHERE ctid IN (
               SELECT ctid FROM omnix_trading_strategy_events
                WHERE observed_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                LIMIT %s)""",
        (days, batch),
    ).rowcount)


RETENTION = (RetentionDeclaration("trading_strategy_events", delete=_delete_strategy_events),)
