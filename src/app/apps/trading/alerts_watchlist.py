"""Watchlist alerts (TVP-1.7): one alert on every symbol of a watchlist.

A watchlist alert's instrument is ``watchlist:<record id>``; its members are read from the watchlist document at
evaluation time, so adding or removing a symbol takes effect on the next pass. Each pass fetches one history per
member symbol, so the symbols evaluated are capped: by the alert's own limit (default 100), and by one budget for
the whole pass (``plan_watchlist_pass``). Per upstream, a pass's alert history fetches (ordinary alerts' first) stay
within half of what its request budget (``providers/request_budget.py``) refills between passes; an upstream with no
published budget (Yahoo) gets a fixed, conservative number of fetches per pass.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .alerts import WATCHLIST_SYMBOL_DEFAULT, WATCHLIST_SYMBOL_MAX, TradingAlert, watchlist_symbols
from .providers.request_budget import DEFAULT_LIMITS, upstream_of

# The share of a provider's budget per pass that alert history fetches may spend.
WATCHLIST_BUDGET_SHARE = 0.5
# History fetches per pass from an upstream without a published budget (Yahoo answers bursts with 429s).
UNBUDGETED_PASS_FETCHES = 25
# Request weight of one bars request, where a provider counts weight (Binance klines up to 100 bars weigh 2).
_BARS_REQUEST_WEIGHT = {"binance": 2.0}

# A pass's fetch target, as the monitor groups alerts: (instrument, requested binding, interval).
Target = tuple[str, str | None, str]


def provider_symbol_cap(upstream: str, pass_seconds: float) -> int:
    """How many histories one pass may fetch from ``upstream``: its share of the budget, or the fixed cap without one."""
    limit = DEFAULT_LIMITS.get(upstream)
    if limit is None:
        return UNBUDGETED_PASS_FETCHES
    per_pass = limit.weight / limit.per_seconds * max(1.0, pass_seconds) * WATCHLIST_BUDGET_SHARE
    return max(1, min(WATCHLIST_SYMBOL_MAX, math.floor(per_pass / _BARS_REQUEST_WEIGHT.get(upstream, 1.0))))


def watchlist_symbol_cap(symbols: Sequence[str], provider_of: Callable[[str], str | None], pass_seconds: float) -> int:
    """The most symbols of this list a pass could evaluate were it the only alert: the strictest of their providers' caps."""
    caps = [WATCHLIST_SYMBOL_MAX]
    for upstream in {upstream_of(provider) for provider in (provider_of(symbol) for symbol in symbols) if provider}:
        caps.append(provider_symbol_cap(upstream, pass_seconds))
    return min(caps)


def watchlist_members(document: dict[str, Any] | None) -> list[str] | None:
    """The symbols of a watchlist document as the document repository returns it; None for a missing or archived one."""
    if not document or document.get("status", "active") != "active":
        return None
    payload = document.get("payload")
    return watchlist_symbols(payload) if isinstance(payload, dict) else []


def plan_watchlist_pass(
    alerts: Iterable[TradingAlert],
    members: Mapping[str, list[str] | None],
    fetched: Iterable[Target],
    upstream_for: Callable[[str], str | None],
    pass_seconds: float,
) -> tuple[dict[Target, list[TradingAlert]], dict[str, dict[str, Any]]]:
    """Which member symbols each watchlist alert evaluates this pass, and a status per alert.

    ``fetched`` are the ordinary alerts' targets: they are fetched anyway, so they cost the watchlist alerts nothing
    and count against the budget first. Alerts are planned in id order, each member in list order up to the alert's
    limit; a member whose history would exceed its upstream's budget, or with no provider (an unknown symbol), is
    skipped this pass. A history is fetched once however many alerts read it.
    """
    spent: Counter[str] = Counter()
    planned: set[Target] = set()
    for target in fetched:
        planned.add(target)
        upstream = upstream_for(target[0])
        if upstream is not None:
            spent[upstream] += 1
    watch_targets: dict[Target, list[TradingAlert]] = defaultdict(list)
    status: dict[str, dict[str, Any]] = {}
    for alert in sorted(alerts, key=lambda item: item.alert_id):
        symbols = members.get(alert.watchlist_id or "")
        if symbols is None:
            status[alert.alert_id] = {"watchlist_missing": True, "members": 0, "evaluated": 0, "skipped": 0}
            continue
        limit = alert.evaluation_policy.symbol_limit or WATCHLIST_SYMBOL_DEFAULT
        evaluated = skipped = 0
        for symbol in symbols[:limit]:
            target = (symbol, None, alert.evaluation_policy.interval)
            if target not in planned:
                upstream = upstream_for(symbol)
                if upstream is None or spent[upstream] >= provider_symbol_cap(upstream, pass_seconds):
                    skipped += 1
                    continue
                spent[upstream] += 1
                planned.add(target)
            watch_targets[target].append(alert)
            evaluated += 1
        status[alert.alert_id] = {"watchlist_missing": False, "members": len(symbols), "evaluated": evaluated, "skipped": skipped}
    return dict(watch_targets), status
