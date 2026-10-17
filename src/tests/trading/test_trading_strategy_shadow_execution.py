from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_shadow_execution import observe_shadow_execution


INSTRUMENT = "equity:NASDAQ:SHADOW"
BINDING = "alpaca:iex:equity:NASDAQ:SHADOW"


def _execution(*, eligible: bool):
    return SimpleNamespace(
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        provider="alpaca_iex",
        last=Decimal("10.10"),
        bid=Decimal("10.09"),
        ask=Decimal("10.11"),
        bid_size=Decimal("500"),
        ask_size=Decimal("400"),
        high=Decimal("10.20"),
        low=Decimal("9.95"),
        bar_volume=Decimal("12000"),
        bar_start_time=datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc),
        source_time=datetime(2026, 8, 24, 14, 0, 30, tzinfo=timezone.utc),
        spread_bps=Decimal("19.80"),
        execution_eligible=eligible,
        freshness_mode="realtime",
        rejection_reasons=() if eligible else ("book_missing",),
        halted=False,
    )


def _indicator_bars() -> list[MarketBar]:
    start = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)  # 04:00 ET
    bars: list[MarketBar] = []
    for index in range(360):
        observed = start + timedelta(minutes=index)
        close = (
            Decimal("5")
            + Decimal(index) * Decimal("0.002")
            + Decimal(index * index) * Decimal("0.00001")
        )
        bars.append(
            MarketBar(
                instrument_id=INSTRUMENT,
                interval="1m",
                start_time=observed,
                end_time=observed + timedelta(minutes=1),
                open=close - Decimal("0.01"),
                high=close + Decimal("0.02"),
                low=close - Decimal("0.02"),
                close=close,
                volume=Decimal("10000") + Decimal(index),
                is_final=True,
                session="extended_pre" if index < 330 else "regular",
                provider="alpaca_iex",
                provider_event_id=f"bar-{index}",
                received_at=observed + timedelta(minutes=1),
            )
        )
    return bars


class FakeMarketService:
    def __init__(self, execution, *, history_error: Exception | None = None) -> None:
        self.execution = execution
        self.history_error = history_error
        self.calls: list[tuple[str, str | None]] = []
        self.indicator_calls: list[tuple[str, str | None, datetime]] = []

    def execution_observation(self, instrument_id: str, binding_id: str | None):
        self.calls.append((instrument_id, binding_id))
        return self.execution

    def execution_indicator_bars(
        self,
        instrument_id: str,
        binding_id: str | None,
        *,
        as_of: datetime,
    ) -> list[MarketBar]:
        self.indicator_calls.append((instrument_id, binding_id, as_of))
        if self.history_error is not None:
            raise self.history_error
        return _indicator_bars()


def test_shadow_execution_captures_eligible_iex_evidence_without_order_authority() -> None:
    execution = _execution(eligible=True)
    service = FakeMarketService(execution)
    evidence = observe_shadow_execution(
        service,
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
    )

    assert service.calls == [(INSTRUMENT, BINDING)]
    assert service.indicator_calls == [(INSTRUMENT, BINDING, execution.source_time)]
    assert evidence.reason_code == "SHADOW_EXECUTION_OBSERVED"
    assert evidence.execution["execution_eligible"] is True
    assert evidence.execution["bid"] == Decimal("10.09")
    assert evidence.execution["ask"] == Decimal("10.11")
    assert evidence.execution["source_time"] == datetime(2026, 8, 24, 14, 0, 30, tzinfo=timezone.utc)
    assert evidence.execution["indicator_context_source"] == "alpaca_iex_same_day_1m"
    assert evidence.execution["indicator_context_partial_market"] is True
    assert evidence.execution["indicator_context_cutoff"] == execution.source_time
    assert evidence.execution["indicator_context_bar_count"] == 360
    assert evidence.execution["indicator_context_full_warmup"] is True
    assert evidence.execution["indicator_entry_confirmed"] is True
    assert evidence.execution["indicator_entry_reason_codes"] == ()
    assert evidence.execution["indicator_context_error"] is None
    context = evidence.execution["indicator_context"]
    assert isinstance(context, dict)
    assert context["source_bar_count"] == 360
    assert context["one_minute"]["macd"] is not None
    assert context["one_minute"]["stochastic_rsi_k"] is not None
    assert context["five_minute"]["ema20"] is not None
    assert context["five_minute"]["macd"] is not None
    assert context["five_minute"]["stochastic_rsi_k"] is not None

    prospective = evidence.execution["prospective_signal_features"]
    assert isinstance(prospective, dict)
    assert prospective["schema_version"] == "v2-prospective-signal-features-2"
    assert prospective["execution_authority"] is False
    assert prospective["market_data_source"] == "canonical_causal_bars"
    assert prospective["source_providers"] == ["alpaca_iex"]
    assert prospective["premarket"]["bar_count"] == 330
    assert prospective["momentum"]["full_warmup"] is True
    assert prospective["completeness"]["premarket_available"] is True
    assert prospective["completeness"]["momentum_full_warmup"] is True
    assert len(prospective["immutable_fingerprint"]) == 64
    assert evidence.execution["prospective_signal_features_error"] is None


