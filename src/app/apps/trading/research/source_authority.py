from __future__ import annotations

from urllib.parse import urlparse

_TIER2 = {"reuters.com", "bloomberg.com", "wsj.com", "dowjones.com", "marketwatch.com"}
_TIER3 = {"finance.yahoo.com", "yahoo.com", "seekingalpha.com"}


def source_authority_tier(source_type: str, locator: str = "") -> int:
    kind = str(source_type or "").lower()
    if kind in {"sec", "company_ir", "government", "fda", "clinical_trials", "court"}:
        return 1
    host = (urlparse(locator).hostname or "").lower().removeprefix("www.")
    if any(host == value or host.endswith("." + value) for value in _TIER2):
        return 2
    if any(host == value or host.endswith("." + value) for value in _TIER3):
        return 3
    return 4 if kind in {"social", "forum", "blog"} else 3


def primary_source(source_type: str, locator: str = "") -> bool:
    return source_authority_tier(source_type, locator) == 1
