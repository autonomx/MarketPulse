"""Indicators backed by external data series (TVP-0.2): their metric, the instruments they apply to and their outputs.

The browser draws these from ``/api/trading/metrics`` (``indicators/externalIndicatorData.ts``, the same table); the
server reads the same metric for alerts and the screener (``external_series.py``). An output key is
``<indicator id>:<series key>``, as the chart names its lines. They take no inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ExternalScope = Literal["binance-crypto", "equity", "bitcoin", "market"]


@dataclass(frozen=True)
class ExternalIndicator:
    indicator_id: str
    metric: str
    scope: ExternalScope
    series_keys: tuple[str, ...]

    @property
    def output_keys(self) -> tuple[str, ...]:
        return tuple(f"{self.indicator_id}:{key}" for key in self.series_keys)


_LONG_SHORT = ("long-percent", "short-percent")
_TARGETS = ("target-low", "target-mean", "target-high")
_EXCHANGES = ("nyse", "nasdaq")

_DEFINITIONS: tuple[ExternalIndicator, ...] = (
    ExternalIndicator("tv-open-interest", "binance.open_interest", "binance-crypto", ("open-interest",)),
    ExternalIndicator("tv-understanding-crypto-open-interest", "binance.open_interest", "binance-crypto", ("open-interest",)),
    ExternalIndicator("tv-funding-rate-a-guide-to-market-sentiment", "binance.funding_rate", "binance-crypto", ("funding-rate",)),
    ExternalIndicator(
        "tv-liquidation-data-what-to-watch-and-why-it-matters", "binance.liquidations", "binance-crypto",
        ("long-liquidations", "short-liquidations"),
    ),
    ExternalIndicator("tv-long-short-ratio-accounts", "binance.global_long_short_accounts", "binance-crypto", ("ratio",)),
    ExternalIndicator("tv-long-short-accounts", "binance.global_long_short_accounts", "binance-crypto", _LONG_SHORT),
    ExternalIndicator("tv-top-trader-long-short-accounts", "binance.top_long_short_accounts", "binance-crypto", _LONG_SHORT),
    ExternalIndicator("tv-top-trader-long-short-accounts-ratio", "binance.top_long_short_accounts", "binance-crypto", ("ratio",)),
    ExternalIndicator("tv-top-trader-long-short-positions", "binance.top_long_short_positions", "binance-crypto", _LONG_SHORT),
    ExternalIndicator("tv-top-trader-long-short-positions-ratio", "binance.top_long_short_positions", "binance-crypto", ("ratio",)),
    ExternalIndicator("tv-basis", "binance.basis", "binance-crypto", ("basis",)),
    ExternalIndicator("tv-mark-price", "binance.mark_price", "binance-crypto", ("mark",)),
    ExternalIndicator("tv-index-price", "binance.index_price", "binance-crypto", ("index",)),
    ExternalIndicator("tv-premium", "binance.premium", "binance-crypto", ("premium",)),
    ExternalIndicator("tv-analyst-price-forecast", "yahoo.analyst_price_forecast", "equity", _TARGETS),
    ExternalIndicator("tv-price-target-indicator", "yahoo.price_target", "equity", _TARGETS),
    ExternalIndicator("tv-dividend-yield", "yahoo.dividend_yield", "equity", ("dividend-yield",)),
    ExternalIndicator("tv-advance-decline-line", "breadth.ad_line", "market", _EXCHANGES),
    ExternalIndicator("tv-advance-decline-ratio", "breadth.ad_ratio", "market", _EXCHANGES),
    ExternalIndicator("tv-cumulative-volume-index-cvi", "breadth.cvi", "market", _EXCHANGES),
    ExternalIndicator("tv-hash-rate", "blockchain.hash_rate", "bitcoin", ("hash-rate",)),
    ExternalIndicator("tv-difficulty", "blockchain.difficulty", "bitcoin", ("difficulty",)),
    ExternalIndicator("tv-total-utxos", "blockchain.total_utxos", "bitcoin", ("utxo-count",)),
    ExternalIndicator("tv-transaction-fees", "blockchain.transaction_fees", "bitcoin", ("transaction-fees",)),
    ExternalIndicator("tv-transaction-rate", "blockchain.transaction_rate", "bitcoin", ("transactions-per-second",)),
    ExternalIndicator("tv-blocks-mined", "blockchain.blocks_mined", "bitcoin", ("n-blocks-mined",)),
    ExternalIndicator("tv-mean-block-size-in-bytes", "blockchain.mean_block_size_bytes", "bitcoin", ("avg-block-size",)),
    ExternalIndicator("tv-total-block-size-in-bytes", "blockchain.total_block_size_bytes", "bitcoin", ("blocks-size",)),
)

EXTERNAL_INDICATORS: dict[str, ExternalIndicator] = {definition.indicator_id: definition for definition in _DEFINITIONS}

_SCOPE_PATTERNS: dict[ExternalScope, re.Pattern[str]] = {
    "binance-crypto": re.compile(r"^crypto:BINANCE:", re.IGNORECASE),
    "equity": re.compile(r"^equity:", re.IGNORECASE),
    "bitcoin": re.compile(r"^crypto:[^:]+:(?:spot|perpetual):BTC-", re.IGNORECASE),
    "market": re.compile(r""),
}

_SCOPE_NAMES: dict[ExternalScope, str] = {
    "binance-crypto": "Binance crypto symbols",
    "equity": "stocks",
    "bitcoin": "Bitcoin symbols",
    "market": "any symbol",
}


def external_indicator(indicator_id: str) -> ExternalIndicator | None:
    return EXTERNAL_INDICATORS.get(indicator_id)


def external_indicator_ids() -> list[str]:
    return sorted(EXTERNAL_INDICATORS)


def external_available_for(indicator_id: str, instrument_id: str) -> bool:
    """Whether the indicator's data exists for this instrument (as the chart offers it)."""
    definition = EXTERNAL_INDICATORS.get(indicator_id)
    return definition is not None and _SCOPE_PATTERNS[definition.scope].search(instrument_id) is not None


def external_scope_name(indicator_id: str) -> str:
    definition = EXTERNAL_INDICATORS.get(indicator_id)
    return _SCOPE_NAMES[definition.scope] if definition else ""


__all__ = [
    "EXTERNAL_INDICATORS",
    "ExternalIndicator",
    "external_available_for",
    "external_indicator",
    "external_indicator_ids",
    "external_scope_name",
]
