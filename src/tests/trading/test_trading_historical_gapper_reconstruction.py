from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading import historical_gapper_reconstruction as reconstruction
from app.apps.trading.strategies.models import GapPullbackConfig


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeRuntime:
    def __init__(self, session_date: date):
        self.session_date = session_date
        self.bar_feeds: list[str] = []

    def get(self, url, *, params=None, headers=None, timeout=None, cancellation=None):
        assert headers == {
            "APCA-API-KEY-ID": "test-key",
            "APCA-API-SECRET-KEY": "test-secret",
        }
        if url.endswith("/v2/assets"):
            return FakeResponse([
                {
                    "symbol": "ABC",
                    "exchange": "NASDAQ",
                    "status": "active",
                    "tradable": True,
                }
            ])
        assert url.endswith("/v2/stocks/bars")
        self.bar_feeds.append(str(params["feed"]))
        timeframe = params["timeframe"]
        if timeframe == "1Day":
            previous = self.session_date - timedelta(days=1)
            return FakeResponse({
                "bars": {
                    "ABC": [
                        {"t": f"{previous.isoformat()}T20:00:00Z", "o": 8, "c": 8},
                        # Deliberately contradict the 09:20 gap. A causal 09:20
                        # reconstruction must ignore this future 09:30 daily open.
                        {"t": f"{self.session_date.isoformat()}T20:00:00Z", "o": 7, "c": 11},
                    ]
                },
                "next_page_token": None,
            })
        assert timeframe == "1Min"
        bars = []
        for days_back in range(5, 0, -1):
            prior = self.session_date - timedelta(days=days_back)
            if prior.weekday() < 5:
                bars.append({"t": f"{prior.isoformat()}T13:19:00Z", "c": 8, "v": 100})
        bars.append({"t": f"{self.session_date.isoformat()}T13:19:00Z", "c": 10.4, "v": 600})
        # Alpaca timestamps minute bars at their start. This 09:20 ET bar is
        # not complete at a 09:20 scan and must be causally invisible.
        bars.append({"t": f"{self.session_date.isoformat()}T13:20:00Z", "c": 99, "v": 999999})
        return FakeResponse({"bars": {"ABC": bars}, "next_page_token": None})


def test_recent_alpaca_reconstruction_builds_explicit_approximate_universe_without_daily_open_lookahead(monkeypatch) -> None:
    session_date = date(2026, 8, 18)
    monkeypatch.setattr(
        reconstruction,
        "alpaca_iex_auth_headers",
        lambda: {
            "APCA-API-KEY-ID": "test-key",
            "APCA-API-SECRET-KEY": "test-secret",
        },
    )
    config = GapPullbackConfig(
        strategy_version="1.1.0",
        minimum_gap_pct=Decimal("20"),
        minimum_premarket_dollar_volume=Decimal("0"),
        minimum_tod_rvol=Decimal("2"),
        universe_discovery_count=10,
    )

    result = reconstruction.reconstruct_recent_alpaca_gapper_universe(
        session_date=session_date,
        scan_time=time(9, 20),
        config=config,
        assumed_spread_bps=Decimal("40"),
        max_age_days=30,
        clock=datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc),
        runtime=FakeRuntime(session_date),
    )

    assert result.snapshot is not None
    assert result.fidelity == "reconstructed_current_listings_iex"
    assert result.snapshot.universe_id == "reconstructed-alpaca-2026-08-18-0920"
    assert len(result.snapshot.candidates) == 1
    candidate = result.snapshot.candidates[0]
    assert candidate.instrument_id == "equity:NASDAQ:ABC"
    assert candidate.gap_pct == Decimal("30.0")
    assert candidate.premarket_price == Decimal("10.4")
    assert candidate.premarket_volume == Decimal("600")
    assert candidate.tod_rvol == Decimal("6")
    assert candidate.spread_bps == Decimal("40")
    assert candidate.catalyst_evidence_ids == ()
    assert result.warnings


