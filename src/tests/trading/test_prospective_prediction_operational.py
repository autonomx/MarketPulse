from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.apps.trading.prospective_prediction_operational as operational
from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_operational import (
    build_cash_preserving_shadow_portfolio,
    build_operational_formal_outcome,
    evaluate_operational_confirmation,
    load_operational_premarket_state,
)
from app.apps.trading.prospective_prediction_v4 import (
    FinvizFrozenCohort,
    TradeAuthorizationReceipt,
)
from app.apps.trading.strategies.models import (
    GapPullbackFeatures,
    GapPullbackResult,
    StrategySignal,
)


SESSION = date(2026, 9, 21)
CUTOFF = datetime(2026, 9, 21, 13, 29, tzinfo=timezone.utc)
OPEN = datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc)


def _cohort() -> FinvizFrozenCohort:
    return FinvizFrozenCohort(
        cohort_id="finviz-2026-09-21",
        session_date=SESSION,
        discovery_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(minutes=5),
        symbols=("AAA",),
    )


def _candidate(**updates) -> GapperCandidate:
    payload = {
        "instrument_id": "equity:US:AAA",
        "binding_id": "alpaca_sip:test",
        "observed_at": CUTOFF - timedelta(minutes=6),
        "evidence_observed_at": {"finviz_top_gainers": CUTOFF - timedelta(minutes=6)},
        "previous_close": Decimal("10"),
        "premarket_price": Decimal("15"),
        "gap_pct": Decimal("50"),
        "premarket_volume": Decimal("500000"),
        "premarket_dollar_volume": Decimal("7500000"),
        "tod_rvol": Decimal("8"),
        "float_shares": Decimal("2000000"),
        "spread_bps": Decimal("45"),
        "catalyst_evidence_ids": ("news-1",),
    }
    payload.update(updates)
    return GapperCandidate(**payload)


def _bar(
    *,
    start: datetime,
    interval: str,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1000",
    session: str = "premarket",
    provider_event_id: str | None = None,
    received_at: datetime | None = None,
) -> MarketBar:
    minutes = 1 if interval == "1m" else 5
    end = start + timedelta(minutes=minutes)
    return MarketBar(
        instrument_id="equity:US:AAA",
        interval=interval,
        start_time=start,
        end_time=end,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        provider="alpaca_sip",
        provider_event_id=provider_event_id or f"{interval}-{start.isoformat()}",
        provider_sequence=int(start.timestamp()),
        received_at=received_at or end,
        session=session,
        adjustment_mode=AdjustmentMode.RAW,
    )


class _Service:
    def __init__(self, bars=None, error: Exception | None = None):
        self._bars = list(bars or [])
        self._error = error
        self.calls = []

    def bars(self, instrument_id, interval, limit, binding_id):
        self.calls.append((instrument_id, interval, limit, binding_id))
        if self._error is not None:
            raise self._error
        return SimpleNamespace(bars=list(self._bars))


def test_premarket_operational_state_prefers_causal_raw_1m_enrichment() -> None:
    bars = [
        _bar(
            start=datetime(2026, 9, 21, 13, 10, tzinfo=timezone.utc),
            interval="1m",
            open_="14.50",
            high="14.70",
            low="14.40",
            close="14.60",
            volume="100000",
        ),
        _bar(
            start=datetime(2026, 9, 21, 13, 11, tzinfo=timezone.utc),
            interval="1m",
            open_="14.60",
            high="14.90",
            low="14.55",
            close="14.80",
            volume="120000",
        ),
        _bar(
            start=datetime(2026, 9, 21, 13, 12, tzinfo=timezone.utc),
            interval="1m",
            open_="14.80",
            high="15.10",
            low="14.75",
            close="15.00",
            volume="150000",
        ),
    ]
    result = load_operational_premarket_state(
        market_service=_Service(bars),
        cohort=_cohort(),
        candidate=_candidate(),
        snapshot_id="state-aaa",
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(seconds=1),
        prior_1d_return_pct=Decimal("3"),
        prior_3d_return_pct=Decimal("8"),
    )
    assert result.source_mode == "CANONICAL_RAW_1M"
    assert result.market_state.live_value("gap_from_prior_close_pct") == Decimal("50")
    assert result.market_state.live_value("float_turnover") == Decimal("370000") / Decimal("2000000")
    assert result.market_state.live_value("distance_from_premarket_vwap_pct") is not None
    assert result.evidence_quality.quality == "DEGRADED"
    assert "PREMARKET_REAL_BAR_COUNT_BELOW_12" in result.evidence_quality.reasons
    assert "PREMARKET_LATEST_BAR_STALE_OR_UNKNOWN" in result.evidence_quality.reasons


