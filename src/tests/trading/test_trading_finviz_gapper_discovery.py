from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from decimal import Decimal

from app.apps.trading.finviz_gapper_discovery import (
    FINVIZ_ATOMIC_SOURCE_LOCATOR,
    _finviz_symbols,
    discover_finviz_gappers,
    parse_finviz_top_gainer_symbols,
)


class Response:
    def __init__(self, *, text="", payload=None):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class Runtime:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_parse_finviz_symbols_preserves_rank_and_deduplicates():
    html = """
    <a href="quote.ashx?t=FNGR&p=d">FNGR</a>
    <a href="/quote.ashx?t=QNRX">QNRX</a>
    <a href="quote.ashx?t=FNGR">FNGR duplicate</a>
    <a href="quote.ashx?t=PSQL&ty=c">PSQL</a>
    """
    assert parse_finviz_top_gainer_symbols(html) == ["FNGR", "QNRX", "PSQL"]


def test_parse_finviz_symbols_supports_current_stock_links_and_query_order():
    html = """
    <a href="/stock?b=1&amp;p=d&amp;t=AEHL&amp;ty=c">AEHL</a>
    <a href="https://finviz.com/stock?p=d&amp;t=NCRA">NCRA</a>
    <a href="/stock?b=1&amp;p=d&amp;t=AEHL">AEHL duplicate</a>
    <a href="/news?t=IGNORED">news</a>
    """
    assert parse_finviz_top_gainer_symbols(html) == ["AEHL", "NCRA"]


def _chart_payload(now: datetime):
    # Diagnostic Yahoo evidence includes five same-clock historical sessions so
    # TOD RVOL itself is well-defined, but its fallback policy remains ineligible
    # for market-evidence-v2 AUTO PAPER authority.
    timestamps = []
    closes = []
    volumes = []
    for day in (21, 24, 25, 26, 27):
        prior_pre = datetime(2026, 8, day, 13, 18, tzinfo=timezone.utc)
        prior_regular = datetime(2026, 8, day, 19, 59, tzinfo=timezone.utc)
        timestamps.extend([int(prior_pre.timestamp()), int(prior_regular.timestamp())])
        closes.extend([Decimal("9.8"), Decimal("10.0")])
        volumes.extend([500, 200])
    current_pre = datetime(2026, 8, 28, 13, 18, tzinfo=timezone.utc)
    timestamps.append(int(current_pre.timestamp()))
    closes.append(Decimal("12.0"))
    volumes.append(1000)
    return {
        "chart": {
            "result": [{
                "meta": {"exchangeName": "NMS", "regularMarketPrice": 12.0},
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "close": [float(value) for value in closes],
                        "volume": volumes,
                    }]
                },
            }]
        }
    }


def _chart_payload_without_premarket():
    prior_regular = datetime(2026, 8, 27, 19, 59, tzinfo=timezone.utc)
    return {
        "chart": {
            "result": [{
                "meta": {"exchangeName": "NMS", "regularMarketPrice": 12.0},
                "timestamp": [int(prior_regular.timestamp())],
                "indicators": {
                    "quote": [{
                        "close": [10.0],
                        "volume": [200],
                    }]
                },
            }]
        }
    }


def test_finviz_discovery_uses_finviz_for_rank_and_yahoo_for_point_in_time_enrichment(monkeypatch):
    now = datetime(2026, 8, 28, 13, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.apps.trading.finviz_gapper_discovery._ALLOWED_DISCOVERY_SKEW_SECONDS",
        10**9,
    )
    finviz = Runtime([
        Response(text='<a href="quote.ashx?t=TEST">TEST</a>'),
    ])
    yahoo = Runtime([
        Response(payload=_chart_payload(now)),
        Response(payload={
            "quotes": [{
                "symbol": "TEST",
                "quoteType": "EQUITY",
                "exchange": "NMS",
                "marketCap": 50000000,
                "floatShares": 5000000,
                "bid": 11.95,
                "ask": 12.05,
            }]
        }),
    ])

    snapshot = discover_finviz_gappers(
        universe_id="finviz-test",
        evaluation_time=now,
        count=1,
        minimum_gap_pct=Decimal("10"),
        minimum_price=Decimal("1"),
        maximum_price=Decimal("20"),
        finviz_runtime=finviz,
        yahoo_runtime=yahoo,
    )

    assert snapshot.discovery_source == "finviz"
    assert snapshot.source_candidate_symbols == ("TEST",)
    assert snapshot.source_locator == FINVIZ_ATOMIC_SOURCE_LOCATOR
    assert len(snapshot.source_member_dispositions) == 1
    assert snapshot.source_member_dispositions[0].status == "materialized"
    assert len(snapshot.candidates) == 1
    candidate = snapshot.candidates[0]
    assert candidate.discovery_rank == 1
    assert candidate.instrument_id == "equity:NASDAQ:TEST"
    assert candidate.premarket_price == Decimal("12.0")
    assert candidate.previous_close == Decimal("10.0")
    assert candidate.gap_pct == Decimal("20.0")
    assert candidate.float_shares == Decimal("5000000")
    assert candidate.premarket_bar_count == 1
    assert candidate.tod_rvol == Decimal("2")
    assert candidate.premarket_liquidity is not None
    assert candidate.premarket_liquidity.provider == "yahoo"
    assert candidate.premarket_liquidity.baseline_session_count == 5
    assert candidate.market_data_complete is True
    assert "MARKET_EVIDENCE_POLICY_MISMATCH" not in candidate.data_quality_flags
    assert "YAHOO_PREMARKET_PRIMARY_RECOVERY" in candidate.research_quality_flags
    assert candidate.spread_bps is not None
    assert "finviz_top_gainers" in candidate.evidence_observed_at


