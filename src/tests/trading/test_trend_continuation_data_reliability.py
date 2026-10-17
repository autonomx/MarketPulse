from __future__ import annotations

from datetime import datetime, timezone

from app.apps.trading.strategy_session_evidence import _CurrentSessionMarketDataProxy


class _UnavailableHistory:
    def bars(self, *_args, **_kwargs):
        raise RuntimeError("Yahoo returned no bars")

    def execution_indicator_bars(self, *_args, **_kwargs):
        return []


def test_trend_shadow_history_failure_becomes_waiting_prefix_at_open() -> None:
    """The trend collector must not log Yahoo no-bars as a strategy failure.

    Trading-session reliability calls this proxy stack before evaluating the
    trend-continuation overlay. With neither primary nor fallback history at
    09:30:14 ET, the correct causal state is simply an empty/waiting prefix.
    """

    observed = datetime(2026, 9, 11, 13, 30, 14, tzinfo=timezone.utc)
    proxy = _CurrentSessionMarketDataProxy(
        _UnavailableHistory(),
        session_date=observed.date(),
        observed_at=observed,
    )

    response = proxy.bars("equity:NASDAQ:FTFT", "1m", 500, "binding")

    assert list(response.bars) == []
