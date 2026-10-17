"""Pins count-mode aggregation output and fingerprints (TVP-2.5 review).

The strategy runner reads count-mode aggregation. Clock alignment for charts is a
separate mode; these goldens make sure count mode stays byte-identical.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading.providers.aggregation import (
    aggregate_market_bars,
    aggregated_dataset_fingerprint,
)


def _bars(interval: str, step: timedelta, count: int, *, start: datetime, session: str) -> list[MarketBar]:
    bars: list[MarketBar] = []
    for index in range(count):
        bar_start = start + step * index
        base = Decimal(100 + (index * 7) % 13)
        bars.append(
            MarketBar(
                instrument_id="golden:instrument",
                interval=interval,
                start_time=bar_start,
                end_time=bar_start + step,
                open=base,
                high=base + Decimal("2.5"),
                low=base - Decimal("1.25"),
                close=base + Decimal("0.5"),
                volume=Decimal(10 + index % 5),
                is_final=True,
                session=session if session != "equity" else ("regular" if index % 9 else "extended_post"),
                provider="golden",
                provider_event_id=f"golden-{index}",
                received_at=bar_start + step,
            )
        )
    return bars


def _digest(bars: list[MarketBar]) -> str:
    payload = [bar.model_dump(mode="json") for bar in bars]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


CRYPTO = _bars("1m", timedelta(minutes=1), 600, start=datetime(2026, 10, 7, 22, 3, tzinfo=timezone.utc), session="24x7")
EQUITY = _bars("1h", timedelta(hours=1), 120, start=datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc), session="equity")


def test_count_mode_crypto_output_is_pinned() -> None:
    result = aggregate_market_bars(CRYPTO, target_interval="7m", base_interval="1m", factor=7)
    assert len(result) == 86
    assert result[0].provider_event_id == "aggregate:1m:golden-0:golden-6"
    assert _digest(result) == GOLDEN["crypto_7m"]


def test_count_mode_equity_output_is_pinned() -> None:
    result = aggregate_market_bars(EQUITY, target_interval="3h", base_interval="1h", factor=3)
    assert _digest(result) == GOLDEN["equity_3h"]
    one = aggregate_market_bars(EQUITY, target_interval="1h", base_interval="1h", factor=1)
    assert _digest(one) == GOLDEN["equity_1h_factor_1"]


def test_count_mode_fingerprint_is_pinned() -> None:
    assert aggregated_dataset_fingerprint(
        "base-fingerprint",
        target_interval="7m",
        base_interval="1m",
        factor=7,
    ) == GOLDEN["fingerprint_7m"]


GOLDEN = {
    "crypto_7m": "78692487085fd8b5ea5c9519e30a5263edfce7275c63ae529be9d3d027c87e41",
    "equity_3h": "c35aaa7de6603508720649987db93c9b266b8e5f7ec8ab8cee4852abc7e4a41a",
    "equity_1h_factor_1": "38a4a715725b11f7e715307119dfb074010865fb3470bd09d93b352d0bf3d7a8",
    "fingerprint_7m": "c175f3dd12a745699b783b61438ed49997017cc14cd722e864d43d5526eccf6e",
}
