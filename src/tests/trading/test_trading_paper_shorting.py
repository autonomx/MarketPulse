"""Paper shorting (TVP-7.2a): short risk entries, their risk and their brackets."""
from __future__ import annotations

from decimal import Decimal

from app.apps.trading.paper import PaperAccountCreate, PaperOrder, PaperPosition
from app.apps.trading.paper_protection import PaperPositionProtection
from app.apps.trading.paper_risk import (
    PaperRiskOrderRequest,
    PaperRiskPreviewRequest,
    paper_account_open_risk,
    preview_paper_risk,
    risk_order_request,
    risk_protection_request,
)

from .test_trading_paper_risk_authority import BINDING, INSTRUMENT, DailyPnl, Protections, Repo, _client, observation, snapshot


def _short_account(allow_short: bool = True):
    base = snapshot()
    return base.model_copy(update={"account": base.account.model_copy(update={"allow_short": allow_short})})


def _preview(stop: str, entry: str = "10", *, allow_short: bool = True, side: str = "sell"):
    return preview_paper_risk(
        snapshot=_short_account(allow_short),
        protections=[],
        observation=observation(),
        request=PaperRiskPreviewRequest(instrument_id=INSTRUMENT, binding_id=BINDING, entry_price=Decimal(entry), stop_price=Decimal(stop), side=side),
    )


def test_a_short_is_sized_from_its_stop_above_the_entry() -> None:
    preview = _preview("11")
    assert preview.allowed, preview.reason_codes
    # 0.35% of 100,000 = 350 at 1.00 a share.
    assert preview.recommended_quantity == Decimal("350")
    assert "STOP_NOT_ABOVE_ENTRY" in _preview("9").reason_codes


def test_shorting_needs_the_account_setting() -> None:
    assert "SHORTING_NOT_ALLOWED" in _preview("11", allow_short=False).reason_codes
    # A long entry is unaffected by the setting.
    assert _preview("9", allow_short=False, side="buy").allowed


def test_new_accounts_default_to_no_shorting() -> None:
    # Strategies create accounts through this model: shorting stays off unless asked for.
    assert PaperAccountCreate(account_id="a", name="A").allow_short is False


def test_open_risk_counts_shorts_and_short_entries() -> None:
    short_position = PaperPosition(instrument_id=INSTRUMENT, quantity=Decimal("-100"), average_cost=Decimal("10"), realized_pnl=Decimal("0"))
    short_entry = PaperOrder(
        account_id="paper-1", order_id="s-1", instrument_id="equity:NYSE:OTHER", side="sell", order_type="limit",
        quantity=Decimal("50"), limit_price=Decimal("20"), idempotency_key="s-1",
    )
    state = snapshot(positions=[short_position], open_orders=[short_entry])
    protections = [
        PaperPositionProtection(account_id="paper-1", instrument_id=INSTRUMENT, stop_loss=Decimal("11"), status="active"),
        PaperPositionProtection(account_id="paper-1", instrument_id="equity:NYSE:OTHER", entry_order_id="s-1", stop_loss=Decimal("21"), status="pending_entry"),
    ]
    total, unprotected = paper_account_open_risk(state, protections)
    assert (total, unprotected) == (Decimal("150"), 0)  # 100 x 1 + 50 x 1
    assert paper_account_open_risk(state, [])[1] == 2


def test_a_short_risk_order_sells_and_trails_above() -> None:
    intent = PaperRiskOrderRequest(
        order_id="s", instrument_id=INSTRUMENT, side="sell", order_type="limit", trigger_price=Decimal("10"),
        stop_loss=Decimal("11"), take_profit=Decimal("8"), trailing_stop_loss=True, idempotency_key="s",
    )
    order = risk_order_request(intent, entry_price=Decimal("10"), quantity=Decimal("5"))
    assert (order.side, order.limit_price) == ("sell", Decimal("10"))
    assert risk_protection_request(intent, entry_price=Decimal("10")).trail_amount == Decimal("1")