def test_historical_reconstruction_passes_selected_sip_feed(monkeypatch) -> None:
    session_date = date(2026, 8, 18)
    monkeypatch.setattr(
        reconstruction,
        "alpaca_iex_auth_headers",
        lambda: {
            "APCA-API-KEY-ID": "test-key",
            "APCA-API-SECRET-KEY": "test-secret",
        },
    )
    config = GapPullbackConfig(
        strategy_version="1.1.0",
        minimum_gap_pct=Decimal("20"),
        minimum_premarket_dollar_volume=Decimal("0"),
        minimum_tod_rvol=Decimal("2"),
        universe_discovery_count=10,
    )
    runtime = FakeRuntime(session_date)

    result = reconstruction.AlpacaHistoricalGapperReconstructor(
        start_date=session_date,
        end_date=session_date,
        config=config,
        assumed_spread_bps=Decimal("40"),
        max_age_days=30,
        clock=datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc),
        runtime=runtime,
        feed="sip",
    )(
        session_date=session_date,
        scan_time=time(9, 20),
    )

    assert result.snapshot is not None
    assert result.fidelity == "reconstructed_current_listings_sip"
    assert runtime.bar_feeds and set(runtime.bar_feeds) == {"sip"}



def test_seed_scan_batches_one_thousand_symbols_without_changing_data_contract(monkeypatch) -> None:
    session_date = date(2026, 8, 18)
    symbols = [f"S{index:04d}" for index in range(1001)]

    class BatchRuntime:
        def __init__(self):
            self.minute_seed_requests: list[tuple[str, ...]] = []

        def get(self, url, *, params=None, headers=None, timeout=None, cancellation=None):
            if url.endswith("/v2/assets"):
                return FakeResponse([
                    {"symbol": symbol, "exchange": "NASDAQ", "status": "active", "tradable": True}
                    for symbol in symbols
                ])
            assert url.endswith("/v2/stocks/bars")
            requested = tuple(str(params["symbols"]).split(","))
            if params["timeframe"] == "1Day":
                prior = session_date - timedelta(days=1)
                return FakeResponse({
                    "bars": {
                        symbol: [{"t": f"{prior.isoformat()}T20:00:00Z", "o": 10, "c": 10}]
                        for symbol in requested
                    },
                    "next_page_token": None,
                })
            self.minute_seed_requests.append(requested)
            return FakeResponse({"bars": {}, "next_page_token": None})

    monkeypatch.setattr(
        reconstruction,
        "alpaca_iex_auth_headers",
        lambda: {"APCA-API-KEY-ID": "test-key", "APCA-API-SECRET-KEY": "test-secret"},
    )
    runtime = BatchRuntime()
    config = GapPullbackConfig(
        strategy_version="1.1.0",
        minimum_gap_pct=Decimal("20"),
        minimum_premarket_dollar_volume=Decimal("0"),
        minimum_tod_rvol=Decimal("0"),
        universe_discovery_count=10,
    )

    result = reconstruction.AlpacaHistoricalGapperReconstructor(
        start_date=session_date,
        end_date=session_date,
        config=config,
        assumed_spread_bps=Decimal("40"),
        max_age_days=30,
        clock=datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc),
        runtime=runtime,
    )(
        session_date=session_date,
        scan_time=time(9, 20),
    )

    assert result.snapshot is None
    assert result.fidelity == "reconstructed_current_listings_iex"
    assert [len(batch) for batch in runtime.minute_seed_requests] == [1000, 1]
    assert reconstruction._SCAN_SEED_CHUNK_SIZE == 1000

def test_reconstructed_strategy_config_relaxes_only_unavailable_historical_evidence() -> None:
    config = GapPullbackConfig(
        strategy_version="1.1.0",
        require_catalyst_evidence=True,
        reject_dilution_flags=("atm", "warrants"),
        float_preference_mode="require",
    )

    adjusted, warnings = reconstruction.reconstructed_strategy_config(config)

    assert adjusted.require_catalyst_evidence is False
    assert adjusted.reject_dilution_flags == ()
    assert adjusted.float_preference_mode == "score"
    assert adjusted.minimum_gap_pct == config.minimum_gap_pct
    assert adjusted.minimum_quality_score == config.minimum_quality_score
    assert len(warnings) == 3
