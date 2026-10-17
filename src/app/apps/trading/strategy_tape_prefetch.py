"""Warm the shared bar tape for a strategy pass before its candidate loop.

The loop still asks for each candidate's bars itself, in the same order and
with its own clock, so its events and decisions are unchanged; the requests it
makes within the same minute are answered from the tape the prefetch built a
few candidates at a time.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

PREFETCH_WORKERS = 4


async def prefetch_candidate_tapes(market_service, universe, *, interval: str, as_of: datetime, limit: int = 500) -> None:
    prefetch = getattr(market_service, "prefetch_recovered_bars", None)
    candidates = list(getattr(universe, "candidates", ()) or ())
    if not callable(prefetch) or not candidates:
        return
    requests = [(candidate.instrument_id, interval, limit, candidate.binding_id) for candidate in candidates]
    await asyncio.to_thread(
        prefetch,
        requests,
        session_date=universe.session_date,
        as_of=as_of,
        workers=PREFETCH_WORKERS,
    )


__all__ = ["PREFETCH_WORKERS", "prefetch_candidate_tapes"]
