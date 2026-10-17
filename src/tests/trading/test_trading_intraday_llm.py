from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.strategies.models import (
    GapPullbackConfig,
    GapPullbackFeatures,
    GapPullbackResult,
    StrategySignal,
)
from app.apps.trading.strategy_intraday_learning import IntradayLearningSnapshot
from app.apps.trading.strategy_intraday_llm import (
    EVENT_BATCH_COOLDOWN_MINUTES,
    FULL_REFRESH_MINUTES,
    IntradayLLMAssessment,
    IntradayLLMAnalyzer,
    IntradayLLMResult,
    build_intraday_llm_payload,
    intraday_llm_trigger_reasons,
    select_intraday_llm_candidates,
    should_run_intraday_llm_batch,
)
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument


OBSERVED = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)


def candidate(symbol: str, rank: int) -> GapperCandidate:
    return GapperCandidate(
        instrument_id=f"equity:NASDAQ:{symbol}",
        previous_close=Decimal("8"),
        premarket_price=Decimal("10"),
        gap_pct=Decimal("25"),
        premarket_volume=Decimal("500000"),
        premarket_dollar_volume=Decimal("5000000"),
        tod_rvol=Decimal("5"),
        float_shares=Decimal("2000000"),
        spread_bps=Decimal("40"),
        catalyst_evidence_ids=("ev-1",),
        dilution_flags=(),
        discovery_rank=rank,
    )


def result(symbol: str, *, state: str = "higher_low_confirmed") -> GapPullbackResult:
    signal = None
    if state == "entry_ready":
        signal = StrategySignal(
            instrument_id=f"equity:NASDAQ:{symbol}",
            state="entry_ready",
            entry_price=Decimal("11"),
            stop_price=Decimal("10"),
            target_price=Decimal("13"),
            risk_per_share=Decimal("1"),
            reason_code="ENTRY_READY",
            quality_score=8,
        )
    return GapPullbackResult(
        instrument_id=f"equity:NASDAQ:{symbol}",
        state=state,
        reason_code="WAITING" if signal is None else "ENTRY_READY",
        features=GapPullbackFeatures(
            gap_pct=Decimal("25"),
            session_vwap=Decimal("10.4"),
            float_shares=Decimal("2000000"),
            quality_score=8,
        ),
        transitions=("discovered", "qualified_gap", "higher_low_confirmed"),
        signal=signal,
        evaluated_bar_count=30,
    )


def learning(
    *,
    opportunity: int = 8,
    pattern: str = "failed_selloff_watch",
    **updates,
) -> IntradayLearningSnapshot:
    base = IntradayLearningSnapshot(
        catalyst_quality_score=8,
        supply_risk_score=3,
        float_structure_risk_score=7,
        extension_risk_score=2,
        squeeze_probability_score=7,
        failed_selloff_probability_score=8,
        trend_continuation_score=6,
        gap_retention_score=8,
        execution_quality_score=9,
        opportunity_score=opportunity,
        pattern=pattern,
        current_price=Decimal("10.8"),
        session_open=Decimal("10"),
        session_high=Decimal("11"),
        session_low=Decimal("9.5"),
        session_vwap=Decimal("10.4"),
        close_location=Decimal("0.8667"),
        gap_retention_ratio=Decimal("0.9"),
        turnover_to_float=Decimal("2.4"),
        current_vs_premarket_pct=Decimal("8"),
        session_return_pct=Decimal("8"),
        deterministic_state="higher_low_confirmed",
        deterministic_reason_code="WAITING",
        execution_authority=False,
    )
    return base.model_copy(update=updates)


def row(
    symbol: str,
    rank: int,
    *,
    state: str = "higher_low_confirmed",
    opportunity: int = 8,
    observed_at: datetime = OBSERVED,
    learning_updates: dict | None = None,
):
    return (
        candidate(symbol, rank),
        result(symbol, state=state),
        observed_at,
        learning(opportunity=opportunity, **(learning_updates or {})),
    )


