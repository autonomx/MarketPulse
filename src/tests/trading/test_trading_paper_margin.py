"""Paper leverage, margin and fixed commission rules (TVP-7.2b)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountCreate,
    PaperMargin,
    PaperMarginPosition,
    PaperOrderRequest,
    paper_asset_class,
    paper_buy_reservation,
    paper_buying_power,
    paper_margin_call_closes,
    paper_margin_fraction,
    paper_margin_status,
    paper_order_commission,
)

D = Decimal


def account(**fields) -> PaperAccount:
    return PaperAccount(account_id="a", name="A", base_currency="USD", commission_bps=D("10"), **fields)


def test_without_margin_settings_buying_power_is_cash_less_twice_the_shorts() -> None:
    positions = [PaperMarginPosition("equity:X", D("-10"), D("50")), PaperMarginPosition("crypto:Y", D("2"), D("100"))]
    assert paper_buying_power(account(), D("5000"), positions) == D("5000") - 2 * D("500")


def test_margin_is_per_asset_class_and_short_or_long() -> None:
    leveraged = account(margin={"crypto": PaperMargin(long_pct=D("10"), short_pct=D("20"))})
    assert paper_asset_class("crypto:BINANCE:spot:BTC-USDT") == "crypto"
    assert paper_asset_class("weird") == "other"
    assert paper_margin_fraction(leveraged, "crypto:BINANCE:spot:BTC-USDT", short=False) == D("0.1")
    assert paper_margin_fraction(leveraged, "crypto:BINANCE:spot:BTC-USDT", short=True) == D("0.2")
    assert paper_margin_fraction(leveraged, "equity:NASDAQ:AAPL", short=False) == D("1")
    # A 10% long frees 90% of its cost; a 20% short holds its proceeds and 20%.
    positions = [PaperMarginPosition("crypto:A", D("10"), D("100")), PaperMarginPosition("crypto:B", D("-10"), D("100"))]
    assert paper_buying_power(leveraged, D("0"), positions) == D("900") - D("1200")


def test_a_buy_holds_its_margin_share_and_a_fixed_commission() -> None:
    request = PaperOrderRequest(
        order_id="o", instrument_id="equity:X", side="buy", order_type="limit", quantity=D("10"),
        limit_price=D("100"), idempotency_key="k",
    )
    assert paper_buy_reservation(request, available_cash=D("0"), commission_bps=D("10")) == D("1001")
    assert paper_buy_reservation(request, available_cash=D("0"), commission_bps=D("10"), margin=D("0.25"), fixed_commission=D("2")) == D("252")


def test_a_fixed_commission_is_charged_on_the_first_fill_only() -> None:
    fixed = account(commission_type="fixed_per_order", commission_fixed=D("3"))
    assert paper_order_commission(fixed, D("1000"), first_fill=True) == D("3")
    assert paper_order_commission(fixed, D("1000"), first_fill=False) == D("0")
    assert paper_order_commission(account(), D("1000"), first_fill=False) == D("1")


def test_a_margin_call_closes_the_largest_margin_use_first_and_only_as_far_as_needed() -> None:
    leveraged = account(margin={"equity": PaperMargin(long_pct=D("50")), "crypto": PaperMargin(long_pct=D("25"))})
    positions = [
        PaperMarginPosition("crypto:SMALL", D("10"), D("100"), D("100")),  # uses 250
        PaperMarginPosition("equity:BIG", D("100"), D("100"), D("50")),  # uses 2,500
    ]
    status = paper_margin_status(leveraged, D("-3000"), D("0"), positions)
    assert (status.equity, status.maintenance, status.margin_call) == (D("3000"), D("2750"), False)
    assert paper_margin_call_closes(leveraged, status, positions) == []
    status = paper_margin_status(leveraged, D("-3800"), D("0"), positions)
    # Short 550: the big position frees 25 a unit, so 22 units close; the small one is untouched.
    assert status.maintenance - status.equity == D("550")
    assert paper_margin_call_closes(leveraged, status, positions) == [("equity:BIG", D("22"))]
    # A shortfall the biggest can't cover moves on to the next.
    status = paper_margin_status(leveraged, D("-10000"), D("0"), positions)
    closes = paper_margin_call_closes(leveraged, status, positions)
    assert closes[0] == ("equity:BIG", D("100")) and closes[1][0] == "crypto:SMALL"


def test_margin_settings_are_validated() -> None:
    with pytest.raises(ValidationError, match="per asset class"):
        PaperAccountCreate(account_id="a", name="A", margin={"stocks": PaperMargin()})
    with pytest.raises(ValidationError):
        PaperMargin(long_pct=D("0"))
    with pytest.raises(ValidationError):
        PaperMargin(short_pct=D("101"))
    assert PaperAccountCreate(account_id="a", name="A").margin == {}


def test_the_monitor_watches_accounts_that_can_be_margin_called() -> None:
    from app.apps.trading.paper import PaperAccountSnapshot, PaperPosition
    from app.apps.trading.paper_monitor import _uses_margin

    def snapshot(acct: PaperAccount, quantity: str) -> PaperAccountSnapshot:
        position = PaperPosition(instrument_id="equity:X", quantity=D(quantity), average_cost=D("10"), realized_pnl=D("0"))
        return PaperAccountSnapshot(account=acct, balances=[], positions=[position], open_orders=[], recent_fills=[], recent_ledger=[])

    # A cash long can't fall below its margin; a short or any leverage can.
    assert _uses_margin(snapshot(account(), "10")) is False
    assert _uses_margin(snapshot(account(), "-10")) is True
    assert _uses_margin(snapshot(account(margin={"crypto": PaperMargin(long_pct=D("50"))}), "10")) is True


def test_the_risk_preview_sizes_with_leverage_and_a_fixed_commission() -> None:
    from datetime import datetime, timezone

    from app.apps.trading.execution import ExecutionObservation
    from app.apps.trading.paper import PaperAccountSnapshot, PaperBalance
    from app.apps.trading.paper_risk import PaperRiskPreviewRequest, preview_paper_risk

    now = datetime.now(timezone.utc)
    quote = ExecutionObservation(
        instrument_id="equity:NYSE:T", binding_id="b", provider="fixture", bid=D("9.99"), ask=D("10.01"), last=D("10"),
        source_time=now, received_at=now, session="regular", freshness_mode="polled", execution_eligible=True,
    )

    def preview(acct: PaperAccount):
        snap = PaperAccountSnapshot(
            account=acct, balances=[PaperBalance(currency="USD", available=D("1000"))], positions=[], open_orders=[], recent_fills=[], recent_ledger=[],
        )
        # A wide stop and a large risk: buying power is what limits the size.
        request = PaperRiskPreviewRequest(instrument_id="equity:NYSE:T", binding_id="b", entry_price=D("10"), stop_price=D("9.99"), desired_risk_pct=D("1"))
        return preview_paper_risk(snapshot=snap, protections=[], observation=quote, request=request)

    def plain(**fields) -> PaperAccount:
        return PaperAccount(account_id="a", name="A", base_currency="USD", commission_bps=D("0"), **fields)

    cash_only = preview(plain())
    leveraged = preview(plain(margin={"equity": PaperMargin(long_pct=D("25"))}))
    fixed = preview(plain(commission_type="fixed_per_order", commission_fixed=D("10")))
    assert cash_only.recommended_quantity == D("100")
    assert leveraged.recommended_quantity == D("400")
    # The fixed commission comes out of the buying power once.
    assert fixed.recommended_quantity == D("99")


def test_a_fractional_position_closes_to_the_satoshi_rounded_up() -> None:
    leveraged = account(margin={"crypto": PaperMargin(long_pct=D("10"))})
    positions = [PaperMarginPosition("crypto:BTC", D("1.5"), D("60000"), D("50000"))]
    status = paper_margin_status(leveraged, D("-68001"), D("0"), positions)
    [(_, quantity)] = paper_margin_call_closes(leveraged, status, positions)
    assert quantity == quantity.quantize(D("0.00000001"))
    assert quantity * D("50000") * D("0.1") >= status.maintenance - status.equity