def test_finviz_source_capture_is_one_request_and_never_paginates():
    html = "".join(
        f'<a href="quote.ashx?t=T{index:02d}">T{index:02d}</a>'
        for index in range(25)
    )
    finviz = Runtime([Response(text=html)])

    symbols, _ = _finviz_symbols(finviz, count=50)

    assert len(finviz.calls) == 1
    assert len(symbols) == 20
    assert symbols[0] == "T00"
    assert symbols[-1] == "T19"
    assert finviz.calls[0][1]["params"]["r"] == 1


def test_finviz_candidate_marks_missing_premarket_evidence_instead_of_fake_low_volume(monkeypatch):
    now = datetime(2026, 8, 28, 13, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.apps.trading.finviz_gapper_discovery._ALLOWED_DISCOVERY_SKEW_SECONDS",
        10**9,
    )
    finviz = Runtime([Response(text='<a href="quote.ashx?t=TEST">TEST</a>')])
    yahoo = Runtime([
        Response(payload=_chart_payload_without_premarket()),
        Response(payload={
            "quotes": [{
                "symbol": "TEST",
                "quoteType": "EQUITY",
                "exchange": "NMS",
                "marketCap": 50000000,
                "floatShares": 5000000,
                "bid": 11.95,
                "ask": 12.05,
            }]
        }),
    ])

    snapshot = discover_finviz_gappers(
        universe_id="finviz-incomplete",
        evaluation_time=now,
        count=1,
        minimum_gap_pct=Decimal("10"),
        minimum_price=Decimal("1"),
        maximum_price=Decimal("20"),
        finviz_runtime=finviz,
        yahoo_runtime=yahoo,
    )

    candidate = snapshot.candidates[0]
    assert candidate.premarket_volume == Decimal("0")
    assert candidate.premarket_bar_count == 0
    assert candidate.market_data_complete is False
    assert "PREMARKET_BARS_MISSING" in candidate.data_quality_flags
    assert "TOD_RVOL_MISSING" in candidate.data_quality_flags


class ExecutionProvider:
    def execution_observation(self, instrument_id):
        assert instrument_id == "equity:NASDAQ:TEST"
        return SimpleNamespace(spread_bps=Decimal("42"))


def test_finviz_discovery_can_use_alpaca_spread_as_research_evidence(monkeypatch):
    now = datetime(2026, 8, 28, 13, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.apps.trading.finviz_gapper_discovery._ALLOWED_DISCOVERY_SKEW_SECONDS",
        10**9,
    )
    finviz = Runtime([Response(text='<a href="quote.ashx?t=TEST">TEST</a>')])
    yahoo = Runtime([
        Response(payload=_chart_payload(now)),
        Response(payload={
            "quotes": [{
                "symbol": "TEST",
                "quoteType": "EQUITY",
                "exchange": "NMS",
                "marketCap": 50000000,
                "floatShares": 5000000,
            }]
        }),
    ])

    snapshot = discover_finviz_gappers(
        universe_id="finviz-alpaca-spread",
        evaluation_time=now,
        count=1,
        minimum_gap_pct=Decimal("10"),
        minimum_price=Decimal("1"),
        maximum_price=Decimal("20"),
        finviz_runtime=finviz,
        yahoo_runtime=yahoo,
        execution_provider=ExecutionProvider(),
    )

    candidate = snapshot.candidates[0]
    assert candidate.spread_bps == Decimal("42")
    assert "alpaca_iex_research_quote" in candidate.evidence_observed_at


def test_membership_only_materializes_finviz_symbol_when_yahoo_enrichment_is_down(monkeypatch):
    now = datetime(2026, 9, 8, 13, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.apps.trading.finviz_gapper_discovery._ALLOWED_DISCOVERY_SKEW_SECONDS",
        10**9,
    )
    finviz = Runtime([Response(text='<a href="quote.ashx?t=RECOVER">RECOVER</a>')])
    yahoo = Runtime([])

    snapshot = discover_finviz_gappers(
        universe_id="finviz-membership-recovery",
        evaluation_time=now,
        count=1,
        minimum_gap_pct=Decimal("99"),
        minimum_price=Decimal("100"),
        maximum_price=Decimal("200"),
        finviz_runtime=finviz,
        yahoo_runtime=yahoo,
        membership_only=True,
    )

    assert snapshot.source_candidate_symbols == ("RECOVER",)
    assert len(snapshot.candidates) == 1
    candidate = snapshot.candidates[0]
    assert candidate.instrument_id == "equity:US:RECOVER"
    assert candidate.discovery_rank == 1
    assert candidate.market_data_complete is False
    assert snapshot.source_member_dispositions[0].status == "materialized"
    assert "PROVISIONAL_US_INSTRUMENT_IDENTITY" in candidate.data_quality_flags