def assessment(instrument_id: str):
    return {
        "instrument_id": instrument_id,
        "market_regime": "failed_selloff",
        "squeeze_probability": 62,
        "failed_selloff_probability": 81,
        "trend_continuation_probability": 58,
        "distribution_probability": 24,
        "confidence": 78,
        "thesis_change": "strengthened",
        "summary": "Recovery evidence strengthened while price remains above VWAP.",
        "bull_case": "Higher lows and gap retention support continued recovery.",
        "bear_case": "A VWAP loss with expanding sell pressure would weaken the recovery.",
        "what_would_change_my_mind": "Sustained acceptance below VWAP and failure of the higher-low structure.",
        "execution_authority": False,
    }


def previous_payload(
    symbol: str,
    *,
    rank: int = 1,
    state: str = "higher_low_confirmed",
    source_learning: IntradayLearningSnapshot | None = None,
    payload_mode: str = "delta",
):
    source = source_learning or learning()
    return {
        "live_research_rank": rank,
        "deterministic_state": state,
        "deterministic_reason_code": "WAITING",
        "source_learning": source.model_dump(mode="json"),
        "assessment": assessment(f"equity:NASDAQ:{symbol}"),
        "payload_mode": payload_mode,
    }


class FakeProvider:
    provider_name = "fixture"

    def __init__(self, payload, *, usage=None):
        self.payload = payload
        self.usage = usage
        self.config = SimpleNamespace(model="fixture-model")
        self.messages = None
        self.kwargs = None

    def chat_completion(self, *, messages, model=None, stream=False, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return SimpleNamespace(
            content=json.dumps(self.payload),
            model=model or "fixture-model",
            usage=self.usage,
        )


def test_event_batch_cooldown_is_bounded_but_not_the_heartbeat():
    assert should_run_intraday_llm_batch(
        observed_at=OBSERVED,
        previous_batch_at=None,
        minimum_interval_minutes=EVENT_BATCH_COOLDOWN_MINUTES,
    )
    assert not should_run_intraday_llm_batch(
        observed_at=OBSERVED,
        previous_batch_at=OBSERVED - timedelta(minutes=4, seconds=59),
        minimum_interval_minutes=EVENT_BATCH_COOLDOWN_MINUTES,
    )
    assert should_run_intraday_llm_batch(
        observed_at=OBSERVED,
        previous_batch_at=OBSERVED - timedelta(minutes=5),
        minimum_interval_minutes=EVENT_BATCH_COOLDOWN_MINUTES,
    )


def test_initial_selection_uses_top_n_and_always_keeps_entry_ready_candidate():
    rows = [
        row("AAA", 1, opportunity=10),
        row("BBB", 2, opportunity=9),
        row("CCC", 3, state="entry_ready", opportunity=3),
    ]
    selected, reasons = select_intraday_llm_candidates(rows, top_n=2)
    assert [item[0].instrument_id for item in selected] == [
        "equity:NASDAQ:AAA",
        "equity:NASDAQ:BBB",
        "equity:NASDAQ:CCC",
    ]
    assert reasons["equity:NASDAQ:AAA"] == ("initial_top_rank",)
    assert "entry_ready" in reasons["equity:NASDAQ:CCC"]


def test_quiet_candidate_skips_llm_until_ten_minute_heartbeat():
    current = row("AAA", 1)
    previous = {"equity:NASDAQ:AAA": previous_payload("AAA")}

    selected, _ = select_intraday_llm_candidates(
        [current],
        top_n=5,
        previous_by_instrument=previous,
        previous_observed_at_by_instrument={
            "equity:NASDAQ:AAA": OBSERVED - timedelta(minutes=9, seconds=59)
        },
        heartbeat_minutes=10,
    )
    assert selected == []

    selected, reasons = select_intraday_llm_candidates(
        [current],
        top_n=5,
        previous_by_instrument=previous,
        previous_observed_at_by_instrument={
            "equity:NASDAQ:AAA": OBSERVED - timedelta(minutes=10)
        },
        heartbeat_minutes=10,
    )
    assert [item[0].instrument_id for item in selected] == ["equity:NASDAQ:AAA"]
    assert reasons["equity:NASDAQ:AAA"] == ("heartbeat",)


def test_quiet_unassessed_top_name_does_not_call_llm_outside_entry_window():
    selected, reasons = select_intraday_llm_candidates(
        [row("AAA", 1)],
        top_n=5,
        heartbeat_minutes=10,
        heartbeat_enabled=False,
    )
    assert selected == []
    assert reasons == {}


def test_heartbeat_can_be_disabled_outside_the_entry_window():
    selected, _ = select_intraday_llm_candidates(
        [row("AAA", 1)],
        top_n=5,
        previous_by_instrument={"equity:NASDAQ:AAA": previous_payload("AAA")},
        previous_observed_at_by_instrument={
            "equity:NASDAQ:AAA": OBSERVED - timedelta(minutes=30)
        },
        heartbeat_minutes=10,
        heartbeat_enabled=False,
    )
    assert selected == []


def test_material_state_change_outside_top_n_triggers_llm():
    current = row("CCC", 8, state="entry_ready")
    selected, reasons = select_intraday_llm_candidates(
        [row("AAA", 1), current],
        top_n=1,
        previous_by_instrument={
            "equity:NASDAQ:AAA": previous_payload("AAA", rank=1),
            "equity:NASDAQ:CCC": previous_payload("CCC", rank=8),
        },
        previous_observed_at_by_instrument={
            "equity:NASDAQ:AAA": OBSERVED - timedelta(minutes=1),
            "equity:NASDAQ:CCC": OBSERVED - timedelta(minutes=1),
        },
        heartbeat_minutes=10,
    )
    assert [item[0].instrument_id for item in selected] == ["equity:NASDAQ:CCC"]
    assert "entry_ready" in reasons["equity:NASDAQ:CCC"]
    assert "deterministic_state_changed" in reasons["equity:NASDAQ:CCC"]


def test_turnover_threshold_and_vwap_cross_are_material_events():
    prior_learning = learning(
        turnover_to_float=Decimal("0.9"),
        current_price=Decimal("10.2"),
        session_vwap=Decimal("10.4"),
    )
    current = row(
        "AAA",
        6,
        learning_updates={
            "turnover_to_float": Decimal("1.1"),
            "current_price": Decimal("10.6"),
            "session_vwap": Decimal("10.4"),
        },
    )
    reasons = intraday_llm_trigger_reasons(
        current,
        current_rank=6,
        top_n=5,
        previous=previous_payload("AAA", rank=6, source_learning=prior_learning),
        previous_observed_at=OBSERVED - timedelta(minutes=1),
        heartbeat_minutes=10,
    )
    assert "turnover_threshold_crossed" in reasons
    assert "vwap_side_changed" in reasons


def test_material_score_change_can_surface_name_outside_top_n():
    previous = previous_payload(
        "CCC",
        rank=9,
        source_learning=learning(
            opportunity=5,
            squeeze_probability_score=4,
            failed_selloff_probability_score=5,
        ),
    )
    current = row(
        "CCC",
        8,
        opportunity=9,
        learning_updates={
            "squeeze_probability_score": 8,
            "failed_selloff_probability_score": 8,
        },
    )
    selected, reasons = select_intraday_llm_candidates(
        [row("AAA", 1), current],
        top_n=1,
        previous_by_instrument={
            "equity:NASDAQ:AAA": previous_payload("AAA"),
            "equity:NASDAQ:CCC": previous,
        },
        previous_observed_at_by_instrument={
            "equity:NASDAQ:AAA": OBSERVED - timedelta(minutes=1),
            "equity:NASDAQ:CCC": OBSERVED - timedelta(minutes=1),
        },
        heartbeat_minutes=10,
    )
    assert [item[0].instrument_id for item in selected] == ["equity:NASDAQ:CCC"]
    assert "material_score_change" in reasons["equity:NASDAQ:CCC"]


def test_delta_payload_is_smaller_and_omits_repeated_full_feature_dump():
    current = row(
        "AAA",
        1,
        opportunity=9,
        learning_updates={"squeeze_probability_score": 9},
    )
    previous = {"equity:NASDAQ:AAA": previous_payload("AAA")}
    common = {
        "ranks": {"equity:NASDAQ:AAA": 1},
        "previous_by_instrument": previous,
        "trigger_reasons_by_instrument": {
            "equity:NASDAQ:AAA": ("material_score_change",)
        },
    }
    delta = build_intraday_llm_payload(
        [current],
        **common,
        payload_modes_by_instrument={"equity:NASDAQ:AAA": "delta"},
    )
    full = build_intraday_llm_payload(
        [current],
        **common,
        payload_modes_by_instrument={"equity:NASDAQ:AAA": "full"},
    )

    delta_item = delta["candidates"][0]
    assert "full_context" not in delta_item
    assert "changed_since_previous_llm" in delta_item
    assert delta_item["previous_llm_assessment"]["summary"]
    assert "schema" not in delta
    assert "output_fields" in delta
    assert len(json.dumps(delta)) < len(json.dumps(full))
    assert "deterministic_features" in full["candidates"][0]["full_context"]


def test_full_refresh_constant_is_periodic_not_every_call():
    assert FULL_REFRESH_MINUTES == 30


def test_analyzer_uses_default_provider_contract_and_returns_strict_research_assessment():
    instrument_id = "equity:NASDAQ:AAA"
    provider = FakeProvider({"assessments": [assessment(instrument_id)]})
    analyzer = IntradayLLMAnalyzer(provider_factory=lambda: provider)

    output = analyzer.assess(
        [row("AAA", 1)],
        ranks={instrument_id: 1},
        trigger_reasons_by_instrument={instrument_id: ("initial_top_rank",)},
        payload_modes_by_instrument={instrument_id: "full"},
    )

    assert output.provider == "fixture"
    assert output.model == "fixture-model"
    assert output.input_characters > 0
    assert output.input_tokens > 0
    assert output.output_tokens > 0
    assert output.total_tokens == output.input_tokens + output.output_tokens
    assert output.usage_source == "estimated"
    assert output.assessments[0].instrument_id == instrument_id
    assert output.assessments[0].execution_authority is False
    assert provider.messages is not None
    assert "Never say" in provider.messages[0].content
    assert provider.kwargs is not None
    assert provider.kwargs["response_format"] == {"type": "json_object"}
    user_payload = json.loads(provider.messages[1].content)
    item = user_payload["candidates"][0]
    assert item["current"]["deterministic_state"] == "higher_low_confirmed"
    assert item["payload_mode"] == "full"


def test_analyzer_prefers_provider_reported_token_usage_when_available():
    instrument_id = "equity:NASDAQ:AAA"
    provider = FakeProvider(
        {"assessments": [assessment(instrument_id)]},
        usage={"prompt_tokens": 321, "completion_tokens": 87, "total_tokens": 408},
    )
    analyzer = IntradayLLMAnalyzer(provider_factory=lambda: provider)

    output = analyzer.assess(
        [row("AAA", 1)],
        ranks={instrument_id: 1},
    )

    assert output.usage_source == "provider"
    assert output.input_tokens == 321
    assert output.output_tokens == 87
    assert output.total_tokens == 408


def test_analyzer_fails_closed_when_provider_omits_requested_candidate():
    provider = FakeProvider({"assessments": []})
    analyzer = IntradayLLMAnalyzer(provider_factory=lambda: provider)

    with pytest.raises(RuntimeError, match="missing_assessments"):
        analyzer.assess(
            [row("AAA", 1)],
            ranks={"equity:NASDAQ:AAA": 1},
        )


def test_intraday_llm_requires_deterministic_learning_layer():
    with pytest.raises(ValueError, match="requires intraday learning"):
        GapPullbackConfig(
            intraday_learning_enabled=False,
            intraday_llm_enabled=True,
        )


def test_default_llm_policy_reduces_top_n_and_uses_ten_minute_heartbeat():
    config = GapPullbackConfig()
    assert config.intraday_llm_top_n == 5
    assert config.intraday_llm_interval_minutes == 10


class MemoryEventRepository:
    def __init__(self):
        self.events = []

    def recent_events(self, strategy_id, limit=200):
        matching = [event for event in self.events if event.strategy_id == strategy_id]
        return sorted(matching, key=lambda event: event.observed_at, reverse=True)[:limit]

    def append_event(self, event):
        if any(existing.idempotency_key == event.idempotency_key for existing in self.events):
            return False
        self.events.append(event)
        return True


class FixtureAnalyzer:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0
        self.last_trigger_reasons = None
        self.last_payload_modes = None

    def assess(
        self,
        rows,
        *,
        ranks,
        previous_by_instrument=None,
        trigger_reasons_by_instrument=None,
        payload_modes_by_instrument=None,
    ):
        del ranks, previous_by_instrument
        self.calls += 1
        self.last_trigger_reasons = trigger_reasons_by_instrument
        self.last_payload_modes = payload_modes_by_instrument
        if self.fail:
            raise RuntimeError("fixture provider unavailable")
        assessments = [
            IntradayLLMAssessment.model_validate(assessment(candidate_.instrument_id))
            for candidate_, *_ in rows
        ]
        return IntradayLLMResult(
            assessments=tuple(assessments),
            provider="fixture",
            model="fixture-model",
            input_characters=800,
            output_characters=200,
            input_tokens=200,
            output_tokens=50,
            total_tokens=250,
            usage_source="estimated",
        )


def _llm_strategy() -> TradingStrategyConfigDocument:
    config = GapPullbackConfig(
        strategy_version="2.0.0",
        universe_discovery_source="finviz",
        intraday_learning_enabled=True,
        intraday_llm_enabled=True,
        intraday_llm_top_n=1,
        intraday_llm_interval_minutes=10,
    )
    return TradingStrategyConfigDocument(
        strategy_id="intraday-llm-test",
        account_id="paper-test",
        strategy_version="2.0.0",
        mode="shadow",
        config=config,
    )


def _llm_universe():
    observed = OBSERVED - timedelta(minutes=10)
    return freeze_gapper_universe(
        universe_id="finviz-intraday-llm-test",
        session_date=OBSERVED.date(),
        evaluation_time=OBSERVED,
        discovery_source="finviz",
        source_locator="https://finviz.com/screener?v=340&s=ta_topgainers",
        source_candidate_symbols=("AAA",),
        candidates=[candidate("AAA", 1).model_copy(update={"observed_at": observed})],
    )


def test_monitor_persists_event_trigger_payload_mode_and_token_estimate():
    repository = MemoryEventRepository()
    analyzer = FixtureAnalyzer()
    monitor = TradingStrategyMonitor(
        intraday_llm_analyzer_factory=lambda: analyzer,
        interval_seconds=30,
    )
    monitor.current_run_id = "run-test"
    ranked = [row("AAA", 1)]

    asyncio.run(
        monitor._run_intraday_llm(
            _llm_strategy(),
            repository,
            _llm_universe(),
            ranked,
        )
    )

    assert analyzer.calls == 1
    assert monitor.intraday_llm_call_count == 1
    assert monitor.intraday_llm_assessment_count == 1
    assert [event.event_type for event in repository.events] == [
        "intraday_llm",
        "intraday_llm_batch",
    ]
    llm_event = repository.events[0]
    batch_event = repository.events[1]
    assert llm_event.payload["execution_authority"] is False
    assert llm_event.payload["assessment"]["execution_authority"] is False
    assert llm_event.payload["trigger_reasons"] == ["initial_top_rank"]
    assert llm_event.payload["payload_mode"] == "full"
    assert batch_event.payload["heartbeat_minutes"] == 10
    assert batch_event.payload["token_usage"] == {
        "source": "estimated",
        "input_characters": 800,
        "output_characters": 200,
        "input_tokens": 200,
        "output_tokens": 50,
        "total_tokens": 250,
    }
    assert monitor.intraday_llm_input_token_count == 200
    assert monitor.intraday_llm_output_token_count == 50
    assert monitor.intraday_llm_total_token_count == 250
    assert monitor.intraday_llm_estimated_usage_count == 1

    # Same causal minute has neither a material change nor a heartbeat.
    asyncio.run(
        monitor._run_intraday_llm(
            _llm_strategy(),
            repository,
            _llm_universe(),
            ranked,
        )
    )
    assert analyzer.calls == 1
    assert monitor.intraday_llm_call_count == 1


def test_monitor_uses_delta_heartbeat_then_periodic_full_refresh():
    repository = MemoryEventRepository()
    analyzer = FixtureAnalyzer()
    monitor = TradingStrategyMonitor(
        intraday_llm_analyzer_factory=lambda: analyzer,
        interval_seconds=30,
    )
    monitor.current_run_id = "run-refresh"
    strategy = _llm_strategy()
    universe = _llm_universe()

    asyncio.run(
        monitor._run_intraday_llm(
            strategy,
            repository,
            universe,
            [row("AAA", 1, observed_at=OBSERVED)],
        )
    )
    assert analyzer.last_payload_modes == {"equity:NASDAQ:AAA": "full"}

    asyncio.run(
        monitor._run_intraday_llm(
            strategy,
            repository,
            universe,
            [row("AAA", 1, observed_at=OBSERVED + timedelta(minutes=10))],
        )
    )
    assert analyzer.last_payload_modes == {"equity:NASDAQ:AAA": "delta"}

    asyncio.run(
        monitor._run_intraday_llm(
            strategy,
            repository,
            universe,
            [row("AAA", 1, observed_at=OBSERVED + timedelta(minutes=31))],
        )
    )
    assert analyzer.last_payload_modes == {"equity:NASDAQ:AAA": "full"}
    assert analyzer.calls == 3


def test_monitor_failure_checkpoint_bounds_event_driven_provider_retries():
    repository = MemoryEventRepository()
    analyzer = FixtureAnalyzer(fail=True)
    monitor = TradingStrategyMonitor(
        intraday_llm_analyzer_factory=lambda: analyzer,
        interval_seconds=30,
    )
    monitor.current_run_id = "run-error"
    ranked = [row("AAA", 1)]

    asyncio.run(
        monitor._run_intraday_llm(
            _llm_strategy(),
            repository,
            _llm_universe(),
            ranked,
        )
    )

    assert analyzer.calls == 1
    assert monitor.intraday_llm_call_count == 1
    assert monitor.intraday_llm_error_count == 1
    assert len(repository.events) == 1
    assert repository.events[0].event_type == "intraday_llm_batch"
    assert repository.events[0].state == "error"

    asyncio.run(
        monitor._run_intraday_llm(
            _llm_strategy(),
            repository,
            _llm_universe(),
            ranked,
        )
    )
    assert analyzer.calls == 1


def test_entry_ready_can_bypass_short_batch_cooldown():
    repository = MemoryEventRepository()
    failing = FixtureAnalyzer(fail=True)
    monitor = TradingStrategyMonitor(
        intraday_llm_analyzer_factory=lambda: failing,
        interval_seconds=30,
    )
    monitor.current_run_id = "run-entry-ready"
    asyncio.run(
        monitor._run_intraday_llm(
            _llm_strategy(),
            repository,
            _llm_universe(),
            [row("AAA", 1)],
        )
    )
    assert failing.calls == 1

    succeeding = FixtureAnalyzer()
    monitor.intraday_llm_analyzer_factory = lambda: succeeding
    asyncio.run(
        monitor._run_intraday_llm(
            _llm_strategy(),
            repository,
            _llm_universe(),
            [row("AAA", 1, state="entry_ready")],
        )
    )
    assert succeeding.calls == 1
