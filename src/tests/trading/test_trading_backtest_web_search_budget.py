from __future__ import annotations

from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from app.apps.trading.historical_gapper_reconstruction import HistoricalUniverseReconstruction
from app.apps.trading.research.adapters.generic_web import GenericWebAdapter
from app.apps.trading.research.contracts import IssuerIdentity
from app.apps.trading.research.runtime_policy import (
    ExternalWebSearchForbiddenError,
    external_web_search_allowed,
    forbid_external_web_search_scope,
)
from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile
from app.apps.trading.strategy_range_backtest import StrategyRangeBacktestRequest, run_strategy_range_backtest


class _CountingSearchService:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, _query: str, _limit: int):
        self.calls += 1
        return SimpleNamespace(items=[])


def _identity() -> IssuerIdentity:
    now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
    return IssuerIdentity(
        identity_id="issuer-test",
        instrument_id="equity:TEST",
        symbol="TEST",
        exchange="NASDAQ",
        legal_name="Test Corp",
        source="test",
        captured_at=now,
        immutable_fingerprint="0" * 64,
    )


def test_generic_web_search_fails_before_provider_call_when_scope_forbids_it() -> None:
    search = _CountingSearchService()
    adapter = GenericWebAdapter(search_service=search, extractor_factory=lambda: None)

    adapter.find(_identity(), query="TEST catalyst")
    assert search.calls == 1

    with forbid_external_web_search_scope("trading_backtest"):
        with pytest.raises(
            ExternalWebSearchForbiddenError,
            match="external_web_search_forbidden:trading_backtest",
        ):
            adapter.find(_identity(), query="TEST historical catalyst")

    assert search.calls == 1
    assert external_web_search_allowed() is True


def test_range_backtest_installs_search_free_scope_around_reconstruction() -> None:
    observed_search_policy: list[bool] = []

    def reconstruction_probe(**_kwargs) -> HistoricalUniverseReconstruction:
        observed_search_policy.append(external_web_search_allowed())
        return HistoricalUniverseReconstruction(
            snapshot=None,
            fidelity="reconstructed_current_listings_iex",
            warnings=(),
            candidate_seed_count=0,
            active_asset_count=0,
            detail="No qualifying candidates.",
        )

    strategy = SimpleNamespace(
        strategy_id="strategy-test",
        strategy_kind="gap_pullback_v1",
        strategy_version="1.1.0",
        config=GapPullbackConfig(strategy_version="1.1.0"),
        risk=StrategyRiskProfile(),
    )
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
        universe_scan_time_et=time(9, 20),
        universe_mode="reconstructed_only",
    )

    result = run_strategy_range_backtest(
        strategy,
        (),
        request,
        reconstructor=reconstruction_probe,
    )

    assert observed_search_policy == [False]
    assert result.no_candidate_sessions == 1
    assert result.reconstructed_sessions == 1
    assert external_web_search_allowed() is True
