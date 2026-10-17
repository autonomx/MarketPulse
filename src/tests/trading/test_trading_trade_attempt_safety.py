from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies import evaluate_gap_pullback
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_monitor import _trade_attempt_id


INSTRUMENT = "equity:NASDAQ:TEST"
OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, open_: str, high: str, low: str, close: str, volume: str) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def _candidate() -> GapperCandidate:
    return GapperCandidate(
        instrument_id=INSTRUMENT,
        binding_id="fixture:TEST",
        previous_close=Decimal("8"),
        premarket_price=Decimal("10.4"),
        gap_pct=Decimal("30"),
        premarket_volume=Decimal("100000"),
        premarket_dollar_volume=Decimal("1040000"),
        tod_rvol=Decimal("3"),
        market_cap=Decimal("50000000"),
        float_shares=Decimal("5000000"),
        spread_bps=Decimal("40"),
        discovery_rank=1,
    )


def _pattern() -> list[MarketBar]:
    return [
        _bar(0, "10", "10.4", "9.9", "10.3", "100"),
        _bar(1, "10.3", "11.2", "10.2", "11.0", "120"),
        _bar(2, "11.0", "11.05", "10.5", "10.6", "80"),
        _bar(3, "10.6", "10.7", "9.8", "10.0", "70"),
        _bar(4, "10.0", "10.9", "9.95", "10.8", "80"),
        _bar(5, "10.8", "11.1", "10.7", "11.0", "90"),
        _bar(6, "10.9", "11.0", "10.4", "10.5", "80"),
        _bar(7, "10.5", "10.7", "10.2", "10.3", "70"),
        _bar(8, "10.3", "11.0", "10.25", "10.9", "80"),
        _bar(9, "10.9", "11.8", "10.85", "11.7", "400"),
        _bar(10, "11.75", "12.0", "11.6", "11.9", "1000"),
    ]


def _config() -> GapPullbackConfig:
    return GapPullbackConfig(
        pivot_left_bars=1,
        pivot_right_bars=1,
        volume_lookback_bars=5,
        breakout_volume_ratio=Decimal("1.25"),
    )


def test_1x_signal_is_emitted_only_on_its_first_causal_finalized_bar() -> None:
    bars = _pattern()
    signal = evaluate_gap_pullback(_candidate(), bars[:10], _config())
    stale = evaluate_gap_pullback(_candidate(), bars, _config())

    assert signal.state == "entry_ready"
    assert signal.signal is not None
    assert stale.signal is None
    assert stale.state == "lower_high_break"
    assert stale.reason_code == "BREAKOUT_ALREADY_PASSED"


def test_trade_attempt_identity_is_stable_per_signal_and_distinct_by_signal_time() -> None:
    first = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    second = first + timedelta(minutes=7)

    first_id = _trade_attempt_id("strategy-1", INSTRUMENT, first)
    assert first_id == _trade_attempt_id("strategy-1", INSTRUMENT, first)
    assert first_id != _trade_attempt_id("strategy-1", INSTRUMENT, second)
    assert first_id != _trade_attempt_id("strategy-2", INSTRUMENT, first)


def test_auto_paper_arms_protection_persists_risk_and_deduplicates_attempt() -> None:
    trading = Path("src/app/apps/trading")
    paths = [*sorted(trading.glob("strategy_monitor*.py")), trading / "strategy_entry_path.py"]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    entry_block = source.split(
        'order_key = _key(config.strategy_id, trade_attempt_id, "entry")', 1
    )[1]
    arm = entry_block.index(
        "await asyncio.to_thread(strategy_repository.save_protection, protection)"
    )
    submit = entry_block.index("paper_repository.place_entry")

    assert arm < submit
    assert 'event_type="risk_decision"' in source
    assert '"trade_attempt_id": trade_attempt_id' in source
    assert "submitted_attempts" in source
    assert "trade_attempt_already_submitted" in source


def test_trade_attempt_migration_versions_repeat_symbol_correlation_atomically() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0046_trading_trade_attempt_correlation.sql"
    ).read_text()
    for token in (
        "trade_attempt_id",
        "trade-lifecycle-v2",
        "idx_omnix_trading_strategy_events_trade_attempt",
        "idx_omnix_trading_paper_trade_records_trade_attempt",
        "payload ->> 'trade_attempt_id'",
        "event_type = 'entry_order_submitted'",
        "omnix_trading_correlate_paper_trade_record",
        "IF event_row.trade_attempt_id IS NOT NULL THEN",
        "NEW.setup_id := event_row.setup_id",
        "NEW.trade_intent_id := event_row.trade_intent_id",
        "NEW.risk_decision_id := event_row.risk_decision_id",
    ):
        assert token in migration
    assert "CREATE TABLE" not in migration
