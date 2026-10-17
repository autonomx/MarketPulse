from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.apps.trading.market_evidence import (
    PremarketLiquidityEvidence,
    YAHOO_HARDENED_EVIDENCE_POLICY_VERSION,
    YAHOO_RELATIVE_VOLUME,
    evidence_authorizes_feature,
    premarket_evidence_feature_compatible,
)
from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.market_data_recovery import reconcile_recovery
from app.apps.trading.service import TradingMarketDataService
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.evidence_storage import MemoryEvidenceBackend
from app.apps.trading.yahoo_evidence import YahooEvidenceStore


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:TEST"



_BACKENDS: dict[object, MemoryEvidenceBackend] = {}


def _store(key) -> YahooEvidenceStore:
    """A store on the memory backend shared by every store built for ``key`` (a restart)."""
    return YahooEvidenceStore(_BACKENDS.setdefault(str(key), MemoryEvidenceBackend()))

def _bar(session_date: date, minute: int, *, volume: str = "100", price: str = "10") -> MarketBar:
    start = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        9,
        30,
        tzinfo=ET,
    ).astimezone(timezone.utc) + timedelta(minutes=minute)
    value = Decimal(price)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=value,
        high=value + Decimal("0.1"),
        low=value - Decimal("0.1"),
        close=value,
        volume=Decimal(volume),
        is_final=True,
        adjustment_mode=AdjustmentMode.RAW,
        session="regular",
        provider="yahoo",
        provider_event_id=str(int(start.timestamp())),
        received_at=start + timedelta(minutes=1, seconds=2),
    )


def _premarket_bar(session_date: date, minute: int, *, volume: str) -> MarketBar:
    start = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        4,
        0,
        tzinfo=ET,
    ).astimezone(timezone.utc) + timedelta(minutes=minute)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("10"),
        high=Decimal("10"),
        low=Decimal("10"),
        close=Decimal("10"),
        volume=Decimal(volume),
        is_final=True,
        adjustment_mode=AdjustmentMode.RAW,
        session="extended_pre",
        provider="yahoo",
        provider_event_id=str(int(start.timestamp())),
        received_at=start + timedelta(minutes=1, seconds=2),
    )


def test_yahoo_store_persists_and_reloads_finalized_one_minute_bars(tmp_path) -> None:
    store = _store(tmp_path)
    session_date = date(2026, 9, 17)
    bars = [_bar(session_date, 0), _bar(session_date, 1)]

    assert store.persist_market_bars(bars) == 2
    loaded = store.load_market_bars(
        INSTRUMENT,
        start=bars[0].start_time,
        end=bars[-1].end_time,
        session="regular",
    )

    assert [bar.start_time for bar in loaded] == [bar.start_time for bar in bars]
    assert all(bar.provider == "yahoo" for bar in loaded)


def test_yahoo_relative_rvol_uses_persistent_same_feed_baseline(tmp_path) -> None:
    store = _store(tmp_path)
    current = date(2026, 9, 17)
    historical_dates = [
        date(2026, 9, 16),
        date(2026, 9, 15),
        date(2026, 9, 14),
        date(2026, 9, 13),
        date(2026, 9, 12),
    ]
    for session_date in historical_dates:
        store.persist_market_bars(
            [
                _premarket_bar(session_date, 0, volume="50"),
                _premarket_bar(session_date, 1, volume="50"),
            ]
        )
    store.persist_market_bars(
        [
            _premarket_bar(current, 0, volume="100"),
            _premarket_bar(current, 1, volume="100"),
        ]
    )

    evidence = store.premarket_relative_volume(
        INSTRUMENT,
        datetime(2026, 9, 17, 4, 2, tzinfo=ET),
        minimum_baseline_sessions=5,
    )

    assert evidence.current_volume == Decimal("200")
    assert evidence.baseline_mean_volume == Decimal("100")
    assert evidence.relative_volume == Decimal("2")
    assert evidence.baseline_session_count == 5
    assert evidence.coverage_ratio == Decimal("1")