def test_premarket_operational_state_degrades_to_candidate_without_neutral_imputation() -> None:
    result = load_operational_premarket_state(
        market_service=_Service(error=RuntimeError("provider down")),
        cohort=_cohort(),
        candidate=_candidate(),
        snapshot_id="fallback-state",
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(seconds=1),
    )
    assert result.source_mode == "CANDIDATE_FALLBACK"
    assert result.evidence_quality.quality == "DEGRADED"
    assert result.market_state.live_value("gap_from_prior_close_pct") == Decimal("50")
    assert result.market_state.live_value("distance_from_premarket_vwap_pct") is None
    assert result.warnings == ("RAW_1M_FETCH_FAILED:RuntimeError",)


def _regular_5m_bars() -> list[MarketBar]:
    bars: list[MarketBar] = []
    price = Decimal("10")
    for index in range(78):
        start = OPEN + timedelta(minutes=index * 5)
        next_price = price + Decimal("0.02")
        bars.append(
            _bar(
                start=start,
                interval="5m",
                open_=str(price),
                high=str(next_price + Decimal("0.02")),
                low=str(price - Decimal("0.01")),
                close=str(next_price),
                volume="10000",
                session="regular",
            )
        )
        price = next_price
    return bars


def test_postclose_operational_bundle_derives_full_measurements_and_labels() -> None:
    bundle = build_operational_formal_outcome(
        session_date=SESSION,
        bars=_regular_5m_bars(),
    )
    assert bundle.price_authority == "RAW_SIP_5M_FALLBACK"
    assert bundle.canonical_bar_count == 78
    assert bundle.session_boundary_complete is True
    assert bundle.measurements.normalized_slope > 0
    assert bundle.measurements.vwap_occupancy > Decimal("0.60")
    assert bundle.measurements.mfe_from_open > 0
    assert bundle.labels.close_above_open is True
    assert bundle.labels.persistent_uptrend is True
    assert bundle.labels.session_regime == "PERSISTENT_UP"


def test_postclose_fallback_requires_both_regular_session_boundaries() -> None:
    bars = _regular_5m_bars()[1:]
    with pytest.raises(ValueError, match="raw_5m_fallback_open_boundary_missing"):
        build_operational_formal_outcome(session_date=SESSION, bars=bars)


def _result(state: str, reason: str, *, with_signal: bool = False) -> GapPullbackResult:
    signal = None
    if with_signal:
        signal = StrategySignal(
            instrument_id="equity:US:AAA",
            state="entry_ready",
            entry_price=Decimal("11"),
            stop_price=Decimal("10.50"),
            target_price=Decimal("12"),
            risk_per_share=Decimal("0.50"),
            reason_code="FAILED_SELL_OFF_CONFIRMED",
            quality_score=8,
        )
    return GapPullbackResult(
        instrument_id="equity:US:AAA",
        state=state,
        reason_code=reason,
        features=GapPullbackFeatures(
            gap_pct=Decimal("50"),
            quality_score=8 if with_signal else 0,
        ),
        transitions=(state,),
        signal=signal,
        evaluated_bar_count=10,
    )


def test_operational_confirmation_uses_existing_failed_selloff_authority(monkeypatch) -> None:
    monkeypatch.setattr(
        operational,
        "evaluate_gap_pullback",
        lambda candidate, bars, config: _result(
            "entry_ready",
            "FAILED_SELL_OFF_CONFIRMED",
            with_signal=True,
        ),
    )
    bars = [
        _bar(
            start=OPEN + timedelta(minutes=index),
            interval="1m",
            open_="10",
            high="11",
            low="9.8",
            close="10.8",
            volume="10000",
            session="regular",
        )
        for index in range(10)
    ]
    evaluation = evaluate_operational_confirmation(
        candidate=_candidate(),
        bars=bars,
        observed_at=OPEN + timedelta(minutes=10),
    )
    assert evaluation.final_confirmation_state == "CONFIRMED_LONG"
    assert evaluation.actionability == "ACT"
    assert [receipt.new_state for receipt in evaluation.receipts] == [
        "OBSERVE_INITIAL_STRUCTURE",
        "OBSERVE_PULLBACK",
        "CONFIRMED_LONG",
    ]
    assert evaluation.signal_entry_price == Decimal("11")
    assert evaluation.signal_quality_score == 8


