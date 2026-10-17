from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_evidence import (
    EvidenceTimestamps,
    FrozenForecast,
    PremarketEvidenceItem,
    PremarketEvidenceSnapshot,
    SIPTradeEvent,
    select_analysis_session_prices,
)
from app.apps.trading.prospective_prediction_scoring import (
    build_formal_outcome_labels,
    canonical_formal_5m_bars,
    freeze_formal_research_portfolios,
    validate_formal_premarket_snapshot,
)

UTC = timezone.utc
SESSION = date(2026, 9, 16)
OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=UTC)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
INSTRUMENT = "equity:NASDAQ:TEST"


def _prices():
    return select_analysis_session_prices(
        [
            SIPTradeEvent(
                instrument_id=INSTRUMENT,
                price=Decimal("10"),
                event_timestamp=OPEN,
                received_timestamp=OPEN + timedelta(milliseconds=1),
                provider_event_id="open",
                sequence=1,
            ),
            SIPTradeEvent(
                instrument_id=INSTRUMENT,
                price=Decimal("12"),
                event_timestamp=CLOSE - timedelta(milliseconds=1),
                received_timestamp=CLOSE + timedelta(milliseconds=1),
                provider_event_id="close",
                sequence=2,
            ),
        ],
        session_date=SESSION,
        outcome_state="FINAL",
    )


def _bar(
    *,
    interval: str,
    index: int,
    close: str,
    minutes: int,
    provider: str = "alpaca_sip",
    revision: int = 1,
    received_offset_seconds: int = 1,
) -> MarketBar:
    start = OPEN + timedelta(minutes=minutes * index)
    value = Decimal(close)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        open=value - Decimal("0.01"),
        high=value + Decimal("0.02"),
        low=value - Decimal("0.03"),
        close=value,
        volume=Decimal("1000"),
        is_final=True,
        adjustment_mode=AdjustmentMode.RAW,
        session="regular",
        provider=provider,
        provider_sequence=index,
        ingestion_revision=revision,
        received_at=start + timedelta(minutes=minutes, seconds=received_offset_seconds),
    )


def _snapshot(*, evidence_frozen_offset_seconds: int = -20) -> PremarketEvidenceSnapshot:
    cutoff = OPEN
    item = PremarketEvidenceItem(
        evidence_id="evidence-1",
        instrument_id=INSTRUMENT,
        source_type="news",
        source_locator="https://example.test/news",
        timestamps=EvidenceTimestamps(
            published_at=cutoff - timedelta(minutes=10),
            observed_at=cutoff - timedelta(seconds=30),
            ingested_at=cutoff - timedelta(seconds=25),
            frozen_at=cutoff + timedelta(seconds=evidence_frozen_offset_seconds),
        ),
    )
    return PremarketEvidenceSnapshot(
        snapshot_id="snapshot-1",
        session_date=SESSION,
        prediction_cutoff_at=cutoff,
        frozen_at=cutoff - timedelta(seconds=10),
        evidence=(item,),
    )


def _forecast(*, snapshot_id: str = "snapshot-1", frozen_at: datetime | None = None) -> FrozenForecast:
    return FrozenForecast(
        instrument_id=INSTRUMENT,
        evidence_snapshot_id=snapshot_id,
        feature_vector_fingerprint="features-1",
        frozen_at=frozen_at or OPEN - timedelta(seconds=5),
        p_close_above_open=Decimal("0.60"),
        p_persistent_uptrend=Decimal("0.55"),
    )


def test_formal_scoring_ignores_mixed_1m_bars_and_uses_only_5m() -> None:
    five_minute = [
        _bar(interval="5m", index=i, close=str(Decimal("10.1") + Decimal(i) * Decimal("0.025")), minutes=5)
        for i in range(78)
    ]
    # Deliberately bearish 1m data must not alter the formal 5m label.
    one_minute = [
        _bar(interval="1m", index=i, close=str(Decimal("10") - Decimal(i) * Decimal("0.01")), minutes=1)
        for i in range(60)
    ]

    measurements, labels = build_formal_outcome_labels(
        prices=_prices(),
        bars=[*one_minute, *five_minute],
    )

    assert measurements.observed_bar_count == 78
    assert labels.persistent_uptrend is True


def test_formal_scoring_fails_closed_without_5m_data() -> None:
    with pytest.raises(ValueError, match="requires_raw_final_5m_bars"):
        build_formal_outcome_labels(
            prices=_prices(),
            bars=[_bar(interval="1m", index=0, close="10.1", minutes=1)],
        )


def test_canonical_5m_series_resolves_revisions_without_double_counting() -> None:
    old = _bar(interval="5m", index=0, close="10.10", minutes=5, revision=1)
    revised = _bar(
        interval="5m",
        index=0,
        close="10.25",
        minutes=5,
        revision=2,
        received_offset_seconds=10,
    )

    canonical = canonical_formal_5m_bars([old, revised])

    assert len(canonical) == 1
    assert canonical[0].close == Decimal("10.25")
    assert canonical[0].ingestion_revision == 2


def test_canonical_5m_series_rejects_provider_blending() -> None:
    with pytest.raises(ValueError, match="requires_single_provider"):
        canonical_formal_5m_bars(
            [
                _bar(interval="5m", index=0, close="10.1", minutes=5, provider="alpaca_sip"),
                _bar(interval="5m", index=1, close="10.2", minutes=5, provider="other_sip"),
            ]
        )


def test_formal_snapshot_rejects_evidence_frozen_after_snapshot() -> None:
    snapshot = _snapshot(evidence_frozen_offset_seconds=-5)
    with pytest.raises(ValueError, match="formal_evidence_frozen_after_snapshot"):
        validate_formal_premarket_snapshot(snapshot)


def test_formal_snapshot_rejects_freeze_before_ingestion() -> None:
    cutoff = OPEN
    item = PremarketEvidenceItem(
        evidence_id="evidence-ordering",
        source_type="news",
        source_locator="https://example.test/news",
        timestamps=EvidenceTimestamps(
            observed_at=cutoff - timedelta(seconds=30),
            ingested_at=cutoff - timedelta(seconds=20),
            frozen_at=cutoff - timedelta(seconds=25),
        ),
    )
    snapshot = PremarketEvidenceSnapshot(
        snapshot_id="snapshot-ordering",
        session_date=SESSION,
        prediction_cutoff_at=cutoff,
        frozen_at=cutoff - timedelta(seconds=10),
        evidence=(item,),
    )
    with pytest.raises(ValueError, match="formal_evidence_frozen_before_ingested"):
        validate_formal_premarket_snapshot(snapshot)


def test_formal_portfolio_freeze_is_bound_to_snapshot_and_cutoff() -> None:
    snapshot = _snapshot()
    portfolios = freeze_formal_research_portfolios(
        snapshot,
        [_forecast()],
        frozen_at=OPEN - timedelta(seconds=1),
    )
    assert len(portfolios) == 4
    assert portfolios[0].positions[0].instrument_id == INSTRUMENT

    with pytest.raises(ValueError, match="forecast_snapshot_mismatch"):
        freeze_formal_research_portfolios(
            snapshot,
            [_forecast(snapshot_id="other-snapshot")],
            frozen_at=OPEN - timedelta(seconds=1),
        )

    with pytest.raises(ValueError, match="formal_portfolios_frozen_after_prediction_cutoff"):
        freeze_formal_research_portfolios(
            snapshot,
            [_forecast()],
            frozen_at=OPEN + timedelta(seconds=1),
        )