def test_hardened_yahoo_evidence_authorizes_relative_volume_not_consolidated() -> None:
    evidence = PremarketLiquidityEvidence(
        policy_version=YAHOO_HARDENED_EVIDENCE_POLICY_VERSION,
        provider="yahoo",
        feed="extended_hours",
        observed_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        current_premarket_volume=Decimal("1000"),
        current_premarket_dollar_volume=Decimal("10000"),
        tod_rvol=Decimal("5"),
        tod_rvol_numerator=Decimal("1000"),
        tod_rvol_denominator_mean=Decimal("200"),
        baseline_session_count=5,
        premarket_bar_count=10,
        nonzero_volume_bar_count=10,
        coverage_ratio=Decimal("1"),
        ready=True,
        volume_authority="provider_relative",
        volume_basis=YAHOO_RELATIVE_VOLUME,
        consolidated_volume_authority=False,
    )

    assert premarket_evidence_feature_compatible(evidence) is True
    assert evidence_authorizes_feature(evidence, "price_ohlc") is True
    assert evidence_authorizes_feature(evidence, "provider_relative_volume") is True
    assert evidence_authorizes_feature(evidence, YAHOO_RELATIVE_VOLUME) is True
    assert evidence_authorizes_feature(evidence, "consolidated_volume") is False
    assert evidence_authorizes_feature(evidence, "live_bid_ask") is False
    assert evidence_authorizes_feature(evidence, "execution_fill") is False


class _YahooRepairProvider:
    def __init__(self, session_date: date) -> None:
        self.session_date = session_date
        self.calls = 0

    def get_intraday_bars_range(
        self,
        instrument_id,
        *,
        start,
        end,
        include_extended_hours=True,
        cancellation=None,
    ):
        self.calls += 1
        return SimpleNamespace(bars=[_bar(self.session_date, 1)])


class _RecoveryRegistry:
    def __init__(self, session_date: date) -> None:
        self.session_date = session_date
        self.primary_calls = 0
        self.iex_calls = 0
        self.yahoo = _YahooRepairProvider(session_date)

    def provider(self, provider_id):
        if provider_id == "yahoo":
            return self.yahoo
        return SimpleNamespace(provider_id=provider_id)

    def resolve_binding(self, instrument_id, binding_id=None):
        return SimpleNamespace(
            instrument_id=instrument_id,
            binding_id=binding_id or "yahoo:test",
            provider="yahoo",
        )

    def resolve_execution_binding(self, instrument_id, binding_id=None):
        return SimpleNamespace(
            instrument_id=instrument_id,
            binding_id="alpaca_iex:test",
            provider="alpaca_iex",
        )

    def bars(self, instrument_id, interval, limit, binding_id=None, cancellation=None):
        self.primary_calls += 1
        return SimpleNamespace(
            bars=[_bar(self.session_date, 0), _bar(self.session_date, 2)]
        )

    def execution_indicator_bars(self, *args, **kwargs):
        self.iex_calls += 1
        raise AssertionError("IEX fallback should not run after Yahoo exact repair")