def test_operational_confirmation_suspends_on_data_quality_and_can_resume(monkeypatch) -> None:
    suspended = evaluate_operational_confirmation(
        candidate=_candidate(),
        bars=(),
        observed_at=OPEN + timedelta(minutes=6),
        data_quality_ok=False,
        data_quality_reason="CURRENT_TAPE_GAP",
    )
    assert suspended.final_confirmation_state == "SUSPENDED_DATA_QUALITY"
    assert suspended.actionability == "WATCH"

    monkeypatch.setattr(
        operational,
        "evaluate_gap_pullback",
        lambda candidate, bars, config: _result(
            "first_low_confirmed",
            "WAITING_FOR_BOUNCE_HIGH",
        ),
    )
    bars = [
        _bar(
            start=OPEN + timedelta(minutes=index),
            interval="1m",
            open_="10",
            high="10.5",
            low="9.9",
            close="10.2",
            session="regular",
        )
        for index in range(8)
    ]
    resumed = evaluate_operational_confirmation(
        candidate=_candidate(),
        bars=bars,
        observed_at=OPEN + timedelta(minutes=8),
        previous_state="SUSPENDED_DATA_QUALITY",
    )
    assert resumed.final_confirmation_state == "OBSERVE_PULLBACK"
    assert resumed.actionability == "WATCH"


def _authorization(
    fingerprint: str,
    *,
    net: str,
    notional: str = "1000",
    max_positive: str | None = None,
    decision: str = "LONG",
    minute: int = 10,
) -> TradeAuthorizationReceipt:
    return TradeAuthorizationReceipt(
        instrument_id="equity:US:AAA",
        forecast_fingerprint=fingerprint,
        confirmation_receipt_fingerprint=f"confirmation-{fingerprint}",
        decision_at=OPEN + timedelta(minutes=minute),
        decision=decision,
        notional=Decimal(notional),
        max_positive_alpha_notional=(
            Decimal(max_positive) if max_positive is not None else None
        ),
        reference_price=Decimal("10"),
        observed_bid=Decimal("9.99"),
        observed_ask=Decimal("10.01"),
        observed_spread_bps=Decimal("20"),
        estimated_slippage_bps=Decimal("10"),
        estimated_impact_bps=Decimal("5"),
        gross_expected_return=Decimal("0.04"),
        net_expected_return=Decimal(net),
        cost_model_version="execution-cost-v1",
        evidence_fingerprint=f"execution-{fingerprint}",
    )


def test_cash_preserving_shadow_portfolio_caps_positions_and_does_not_redistribute() -> None:
    portfolio = build_cash_preserving_shadow_portfolio(
        [
            _authorization("a", net="0.05"),
            _authorization("b", net="0.04"),
            _authorization("c", net="0.03", max_positive="125"),
            _authorization("d", net="0.02"),
        ],
        starting_equity=Decimal("1000"),
        max_positions=3,
        max_position_fraction=Decimal("0.20"),
    )
    assert [position.allocation for position in portfolio.positions] == [
        Decimal("200"),
        Decimal("200"),
        Decimal("125"),
    ]
    assert portfolio.cash == Decimal("475")
    assert all(position.weight <= Decimal("0.20") for position in portfolio.positions)


def test_cash_preserving_shadow_portfolio_rejects_nonpositive_alpha_and_no_trade() -> None:
    portfolio = build_cash_preserving_shadow_portfolio(
        [
            _authorization("a", net="-0.01"),
            _authorization("b", net="0.02", decision="NO_TRADE"),
        ]
    )
    assert portfolio.positions == ()
    assert portfolio.cash == Decimal("1000")