def test_the_risk_order_endpoint_places_a_short_at_the_bid() -> None:
    events: list[str] = []
    repo = Repo(events)
    repo.current = _short_account()
    response = _client(repo, Protections(events), DailyPnl()).post(
        "/api/trading/paper/accounts/paper-1/risk-orders",
        json={"order_id": "short-1", "instrument_id": INSTRUMENT, "binding_id": BINDING, "side": "sell",
              "order_type": "market", "stop_loss": "11.01", "take_profit": "8", "idempotency_key": "short-1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["order"]["side"], body["order"]["reference_price"]) == ("sell", "9.99")
    assert body["protection"]["stop_loss"] == "11.01"
    assert events == ["arm", "order"]
    refused = Repo([])
    refused.current = _short_account(allow_short=False)
    blocked = _client(refused, Protections([])).post(
        "/api/trading/paper/accounts/paper-1/risk-orders",
        json={"order_id": "short-2", "instrument_id": INSTRUMENT, "binding_id": BINDING, "side": "sell",
              "order_type": "market", "stop_loss": "11.01", "idempotency_key": "short-2"},
    )
    assert blocked.status_code == 409
    assert "SHORTING_NOT_ALLOWED" in blocked.json()["detail"]["reason_codes"]


def test_a_backtest_with_shorting_reverses_on_crosses() -> None:
    import sys
    from pathlib import Path

    from app.apps.trading.backtest import BacktestRequest, MovingAverageCrossStrategy, run_backtest

    sys.path.insert(0, str(Path(__file__).parent))
    from test_trading_phase12_replay_backtest import NOW, snapshot as frozen_snapshot

    def run(allow_short: bool):
        return run_backtest(
            frozen_snapshot(),
            BacktestRequest(
                strategy=MovingAverageCrossStrategy(fast_period=2, slow_period=3),
                execution_policy={"commission_bps": "10", "slippage_bps": "5", "allow_short": allow_short},
                initial_cash=Decimal("10000"),
            ),
            run_id="short",
            now=NOW,
        )

    long_only, shorting = run(False), run(True)
    assert all(trade.position_after >= 0 for trade in long_only.trades)
    assert any(trade.position_after < 0 for trade in shorting.trades)
    # A reversal is a close and an open filled at the same bar.
    fill_bars = [trade.fill_bar_index for trade in shorting.trades]
    assert len(fill_bars) > len(set(fill_bars))
    for point in shorting.equity_curve:
        bar = frozen_snapshot().bars[point.point_index]
        assert point.equity == point.cash + point.position * bar.close
    assert shorting.exposure_percent > long_only.exposure_percent
    assert shorting.economic_result_fingerprint == run(True).economic_result_fingerprint
    assert shorting.economic_result_fingerprint != long_only.economic_result_fingerprint


def _short_position(quantity: str = "-100", cost: str = "10") -> PaperPosition:
    return PaperPosition(instrument_id=INSTRUMENT, quantity=Decimal(quantity), average_cost=Decimal(cost), realized_pnl=Decimal("0"))


def test_a_short_is_bought_back_through_the_raw_order_route() -> None:
    events: list[str] = []
    repo = Repo(events)
    repo.current = snapshot(positions=[_short_position()])
    client = _client(repo, Protections(events))
    body = {"order_id": "cover", "instrument_id": INSTRUMENT, "side": "buy", "order_type": "market", "quantity": "60",
            "reference_price": "10", "idempotency_key": "cover"}
    assert client.post("/api/trading/paper/accounts/paper-1/orders", json=body).status_code == 201
    # More than the short (less what is already working) would open a long: that needs a risk entry.
    over = {**body, "order_id": "over", "idempotency_key": "over", "quantity": "60"}
    refused = client.post("/api/trading/paper/accounts/paper-1/orders", json=over)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "paper_entry_requires_server_risk_authority"


def test_open_shorts_hold_buying_power_and_covers_are_not_entries() -> None:
    held = snapshot(positions=[_short_position("-3000", "10")])
    preview = preview_paper_risk(
        snapshot=held.model_copy(update={"account": held.account.model_copy(update={"allow_short": True})}),
        protections=[PaperPositionProtection(account_id="paper-1", instrument_id=INSTRUMENT, stop_loss=Decimal("11"), status="active")],
        observation=observation(),
        request=PaperRiskPreviewRequest(instrument_id="equity:NYSE:OTHER", entry_price=Decimal("10"), stop_price=Decimal("9.9"), desired_risk_pct=Decimal("1")),
    )
    # 100,000 less 2 x 30,000 held for the short leaves 40,000.
    assert preview.buying_power_before == Decimal("40000")
    cover = PaperOrder(account_id="paper-1", order_id="c", instrument_id=INSTRUMENT, side="buy", order_type="market",
                       quantity=Decimal("3000"), reference_price=Decimal("12"), idempotency_key="c")
    protections = [PaperPositionProtection(account_id="paper-1", instrument_id=INSTRUMENT, stop_loss=Decimal("11"), status="exit_submitted")]
    assert paper_account_open_risk(snapshot(positions=[_short_position("-3000", "10")], open_orders=[cover]), protections) == (Decimal("3000"), 0)
