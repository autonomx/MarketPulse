from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

from app.apps.trading.gapper_dataset import (
    GapperCandidate,
    freeze_gapper_universe,
    gapper_universe_fingerprint,
)
from app.apps.trading.market_evidence import SourceMemberDisposition
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_data_integrity import (
    assess_universe_integrity,
    finviz_atomic_source_locator,
)
from app.apps.trading.strategy_monitor import (
    _current_session_1m_integrity,
    _finalized_bars_for_session,
)


INSTRUMENT = "equity:NASDAQ:TEST"
SESSION = date(2026, 9, 1)
PREOPEN = datetime(2026, 9, 1, 13, 20, tzinfo=timezone.utc)
OPEN = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)


def candidate(*, complete: bool = True) -> GapperCandidate:
    return GapperCandidate(
        instrument_id=INSTRUMENT,
        binding_id="yahoo:historical_polling:equity:NASDAQ:TEST",
        observed_at=PREOPEN,
        evidence_observed_at={"finviz_top_gainers": PREOPEN},
        previous_close=Decimal("8"),
        premarket_price=Decimal("10"),
        gap_pct=Decimal("25"),
        premarket_volume=Decimal("50000"),
        premarket_dollar_volume=Decimal("500000"),
        premarket_bar_count=20 if complete else 0,
        tod_rvol=Decimal("5") if complete else None,
        market_data_complete=complete,
        data_quality_flags=() if complete else ("PREMARKET_BARS_MISSING", "TOD_RVOL_MISSING"),
        spread_bps=Decimal("40") if complete else None,
        discovery_rank=1,
    )


def disposition() -> SourceMemberDisposition:
    return SourceMemberDisposition(
        symbol="TEST",
        source_rank=1,
        status="materialized",
        instrument_id=INSTRUMENT,
    )


def bar(start: datetime) -> MarketBar:
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("10"),
        high=Decimal("10.1"),
        low=Decimal("9.9"),
        close=Decimal("10"),
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def test_legacy_candidate_defaults_preserve_historical_universe_fingerprint() -> None:
    legacy_candidate = candidate().model_copy(
        update={
            "premarket_bar_count": None,
            "market_data_complete": True,
            "data_quality_flags": (),
        }
    )
    legacy_payload = {
        "universe_id": "legacy-fingerprint",
        "session_date": SESSION.isoformat(),
        "evaluation_time": PREOPEN.isoformat(),
        "discovery_source": "provider",
        "candidates": [
            legacy_candidate.model_dump(
                mode="json",
                exclude={
                    "premarket_bar_count",
                    "market_data_complete",
                    "data_quality_flags",
                    "research_quality_flags",
                    "premarket_liquidity",
                    "market_evidence_policy_version",
                },
            )
        ],
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    actual = gapper_universe_fingerprint(
        universe_id="legacy-fingerprint",
        session_date=SESSION,
        evaluation_time=PREOPEN,
        discovery_source="provider",
        candidates=[legacy_candidate],
    )

    assert actual == expected


def test_new_integrity_evidence_changes_universe_fingerprint() -> None:
    legacy = candidate().model_copy(
        update={
            "premarket_bar_count": None,
            "market_data_complete": True,
            "data_quality_flags": (),
        }
    )
    current = legacy.model_copy(update={"premarket_bar_count": 20})

    legacy_fingerprint = gapper_universe_fingerprint(
        universe_id="integrity-fingerprint",
        session_date=SESSION,
        evaluation_time=PREOPEN,
        discovery_source="provider",
        candidates=[legacy],
    )
    current_fingerprint = gapper_universe_fingerprint(
        universe_id="integrity-fingerprint",
        session_date=SESSION,
        evaluation_time=PREOPEN,
        discovery_source="provider",
        candidates=[current],
    )

    assert current_fingerprint != legacy_fingerprint


def test_legacy_paginated_finviz_archive_is_not_prospective_evidence() -> None:
    snapshot = freeze_gapper_universe(
        universe_id="legacy-finviz",
        session_date=SESSION,
        evaluation_time=PREOPEN,
        discovery_source="finviz",
        source_locator="https://finviz.com/screener?v=340&s=ta_topgainers",
        source_candidate_symbols=("TEST",),
        candidates=[candidate()],
    )

    integrity = assess_universe_integrity(snapshot)

    assert integrity.cohort_integrity == "invalid"
    assert integrity.cohort_complete is False
    assert integrity.prospective_eligible is False
    assert "FINVIZ_ATOMIC_COHORT_UNPROVEN" in integrity.reason_codes


def test_atomic_preopen_finviz_archive_is_prospectively_eligible() -> None:
    snapshot = freeze_gapper_universe(
        universe_id="atomic-finviz",
        session_date=SESSION,
        evaluation_time=PREOPEN,
        discovery_source="finviz",
        source_locator=finviz_atomic_source_locator(
            "https://finviz.com/screener?v=340&s=ta_topgainers"
        ),
        source_candidate_symbols=("TEST",),
        source_member_dispositions=(disposition(),),
        candidates=[candidate()],
    )

    integrity = assess_universe_integrity(snapshot)

    assert integrity.capture_on_time is True
    assert integrity.cohort_complete is True
    assert integrity.cohort_integrity == "valid"
    assert integrity.market_data_complete is True
    assert integrity.prospective_eligible is True


def test_candidate_market_data_incomplete_does_not_invalidate_atomic_source_cohort() -> None:
    snapshot = freeze_gapper_universe(
        universe_id="atomic-incomplete-candidate",
        session_date=SESSION,
        evaluation_time=PREOPEN,
        discovery_source="finviz",
        source_locator=finviz_atomic_source_locator(
            "https://finviz.com/screener?v=340&s=ta_topgainers"
        ),
        source_candidate_symbols=("TEST",),
        source_member_dispositions=(disposition(),),
        candidates=[candidate(complete=False)],
    )

    integrity = assess_universe_integrity(snapshot)

    assert integrity.prospective_eligible is True
    assert integrity.market_data_complete is False
    assert "CANDIDATE_MARKET_DATA_INCOMPLETE" in integrity.reason_codes


def test_session_filter_never_uses_previous_day_bars() -> None:
    prior = bar(datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc))
    current = bar(OPEN)

    filtered = _finalized_bars_for_session([prior, current], SESSION)

    assert filtered == [current]


def test_current_session_integrity_requires_the_opening_minute() -> None:
    missing_open = [bar(OPEN + timedelta(minutes=30))]
    ready = [bar(OPEN), bar(OPEN + timedelta(minutes=1))]

    assert _current_session_1m_integrity(
        [],
        session_date=SESSION,
        observed_at=PREOPEN,
    ) == (False, "CURRENT_SESSION_NOT_STARTED")
    assert _current_session_1m_integrity(
        missing_open,
        session_date=SESSION,
        observed_at=OPEN + timedelta(minutes=31),
    ) == (False, "OPENING_1M_HISTORY_INCOMPLETE")
    assert _current_session_1m_integrity(
        ready,
        session_date=SESSION,
        observed_at=OPEN + timedelta(minutes=2),
    ) == (True, "CURRENT_SESSION_1M_READY")