def test_shadow_execution_preserves_ineligible_reasons() -> None:
    service = FakeMarketService(_execution(eligible=False))
    evidence = observe_shadow_execution(
        service,
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
    )

    assert evidence.reason_code == "SHADOW_EXECUTION_INELIGIBLE"
    assert evidence.execution["execution_eligible"] is False
    assert evidence.execution["rejection_reasons"] == ("book_missing",)
    # Entry-indicator research remains observational even when execution evidence
    # is ineligible; it can never upgrade the execution decision.
    assert evidence.execution["indicator_entry_confirmed"] is True
    assert evidence.execution["prospective_signal_features"]["execution_authority"] is False


def test_shadow_indicator_failure_cannot_change_execution_evidence() -> None:
    service = FakeMarketService(
        _execution(eligible=False),
        history_error=RuntimeError("indicator history unavailable"),
    )

    evidence = observe_shadow_execution(
        service,
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
    )

    assert evidence.reason_code == "SHADOW_EXECUTION_INELIGIBLE"
    assert evidence.execution["execution_eligible"] is False
    assert evidence.execution["rejection_reasons"] == ("book_missing",)
    assert evidence.execution["indicator_context"] is None
    assert evidence.execution["indicator_context_bar_count"] == 0
    assert evidence.execution["indicator_context_full_warmup"] is False
    assert evidence.execution["indicator_entry_confirmed"] is None
    assert evidence.execution["indicator_entry_reason_codes"] == ()
    assert evidence.execution["indicator_context_error"] == (
        "RuntimeError: indicator history unavailable"
    )
    prospective = evidence.execution["prospective_signal_features"]
    assert prospective["premarket"]["available"] is False
    assert prospective["momentum"]["available"] is False
    assert prospective["execution_authority"] is False


def test_shadow_execution_module_has_no_order_or_paper_repository_dependency() -> None:
    source = Path("src/app/apps/trading/strategy_shadow_execution.py").read_text(encoding="utf-8")
    assert "paper_repository" not in source
    assert "place_order" not in source
    assert "PaperOrder" not in source


def test_strategy_monitor_shadow_observation_precedes_auto_paper_order_boundary() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in [*sorted(Path("src/app/apps/trading").glob("strategy_monitor*.py")), Path("src/app/apps/trading/strategy_entry_path.py")])
    shadow_start = source.index('if config.mode == "shadow" and proposals:')
    auto_paper_start = source.index(
        "snapshot = await asyncio.to_thread(paper_repository.snapshot, config.account_id)",
        shadow_start,
    )
    shadow_block = source[shadow_start:auto_paper_start]

    assert "observe_shadow_execution" in shadow_block
    assert 'event_type="shadow_execution"' in shadow_block
    assert '"execution_authority": False' in shadow_block
    assert "place_order" not in shadow_block
    assert "save_protection" not in shadow_block
