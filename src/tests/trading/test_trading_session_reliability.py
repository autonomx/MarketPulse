from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.apps.trading.models import MarketBar
from app.apps.trading.providers.errors import ProviderContractError
from app.apps.trading.strategy_deep_recovery import (
    DeepRecoveryShadowEvaluation,
    apply_deep_recovery_risk_policy,
)
from app.apps.trading.strategy_evaluability import assess_bar_coverage
from app.apps.trading.providers.alpaca_iex import _parse_timestamp as parse_alpaca_timestamp
from app.apps.trading.strategy_intraday_llm import _IntradaySchemaProviderProxy
from app.apps.trading.strategy_session_evidence import (
    FULL_SESSION_1M_LIMIT,
    _FullSessionMarketServiceProxy,
    _trend_events_for_session,
    evaluate_trend_continuation_shadow,
)


_ET = ZoneInfo("America/New_York")
UTC = timezone.utc
INSTRUMENT_ID = "equity:NASDAQ:TEST"
BINDING_ID = "yahoo:TEST"


def _bar(
    start_et: datetime,
    *,
    open_price: Decimal,
    close_price: Decimal,
    volume: Decimal = Decimal("1000"),
    provider: str = "yahoo",
) -> MarketBar:
    high = max(open_price, close_price) + Decimal("0.01")
    low = min(open_price, close_price) - Decimal("0.01")
    return MarketBar(
        instrument_id=INSTRUMENT_ID,
        interval="1m",
        start_time=start_et.astimezone(UTC),
        end_time=(start_et + timedelta(minutes=1)).astimezone(UTC),
        open=open_price,
        high=high,
        low=low,
        close=close_price,
        volume=volume,
        is_final=True,
        session="regular",
        provider=provider,
    )


def _session_bars(
    count: int = 30,
    *,
    step: Decimal = Decimal("0.04"),
    last_three_step: Decimal | None = None,
) -> list[MarketBar]:
    start = datetime(2026, 9, 10, 9, 30, tzinfo=_ET)
    bars: list[MarketBar] = []
    price = Decimal("10")
    for index in range(count):
        bar_step = (
            last_three_step
            if last_three_step is not None and index >= count - 3
            else step
        )
        assert bar_step is not None
        close = price + bar_step
        volume = Decimal("3000") if index >= count - 3 else Decimal("1000")
        bars.append(
            _bar(
                start + timedelta(minutes=index),
                open_price=price,
                close_price=close,
                volume=volume,
            )
        )
        price = close
    return bars


def _six_minute_session(*, missing_index: int | None = None, provider: str = "yahoo"):
    start = datetime(2026, 9, 10, 9, 30, tzinfo=_ET)
    bars = []
    price = Decimal("10")
    for index in range(6):
        close = price + Decimal("0.01")
        if index != missing_index:
            bars.append(
                _bar(
                    start + timedelta(minutes=index),
                    open_price=price,
                    close_price=close,
                    provider=provider,
                )
            )
        price = close
    return bars


class _FakeMarketService:
    def __init__(self, primary, fallback):
        self.primary = list(primary)
        self.fallback = list(fallback)
        self.last_limit = None
        self.fallback_calls = 0

    def bars(self, instrument_id, interval, limit=500, binding_id=None, cancellation=None):
        self.last_limit = limit
        return SimpleNamespace(bars=list(self.primary))

    def execution_indicator_bars(self, instrument_id, binding_id=None, *, as_of):
        self.fallback_calls += 1
        return list(self.fallback)


