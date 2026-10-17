from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.apps.trading.alerts import TradingAlert
from app.apps.trading.alerts_repository import AlertListing
from app.apps.trading.alerts_monitor import TradingAlertMonitor


NOW = datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc)
INSTRUMENT = "crypto:BINANCE:spot:BTC-USDT"


def alert(*, expires_at: datetime | None = None, enabled: bool = True) -> TradingAlert:
    return TradingAlert(
        alert_id="chart-alert-1",
        instrument_id=INSTRUMENT,
        binding_id="binance:websocket_and_rest:crypto:BINANCE:spot:BTC-USDT",
        condition_type="price_above",
        threshold=Decimal("70000"),
        evaluation_policy={
            "interval": "2h",
            "allow_partial_bars": False,
            "formula_version": "omnix-indicators-v2",
        },
        enabled=enabled,
        cooldown_seconds=0,
        expires_at=expires_at,
        revision=1,
    )


class ExpiredOnlyRepository:
    def __init__(self) -> None:
        self.evaluations = 0

    def list_alerts(self, limit: int = 200):
        return [alert(expires_at=NOW - timedelta(minutes=1))]

    def list_alerts_report(self, limit: int = 200):
        return AlertListing(self.list_alerts(limit), [])

    def evaluate(self, evaluation):
        self.evaluations += 1
        return []


class NoMarketCalls:
    def __init__(self) -> None:
        self.calls = 0

    def bars(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("expired alerts must not request market data")


def test_alert_expiration_state_is_timezone_aware_and_deterministic() -> None:
    assert alert(expires_at=NOW - timedelta(seconds=1)).is_expired(NOW)
    assert not alert(expires_at=NOW + timedelta(seconds=1)).is_expired(NOW)
    assert not alert().is_expired(NOW)


def test_monitor_does_not_poll_or_evaluate_expired_chart_alerts() -> None:
    repository = ExpiredOnlyRepository()
    market = NoMarketCalls()
    monitor = TradingAlertMonitor(
        repository_factory=lambda: repository,
        market_service_factory=lambda: market,
        interval_seconds=5,
    )
    assert asyncio.run(monitor.run_once()) == 0
    assert market.calls == 0
    assert repository.evaluations == 0


def test_expiration_is_postgres_authority_and_evaluation_filter() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0026_trading_alert_expiration.sql"
    ).read_text()
    implementation = chr(10).join(
        Path(f"src/app/apps/trading/{name}.py").read_text() for name in ("alerts", "alerts_repository")
    )
    assert "ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ" in migration
    assert "expires_at > %s" in implementation
    assert '"expires_at": alert.expires_at.isoformat()' in implementation


def test_a_line_alert_keeps_its_drawing_link() -> None:
    """TVP-1.4: the drawing and level a line alert follows round-trip through the parameters."""
    from app.apps.trading.alerts import TradingAlertParameters

    parameters = TradingAlertParameters(drawing_id="channel-1", drawing_level="lower")
    restored = TradingAlertParameters.model_validate(parameters.model_dump(mode="json"))
    assert (restored.drawing_id, restored.drawing_level) == ("channel-1", "lower")
    assert TradingAlertParameters().drawing_id is None