def test_service_repairs_exact_yahoo_gap_before_iex_fallback(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    registry = _RecoveryRegistry(session_date)
    service = TradingMarketDataService(
        registry=registry,
        yahoo_evidence_store=_store(tmp_path),
    )
    observed = datetime(2026, 9, 17, 9, 33, 10, tzinfo=ET)

    recovered = service.recovered_bars(
        INSTRUMENT,
        "1m",
        500,
        "yahoo:test",
        session_date=session_date,
        as_of=observed,
    )

    assert recovered.report.unresolved_gaps == ()
    assert [bar.start_time.astimezone(ET).minute for bar in recovered.bars] == [30, 31, 32]
    assert registry.yahoo.calls == 1
    assert registry.iex_calls == 0
    diagnostics = service.yahoo_evidence_store.diagnostics()
    assert diagnostics["repair_attempt_count"] == 1
    assert diagnostics["repair_success_count"] == 1
    assert diagnostics["repaired_bar_count"] == 1



class _CanonicalOneMinuteRegistry:
    def __init__(self, session_date: date) -> None:
        self.session_date = session_date
        self.requested_intervals: list[str] = []
        self.yahoo = _YahooRepairProvider(session_date)

    def provider(self, provider_id):
        if provider_id == "yahoo":
            return self.yahoo
        return SimpleNamespace(provider_id=provider_id)

    def resolve_binding(self, instrument_id, binding_id=None):
        return SimpleNamespace(
            instrument_id=instrument_id,
            binding_id=binding_id or "yahoo:test",
            provider="yahoo",
        )

    def resolve_execution_binding(self, instrument_id, binding_id=None):
        return SimpleNamespace(
            instrument_id=instrument_id,
            binding_id="alpaca_iex:test",
            provider="alpaca_iex",
        )

    def bars(self, instrument_id, interval, limit, binding_id=None, cancellation=None):
        self.requested_intervals.append(interval)
        assert interval == "1m"
        return SimpleNamespace(
            bars=[_bar(self.session_date, minute) for minute in range(10)]
        )

    def execution_indicator_bars(self, *args, **kwargs):
        raise AssertionError("complete Yahoo 1m tape should not use IEX")


def test_recovered_five_minute_yahoo_tape_is_derived_from_one_minute_authority(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    registry = _CanonicalOneMinuteRegistry(session_date)
    service = TradingMarketDataService(
        registry=registry,
        yahoo_evidence_store=_store(tmp_path),
    )

    recovered = service.recovered_bars(
        INSTRUMENT,
        "5m",
        500,
        "yahoo:test",
        session_date=session_date,
        as_of=datetime(2026, 9, 17, 9, 40, 10, tzinfo=ET),
    )

    assert registry.requested_intervals == ["1m"]
    assert [bar.interval for bar in recovered.bars] == ["5m", "5m"]
    assert [bar.start_time.astimezone(ET).minute for bar in recovered.bars] == [30, 35]
    assert recovered.primary_response is None
    assert recovered.report.unresolved_gaps == ()



def test_strategy_monitor_exposes_evaluation_level_yahoo_recovery_metrics() -> None:
    diagnostics = TradingStrategyMonitor(interval_seconds=30).diagnostics()

    assert diagnostics["yahoo_recovered_candidate_evaluation_count"] == 0
    assert diagnostics["yahoo_unresolved_candidate_evaluation_count"] == 0



def test_causal_replay_excludes_bar_learned_after_decision_but_research_can_use_it(tmp_path) -> None:
    store = _store(tmp_path)
    session_date = date(2026, 9, 17)
    bar = _bar(session_date, 0).model_copy(
        update={
            "received_at": datetime(2026, 9, 17, 10, 0, tzinfo=ET).astimezone(timezone.utc)
        }
    )
    store.persist_market_bars([bar])
    decision = datetime(2026, 9, 17, 9, 35, tzinfo=ET)

    causal = store.load_market_bars(
        INSTRUMENT,
        start=bar.start_time,
        end=bar.end_time,
        session="regular",
        knowledge_mode="causal_replay",
        known_by=decision,
    )
    research = store.load_market_bars(
        INSTRUMENT,
        start=bar.start_time,
        end=bar.end_time,
        session="regular",
        knowledge_mode="retroactive_research",
        known_by=decision,
    )

    assert causal == []
    assert [item.start_time for item in research] == [bar.start_time]
    assert store.diagnostics()["causal_replay_rejection_count"] >= 1


def test_yahoo_union_happens_before_five_minute_aggregation(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    store = _store(tmp_path)
    store.persist_market_bars([_bar(session_date, 4)])

    class Registry(_CanonicalOneMinuteRegistry):
        def bars(self, instrument_id, interval, limit, binding_id=None, cancellation=None):
            self.requested_intervals.append(interval)
            assert interval == "1m"
            return SimpleNamespace(
                bars=[
                    _bar(self.session_date, minute)
                    for minute in range(10)
                    if minute != 4
                ]
            )

    registry = Registry(session_date)
    service = TradingMarketDataService(
        registry=registry,
        yahoo_evidence_store=store,
    )

    recovered = service.recovered_bars(
        INSTRUMENT,
        "5m",
        500,
        "yahoo:test",
        session_date=session_date,
        as_of=datetime(2026, 9, 17, 9, 40, 10, tzinfo=ET),
    )

    assert [bar.start_time.astimezone(ET).minute for bar in recovered.bars] == [30, 35]
    assert registry.yahoo.calls == 0
    assert recovered.report.unresolved_gaps == ()


def test_confirmed_nontrading_interval_is_not_an_unresolved_gap() -> None:
    session_date = date(2026, 9, 17)
    bars = [_bar(session_date, 0), _bar(session_date, 2)]
    halt_start = _bar(session_date, 1).start_time

    recovered = reconcile_recovery(
        instrument_id=INSTRUMENT,
        interval="1m",
        session_date=session_date,
        as_of=datetime(2026, 9, 17, 9, 33, 5, tzinfo=ET),
        primary_bars=bars,
        primary_provider="yahoo",
        confirmed_nontrading_starts=(halt_start,),
    )

    assert recovered.report.confirmed_nontrading_starts == (halt_start,)
    assert recovered.report.unresolved_gaps == ()
    assert [bar.start_time for bar in recovered.bars] == [bars[0].start_time, bars[1].start_time]


def test_rvol_rejects_incomplete_historical_baseline_session(tmp_path) -> None:
    store = _store(tmp_path)
    current = date(2026, 9, 17)
    for offset in range(1, 6):
        session_date = current - timedelta(days=offset)
        store.persist_market_bars(
            [
                _premarket_bar(session_date, 0, volume="50"),
                _premarket_bar(session_date, 1, volume="50"),
            ]
        )
    incomplete = current - timedelta(days=6)
    store.persist_market_bars([_premarket_bar(incomplete, 0, volume="5000")])
    store.persist_market_bars(
        [
            _premarket_bar(current, 0, volume="100"),
            _premarket_bar(current, 1, volume="100"),
        ]
    )

    evidence = store.premarket_relative_volume(
        INSTRUMENT,
        datetime(2026, 9, 17, 4, 2, tzinfo=ET),
        minimum_baseline_sessions=5,
        minimum_baseline_coverage_ratio=Decimal("0.90"),
    )

    assert evidence.baseline_session_count == 5
    assert evidence.rejected_baseline_session_count == 1
    assert evidence.baseline_mean_volume == Decimal("100")
    assert evidence.relative_volume == Decimal("2")



def test_yahoo_diagnostics_survive_store_restart(tmp_path) -> None:
    first = _store(tmp_path)
    first.record_acquisition(
        attempted=3,
        succeeded=2,
        failed=1,
        symbols=3,
    )
    first.record_repair(
        attempted=True,
        recovered_bar_count=2,
        unresolved=False,
    )
    first.record_evaluation_outcome(
        repaired=True,
        unresolved=False,
    )

    restarted = _store(tmp_path)
    diagnostics = restarted.diagnostics()

    assert diagnostics["acquisition_attempt_count"] == 3
    assert diagnostics["acquisition_success_count"] == 2
    assert diagnostics["acquisition_failure_count"] == 1
    assert diagnostics["acquisition_symbol_count"] == 3
    assert diagnostics["repair_attempt_count"] == 1
    assert diagnostics["repair_success_count"] == 1
    assert diagnostics["repaired_bar_count"] == 2
    assert diagnostics["evaluation_repaired_count"] == 1
    assert diagnostics["storage"] == "memory"

def test_yahoo_session_metrics_are_durable_and_distinguish_repaired_from_blocked(tmp_path) -> None:
    session_date = date(2026, 9, 18)
    first = _store(tmp_path)

    first.record_evaluation_outcome(
        repaired=True,
        unresolved=False,
        session_date=session_date,
    )
    first.record_evaluation_outcome(
        repaired=False,
        unresolved=True,
        session_date=session_date,
        reason="YAHOO_PROVIDER_UNAVAILABLE",
    )
    first.record_evaluation_outcome(
        repaired=True,
        unresolved=True,
        session_date=session_date,
        reason="ACTUAL_MISSING_BAR",
    )

    restarted = _store(tmp_path)
    diagnostics = restarted.session_diagnostics(session_date)

    assert diagnostics["evaluation_count"] == 3
    assert diagnostics["repaired_evaluation_count"] == 2
    assert diagnostics["genuinely_blocked_evaluation_count"] == 2
    assert diagnostics["passed_evaluation_count"] == 1
    assert diagnostics["repaired_but_blocked_evaluation_count"] == 1
    assert diagnostics["blocked_reason_counts"] == {
        "ACTUAL_MISSING_BAR": 1,
        "YAHOO_PROVIDER_UNAVAILABLE": 1,
    }



def test_causal_replay_preserves_revision_known_at_historical_cutoff(tmp_path) -> None:
    store = _store(tmp_path)
    session_date = date(2026, 9, 17)
    original = _bar(session_date, 0, price="10").model_copy(
        update={
            "received_at": datetime(2026, 9, 17, 9, 31, 5, tzinfo=ET).astimezone(timezone.utc),
            "provider_event_id": "original",
        }
    )
    revised = original.model_copy(
        update={
            "open": Decimal("11"),
            "high": Decimal("11.1"),
            "low": Decimal("10.9"),
            "close": Decimal("11"),
            "received_at": datetime(2026, 9, 17, 10, 0, tzinfo=ET).astimezone(timezone.utc),
            "provider_event_id": "revision",
        }
    )

    assert store.persist_market_bars([original]) == 1
    assert store.persist_market_bars([revised]) == 1

    decision = datetime(2026, 9, 17, 9, 35, tzinfo=ET)
    causal = store.load_market_bars(
        INSTRUMENT,
        start=original.start_time,
        end=original.end_time,
        session="regular",
        knowledge_mode="causal_replay",
        known_by=decision,
    )
    research = store.load_market_bars(
        INSTRUMENT,
        start=original.start_time,
        end=original.end_time,
        session="regular",
        knowledge_mode="retroactive_research",
        known_by=decision,
    )

    assert len(causal) == 1
    assert causal[0].close == Decimal("10")
    assert causal[0].provider_event_id == "original"
    assert len(research) == 1
    assert research[0].close == Decimal("11")
    assert research[0].provider_event_id == "revision"


def test_global_metrics_merge_concurrent_process_deltas(tmp_path) -> None:
    first = _store(tmp_path)
    second = _store(tmp_path)

    first.record_acquisition(attempted=1, succeeded=1, symbols=1)
    second.record_acquisition(attempted=1, succeeded=1, symbols=1)

    restarted = _store(tmp_path)
    diagnostics = restarted.diagnostics()
    assert diagnostics["acquisition_attempt_count"] == 2
    assert diagnostics["acquisition_success_count"] == 2
    assert diagnostics["acquisition_symbol_count"] == 2


def test_live_tape_is_shared_within_the_minute_but_not_across_minutes_or_replays(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    registry = _RecoveryRegistry(session_date)
    service = TradingMarketDataService(registry=registry, yahoo_evidence_store=_store(tmp_path))

    def recover(hh, mm, ss, **kwargs):
        return service.recovered_bars(
            INSTRUMENT, "1m", 500, "yahoo:test", session_date=session_date,
            as_of=datetime(2026, 9, 17, hh, mm, ss, tzinfo=ET), **kwargs,
        )

    def chains() -> int:
        return service.recovery_metrics.snapshot()["total"]["calls"]

    first = recover(9, 33, 10)
    assert recover(9, 33, 40) is first  # another strategy, same minute: no provider calls
    assert chains() == 1
    recover(9, 33, 50, knowledge_mode="causal_replay")  # a replay never reuses live tapes
    assert chains() == 2
    recover(9, 34, 5)  # a new minute can hold a new bar
    assert chains() == 3
    assert service.recovery_metrics.snapshot()["reused"]["calls"] == 1


def test_a_tape_missing_the_latest_bar_is_shared_only_late_in_the_minute(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    registry = _RecoveryRegistry(session_date)  # has 09:30-09:32 only, so 09:33 is missing at 09:34
    service = TradingMarketDataService(registry=registry, yahoo_evidence_store=_store(tmp_path))

    def recover(ss):
        return service.recovered_bars(
            INSTRUMENT, "1m", 500, "yahoo:test", session_date=session_date,
            as_of=datetime(2026, 9, 17, 9, 34, ss, tzinfo=ET),
        )

    def chains() -> int:
        return service.recovery_metrics.snapshot()["total"]["calls"]

    recover(5)
    recover(10)  # early in the minute the missing bar may still be in flight: fetch again
    assert chains() == 2
    late = recover(25)
    assert recover(40) is late  # 25 s in, a missing bar means no trade: share it
    assert chains() == 3


def test_prefetch_warms_the_tape_the_strategy_loop_then_reads(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    registry = _RecoveryRegistry(session_date)
    service = TradingMarketDataService(registry=registry, yahoo_evidence_store=_store(tmp_path))
    as_of = datetime(2026, 9, 17, 9, 33, 10, tzinfo=ET)

    service.prefetch_recovered_bars(
        [(INSTRUMENT, "1m", 500, "yahoo:test")] * 3, session_date=session_date, as_of=as_of,
    )
    assert service.recovery_metrics.snapshot()["total"]["calls"] == 1  # duplicates are fetched once
    service.recovered_bars(
        INSTRUMENT, "1m", 500, "yahoo:test", session_date=session_date, as_of=as_of + timedelta(seconds=5),
    )
    assert service.recovery_metrics.snapshot()["total"]["calls"] == 1
    assert service.recovery_metrics.snapshot()["reused"]["calls"] == 1


def test_ibkr_repair_uses_aligned_blocks_and_skips_blocks_without_trades(tmp_path) -> None:
    from app.apps.trading.providers.errors import ProviderDataUnavailableError
    from app.apps.trading.service import _aligned_repair_blocks

    session_date = date(2026, 9, 17)
    open_utc = datetime(2026, 9, 17, 9, 30, tzinfo=ET).astimezone(timezone.utc)
    minute = timedelta(minutes=1)

    # Blocks stay put as gaps grow, and never reach past the last completed minute.
    through = open_utc + 21 * minute
    assert _aligned_repair_blocks({open_utc + 3 * minute}, through=through) == [(open_utc, open_utc + 15 * minute)]
    assert _aligned_repair_blocks({open_utc + 3 * minute, open_utc + 4 * minute, open_utc + 16 * minute}, through=through) == [
        (open_utc, open_utc + 15 * minute), (open_utc + 15 * minute, through),
    ]

    def ibkr_bar(offset: int) -> MarketBar:
        return _bar(session_date, offset).model_copy(update={"provider": "ibkr"})

    class _Ibkr:
        runtime = SimpleNamespace(enabled=True, recovery_authority_enabled=True)

        def __init__(self) -> None:
            self.calls = []

        def get_intraday_bars_range(self, instrument_id, *, start, end, include_extended_hours=True, cancellation=None):
            self.calls.append((start, end))
            if start == open_utc:
                raise ProviderDataUnavailableError("IBKR returned no 1m TRADES bars")
            return SimpleNamespace(bars=[ibkr_bar(15), ibkr_bar(16)])

        def get_binding(self, instrument_id):
            return SimpleNamespace(binding_id="ibkr:test")

    class _Registry(_RecoveryRegistry):
        def __init__(self, session_date):
            super().__init__(session_date)
            self.ibkr = _Ibkr()

        def provider(self, provider_id):
            return self.ibkr if provider_id == "ibkr" else super().provider(provider_id)

        def bars(self, instrument_id, interval, limit, binding_id=None, cancellation=None):
            self.primary_calls += 1
            return SimpleNamespace(bars=[_bar(self.session_date, offset) for offset in (0, 1, 2, 17, 18, 19, 20)])

    registry = _Registry(session_date)
    service = TradingMarketDataService(registry=registry, yahoo_evidence_store=_store(tmp_path))
    recovered = service.recovered_bars(
        INSTRUMENT, "1m", 500, "yahoo:test", session_date=session_date,
        as_of=datetime(2026, 9, 17, 9, 51, 30, tzinfo=ET),
    )

    # The empty first block no longer abandons the second one.
    assert registry.ibkr.calls == [
        (open_utc, open_utc + 15 * minute), (open_utc + 15 * minute, open_utc + 21 * minute),
    ]
    assert recovered.report.ibkr_repair_applied_count == 2


class _UnfillableMinuteRegistry(_RecoveryRegistry):
    """09:31 never traded (a halt, or no prints): Yahoo has no bar for it, now or later."""

    def __init__(self, session_date: date, *, repair_error: Exception | None = None) -> None:
        super().__init__(session_date)
        self.repair_error = repair_error
        registry = self

        class _EmptyRepair(_YahooRepairProvider):
            def get_intraday_bars_range(self, instrument_id, *, start, end, include_extended_hours=True, cancellation=None):
                self.calls += 1
                if registry.repair_error is not None:
                    raise registry.repair_error
                return SimpleNamespace(bars=[])

        self.yahoo = _EmptyRepair(session_date)

    def bars(self, instrument_id, interval, limit, binding_id=None, cancellation=None):
        self.primary_calls += 1
        return SimpleNamespace(bars=[_bar(self.session_date, minute) for minute in range(30) if minute != 1])

    def execution_indicator_bars(self, *args, **kwargs):
        raise RuntimeError("no IEX in this test")


def _recover_at(service, session_date, hh, mm, ss=10):
    return service.recovered_bars(
        INSTRUMENT, "1m", 500, "yahoo:test", session_date=session_date,
        as_of=datetime(2026, 9, 17, hh, mm, ss, tzinfo=ET),
    )


def test_a_settled_minute_yahoo_could_not_fill_is_not_asked_for_every_cycle(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    registry = _UnfillableMinuteRegistry(session_date)
    service = TradingMarketDataService(registry=registry, yahoo_evidence_store=_store(tmp_path))

    first = _recover_at(service, session_date, 9, 42)
    assert [gap.start.astimezone(ET).minute for gap in first.report.unresolved_gaps] == [31]
    assert registry.yahoo.calls == 1
    for minute in (43, 44, 45):
        later = _recover_at(service, session_date, 9, minute)
        # Still reported as a gap; just not re-requested from Yahoo.
        assert [gap.start.astimezone(ET).minute for gap in later.report.unresolved_gaps] == [31]
    assert registry.yahoo.calls == 1


def test_a_fresh_gap_or_a_failed_repair_is_asked_for_again(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    # 09:31 closed at 09:32: within the settle window at 09:34, Yahoo may still publish it.
    fresh = _UnfillableMinuteRegistry(session_date)
    service = TradingMarketDataService(registry=fresh, yahoo_evidence_store=_store(tmp_path / "fresh"))
    _recover_at(service, session_date, 9, 34)
    _recover_at(service, session_date, 9, 35)
    assert fresh.yahoo.calls == 2

    # A request that failed proves nothing about the bucket.
    failing = _UnfillableMinuteRegistry(session_date, repair_error=RuntimeError("yahoo 429"))
    service = TradingMarketDataService(registry=failing, yahoo_evidence_store=_store(tmp_path / "failing"))
    _recover_at(service, session_date, 9, 42)
    _recover_at(service, session_date, 9, 43)
    assert failing.yahoo.calls == 2