class _FakeCodexProvider:
    provider_name = "chatgpt_codex"

    def __init__(self):
        self.kwargs = None

    def chat_completion(self, *args, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(content='{"assessments":[]}')


def _assert_strict_objects_closed(node):
    if isinstance(node, list):
        for value in node:
            _assert_strict_objects_closed(value)
        return
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if node.get("type") == "object" or isinstance(properties, dict):
        assert node.get("additionalProperties") is False
        if isinstance(properties, dict):
            assert node.get("required") == list(properties.keys())
    for value in node.values():
        _assert_strict_objects_closed(value)


def test_alpaca_timestamp_accepts_nanosecond_rfc3339():
    parsed = parse_alpaca_timestamp(
        "2026-09-10T14:20:22.799401485Z",
        field="trade",
    )
    assert parsed == datetime(2026, 9, 10, 14, 20, 22, 799401, tzinfo=UTC)


def test_alpaca_timestamp_accepts_epoch_milliseconds():
    expected = datetime(2026, 9, 10, 14, 20, 22, tzinfo=UTC)
    epoch_ms = str(int(expected.timestamp() * 1000))
    assert parse_alpaca_timestamp(epoch_ms, field="trade") == expected


def test_alpaca_timestamp_rejects_bad_value_with_bounded_diagnostic():
    with pytest.raises(ProviderContractError) as exc:
        parse_alpaca_timestamp({"unexpected": "shape"}, field="trade")
    text = str(exc.value)
    assert "invalid trade timestamp" in text
    assert "unexpected" in text
    assert len(text) < 240


def test_intraday_codex_proxy_uses_real_closed_strict_schema():
    provider = _FakeCodexProvider()
    proxy = _IntradaySchemaProviderProxy(provider)

    proxy.chat_completion(
        messages=[],
        response_format={"type": "json_object"},
    )

    assert provider.kwargs is not None
    response_format = provider.kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    wrapper = response_format["json_schema"]
    assert wrapper["strict"] is True
    assert wrapper["name"] == "intraday_llm_batch_response"
    schema = wrapper["schema"]
    assert "assessments" in schema["properties"]
    _assert_strict_objects_closed(schema)


def test_full_session_proxy_raises_1m_limit_and_merges_only_observed_holes():
    primary = _six_minute_session(missing_index=3, provider="yahoo")
    fallback = [_six_minute_session(provider="alpaca_iex")[3]]
    service = _FakeMarketService(primary, fallback)
    observed_at = datetime(2026, 9, 10, 9, 36, 30, tzinfo=_ET)
    proxy = _FullSessionMarketServiceProxy(
        service,
        session_date=observed_at.date(),
        observed_at=observed_at,
        allow_shadow_fallback=True,
    )

    response = proxy.bars(INSTRUMENT_ID, "1m", 240, BINDING_ID)

    assert service.last_limit == FULL_SESSION_1M_LIMIT
    assert service.fallback_calls == 1
    assert len(response.bars) == 6
    recovered = next(
        bar for bar in response.bars
        if bar.start_time.astimezone(_ET).time() == time(9, 33)
    )
    assert recovered.provider == "alpaca_iex"
    coverage = assess_bar_coverage(
        response.bars,
        session_date=observed_at.date(),
        observed_at=observed_at,
        provider="merged-test",
    )
    assert coverage.ready is True
    assert coverage.missing_minutes == ()


def test_full_session_proxy_never_synthesizes_gap_missing_from_both_feeds():
    primary = _six_minute_session(missing_index=3, provider="yahoo")
    fallback = _six_minute_session(missing_index=3, provider="alpaca_iex")
    service = _FakeMarketService(primary, fallback)
    observed_at = datetime(2026, 9, 10, 9, 36, 30, tzinfo=_ET)
    proxy = _FullSessionMarketServiceProxy(
        service,
        session_date=observed_at.date(),
        observed_at=observed_at,
        allow_shadow_fallback=True,
    )

    response = proxy.bars(INSTRUMENT_ID, "1m", 240, BINDING_ID)

    assert len(response.bars) == 5
    coverage = assess_bar_coverage(
        response.bars,
        session_date=observed_at.date(),
        observed_at=observed_at,
        provider="unresolved-gap-test",
    )
    assert coverage.ready is False
    assert "CURRENT_SESSION_1M_GAPS" in coverage.reason_codes


def test_full_session_proxy_does_not_use_fallback_outside_shadow():
    primary = _six_minute_session(missing_index=3, provider="yahoo")
    fallback = _six_minute_session(provider="alpaca_iex")
    service = _FakeMarketService(primary, fallback)
    observed_at = datetime(2026, 9, 10, 9, 36, 30, tzinfo=_ET)
    proxy = _FullSessionMarketServiceProxy(
        service,
        session_date=observed_at.date(),
        observed_at=observed_at,
        allow_shadow_fallback=False,
    )

    response = proxy.bars(INSTRUMENT_ID, "1m", 240, BINDING_ID)

    assert service.last_limit == FULL_SESSION_1M_LIMIT
    assert service.fallback_calls == 0
    assert len(response.bars) == 5


def test_deep_recovery_overlay_rejects_ipdn_style_wide_risk():
    evaluation = DeepRecoveryShadowEvaluation(
        state="signal_ready",
        reason_code="DEEP_RECOVERY_30PCT_CONTINUATION_SHADOW",
        research_risk_pct=Decimal("14.55"),
        vwap_distance_pct=Decimal("19.10"),
        hard_gate_features={},
    )

    hardened = apply_deep_recovery_risk_policy(evaluation)

    assert hardened.signal_ready is False
    assert hardened.reason_code == "DEEP_RECOVERY_RISK_TOO_WIDE"
    overlay = hardened.hard_gate_features["deep_recovery_risk_overlay"]
    assert overlay["max_research_risk_pct"] == "8"


def test_deep_recovery_overlay_rejects_chase_even_with_acceptable_stop():
    evaluation = DeepRecoveryShadowEvaluation(
        state="signal_ready",
        reason_code="DEEP_RECOVERY_30PCT_CONTINUATION_SHADOW",
        research_risk_pct=Decimal("5"),
        vwap_distance_pct=Decimal("13"),
        hard_gate_features={},
    )

    hardened = apply_deep_recovery_risk_policy(evaluation)

    assert hardened.signal_ready is False
    assert hardened.reason_code == "DEEP_RECOVERY_EXTENSION_TOO_HIGH"


def test_deep_recovery_overlay_preserves_clean_signal_and_records_policy():
    evaluation = DeepRecoveryShadowEvaluation(
        state="signal_ready",
        reason_code="DEEP_RECOVERY_30PCT_CONTINUATION_SHADOW",
        research_risk_pct=Decimal("5"),
        vwap_distance_pct=Decimal("4"),
        hard_gate_features={},
    )

    hardened = apply_deep_recovery_risk_policy(evaluation)

    assert hardened.signal_ready is True
    assert hardened.reason_code == evaluation.reason_code
    assert hardened.hard_gate_features["deep_recovery_risk_overlay"]["version"]


def test_trend_continuation_can_signal_without_v2_l1_structure():
    result = evaluate_trend_continuation_shadow(
        _session_bars(),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
        stop_buffer_bps=Decimal("15"),
    )

    assert result.signal_ready is True
    assert result.reason_code == "TREND_CONTINUATION_BREAKOUT_SHADOW"
    assert result.ema9 is not None and result.ema20 is not None
    assert result.ema9 > result.ema20
    assert result.breakout_confirmed is True
    assert result.volume_ratio is not None and result.volume_ratio >= Decimal("1.25")
    assert result.research_risk_pct is not None
    assert result.research_risk_pct <= Decimal("8")
    assert result.execution_authority is False


def test_trend_continuation_rejects_late_chase_extension():
    result = evaluate_trend_continuation_shadow(
        _session_bars(last_three_step=Decimal("0.80")),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
        stop_buffer_bps=Decimal("15"),
    )

    assert result.signal_ready is False
    assert result.state == "too_extended"
    assert result.reason_code == "TREND_CONTINUATION_EMA_EXTENSION_TOO_HIGH"


def test_trend_signal_uses_bounded_event_api_when_recent_events_is_absent() -> None:
    session_date = datetime(2026, 9, 10, tzinfo=_ET).date()

    class Repository:
        def __init__(self):
            self.request = None

        def events_by_types_between(self, strategy_id, *, event_types, start_time, end_time, limit):
            self.request = (strategy_id, event_types, start_time, end_time, limit)
            return [SimpleNamespace(event_type="trend_continuation_shadow")]

    repository = Repository()
    events = _trend_events_for_session(repository, "strategy", session_date)

    assert len(events) == 1
    assert repository.request == (
        "strategy",
        ("trend_continuation_shadow",),
        datetime(2026, 9, 10, 4, 0, tzinfo=UTC),
        datetime(2026, 9, 11, 4, 0, tzinfo=UTC),
        10_000,
    )
