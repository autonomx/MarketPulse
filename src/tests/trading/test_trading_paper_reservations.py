from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.apps.trading.paper import (
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    paper_buy_reservation,
    paper_fill_decision,
    paper_order_request_matches,
)


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
INSTRUMENT = "crypto:BINANCE:spot:BTC-USDT"


def _order(order_type: str, side: str, price: str = "100") -> PaperOrder:
    return PaperOrder(
        account_id="paper-1",
        order_id=f"{side}-{order_type}",
        instrument_id=INSTRUMENT,
        side=side,
        order_type=order_type,
        quantity=Decimal("2"),
        limit_price=Decimal(price) if order_type == "limit" else None,
        stop_price=Decimal(price) if order_type == "stop" else None,
        idempotency_key=f"{side}-{order_type}",
    )


def _range_observation(*, price: str, high: str, low: str) -> PaperMarketObservation:
    return PaperMarketObservation(
        instrument_id=INSTRUMENT,
        provider="fixture",
        price=Decimal(price),
        high=Decimal(high),
        low=Decimal(low),
        source_time=NOW,
        evaluated_at=NOW,
    )


def test_limit_and_stop_orders_use_observation_high_low_not_close_only() -> None:
    # Close is above the buy limit, but the bar traded through the limit.
    buy_limit = paper_fill_decision(
        _order("limit", "buy"),
        _range_observation(price="105", high="106", low="99"),
    )
    assert buy_limit.should_fill is True
    assert buy_limit.fill_price == Decimal("100")

    # Close is below the sell limit, but the bar high traded through it.
    sell_limit = paper_fill_decision(
        _order("limit", "sell"),
        _range_observation(price="95", high="101", low="94"),
    )
    assert sell_limit.should_fill is True
    assert sell_limit.fill_price == Decimal("100")

    # Stop triggers use the full range and pessimistic stop slippage.
    buy_stop = paper_fill_decision(
        _order("stop", "buy"),
        _range_observation(price="98", high="101", low="97"),
    )
    sell_stop = paper_fill_decision(
        _order("stop", "sell"),
        _range_observation(price="102", high="103", low="99"),
    )
    assert buy_stop.should_fill is True
    assert sell_stop.should_fill is True
    assert buy_stop.fill_price == Decimal("100.2500")
    assert sell_stop.fill_price == Decimal("99.7500")


def test_buying_power_reservation_is_deterministic_and_conservative() -> None:
    limit = PaperOrderRequest(
        order_id="limit-1",
        instrument_id=INSTRUMENT,
        side="buy",
        order_type="limit",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        idempotency_key="limit-1",
    )
    assert paper_buy_reservation(
        limit,
        available_cash=Decimal("1000"),
        commission_bps=Decimal("10"),
    ) == Decimal("200.2")

    market = PaperOrderRequest(
        order_id="market-1",
        instrument_id=INSTRUMENT,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        idempotency_key="market-1",
    )
    assert paper_buy_reservation(
        market,
        available_cash=Decimal("750"),
        commission_bps=Decimal("10"),
    ) == Decimal("750")

    quoted_market = market.model_copy(update={"reference_price": Decimal("100")})
    assert paper_buy_reservation(
        quoted_market,
        available_cash=Decimal("750"),
        commission_bps=Decimal("10"),
    ) == Decimal("100.1")


def test_idempotency_key_reuse_requires_identical_semantic_payload() -> None:
    request = PaperOrderRequest(
        order_id="limit-1",
        instrument_id=INSTRUMENT,
        side="buy",
        order_type="limit",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        idempotency_key="stable-key",
    )
    existing = PaperOrder(account_id="paper-1", **request.model_dump())
    assert paper_order_request_matches(existing, request) is True
    changed = request.model_copy(update={"quantity": Decimal("3")})
    assert paper_order_request_matches(existing, changed) is False


def test_paper_reservation_schema_and_repository_guards_are_present() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0035_trading_paper_reservations.sql"
    ).read_text()
    assert "reserved_quantity" in migration
    assert "reserved_cash" in migration

    repository = Path("src/app/apps/trading/paper_repository.py").read_text()
    assert "paper_idempotency_payload_mismatch" in repository
    assert "available = available - %s" in repository
    assert "reserved_quantity = reserved_quantity + %s" in repository
    assert "reserved_quantity = reserved_quantity - %s" in repository
