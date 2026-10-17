"""Compatibility surface for the retired heuristic authority blocker.

Earlier PostgreSQL work attempted to retire mutable JSON stores by installing a
``sys.meta_path`` hook and replacing callables based on words in their names.
That approach was unsafe: importing an active gateway module could disable
unrelated factories before the corresponding PostgreSQL service had been wired.

Runtime retirement is now explicit. Each feature factory is migrated to a
PostgreSQL implementation and the legacy module is deleted when its migration
window closes. This module remains temporarily so older imports fail neither
mysteriously nor globally while the correction roadmap removes those imports.
"""

from __future__ import annotations


RETIRED_MUTABLE_AUTHORITY_MODULES: frozenset[str] = frozenset()


def install_legacy_authority_block() -> bool:
    """Return ``False`` because implicit module patching is no longer supported."""

    return False
