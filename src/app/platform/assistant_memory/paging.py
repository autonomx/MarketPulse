"""Walking memory collections page by page (WP-5.5)."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.conversation.memory_contracts import MemoryCandidate, MemoryRecord
from app.runtime.pagination import MAX_PAGE_SIZE

# Safety stop for a runaway walk.
MAX_PAGES = 10_000


def iter_records(repository: Any, **filters: Any) -> Iterator[MemoryRecord]:
    """Every matching record, in the repository's order."""
    for page_number in range(MAX_PAGES):
        page = repository.list_records(limit=MAX_PAGE_SIZE, offset=page_number * MAX_PAGE_SIZE, **filters)
        yield from page
        if len(page) < MAX_PAGE_SIZE:
            return
    raise RuntimeError("memory record walk exceeded its page limit")


def iter_candidates(repository: Any, **filters: Any) -> Iterator[MemoryCandidate]:
    """Every matching candidate, oldest first."""
    for page_number in range(MAX_PAGES):
        page = repository.list_candidates(limit=MAX_PAGE_SIZE, offset=page_number * MAX_PAGE_SIZE, **filters)
        yield from page
        if len(page) < MAX_PAGE_SIZE:
            return
    raise RuntimeError("memory candidate walk exceeded its page limit")


__all__ = ["iter_candidates", "iter_records"]
